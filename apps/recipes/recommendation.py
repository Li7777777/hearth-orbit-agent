"""首页菜谱推荐服务。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from django.db.models import Count
from django.utils import timezone

from apps.dishes.models import Dish

from .models import Recipe, RecipeRecommendationHistory

MATCH_THRESHOLD = 0.6


@dataclass
class DishCandidate:
    id: int
    name: str
    normalized: str
    days_in_stock: int


def _normalize_name(name: str) -> str:
    return re.sub(r'[\s·\-—_*#•,，/（）()【】\[\]]+', '', (name or '').lower())


def _load_dish_candidates(today):
    candidates = []
    dishes = Dish.objects.filter(is_active=True).only('id', 'name', 'stock_in_date')
    for dish in dishes:
        normalized = _normalize_name(dish.name)
        if not normalized:
            continue
        days_in_stock = max((today - dish.stock_in_date).days, 0) if dish.stock_in_date else 0
        candidates.append(DishCandidate(
            id=dish.id,
            name=dish.name,
            normalized=normalized,
            days_in_stock=days_in_stock,
        ))
    return candidates


def _best_match(name: str, candidates: list[DishCandidate], cache: dict[str, tuple[Optional[DishCandidate], float]]):
    normalized = _normalize_name(name)
    if len(normalized) < 2:
        return None, 0.0

    if normalized in cache:
        return cache[normalized]

    best_candidate = None
    best_score = 0.0
    for candidate in candidates:
        if candidate.normalized in normalized or normalized in candidate.normalized:
            score = 1.0
        else:
            score = SequenceMatcher(None, normalized, candidate.normalized).ratio()

        if score > best_score:
            best_score = score
            best_candidate = candidate

    cache[normalized] = (best_candidate, best_score)
    return best_candidate, best_score


def _history_count_map():
    rows = (
        RecipeRecommendationHistory.objects
        .values('recipe_id')
        .annotate(total=Count('id'))
    )
    return {row['recipe_id']: int(row['total'] or 0) for row in rows}


def recommend_homepage_recipes(limit: int = 5, mark_recommended: bool = True):
    today = timezone.localdate()
    candidates = _load_dish_candidates(today)
    if not candidates:
        return []

    recipes = (
        Recipe.objects
        .filter(is_published=True)
        .select_related('category')
        .prefetch_related('ingredients')
    )
    history_counts = _history_count_map()
    match_cache: dict[str, tuple[Optional[DishCandidate], float]] = {}

    recommendations = []
    for recipe in recipes:
        ingredients = list(recipe.ingredients.all())
        total_ingredients = len(ingredients)
        if total_ingredients == 0:
            continue

        matched = {}
        for ing in ingredients:
            candidate, score = _best_match(ing.name, candidates, match_cache)
            if candidate and score >= MATCH_THRESHOLD:
                matched[candidate.id] = candidate

        matched_count = len(matched)
        if matched_count == 0:
            continue

        avg_stock_days = sum(item.days_in_stock for item in matched.values()) / matched_count
        coverage = matched_count / total_ingredients
        history_count = history_counts.get(recipe.id, 0)

        # 权重：
        # 1) 命中食材数越多越高
        # 2) 食材入库时间越长越高
        # 3) 曾推荐次数越多越低
        score = (
            matched_count * 6.0
            + coverage * 10.0
            + avg_stock_days * 0.35
            - history_count * 2.0
        )

        recommendations.append({
            'recipe': recipe,
            'score': round(score, 2),
            'matched_ingredient_count': matched_count,
            'total_ingredient_count': total_ingredients,
            'avg_stock_days': round(avg_stock_days, 1),
            'history_count': history_count,
            'matched_dish_names': [item.name for item in list(matched.values())[:3]],
        })

    recommendations.sort(
        key=lambda item: (
            item['score'],
            item['matched_ingredient_count'],
            item['avg_stock_days'],
            item['recipe'].updated_at,
        ),
        reverse=True,
    )
    top = recommendations[:limit]

    if mark_recommended:
        for item in top:
            RecipeRecommendationHistory.objects.get_or_create(
                recipe=item['recipe'],
                recommended_date=today,
                defaults={
                    'score': item['score'],
                    'matched_ingredient_count': item['matched_ingredient_count'],
                },
            )

    return top
