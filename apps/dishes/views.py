import tempfile
from pathlib import Path

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.ocr.models import VisionProviderConfig
from apps.ocr.vision import (
    VisionConfigError,
    VisionDishRecognitionResult,
    VisionProviderError,
    check_vision_config,
    recognize_dish_image_with_vision,
)

from .forms import DishForm
from .models import Dish, DishCategory
from .services import infer_dish_category


def dish_list(request):
    category_id = request.GET.get('category', '')

    dishes = Dish.objects.select_related('category').all()
    if category_id:
        dishes = dishes.filter(category_id=category_id)

    show_inactive = request.GET.get('inactive') == '1'
    if not show_inactive:
        dishes = dishes.filter(is_active=True)

    categories = DishCategory.objects.all()

    context = {
        'dishes': dishes,
        'categories': categories,
        'current_category': category_id,
        'show_inactive': show_inactive,
    }

    # HTMX 局部请求
    if request.headers.get('HX-Request'):
        return render(request, 'dishes/_list_partial.html', context)

    return render(request, 'dishes/list.html', context)


def dish_create(request):
    if request.method == 'POST':
        form = DishForm(request.POST, request.FILES)
        if form.is_valid():
            dish = form.save(commit=False)
            dish.created_by = request.user
            _apply_deactivation_metadata(dish)
            dish.save()
            messages.success(request, f'食材「{dish.name}」已创建')
            return redirect('dishes:list')
    else:
        form = DishForm()
    return render(request, 'dishes/form.html', {'form': form, 'title': '新增食材'})


@require_POST
def dish_recognize_image(request):
    image_file = request.FILES.get('image')
    if not image_file:
        return JsonResponse({'ok': False, 'error': '请先拍照或选择食材图片'}, status=400)
    if image_file.content_type and not image_file.content_type.startswith('image/'):
        return JsonResponse({'ok': False, 'error': '请选择图片文件'}, status=400)

    vision_config = VisionProviderConfig.get_solo()
    vision_check = check_vision_config(vision_config)
    if not vision_check.ok:
        return JsonResponse({
            'ok': False,
            'error': f'视觉辅助配置未完整：{"；".join(vision_check.messages)}',
            'settings_url': reverse('ocr:vision_settings'),
        }, status=400)

    image_path = _write_upload_to_temp_file(image_file)
    try:
        result = recognize_dish_image_with_vision(vision_config, image_path)
    except VisionConfigError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except VisionProviderError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=502)
    finally:
        image_path.unlink(missing_ok=True)

    fields = _build_dish_recognition_fields(result)
    return JsonResponse({
        'ok': True,
        'message': f'已通过 {result.provider_label} 识别并预填',
        'provider_label': result.provider_label,
        'raw_text': result.raw_text,
        'fields': fields,
    })


def dish_detail(request, pk):
    dish = get_object_or_404(Dish, pk=pk)
    recent_items = dish.order_items.select_related('order').order_by('-order__order_date')[:20]
    return render(request, 'dishes/detail.html', {'dish': dish, 'recent_items': recent_items})


def dish_edit(request, pk):
    dish = get_object_or_404(Dish, pk=pk)
    if request.method == 'POST':
        form = DishForm(request.POST, request.FILES, instance=dish)
        if form.is_valid():
            dish = form.save(commit=False)
            _apply_deactivation_metadata(dish)
            dish.save()
            messages.success(request, f'食材「{dish.name}」已更新')
            return redirect('dishes:detail', pk=pk)
    else:
        form = DishForm(instance=dish)
    return render(request, 'dishes/form.html', {'form': form, 'title': '编辑食材', 'dish': dish})


def dish_delete(request, pk):
    dish = get_object_or_404(Dish, pk=pk)
    if request.method == 'POST':
        _deactivate_dish(dish, reason='eaten')
        messages.success(request, f'食材「{dish.name}」已标记为吃完')
    return redirect('dishes:list')


def dish_mark_eaten(request, pk):
    dish = get_object_or_404(Dish, pk=pk)
    if request.method == 'POST':
        _deactivate_dish(dish, reason='eaten')
        messages.success(request, f'食材「{dish.name}」已标记为吃完')
    return redirect('dishes:list')


def dish_mark_discarded(request, pk):
    dish = get_object_or_404(Dish, pk=pk)
    if request.method == 'POST':
        _deactivate_dish(dish, reason='discarded')
        messages.success(request, f'食材「{dish.name}」已标记为丢掉')
    return redirect('dishes:list')


def dish_bulk_mark_eaten(request):
    return _bulk_deactivate_from_post(request, reason='eaten', reason_label='吃完了')


def dish_bulk_mark_discarded(request):
    return _bulk_deactivate_from_post(request, reason='discarded', reason_label='丢掉了')


def _apply_deactivation_metadata(dish: Dish):
    if dish.is_active:
        dish.deactivation_reason = ''
        dish.deactivated_at = None
        return

    if not dish.deactivation_reason:
        dish.deactivation_reason = 'eaten'
    if not dish.deactivated_at:
        dish.deactivated_at = timezone.localdate()


def _deactivate_dish(dish: Dish, reason: str):
    dish.is_active = False
    dish.deactivation_reason = reason
    dish.deactivated_at = timezone.localdate()
    dish.save(update_fields=['is_active', 'deactivation_reason', 'deactivated_at'])


def _bulk_deactivate_from_post(request, reason: str, reason_label: str):
    if request.method != 'POST':
        return redirect('dishes:list')

    next_url = request.POST.get('next') or reverse('dishes:list')
    dish_ids = _parse_dish_ids(request.POST.get('dish_ids', ''))
    if not dish_ids:
        messages.warning(request, '请先选择要操作的食材')
        return redirect(next_url)

    affected = Dish.objects.filter(pk__in=dish_ids, is_active=True).update(
        is_active=False,
        deactivation_reason=reason,
        deactivated_at=timezone.localdate(),
    )
    if affected:
        messages.success(request, f'已将{affected}个食材标记为{reason_label}')
    else:
        messages.info(request, '所选食材已是停用状态')
    return redirect(next_url)


def _parse_dish_ids(value: str):
    ids = set()
    for part in value.split(','):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return sorted(ids)


def _write_upload_to_temp_file(image_file) -> Path:
    suffix = Path(image_file.name or '').suffix.lower()
    if suffix not in {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}:
        suffix = '.png'

    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        with tmp:
            for chunk in image_file.chunks():
                tmp.write(chunk)
    except Exception:
        Path(tmp.name).unlink(missing_ok=True)
        raise
    return Path(tmp.name)


def _build_dish_recognition_fields(result: VisionDishRecognitionResult) -> dict:
    category = _resolve_recognized_category(result)
    return {
        'name': result.name,
        'category_id': category.id if category else '',
        'category_name': category.name if category else result.category,
        'unit': result.unit,
        'specification': result.specification,
        'default_price': _format_price(result.default_price),
        'storage': result.storage if result.storage in dict(Dish.STORAGE_CHOICES) else '',
        'description': result.description,
        'confidence': result.confidence if result.confidence is not None else '',
    }


def _resolve_recognized_category(result: VisionDishRecognitionResult):
    category_name = (result.category or '').strip()
    if category_name:
        category = DishCategory.objects.filter(name=category_name).first()
        if category:
            return category
    if result.name:
        return infer_dish_category(result.name)
    return None


def _format_price(value):
    if value is None:
        return ''
    return f'{value:.2f}'.rstrip('0').rstrip('.')
