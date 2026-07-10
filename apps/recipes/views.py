import json
import re
from difflib import SequenceMatcher

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe

from apps.dishes.models import Dish

from .external_source import SOURCE_NAME, pull_and_sync
from .forms import RecipeForm
from .models import Recipe, RecipeCategory, RecipeIngredient, RecipeStep


def recipe_list(request):
    category_id = request.GET.get('category', '')
    q = request.GET.get('q', '').strip()

    recipes = (
        Recipe.objects
        .select_related('category', 'dish')
        .prefetch_related('ingredients')
        .filter(is_published=True)
    )
    if category_id:
        recipes = recipes.filter(category_id=category_id)
    if q:
        recipes = _search_recipes_by_all_fields(recipes, q)

    dish_candidates = _load_dish_candidates()
    recipes = _sort_recipes_by_match_count(list(recipes), dish_candidates)

    paginator = Paginator(recipes, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    categories = RecipeCategory.objects.all()

    return render(request, 'recipes/list.html', {
        'page_obj': page_obj,
        'categories': categories,
        'current_category': category_id,
        'query': q,
    })


def recipe_create(request):
    if request.method == 'POST':
        form = RecipeForm(request.POST, request.FILES)
        if form.is_valid():
            recipe = form.save(commit=False)
            recipe.created_by = request.user
            recipe.save()
            _save_ingredients(request, recipe)
            _save_steps(request, recipe)
            messages.success(request, f'菜谱「{recipe.name}」已创建')
            return redirect('recipes:detail', pk=recipe.pk)
    else:
        form = RecipeForm()

    categories = RecipeCategory.objects.all()
    return render(request, 'recipes/form.html', {
        'form': form,
        'title': '新增菜谱',
        'categories': categories,
    })


def recipe_detail(request, pk):
    recipe = get_object_or_404(
        Recipe.objects.select_related('category', 'dish')
        .prefetch_related('ingredients', 'steps'),
        pk=pk,
    )
    dish_candidates = _load_dish_candidates()
    linked_recipe_previews = _build_linked_recipe_previews(recipe)
    main_ingredients = _mark_ingredient_matches(recipe.ingredients.filter(is_main=True), dish_candidates)
    sub_ingredients = _mark_ingredient_matches(recipe.ingredients.filter(is_main=False), dish_candidates)
    _apply_recipe_link_markup(main_ingredients, linked_recipe_previews, 'name', 'name_html')
    _apply_recipe_link_markup(sub_ingredients, linked_recipe_previews, 'name', 'name_html')
    steps = list(recipe.steps.all())
    _apply_recipe_link_markup(steps, linked_recipe_previews, 'description', 'description_html')
    return render(request, 'recipes/detail.html', {
        'recipe': recipe,
        'recipe_description_html': _linkify_recipe_text(recipe.description, linked_recipe_previews),
        'main_ingredients': main_ingredients,
        'sub_ingredients': sub_ingredients,
        'steps': steps,
        'linked_recipe_previews': linked_recipe_previews,
    })


def recipe_edit(request, pk):
    recipe = get_object_or_404(Recipe, pk=pk)
    if request.method == 'POST':
        form = RecipeForm(request.POST, request.FILES, instance=recipe)
        if form.is_valid():
            form.save()
            recipe.ingredients.all().delete()
            recipe.steps.all().delete()
            _save_ingredients(request, recipe)
            _save_steps(request, recipe)
            messages.success(request, f'菜谱「{recipe.name}」已更新')
            return redirect('recipes:detail', pk=pk)
    else:
        form = RecipeForm(instance=recipe)

    ingredients_data = list(
        recipe.ingredients.values('name', 'amount', 'is_main').order_by('-is_main', 'sort_order')
    )
    steps_data = list(
        recipe.steps.values('step_number', 'description').order_by('step_number')
    )

    categories = RecipeCategory.objects.all()
    return render(request, 'recipes/form.html', {
        'form': form,
        'title': '编辑菜谱',
        'recipe': recipe,
        'categories': categories,
        'ingredients_json': json.dumps(ingredients_data, ensure_ascii=False),
        'steps_json': json.dumps(steps_data, ensure_ascii=False),
    })


def recipe_delete(request, pk):
    recipe = get_object_or_404(Recipe, pk=pk)
    if request.method == 'POST':
        recipe.delete()
        messages.success(request, '菜谱已删除')
    return redirect('recipes:list')


def sync_external_recipes(request):
    if request.method != 'POST':
        return redirect('recipes:list')

    try:
        result = pull_and_sync(refresh=True, prune=True)
    except Exception as exc:
        messages.error(request, f'外部菜谱同步失败：{exc}')
        return redirect('recipes:list')

    messages.success(
        request,
        (
            f"外部菜谱同步完成：扫描 {result['files_total']}，"
            f"导入 {result['imported']}，新增 {result['created']}，更新 {result['updated']}"
        ),
    )
    return redirect('recipes:list')


# ── 辅助函数 ────────────────────────────────────

def _save_ingredients(request, recipe):
    names = request.POST.getlist('ingredient_name[]')
    amounts = request.POST.getlist('ingredient_amount[]')
    is_mains = request.POST.getlist('ingredient_is_main[]')

    objs = []
    for i, name in enumerate(names):
        name = name.strip()
        if not name:
            continue
        amount = amounts[i].strip() if i < len(amounts) else ''
        is_main = is_mains[i] == '1' if i < len(is_mains) else True
        objs.append(RecipeIngredient(
            recipe=recipe, name=name, amount=amount,
            is_main=is_main, sort_order=i,
        ))
    if objs:
        RecipeIngredient.objects.bulk_create(objs)


def _normalize_match_text(name: str) -> str:
    cleaned = re.sub(r'[\s·\-—_*#•,，/（）()【】\[\]]+', '', (name or '').lower())
    return cleaned.strip()


def _search_recipes_by_all_fields(recipes, query: str):
    tokens = [t for t in re.split(r'\s+', (query or '').strip()) if t]
    if not tokens:
        return recipes

    for token in tokens:
        token_q = (
            Q(name__icontains=token)
            | Q(description__icontains=token)
            | Q(tips__icontains=token)
            | Q(difficulty__icontains=token)
            | Q(category__name__icontains=token)
            | Q(dish__name__icontains=token)
            | Q(source__icontains=token)
            | Q(source_id__icontains=token)
            | Q(source_url__icontains=token)
            | Q(media_type__icontains=token)
            | Q(media_title__icontains=token)
            | Q(media_url__icontains=token)
            | Q(ingredients__name__icontains=token)
            | Q(ingredients__amount__icontains=token)
            | Q(steps__description__icontains=token)
        )

        if token.isdigit():
            number = int(token)
            token_q = token_q | Q(servings=number) | Q(prep_time_minutes=number) | Q(cook_time_minutes=number)

        recipes = recipes.filter(token_q)

    return recipes.distinct()


def _best_dish_match(name, dish_candidates, cache=None):
    normalized = _normalize_match_text(name)
    if len(normalized) < 2:
        return '', 0.0

    if cache is not None and normalized in cache:
        return cache[normalized]

    best_name = ''
    best_score = 0.0
    for dish_name, dish_normalized in dish_candidates:
        if dish_normalized in normalized or normalized in dish_normalized:
            score = 1.0
        else:
            score = SequenceMatcher(None, normalized, dish_normalized).ratio()
        if score > best_score:
            best_score = score
            best_name = dish_name

    if cache is not None:
        cache[normalized] = (best_name, best_score)
    return best_name, best_score


def _load_dish_candidates():
    candidates = []
    for dish in Dish.objects.filter(is_active=True).only('name'):
        normalized = _normalize_match_text(dish.name)
        if not normalized:
            continue
        candidates.append((dish.name, normalized))
    return candidates


def _sort_recipes_by_match_count(recipes, dish_candidates, threshold: float = 0.6):
    match_cache = {}
    for recipe in recipes:
        matched = 0
        total = 0
        for ing in recipe.ingredients.all():
            total += 1
            _, score = _best_dish_match(ing.name, dish_candidates, cache=match_cache)
            if score >= threshold:
                matched += 1
        recipe.matched_ingredient_count = matched
        recipe.total_ingredient_count = total

    recipes.sort(
        key=lambda r: (
            getattr(r, 'matched_ingredient_count', 0),
            getattr(r, 'total_ingredient_count', 0),
            r.updated_at,
        ),
        reverse=True,
    )
    return recipes


def _mark_ingredient_matches(ingredients, dish_candidates, threshold: float = 0.6):
    items = list(ingredients)
    match_cache = {}
    for ing in items:
        ing.is_dish_matched = False
        ing.matched_dish_name = ''
        ing.match_score = 0

        best_name, best_score = _best_dish_match(
            getattr(ing, 'name', ''),
            dish_candidates,
            cache=match_cache,
        )

        if best_score >= threshold:
            ing.is_dish_matched = True
            ing.matched_dish_name = best_name
            ing.match_score = round(best_score, 2)
    return items


def _build_linked_recipe_previews(recipe: Recipe) -> dict:
    source_links = recipe.external_links if isinstance(recipe.external_links, list) else []
    source_ids = [
        link.get('source_id')
        for link in source_links
        if isinstance(link, dict) and link.get('source_id')
    ]
    if not source_ids:
        return {}

    targets = (
        Recipe.objects
        .select_related('category')
        .prefetch_related('ingredients')
        .filter(source=SOURCE_NAME, source_id__in=source_ids, is_published=True)
    )
    target_map = {target.source_id: target for target in targets}

    previews = {}
    for link in source_links:
        if not isinstance(link, dict):
            continue
        source_id = link.get('source_id', '')
        target = target_map.get(source_id)
        if not target:
            continue
        ingredients = list(target.ingredients.all()[:4])
        previews[source_id] = {
            'key': source_id,
            'trigger_text': link.get('text', '') or target.name,
            'name': target.name,
            'category': target.category.name if target.category else '',
            'category_icon': target.category.icon if target.category else '',
            'description': _preview_text(target.description),
            'image_url': target.display_image_url,
            'image_alt': target.display_image_alt,
            'ingredients': [ing.name for ing in ingredients],
            'detail_url': reverse('recipes:detail', args=[target.pk]),
            'source_url': target.source_url or link.get('source_url', ''),
        }
    return previews


def _preview_text(value: str, limit: int = 90) -> str:
    text = re.sub(r'\s+', ' ', (value or '').strip())
    if len(text) <= limit:
        return text
    return f'{text[:limit].rstrip()}...'


def _apply_recipe_link_markup(items, previews: dict, source_attr: str, target_attr: str):
    for item in items:
        setattr(item, target_attr, _linkify_recipe_text(getattr(item, source_attr, ''), previews))


def _linkify_recipe_text(text: str, previews: dict):
    if not text:
        return ''

    links = sorted(
        [
            (preview.get('trigger_text') or preview.get('name') or '', key)
            for key, preview in previews.items()
        ],
        key=lambda item: len(item[0]),
        reverse=True,
    )
    if not links:
        return mark_safe(escape(text))

    rendered = []
    index = 0
    while index < len(text):
        match = None
        for label, key in links:
            if label and text.startswith(label, index):
                match = (label, key)
                break

        if match:
            label, key = match
            rendered.append(
                '<button type="button" class="recipe-inline-link" '
                f'data-recipe-link-key="{escape(key)}">{escape(label)}</button>'
            )
            index += len(label)
            continue

        rendered.append(escape(text[index]))
        index += 1

    return mark_safe(''.join(rendered))


def _save_steps(request, recipe):
    descriptions = request.POST.getlist('step_desc[]')

    objs = []
    for i, desc in enumerate(descriptions):
        desc = desc.strip()
        if not desc:
            continue
        objs.append(RecipeStep(
            recipe=recipe, step_number=i + 1, description=desc,
        ))
    if objs:
        RecipeStep.objects.bulk_create(objs)
