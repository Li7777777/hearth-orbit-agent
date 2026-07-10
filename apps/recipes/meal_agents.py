"""Daily three-meal recommendation with a lightweight multi-agent pipeline.

The service is intentionally database-migration free: it builds an explainable
plan from the current inventory, eaten/discarded history, orders, prices, and
recipes. If an OpenAI-compatible model is configured, it can optionally act as a
critic/reranker; the deterministic agents remain the fallback and source of
truth so the dashboard stays fast and usable.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from http.client import RemoteDisconnected
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db.models import Count, Sum
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone

from apps.dishes.models import Dish
from apps.ocr.models import LLMProviderConfig, VisionProviderConfig
from apps.orders.models import DailyDishStatistic, Order

from .models import Recipe, RecipeRecommendationHistory

MATCH_THRESHOLD = 0.6
HISTORY_DAYS = 90
ORDER_DAYS = 30
PROCUREMENT_DAYS = 180
MODEL_RATE_WINDOW_SECONDS = 60.0
MODEL_RATE_LIMIT_RETRY_SECONDS = 60.0

_MODEL_RATE_LOCK = threading.Lock()
_MODEL_RATE_TIMESTAMPS: dict[str, deque[float]] = defaultdict(deque)
_MODEL_SEMAPHORES_LOCK = threading.Lock()
_MODEL_SEMAPHORES: dict[tuple[str, int], threading.BoundedSemaphore] = {}

MEAL_SLOTS = [
    {
        'key': 'breakfast',
        'label': '早餐',
        'intent': '快手、温和、主食或蛋奶豆浆优先，避免过油过重。',
    },
    {
        'key': 'lunch',
        'label': '午餐',
        'intent': '能量充足，优先处理库存里的肉蛋蔬菜，可搭配主食。',
    },
    {
        'key': 'dinner',
        'label': '晚餐',
        'intent': '清爽均衡，优先蔬菜、汤粥锅煲和不太油重的库存食材。',
    },
]

BREAKFAST_HINTS = {
    '早餐', '粥', '豆浆', '牛奶', '茶', '馒头', '花卷', '包子', '汤包', '烧麦',
    '春卷', '蛋饼', '油条', '饭团', '粢饭', '元宵', '馄饨', '面', '饼', '蛋',
}
LUNCH_HINTS = {
    '盖饭', '炒', '炖', '烧', '牛肉', '鸡', '鱼', '肉', '排骨', '豆腐', '米饭',
    '面', '土豆', '茄子', '番茄', '鸡蛋', '时蔬', '小炒', '红烧',
}
DINNER_HINTS = {
    '汤', '粥', '蒸', '清炒', '凉拌', '青菜', '娃娃菜', '西兰花', '菠菜',
    '豆腐', '鱼', '虾', '菌', '木耳', '砂锅', '炖', '番茄', '鸡蛋',
}
HEAVY_HINTS = {'炸', '烤', '大鸡腿', '肥肠', '扣肉', '麻辣', '辣子'}
FREE_SEASONING_CATEGORY_NAMES = {'调料免费', '免费佐料'}
FREE_SEASONING_KEYWORDS = {
    '葱', '姜', '蒜', '香菜', '小葱', '生抽', '老抽', '蚝油', '料酒', '醋',
    '盐', '食盐', '白糖', '冰糖', '胡椒', '花椒', '八角', '桂皮', '香叶',
    '辣椒粉', '孜然', '香油', '芝麻', '鸡精', '味精', '调料', '佐料',
}

FULL_LLM_AGENT_SPECS = [
    {
        'key': 'inventory',
        'name': '库存策略 Agent',
        'vote_name': '库存LLM',
        'role': '只看库存、入库天数、储存方式和菜谱用料，判断哪些菜今天更应该先做。',
        'system': (
            '你是库存策略 Agent。你只基于给定库存和菜谱目录判断先入先出、易腐优先和库存匹配，'
            '不能编造 recipe_id 或不存在的食材。只返回 JSON。'
        ),
    },
    {
        'key': 'memory',
        'name': '偏好记忆 Agent',
        'vote_name': '记忆LLM',
        'role': '只看吃完/丢弃历史和历史推荐次数，判断偏好、厌倦和避浪费风险。',
        'system': (
            '你是偏好记忆 Agent。你只基于吃完、丢弃和历史推荐信号判断家庭/小餐饮偏好，'
            '同时避免重复推荐造成厌倦。只返回 JSON。'
        ),
    },
    {
        'key': 'cost',
        'name': '成本风险 Agent',
        'vote_name': '成本LLM',
        'role': '只看近期订单、价格、估算成本和高价值库存，判断成本效率与沉没风险。',
        'system': (
            '你是成本风险 Agent。你只基于近期订单、金额、单价和估算成本判断哪些菜更能减少浪费、'
            '控制成本。只返回 JSON。'
        ),
    },
    {
        'key': 'purchase',
        'name': '采购提醒 Agent',
        'vote_name': '采购LLM',
        'role': '只看常用但未启用、近期高消耗和免费佐料信号，判断哪些食材需要补货或核对余量。',
        'system': (
            '你是采购提醒 Agent。你只基于采购提醒、当前库存、近期订单和免费佐料信号判断补货优先级，'
            '不要把免费佐料当作高成本食材，也不能编造 recipe_id。只返回 JSON。'
        ),
    },
]


@dataclass(frozen=True)
class DishSignal:
    id: int
    name: str
    normalized: str
    category_name: str
    storage: str
    days_in_stock: int
    default_price: float
    recent_quantity: float
    recent_amount: float
    recent_order_count: int
    eaten_count: float
    discarded_count: float
    is_free_seasoning: bool

    @property
    def effective_price(self) -> float:
        if self.recent_quantity > 0 and self.recent_amount > 0:
            return round(self.recent_amount / self.recent_quantity, 2)
        return self.default_price

    @property
    def freshness_score(self) -> float:
        storage_factor = {'冷藏': 1.35, '常温': 0.85, '冷冻': 0.45}.get(self.storage, 0.75)
        return min(self.days_in_stock * storage_factor, 18)

    @property
    def waste_rescue_score(self) -> float:
        return min(self.discarded_count * 2.2 + self.freshness_score * 0.6, 18)

    @property
    def preference_score(self) -> float:
        return min(self.eaten_count * 1.3 + self.recent_order_count * 0.7 + self.recent_quantity * 0.12, 14)

    @property
    def value_score(self) -> float:
        if self.effective_price <= 0:
            return 0
        # High-value ingredients get a gentle nudge so they do not become waste.
        return min(self.effective_price / 4, 12)


def build_daily_meal_plan(
    limit_per_meal: int = 3,
    mark_recommended: bool = True,
    use_llm: bool | None = None,
) -> dict[str, Any]:
    """Build today's breakfast/lunch/dinner plan with explainable agent votes."""
    today = timezone.localdate()
    context = _build_agent_context(today)
    candidates_by_meal = _rank_candidates_by_meal(context, limit=max(limit_per_meal * 4, 8))
    plan = _select_meals(today, candidates_by_meal, context, limit_per_meal)

    if use_llm is None:
        use_llm = bool(_meal_llm_settings().get('enabled'))
    if use_llm:
        plan = _refine_plan_with_llm(plan, candidates_by_meal, context)
    else:
        plan['llm_status'] = {
            'enabled': False,
            'used': False,
            'provider_label': 'DeepSeek / OpenAI-compatible',
            'message': '未启用大模型复核，当前使用本地多 Agent 确定性评分。',
        }

    if mark_recommended:
        _mark_meal_plan_recommended(plan, today)
    return plan


def build_full_llm_multi_agent_meal_plan(
    limit_per_meal: int = 3,
    mark_recommended: bool = True,
) -> dict[str, Any]:
    """Build a second three-meal plan whose expert judgments are all made by LLM agents."""
    today = timezone.localdate()
    base_plan = _empty_full_llm_plan(today)
    config = _resolve_full_llm_config()
    if not config:
        base_plan['llm_status'] = {
            'enabled': True,
            'used': False,
            'provider_label': 'Full-LLM Multi-Agent',
            'message': '全大模型方案已启用，但 API Key、Base URL 或模型未配置完整。',
        }
        return base_plan

    context = _build_agent_context(today)
    llm_context = _build_full_llm_context(today, context, max_recipes=_full_llm_max_recipes())
    if not llm_context['inventory'] or not llm_context['recipes']:
        base_plan['llm_status'] = {
            'enabled': True,
            'used': False,
            'provider_label': config['provider_label'],
            'message': '全大模型方案需要至少 1 个启用食材和 1 个带用料的已发布菜谱。',
        }
        return base_plan

    try:
        agent_reports = _run_full_llm_expert_agents(config, llm_context)
        coordinator_payload = _run_full_llm_coordinator_agent(
            config,
            llm_context,
            agent_reports,
            limit_per_meal=limit_per_meal,
        )
        plan = _build_full_llm_plan_from_response(
            today,
            config,
            llm_context,
            agent_reports,
            coordinator_payload,
            limit_per_meal=limit_per_meal,
        )
    except (RuntimeError, ValueError) as exc:
        base_plan['agent_cards'] = _full_llm_failure_cards(str(exc))
        base_plan['llm_status'] = {
            'enabled': True,
            'used': False,
            'provider_label': config['provider_label'],
            'message': f'全大模型 multi-agent 调用失败：{exc}',
        }
        return base_plan

    if mark_recommended:
        _mark_meal_plan_recommended(plan, today)
    return plan


def _build_agent_context(today) -> dict[str, Any]:
    history_start = today - timedelta(days=HISTORY_DAYS)
    order_start = today - timedelta(days=ORDER_DAYS)
    procurement_start = today - timedelta(days=PROCUREMENT_DAYS)
    history = _load_deactivation_history(history_start)
    order_stats = _load_order_stats(order_start)
    dish_signals = _load_dish_signals(today, history, order_stats)
    purchase_alerts = _build_purchase_alerts(dish_signals, procurement_start)
    return {
        'today': today,
        'history_start': history_start,
        'order_start': order_start,
        'procurement_start': procurement_start,
        'history': history,
        'order_stats': order_stats,
        'dish_signals': dish_signals,
        'purchase_alerts': purchase_alerts,
        'agent_cards': _build_agent_cards(today, dish_signals, history, order_start, purchase_alerts),
    }


def _load_deactivation_history(history_start) -> dict[str, Any]:
    rows = (
        Dish.objects
        .filter(
            is_active=False,
            deactivated_at__gte=history_start,
            deactivation_reason__in=['eaten', 'discarded'],
        )
        .values('name', 'category__name', 'deactivation_reason')
        .annotate(total=Count('id'), total_amount=Sum('default_price'))
    )
    by_name: dict[str, Counter] = defaultdict(Counter)
    by_category: dict[str, Counter] = defaultdict(Counter)
    examples: dict[str, Counter] = defaultdict(Counter)
    amount_by_reason: dict[str, float] = defaultdict(float)

    for row in rows:
        reason = row['deactivation_reason']
        count = int(row['total'] or 0)
        name = row['name'] or ''
        category = row['category__name'] or '未分类'
        normalized = _normalize_name(name)
        if normalized:
            by_name[normalized][reason] += count
            examples[reason][name] += count
        by_category[category][reason] += count
        amount_by_reason[reason] += _float(row['total_amount'])

    return {
        'by_name': by_name,
        'by_category': by_category,
        'examples': examples,
        'amount_by_reason': amount_by_reason,
    }


def _load_order_stats(order_start) -> dict[int, dict[str, float]]:
    rows = (
        DailyDishStatistic.objects
        .filter(stat_date__gte=order_start)
        .values('dish_id')
        .annotate(
            quantity=Sum('total_quantity'),
            amount=Sum('total_amount'),
            order_count=Sum('order_count'),
        )
    )
    return {
        row['dish_id']: {
            'quantity': _float(row['quantity']),
            'amount': _float(row['amount']),
            'order_count': int(row['order_count'] or 0),
        }
        for row in rows
        if row['dish_id']
    }


def _load_dish_signals(today, history: dict[str, Any], order_stats: dict[int, dict[str, float]]) -> list[DishSignal]:
    signals = []
    for dish in Dish.objects.filter(is_active=True).select_related('category'):
        normalized = _normalize_name(dish.name)
        if not normalized:
            continue
        category_name = dish.category.name if dish.category else '未分类'
        days_in_stock = max((today - dish.stock_in_date).days, 0) if dish.stock_in_date else 0
        stat = order_stats.get(dish.id, {})
        name_history = history['by_name'].get(normalized, Counter())
        category_history = history['by_category'].get(category_name, Counter())
        signals.append(DishSignal(
            id=dish.id,
            name=dish.name,
            normalized=normalized,
            category_name=category_name,
            storage=dish.storage,
            days_in_stock=days_in_stock,
            default_price=_float(dish.default_price),
            recent_quantity=_float(stat.get('quantity')),
            recent_amount=_float(stat.get('amount')),
            recent_order_count=int(stat.get('order_count') or 0),
            eaten_count=name_history['eaten'] + category_history['eaten'] * 0.25,
            discarded_count=name_history['discarded'] + category_history['discarded'] * 0.35,
            is_free_seasoning=_is_free_seasoning(dish.name, category_name),
        ))
    return signals


def _build_purchase_alerts(dish_signals: list[DishSignal], order_start) -> list[str]:
    active_by_name = {dish.normalized: dish for dish in dish_signals}
    shortage_rows = []
    rows = (
        DailyDishStatistic.objects
        .filter(stat_date__gte=order_start)
        .values('dish__name', 'dish__category__name', 'dish__is_active')
        .annotate(
            quantity=Sum('total_quantity'),
            amount=Sum('total_amount'),
            order_count=Sum('order_count'),
        )
    )
    for row in rows:
        name = row['dish__name'] or ''
        category_name = row['dish__category__name'] or '未分类'
        normalized = _normalize_name(name)
        if not normalized or normalized in active_by_name:
            continue
        if _is_free_seasoning(name, category_name):
            continue
        order_count = int(row['order_count'] or 0)
        quantity = _float(row['quantity'])
        amount = _float(row['amount'])
        score = order_count * 2 + quantity + amount / 20
        shortage_rows.append((score, name, order_count, quantity, amount))

    shortage_rows.sort(reverse=True)
    alerts = [
        f'{name} 近 180 天出现 {order_count} 次、数量约 {quantity:.1f}，当前未启用，建议补货。'
        for _score, name, order_count, quantity, _amount in shortage_rows[:3]
    ]

    frequent_active = sorted(
        [dish for dish in dish_signals if not dish.is_free_seasoning and dish.recent_order_count >= 3],
        key=lambda dish: (dish.recent_order_count, dish.recent_quantity, dish.recent_amount),
        reverse=True,
    )
    for dish in frequent_active[: max(0, 3 - len(alerts))]:
        alerts.append(
            f'{dish.name} 近 30 天使用 {dish.recent_order_count} 次，建议核对余量并按需补货。'
        )

    free_seasonings = [dish.name for dish in dish_signals if dish.is_free_seasoning][:4]
    if free_seasonings:
        alerts.append('调料免费佐料常备：' + '、'.join(free_seasonings) + '，不纳入采购成本压力。')

    return alerts or ['暂无明显补货缺口；当前优先消耗已有库存。']


def _build_agent_cards(
    today,
    dish_signals: list[DishSignal],
    history: dict[str, Any],
    order_start,
    purchase_alerts: list[str],
) -> list[dict[str, Any]]:
    urgent = sorted(dish_signals, key=lambda dish: (dish.freshness_score, dish.days_in_stock), reverse=True)[:4]
    value = sorted(dish_signals, key=lambda dish: (dish.value_score, dish.recent_amount), reverse=True)[:4]
    eaten_examples = history['examples'].get('eaten', Counter()).most_common(3)
    discarded_examples = history['examples'].get('discarded', Counter()).most_common(3)
    order_summary = Order.objects.filter(order_date__gte=order_start).aggregate(
        total_amount=Sum('total_amount'),
        total_orders=Count('id'),
    )
    total_orders = int(order_summary['total_orders'] or 0)
    total_amount = _float(order_summary['total_amount'])

    return [
        {
            'name': '库存保鲜 Agent',
            'role': '用入库天数和储存方式判断今天应该优先消耗什么。',
            'findings': [
                f'{dish.name} 已入库 {dish.days_in_stock} 天（{dish.storage}）'
                for dish in urgent
            ] or ['暂无启用食材，先通过 OCR 或食材页补充库存。'],
        },
        {
            'name': '行为记忆 Agent',
            'role': '学习近期吃完和丢弃记录，兼顾偏好与避浪费。',
            'findings': _format_history_findings(eaten_examples, discarded_examples),
        },
        {
            'name': '订单价格 Agent',
            'role': '结合近 30 天订单金额、单价和购买频率，避免高价值食材沉没。',
            'findings': [
                f'近 30 天订单 {total_orders} 笔，金额约 ¥{total_amount:.2f}',
                *[f'{dish.name} 近 30 天金额约 ¥{dish.recent_amount:.2f}' for dish in value[:3] if dish.recent_amount > 0],
            ],
        },
        {
            'name': '采购提醒 Agent',
            'role': '识别常用但未启用、近期消耗频繁和免费佐料常备项，提醒补货或核对余量。',
            'findings': purchase_alerts,
        },
        {
            'name': '三餐协调 Agent',
            'role': '按早餐快手、午餐饱腹、晚餐清爽的约束分配菜谱，并避免重复用同一批食材。',
            'findings': [f'{today:%m/%d} 使用 Mixture-of-Agents + Critic 的两阶段编排。'],
        },
    ]


def _format_history_findings(eaten_examples, discarded_examples) -> list[str]:
    findings = []
    if eaten_examples:
        findings.append('近期常吃完：' + '、'.join(f'{name}×{count}' for name, count in eaten_examples))
    if discarded_examples:
        findings.append('近期曾丢弃：' + '、'.join(f'{name}×{count}' for name, count in discarded_examples))
    return findings or ['还没有吃完/丢弃历史，先按库存和订单信号推荐。']


def _rank_candidates_by_meal(context: dict[str, Any], limit: int) -> dict[str, list[dict[str, Any]]]:
    dish_signals = context['dish_signals']
    if not dish_signals:
        return {slot['key']: [] for slot in MEAL_SLOTS}

    recipes = (
        Recipe.objects
        .filter(is_published=True)
        .select_related('category')
        .prefetch_related('ingredients')
    )
    history_counts = _recommendation_history_counts()
    match_cache: dict[str, tuple[DishSignal | None, float]] = {}
    candidates_by_meal: dict[str, list[dict[str, Any]]] = {slot['key']: [] for slot in MEAL_SLOTS}

    for recipe in recipes:
        candidate_base = _score_recipe_base(recipe, dish_signals, match_cache, history_counts)
        if not candidate_base:
            continue
        for slot in MEAL_SLOTS:
            candidate = dict(candidate_base)
            meal_fit = _meal_fit_score(recipe, slot['key'])
            candidate['meal_key'] = slot['key']
            candidate['meal_label'] = slot['label']
            candidate['meal_fit_score'] = round(meal_fit, 2)
            candidate['score'] = round(candidate['base_score'] + meal_fit, 2)
            candidate['reason'] = _build_candidate_reason(candidate, slot['label'])
            candidates_by_meal[slot['key']].append(candidate)

    for meal_key, candidates in candidates_by_meal.items():
        candidates.sort(key=lambda item: (item['score'], item['coverage'], item['avg_stock_days']), reverse=True)
        candidates_by_meal[meal_key] = candidates[:limit]
    return candidates_by_meal


def _score_recipe_base(
    recipe: Recipe,
    dish_signals: list[DishSignal],
    match_cache: dict[str, tuple[DishSignal | None, float]],
    history_counts: dict[int, int],
) -> dict[str, Any] | None:
    ingredients = list(recipe.ingredients.all())
    total_ingredients = len(ingredients)
    if not total_ingredients:
        return None

    matched: dict[int, DishSignal] = {}
    unmatched_names = []
    for ingredient in ingredients:
        dish, score = _best_dish_match(ingredient.name, dish_signals, match_cache)
        if dish and score >= MATCH_THRESHOLD:
            matched[dish.id] = dish
        else:
            unmatched_names.append(ingredient.name)

    matched_count = len(matched)
    if matched_count == 0:
        return None

    matched_dishes = list(matched.values())
    coverage = matched_count / total_ingredients
    freshness_score = sum(dish.freshness_score for dish in matched_dishes) / matched_count
    preference_score = sum(dish.preference_score for dish in matched_dishes) / matched_count
    waste_score = sum(dish.waste_rescue_score for dish in matched_dishes) / matched_count
    value_score = sum(dish.value_score for dish in matched_dishes) / matched_count
    estimated_cost = sum(
        dish.effective_price
        for dish in matched_dishes
        if dish.effective_price > 0 and not dish.is_free_seasoning
    )
    avg_stock_days = sum(dish.days_in_stock for dish in matched_dishes) / matched_count
    history_count = history_counts.get(recipe.id, 0)
    history_penalty = min(history_count * 2.4, 14)

    inventory_vote = matched_count * 5.5 + coverage * 24 + freshness_score * 0.9
    behavior_vote = preference_score * 0.9 + waste_score * 0.55 - history_penalty
    market_vote = value_score * 0.85 + (estimated_cost * 0.08 if estimated_cost else 0)
    base_score = inventory_vote + behavior_vote + market_vote

    return {
        'recipe': recipe,
        'base_score': round(base_score, 2),
        'coverage': round(coverage, 4),
        'coverage_percent': int(round(coverage * 100)),
        'matched_ingredient_count': matched_count,
        'total_ingredient_count': total_ingredients,
        'avg_stock_days': round(avg_stock_days, 1),
        'history_count': history_count,
        'estimated_cost': round(estimated_cost, 2),
        'matched_dish_ids': [dish.id for dish in matched_dishes],
        'matched_dish_names': [dish.name for dish in matched_dishes[:4]],
        'free_seasoning_names': [dish.name for dish in matched_dishes if dish.is_free_seasoning][:4],
        'unmatched_ingredient_names': unmatched_names[:4],
        'agent_votes': [
            {'name': '库存', 'score': round(inventory_vote, 1), 'label': f'命中 {matched_count}/{total_ingredients}'},
            {'name': '行为', 'score': round(behavior_vote, 1), 'label': f'历史推荐 {history_count} 次'},
            {'name': '价格', 'score': round(market_vote, 1), 'label': f'估算 ¥{estimated_cost:.2f}'},
            {'name': '避浪费', 'score': round(waste_score, 1), 'label': f'风险 {waste_score:.1f}'},
        ],
        'signals': {
            'freshness': round(freshness_score, 2),
            'preference': round(preference_score, 2),
            'waste_rescue': round(waste_score, 2),
            'value': round(value_score, 2),
        },
    }


def _select_meals(
    today,
    candidates_by_meal: dict[str, list[dict[str, Any]]],
    context: dict[str, Any],
    limit_per_meal: int,
) -> dict[str, Any]:
    used_recipe_ids = set()
    used_dish_ids = set()
    meals = []

    for slot in MEAL_SLOTS:
        scored_candidates = []
        for candidate in candidates_by_meal.get(slot['key'], []):
            overlap = len(set(candidate['matched_dish_ids']) & used_dish_ids)
            duplicate_penalty = 18 if candidate['recipe'].id in used_recipe_ids else 0
            selection_score = candidate['score'] - overlap * 5.5 - duplicate_penalty
            copied = dict(candidate)
            copied['selection_score'] = round(selection_score, 2)
            scored_candidates.append(copied)

        selected = None
        if scored_candidates:
            scored_candidates.sort(key=lambda item: item['selection_score'], reverse=True)
            selected = scored_candidates[0]
            used_recipe_ids.add(selected['recipe'].id)
            used_dish_ids.update(selected['matched_dish_ids'])

        alternatives = [item for item in scored_candidates if not selected or item['recipe'].id != selected['recipe'].id]
        meals.append({
            'key': slot['key'],
            'label': slot['label'],
            'intent': slot['intent'],
            'selected': selected,
            'alternatives': alternatives[: max(limit_per_meal - 1, 0)],
            'summary': _meal_summary(slot, selected),
        })

    return {
        'date': today,
        'architecture': 'CAPE-MoA 多 Agent：Context 上下文采集 + Agent 专家打分 + Planner 编排 + Critic 复核',
        'agent_cards': context['agent_cards'],
        'meals': meals,
    }


def _meal_summary(slot: dict[str, str], selected: dict[str, Any] | None) -> str:
    if not selected:
        return f'{slot["label"]} 暂无足够候选，建议先补充食材或同步菜谱。'
    return f'{slot["label"]} 推荐 {selected["recipe"].name}：{selected["reason"]}'


def _build_candidate_reason(candidate: dict[str, Any], meal_label: str) -> str:
    pieces = [
        f'{meal_label}适配 {candidate["meal_fit_score"]:.1f}',
        f'库存命中 {candidate["matched_ingredient_count"]}/{candidate["total_ingredient_count"]}',
    ]
    if candidate['avg_stock_days'] > 0:
        pieces.append(f'平均入库 {candidate["avg_stock_days"]} 天')
    if candidate['estimated_cost'] > 0:
        pieces.append(f'关联订单成本约 ¥{candidate["estimated_cost"]:.2f}')
    if candidate.get('free_seasoning_names'):
        pieces.append('含免费佐料：' + '、'.join(candidate['free_seasoning_names']))
    if candidate['signals']['waste_rescue'] > 1:
        pieces.append('有避浪费价值')
    if candidate['history_count']:
        pieces.append(f'已推荐 {candidate["history_count"]} 次，已降权')
    return '，'.join(pieces)


def _meal_fit_score(recipe: Recipe, meal_key: str) -> float:
    text = _normalize_name(' '.join([
        recipe.name,
        recipe.category.name if recipe.category else '',
        recipe.description or '',
    ]))
    cook_time = (recipe.prep_time_minutes or 0) + (recipe.cook_time_minutes or 0)
    score = 0.0
    if meal_key == 'breakfast':
        score += 16 if any(_normalize_name(word) in text for word in BREAKFAST_HINTS) else -5
        score += 3 if recipe.difficulty == '简单' else 0
        score += 4 if cook_time and cook_time <= 20 else 0
        score -= 4 if any(_normalize_name(word) in text for word in HEAVY_HINTS) else 0
    elif meal_key == 'lunch':
        score += 10 if any(_normalize_name(word) in text for word in LUNCH_HINTS) else 2
        score += 5 if recipe.category and recipe.category.name in {'家常热菜', '主食早餐', '汤粥锅煲'} else 0
    elif meal_key == 'dinner':
        score += 10 if any(_normalize_name(word) in text for word in DINNER_HINTS) else 1
        score += 3 if recipe.difficulty == '简单' else 0
        score -= 5 if any(_normalize_name(word) in text for word in HEAVY_HINTS) else 0
    return score


def _recommendation_history_counts() -> dict[int, int]:
    rows = RecipeRecommendationHistory.objects.values('recipe_id').annotate(total=Count('id'))
    return {row['recipe_id']: int(row['total'] or 0) for row in rows}


def _best_dish_match(
    name: str,
    candidates: list[DishSignal],
    cache: dict[str, tuple[DishSignal | None, float]],
) -> tuple[DishSignal | None, float]:
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
            best_candidate = candidate
            best_score = score
    cache[normalized] = (best_candidate, best_score)
    return best_candidate, best_score


def _mark_meal_plan_recommended(plan: dict[str, Any], today) -> None:
    for meal in plan.get('meals', []):
        selected = meal.get('selected')
        if not selected:
            continue
        RecipeRecommendationHistory.objects.get_or_create(
            recipe=selected['recipe'],
            recommended_date=today,
            defaults={
                'score': selected.get('score', 0),
                'matched_ingredient_count': selected.get('matched_ingredient_count', 0),
            },
        )


def _empty_full_llm_plan(today) -> dict[str, Any]:
    return {
        'date': today,
        'kicker': 'Full-LLM Multi-Agent',
        'title': '全大模型三餐方案',
        'architecture': '全 LLM MoA：库存、记忆、成本、采购专家与三餐协调 Agent 均由大模型完成判断',
        'agent_cards': [],
        'meals': [
            {
                'key': slot['key'],
                'label': slot['label'],
                'intent': slot['intent'],
                'selected': None,
                'alternatives': [],
                'summary': f'{slot["label"]} 暂无全大模型推荐结果。',
            }
            for slot in MEAL_SLOTS
        ],
        'llm_status': {
            'enabled': True,
            'used': False,
            'provider_label': 'Full-LLM Multi-Agent',
            'message': '等待模型生成。',
        },
    }


def _full_llm_settings() -> dict[str, Any]:
    data = getattr(settings, 'MEAL_AGENT_FULL_LLM', {})
    return data if isinstance(data, dict) else {}


def _full_llm_max_recipes() -> int:
    return _positive_int(_full_llm_settings().get('max_recipes'), default=36, minimum=6, maximum=80)


def _resolve_full_llm_config() -> dict[str, Any] | None:
    data = _full_llm_settings()
    requests_per_minute = _positive_int(data.get('requests_per_minute'), default=5, minimum=1, maximum=120)
    default_agent_concurrency = len(FULL_LLM_AGENT_SPECS)
    max_concurrency = _positive_int(
        data.get('max_concurrency'),
        default=default_agent_concurrency,
        minimum=1,
        maximum=12,
    )
    expert_concurrency = _positive_int(
        data.get('expert_concurrency'),
        default=default_agent_concurrency,
        minimum=1,
        maximum=len(FULL_LLM_AGENT_SPECS),
    )
    explicit_config = {
        'provider_label': data.get('provider_name') or 'Full-LLM Multi-Agent',
        'api_key': (data.get('api_key') or '').strip(),
        'base_url': (data.get('base_url') or '').strip().rstrip('/'),
        'model': (data.get('model') or '').strip(),
        'timeout_seconds': _positive_int(data.get('timeout_seconds'), default=60, minimum=1),
        'requests_per_minute': requests_per_minute,
        'max_concurrency': max_concurrency,
        'expert_concurrency': expert_concurrency,
    }
    if all(explicit_config[key] for key in ('api_key', 'base_url', 'model')):
        return explicit_config

    managed_config = _resolve_managed_llm_config()
    if managed_config:
        return _apply_full_llm_overrides(managed_config, data, requests_per_minute, max_concurrency, expert_concurrency)

    if data.get('reuse_meal_llm_config', True):
        reused = _resolve_llm_config()
        if reused:
            return _apply_full_llm_overrides(reused, data, requests_per_minute, max_concurrency, expert_concurrency)
    return None


def _apply_full_llm_overrides(
    config: dict[str, Any],
    data: dict[str, Any],
    requests_per_minute: int,
    max_concurrency: int,
    expert_concurrency: int,
) -> dict[str, Any]:
    merged = dict(config)
    if data.get('provider_name'):
        merged['provider_label'] = data['provider_name']
    if data.get('api_key'):
        merged['api_key'] = str(data['api_key']).strip()
    if data.get('base_url'):
        merged['base_url'] = str(data['base_url']).strip().rstrip('/')
    if data.get('model'):
        merged['model'] = str(data['model']).strip()
        merged.pop('model_config_id', None)
    merged['timeout_seconds'] = _positive_int(data.get('timeout_seconds'), merged['timeout_seconds'], 1)
    merged['requests_per_minute'] = requests_per_minute
    merged['max_concurrency'] = max_concurrency
    merged['expert_concurrency'] = expert_concurrency
    return merged


def _resolve_managed_llm_config() -> dict[str, Any] | None:
    try:
        config = next(iter(LLMProviderConfig.active_complete()), None)
    except (OperationalError, ProgrammingError):
        return None
    if not config:
        return None
    return _managed_llm_config_to_dict(config)


def _managed_llm_config_to_dict(config: LLMProviderConfig) -> dict[str, Any]:
    provider_name = config.provider_name.strip()
    return {
        'provider_label': config.name or provider_name or 'Managed LLM',
        'api_key': config.api_key.strip(),
        'base_url': config.base_url.strip().rstrip('/'),
        'model': config.model.strip(),
        'timeout_seconds': _positive_int(config.timeout_seconds, default=60, minimum=1),
        'requests_per_minute': _positive_int(config.requests_per_minute, default=5, minimum=1, maximum=120),
        'max_concurrency': _positive_int(
            config.max_concurrency,
            default=len(FULL_LLM_AGENT_SPECS),
            minimum=1,
            maximum=12,
        ),
        'expert_concurrency': _positive_int(
            config.expert_concurrency,
            default=len(FULL_LLM_AGENT_SPECS),
            minimum=1,
            maximum=len(FULL_LLM_AGENT_SPECS),
        ),
        'model_config_id': config.id,
    }


def _build_full_llm_context(today, context: dict[str, Any], max_recipes: int) -> dict[str, Any]:
    dish_signals: list[DishSignal] = context['dish_signals']
    order_summary = Order.objects.filter(order_date__gte=context['order_start']).aggregate(
        total_amount=Sum('total_amount'),
        total_orders=Count('id'),
    )
    recipe_entries, recipe_objects, recipes_by_id = _load_full_llm_recipe_catalog(dish_signals, max_recipes)
    return {
        'date': str(today),
        'meal_slots': MEAL_SLOTS,
        'context_notes': [
            'inventory 列表只包含当前启用食材；出现在 inventory 中就表示当前可用。',
            '系统目前不记录精确库存数量，recent_order_quantity 只是近 30 天订单统计，不代表当前库存数量。',
            '菜谱的 missing_ingredient_names 为空且 is_feasible=true 时，表示该菜谱所需食材当前可用。',
            'free_seasoning=true 的食材属于调料免费/免费佐料常备项，不应计入采购成本压力。',
        ],
        'inventory': [_serialize_dish_signal(dish) for dish in dish_signals],
        'history': _serialize_history_for_llm(context['history']),
        'purchase_alerts': context.get('purchase_alerts', []),
        'orders': {
            'start_date': str(context['order_start']),
            'total_orders': int(order_summary['total_orders'] or 0),
            'total_amount': round(_float(order_summary['total_amount']), 2),
        },
        'recipes': recipe_entries,
        '_recipe_objects': recipe_objects,
        '_recipes_by_id': recipes_by_id,
    }


def _load_full_llm_recipe_catalog(
    dish_signals: list[DishSignal],
    max_recipes: int,
) -> tuple[list[dict[str, Any]], dict[int, Recipe], dict[int, dict[str, Any]]]:
    recipe_rows = []
    recipe_objects: dict[int, Recipe] = {}
    history_counts = _recommendation_history_counts()
    match_cache: dict[str, tuple[DishSignal | None, float]] = {}
    recipes = (
        Recipe.objects
        .filter(is_published=True)
        .select_related('category')
        .prefetch_related('ingredients')
    )

    for recipe in recipes:
        ingredients = list(recipe.ingredients.all())
        if not ingredients:
            continue
        matched_dishes: dict[int, DishSignal] = {}
        missing_ingredients = []
        for ingredient in ingredients:
            dish, score = _best_dish_match(ingredient.name, dish_signals, match_cache)
            if dish and score >= MATCH_THRESHOLD:
                matched_dishes[dish.id] = dish
            else:
                missing_ingredients.append(ingredient.name)

        matched_list = list(matched_dishes.values())
        entry = {
            'recipe_id': recipe.id,
            'name': recipe.name,
            'category': recipe.category.name if recipe.category else '',
            'difficulty': recipe.difficulty,
            'servings': recipe.servings,
            'time_minutes': (recipe.prep_time_minutes or 0) + (recipe.cook_time_minutes or 0),
            'description': (recipe.description or '')[:120],
            'tips': (recipe.tips or '')[:120],
            'ingredient_names': [ingredient.name for ingredient in ingredients],
            'matched_inventory_ids': [dish.id for dish in matched_list],
            'matched_inventory_names': [dish.name for dish in matched_list],
            'free_seasoning_names': [dish.name for dish in matched_list if dish.is_free_seasoning][:8],
            'missing_ingredient_names': missing_ingredients[:8],
            'matched_ingredient_count': len(matched_list),
            'total_ingredient_count': len(ingredients),
            'is_feasible': not missing_ingredients,
            'estimated_matched_cost': round(
                sum(dish.effective_price for dish in matched_list if not dish.is_free_seasoning),
                2,
            ),
            'recent_recommendation_count': history_counts.get(recipe.id, 0),
        }
        recipe_objects[recipe.id] = recipe
        recipe_rows.append((len(matched_list), -entry['recent_recommendation_count'], recipe.updated_at, entry))

    recipe_rows.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    entries = [row[3] for row in recipe_rows[:max_recipes]]
    recipes_by_id = {entry['recipe_id']: entry for entry in entries}
    recipe_objects = {
        recipe_id: recipe
        for recipe_id, recipe in recipe_objects.items()
        if recipe_id in recipes_by_id
    }
    return entries, recipe_objects, recipes_by_id


def _serialize_dish_signal(dish: DishSignal) -> dict[str, Any]:
    return {
        'dish_id': dish.id,
        'name': dish.name,
        'availability_status': 'available',
        'is_available': True,
        'quantity_note': '当前系统只记录该食材启用可用，不记录精确剩余数量。',
        'category': dish.category_name,
        'storage': dish.storage,
        'days_in_stock': dish.days_in_stock,
        'free_seasoning': dish.is_free_seasoning,
        'default_price': dish.default_price,
        'effective_price': dish.effective_price,
        'recent_order_quantity': dish.recent_quantity,
        'recent_order_amount': dish.recent_amount,
        'recent_order_count': dish.recent_order_count,
        'eaten_count': round(dish.eaten_count, 2),
        'discarded_count': round(dish.discarded_count, 2),
    }


def _serialize_history_for_llm(history: dict[str, Any]) -> dict[str, Any]:
    eaten_examples = history['examples'].get('eaten', Counter()).most_common(8)
    discarded_examples = history['examples'].get('discarded', Counter()).most_common(8)
    return {
        'days': HISTORY_DAYS,
        'eaten_examples': [{'name': name, 'count': count} for name, count in eaten_examples],
        'discarded_examples': [{'name': name, 'count': count} for name, count in discarded_examples],
        'eaten_amount': round(_float(history['amount_by_reason'].get('eaten')), 2),
        'discarded_amount': round(_float(history['amount_by_reason'].get('discarded')), 2),
    }


def _full_llm_public_context(llm_context: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in llm_context.items()
        if not key.startswith('_')
    }


def _run_full_llm_expert_agent(
    config: dict[str, Any],
    spec: dict[str, str],
    llm_context: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        'agent_name': spec['name'],
        'agent_role': spec['role'],
        'context': _full_llm_public_context(llm_context),
        'return_json_schema': {
            'findings': ['3 到 5 条面向用户的关键发现'],
            'priorities': [
                {
                    'recipe_id': 1,
                    'score': 88,
                    'reason': '为什么这个 Agent 认为该菜谱值得推荐',
                }
            ],
            'risks': ['可选，指出需要避开的风险'],
        },
        'rules': [
            'inventory 中的食材均为当前可用库存，不要把 recent_order_quantity=0 理解为库存为 0。',
            '优先提名 is_feasible=true 的菜谱；missing_ingredient_names 非空时说明缺少当前可用食材。',
        ],
    }
    content = _call_openai_compatible_messages(
        config,
        [
            {'role': 'system', 'content': spec['system']},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
        ],
        max_tokens=1200,
        temperature=0.15,
    )
    parsed = _extract_json_payload(content)
    return _normalize_full_llm_agent_report(spec, parsed, llm_context)


def _run_full_llm_expert_agents(config: dict[str, Any], llm_context: dict[str, Any]) -> list[dict[str, Any]]:
    max_workers = _positive_int(
        config.get('expert_concurrency'),
        default=len(FULL_LLM_AGENT_SPECS),
        minimum=1,
        maximum=len(FULL_LLM_AGENT_SPECS),
    )
    reports: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='full-llm-agent') as executor:
        future_map = {
            executor.submit(_run_full_llm_expert_agent, config, spec, llm_context): (index, spec)
            for index, spec in enumerate(FULL_LLM_AGENT_SPECS)
        }
        for future in as_completed(future_map):
            index, spec = future_map[future]
            try:
                reports[index] = future.result()
            except (RuntimeError, ValueError) as exc:
                raise RuntimeError(f'{spec["name"]} 调用失败：{exc}') from exc
    return [reports[index] for index in range(len(FULL_LLM_AGENT_SPECS))]


def _normalize_full_llm_agent_report(
    spec: dict[str, str],
    payload: dict[str, Any],
    llm_context: dict[str, Any],
) -> dict[str, Any]:
    valid_recipe_ids = set(llm_context['_recipes_by_id'])
    priorities = []
    for item in payload.get('priorities') or []:
        if not isinstance(item, dict):
            continue
        recipe_id = _coerce_int(item.get('recipe_id'))
        if recipe_id not in valid_recipe_ids:
            continue
        priorities.append({
            'recipe_id': recipe_id,
            'score': _bounded_score(item.get('score'), default=0),
            'reason': str(item.get('reason') or '').strip()[:120],
        })

    return {
        'key': spec['key'],
        'name': spec['name'],
        'vote_name': spec['vote_name'],
        'role': spec['role'],
        'findings': _text_list(payload.get('findings'), fallback='模型未返回明确发现。', limit=5),
        'risks': _text_list(payload.get('risks'), fallback='', limit=3),
        'priorities': priorities[:8],
    }


def _run_full_llm_coordinator_agent(
    config: dict[str, Any],
    llm_context: dict[str, Any],
    agent_reports: list[dict[str, Any]],
    limit_per_meal: int,
) -> dict[str, Any]:
    payload = {
        'context': _full_llm_public_context(llm_context),
        'expert_agent_reports': agent_reports,
        'return_json_schema': {
            'overall_note': '一句总评',
            'meals': [
                {
                    'key': 'breakfast',
                    'recipe_id': 1,
                    'score': 90,
                    'summary': '早餐推荐摘要',
                    'reason': '面向用户的短理由',
                    'reason_details': ['库存、偏好、成本、餐段适配等详细理由，每条不超过 40 字'],
                    'alternatives': [2, 3],
                    'agent_votes': [
                        {'name': '库存LLM', 'score': 90, 'label': '短标签'}
                    ],
                }
            ],
        },
        'rules': [
            '每餐只能选择 recipes 目录中存在的 recipe_id。',
            '主推菜谱必须优先选择 is_feasible=true 的候选；不要因为 recent_order_quantity=0 判定库存不可用。',
            '早餐、午餐、晚餐尽量不要重复同一个 recipe_id。',
            f'每餐最多返回 {limit_per_meal - 1} 个备选。',
            '只返回 JSON，不要 Markdown，不要解释推理过程。',
        ],
    }
    content = _call_openai_compatible_messages(
        config,
        [
            {
                'role': 'system',
                'content': (
                    '你是三餐协调 Agent。你汇总其他 LLM 专家 Agent 的报告，输出最终 breakfast/lunch/dinner。'
                    '必须使用候选目录里的 recipe_id，不能编造菜谱。只返回 JSON。'
                ),
            },
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
        ],
        max_tokens=1600,
        temperature=0.2,
    )
    parsed = _extract_json_payload(content)
    if not isinstance(parsed.get('meals'), list):
        raise ValueError('协调 Agent 返回 JSON 缺少 meals 数组。')
    return parsed


def _build_full_llm_plan_from_response(
    today,
    config: dict[str, Any],
    llm_context: dict[str, Any],
    agent_reports: list[dict[str, Any]],
    coordinator_payload: dict[str, Any],
    limit_per_meal: int,
) -> dict[str, Any]:
    meal_payloads = {
        item.get('key'): item
        for item in coordinator_payload.get('meals', [])
        if isinstance(item, dict)
    }
    meals = []
    selected_count = 0
    for slot in MEAL_SLOTS:
        payload = meal_payloads.get(slot['key'], {})
        selected = _full_llm_candidate_from_payload(payload, slot, llm_context, agent_reports)
        alternatives = []
        if selected:
            selected_count += 1
            alternatives = _full_llm_alternatives_from_payload(
                payload,
                slot,
                llm_context,
                agent_reports,
                selected['recipe'].id,
                limit=max(limit_per_meal - 1, 0),
            )
        meals.append({
            'key': slot['key'],
            'label': slot['label'],
            'intent': slot['intent'],
            'selected': selected,
            'alternatives': alternatives,
            'summary': str(payload.get('summary') or _meal_summary(slot, selected)).strip(),
        })

    if selected_count == 0:
        raise ValueError('协调 Agent 没有返回任何可用的菜谱 ID。')

    overall_note = str(coordinator_payload.get('overall_note') or '全大模型多 Agent 已完成三餐生成。').strip()
    return {
        'date': today,
        'kicker': 'Full-LLM Multi-Agent',
        'title': '全大模型三餐方案',
        'architecture': '全 LLM MoA：库存、记忆、成本、采购专家与三餐协调 Agent 均由大模型完成判断',
        'agent_cards': [
            {
                'name': report['name'],
                'role': report['role'],
                'findings': report['findings'],
            }
            for report in agent_reports
        ] + [{
            'name': '三餐协调 Agent',
            'role': '汇总各 LLM 专家意见，选择三餐主推与备选菜谱。',
            'findings': [overall_note],
        }],
        'meals': meals,
        'llm_status': {
            'enabled': True,
            'used': True,
            'provider_label': config['provider_label'],
            'message': overall_note,
        },
    }


def _full_llm_candidate_from_payload(
    payload: dict[str, Any],
    slot: dict[str, str],
    llm_context: dict[str, Any],
    agent_reports: list[dict[str, Any]],
) -> dict[str, Any] | None:
    recipe_id = _coerce_int(payload.get('recipe_id'))
    if not recipe_id:
        return None
    recipe = llm_context['_recipe_objects'].get(recipe_id)
    recipe_entry = llm_context['_recipes_by_id'].get(recipe_id)
    if not recipe or not recipe_entry:
        return None

    total_ingredients = max(len(recipe_entry['ingredient_names']), 1)
    matched_count = len(recipe_entry['matched_inventory_names'])
    reason = str(payload.get('reason') or payload.get('summary') or '').strip()
    if not reason:
        reason = f'{slot["label"]}由全大模型 multi-agent 根据库存、历史和成本共同选出。'
    reason_details = _full_llm_reason_details(payload, agent_reports, recipe_id, recipe_entry, reason)
    return {
        'recipe': recipe,
        'meal_key': slot['key'],
        'meal_label': slot['label'],
        'score': _bounded_score(payload.get('score'), default=0),
        'matched_ingredient_count': matched_count,
        'total_ingredient_count': total_ingredients,
        'coverage': round(matched_count / total_ingredients, 4),
        'coverage_percent': int(round(matched_count / total_ingredients * 100)),
        'avg_stock_days': _avg_stock_days_for_recipe(recipe_entry, llm_context),
        'history_count': recipe_entry['recent_recommendation_count'],
        'estimated_cost': recipe_entry['estimated_matched_cost'],
        'matched_dish_ids': recipe_entry['matched_inventory_ids'],
        'matched_dish_names': recipe_entry['matched_inventory_names'][:4],
        'free_seasoning_names': recipe_entry.get('free_seasoning_names', [])[:4],
        'unmatched_ingredient_names': recipe_entry['missing_ingredient_names'][:4],
        'agent_votes': _full_llm_agent_votes(payload.get('agent_votes'), agent_reports, recipe_id),
        'reason': reason[:180],
        'llm_reason': reason[:180],
        'reason_details': reason_details,
    }


def _full_llm_alternatives_from_payload(
    payload: dict[str, Any],
    slot: dict[str, str],
    llm_context: dict[str, Any],
    agent_reports: list[dict[str, Any]],
    selected_recipe_id: int,
    limit: int,
) -> list[dict[str, Any]]:
    alternatives = []
    seen_ids = {selected_recipe_id}
    for recipe_id in payload.get('alternatives') or []:
        recipe_id = _coerce_int(recipe_id)
        if not recipe_id or recipe_id in seen_ids:
            continue
        alt_payload = {
            'recipe_id': recipe_id,
            'score': 0,
            'reason': '全大模型协调 Agent 给出的备选。',
        }
        candidate = _full_llm_candidate_from_payload(alt_payload, slot, llm_context, agent_reports)
        if candidate:
            alternatives.append(candidate)
            seen_ids.add(recipe_id)
        if len(alternatives) >= limit:
            break
    return alternatives


def _full_llm_reason_details(
    payload: dict[str, Any],
    agent_reports: list[dict[str, Any]],
    recipe_id: int,
    recipe_entry: dict[str, Any],
    fallback_reason: str,
) -> list[str]:
    details = _text_list(payload.get('reason_details'), fallback='', limit=5)
    if details:
        return details

    for report in agent_reports:
        priority = next(
            (item for item in report['priorities'] if item['recipe_id'] == recipe_id),
            None,
        )
        if priority and priority.get('reason'):
            details.append(f'{report["name"]}：{priority["reason"]}'[:140])

    if not details and recipe_entry.get('matched_inventory_names'):
        details.append('命中库存：' + '、'.join(recipe_entry['matched_inventory_names'][:4]))
    if not details and fallback_reason:
        details.append(fallback_reason[:140])
    return details[:5]


def _avg_stock_days_for_recipe(recipe_entry: dict[str, Any], llm_context: dict[str, Any]) -> float:
    inventory_by_id = {
        item['dish_id']: item
        for item in llm_context['inventory']
    }
    days = [
        inventory_by_id[dish_id]['days_in_stock']
        for dish_id in recipe_entry['matched_inventory_ids']
        if dish_id in inventory_by_id
    ]
    return round(sum(days) / len(days), 1) if days else 0


def _full_llm_agent_votes(raw_votes, agent_reports: list[dict[str, Any]], recipe_id: int) -> list[dict[str, Any]]:
    votes = []
    if isinstance(raw_votes, list):
        for item in raw_votes:
            if not isinstance(item, dict):
                continue
            votes.append({
                'name': str(item.get('name') or 'LLM').strip()[:12],
                'score': _bounded_score(item.get('score'), default=0),
                'label': str(item.get('label') or '').strip()[:28],
            })
    if votes:
        return votes[:4]

    for report in agent_reports:
        priority = next(
            (item for item in report['priorities'] if item['recipe_id'] == recipe_id),
            None,
        )
        votes.append({
            'name': report['vote_name'],
            'score': priority['score'] if priority else 0,
            'label': (priority['reason'] if priority else '未直接提名')[:28],
        })
    return votes[:4]


def _full_llm_failure_cards(message: str) -> list[dict[str, Any]]:
    cards = [
        {
            'name': spec['name'],
            'role': spec['role'],
            'findings': ['模型调用未完成，等待配置修正后重新生成。'],
        }
        for spec in FULL_LLM_AGENT_SPECS
    ]
    cards.append({
        'name': '三餐协调 Agent',
        'role': '汇总各 LLM 专家意见，选择三餐主推与备选菜谱。',
        'findings': [message],
    })
    return cards


def _text_list(value, fallback: str, limit: int) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        items = []
    if not items and fallback:
        items = [fallback]
    return [item[:140] for item in items[:limit]]


def _bounded_score(value, default: float = 0) -> float:
    score = _float(value)
    if score == 0 and value in (None, ''):
        score = default
    return round(max(min(score, 100), 0), 1)


def _coerce_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _refine_plan_with_llm(
    plan: dict[str, Any],
    candidates_by_meal: dict[str, list[dict[str, Any]]],
    context: dict[str, Any],
) -> dict[str, Any]:
    config = _resolve_llm_config()
    if not config:
        plan['llm_status'] = {
            'enabled': True,
            'used': False,
            'provider_label': 'DeepSeek / OpenAI-compatible',
            'message': '大模型复核已启用，但 API Key、Base URL 或模型未配置完整。',
        }
        return plan

    payload = _llm_planner_payload(plan, candidates_by_meal, context)
    try:
        content = _call_openai_compatible_chat(config, payload)
        critique = _extract_json_payload(content)
        _apply_llm_critique(plan, candidates_by_meal, critique)
        plan['llm_status'] = {
            'enabled': True,
            'used': True,
            'provider_label': config['provider_label'],
            'message': critique.get('overall_note') or '大模型已完成三餐复核与改写。',
        }
    except (ValueError, RuntimeError) as exc:
        plan['llm_status'] = {
            'enabled': True,
            'used': False,
            'provider_label': config['provider_label'],
            'message': f'大模型复核失败，已回退本地评分：{exc}',
        }
    return plan


def _meal_llm_settings() -> dict[str, Any]:
    data = getattr(settings, 'MEAL_AGENT_LLM', {})
    return data if isinstance(data, dict) else {}


def _resolve_llm_config() -> dict[str, Any] | None:
    data = _meal_llm_settings()
    config = {
        'provider_label': data.get('provider_name') or 'DeepSeek',
        'api_key': (data.get('api_key') or '').strip(),
        'base_url': (data.get('base_url') or '').strip().rstrip('/'),
        'model': (data.get('model') or '').strip(),
        'timeout_seconds': _positive_int(data.get('timeout_seconds'), default=45, minimum=1),
    }
    if all(config[key] for key in ('api_key', 'base_url', 'model')):
        return config

    managed_config = _resolve_managed_llm_config()
    if managed_config:
        return managed_config

    if not data.get('reuse_vision_config'):
        return None

    vision_config = VisionProviderConfig.get_solo()
    if (
        vision_config.enabled
        and vision_config.provider == VisionProviderConfig.PROVIDER_OPENAI_COMPATIBLE
        and vision_config.has_api_key
        and vision_config.base_url
        and vision_config.model
    ):
        return {
            'provider_label': vision_config.provider_name or 'OpenAI-compatible',
            'api_key': vision_config.api_key.strip(),
            'base_url': vision_config.effective_base_url.rstrip('/'),
            'model': vision_config.model.strip(),
            'timeout_seconds': _positive_int(vision_config.timeout_seconds, default=45, minimum=1),
        }
    return None


def _llm_planner_payload(
    plan: dict[str, Any],
    candidates_by_meal: dict[str, list[dict[str, Any]]],
    context: dict[str, Any],
) -> dict[str, Any]:
    return {
        'date': str(plan['date']),
        'architecture': plan['architecture'],
        'agent_findings': plan['agent_cards'],
        'active_inventory_count': len(context['dish_signals']),
        'meals': [
            {
                'key': slot['key'],
                'label': slot['label'],
                'intent': slot['intent'],
                'candidates': [_serialize_candidate(candidate) for candidate in candidates_by_meal.get(slot['key'], [])[:5]],
            }
            for slot in MEAL_SLOTS
        ],
    }


def _serialize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    recipe = candidate['recipe']
    return {
        'recipe_id': recipe.id,
        'name': recipe.name,
        'category': recipe.category.name if recipe.category else '',
        'score': candidate['score'],
        'matched_dishes': candidate['matched_dish_names'],
        'coverage_percent': candidate['coverage_percent'],
        'estimated_cost': candidate['estimated_cost'],
        'reason': candidate['reason'],
        'agent_votes': candidate['agent_votes'],
    }


def _call_openai_compatible_chat(config: dict[str, Any], payload: dict[str, Any]) -> str:
    return _call_openai_compatible_messages(
        config,
        [
            {
                'role': 'system',
                'content': (
                    '你是一个家庭/小餐饮三餐菜单 Critic Agent。你只能基于候选菜谱重排或选择，'
                    '不要编造不存在的 recipe_id。目标是库存先入先出、减少丢弃、控制成本、三餐口味不重复。'
                    '只返回 JSON，不要 Markdown，不要解释推理过程。'
                ),
            },
            {
                'role': 'user',
                'content': (
                    '请复核下面的多 Agent 候选，并返回格式：'
                    '{"overall_note":"一句总评","meals":[{"key":"breakfast",'
                    '"recipe_id":1,"summary":"一句推荐理由","reason":"面向用户的短理由"}]}。\n'
                    + json.dumps(payload, ensure_ascii=False)
                ),
            },
        ],
        max_tokens=1600,
        temperature=0.2,
    )


def _call_openai_compatible_messages(
    config: dict[str, Any],
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
) -> str:
    effective_max_tokens = _effective_max_tokens(config.get('model', ''), max_tokens)
    request_payload = {
        'model': config['model'],
        'messages': messages,
        'temperature': temperature,
        'max_tokens': effective_max_tokens,
        'response_format': {'type': 'json_object'},
    }
    request = Request(
        f'{config["base_url"]}/chat/completions',
        data=json.dumps(request_payload, ensure_ascii=False).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': f'Bearer {config["api_key"]}',
        },
        method='POST',
    )
    try:
        body = _perform_openai_compatible_request(config, request)
        message = body['choices'][0]['message']
        content = _message_content_to_text(message.get('content'))
        if content:
            return content
        reasoning = _message_content_to_text(message.get('reasoning') or message.get('reasoning_content'))
        if reasoning:
            reasoning_payload = _extract_json_payload_or_none(reasoning)
            if reasoning_payload is not None:
                return json.dumps(reasoning_payload, ensure_ascii=False)
            raise RuntimeError(
                '模型只返回了思考内容，没有返回最终 JSON；请给思考模型更长的输出预算，或换用非 think 模型。'
            )
        raise RuntimeError('模型返回缺少 message.content。')
    except URLError as exc:
        raise RuntimeError(f'网络连接失败：{exc.reason}') from exc
    except (RemoteDisconnected, TimeoutError, OSError) as exc:
        raise RuntimeError(f'模型连接中断：{exc}') from exc
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError('模型返回格式不符合 OpenAI-compatible Chat Completions。') from exc


def _perform_openai_compatible_request(config: dict[str, Any], request: Request) -> dict[str, Any]:
    limit_key = _model_limit_key(config)
    requests_per_minute = _positive_int(config.get('requests_per_minute'), default=5, minimum=1, maximum=120)
    max_concurrency = _positive_int(config.get('max_concurrency'), default=len(FULL_LLM_AGENT_SPECS), minimum=1, maximum=12)
    timeout = _effective_timeout(config.get('model', ''), config['timeout_seconds'])
    last_rate_limit_error = ''
    for attempt in range(2):
        _wait_for_model_rate_limit(limit_key, requests_per_minute)
        semaphore = _get_model_semaphore(limit_key, max_concurrency)
        try:
            with semaphore:
                with urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            body = exc.read().decode('utf-8', errors='ignore')[:300]
            if exc.code != 429 or attempt == 1:
                raise RuntimeError(f'HTTP {exc.code} {body}') from exc
            last_rate_limit_error = body
            time.sleep(_http_retry_after_seconds(exc))
    raise RuntimeError(f'HTTP 429 {last_rate_limit_error}')


def _http_retry_after_seconds(exc: HTTPError) -> float:
    raw_value = exc.headers.get('Retry-After') if exc.headers else None
    try:
        retry_after = float(raw_value) if raw_value is not None else MODEL_RATE_LIMIT_RETRY_SECONDS
    except (TypeError, ValueError):
        retry_after = MODEL_RATE_LIMIT_RETRY_SECONDS
    return min(max(retry_after, 1.0), MODEL_RATE_LIMIT_RETRY_SECONDS)


def _model_limit_key(config: dict[str, Any]) -> str:
    if config.get('model_config_id'):
        return f'managed:{config["model_config_id"]}'
    return '|'.join([
        str(config.get('base_url') or '').strip().rstrip('/'),
        str(config.get('model') or '').strip(),
    ])


def _wait_for_model_rate_limit(limit_key: str, requests_per_minute: int) -> None:
    while True:
        with _MODEL_RATE_LOCK:
            now = time.monotonic()
            timestamps = _MODEL_RATE_TIMESTAMPS[limit_key]
            while timestamps and now - timestamps[0] >= MODEL_RATE_WINDOW_SECONDS:
                timestamps.popleft()
            if len(timestamps) < requests_per_minute:
                timestamps.append(now)
                return
            wait_seconds = MODEL_RATE_WINDOW_SECONDS - (now - timestamps[0])
        time.sleep(max(wait_seconds, 0.05))


def _get_model_semaphore(limit_key: str, max_concurrency: int) -> threading.BoundedSemaphore:
    with _MODEL_SEMAPHORES_LOCK:
        semaphore_key = (limit_key, max_concurrency)
        semaphore = _MODEL_SEMAPHORES.get(semaphore_key)
        if semaphore is None:
            semaphore = threading.BoundedSemaphore(max_concurrency)
            _MODEL_SEMAPHORES[semaphore_key] = semaphore
        return semaphore


def _effective_max_tokens(model: str, max_tokens: int) -> int:
    if _is_reasoning_model(model):
        return max(max_tokens, 8192)
    return max_tokens


def _effective_timeout(model: str, timeout_seconds: int) -> int:
    if _is_reasoning_model(model):
        return max(timeout_seconds, 90)
    return timeout_seconds


def _is_reasoning_model(model: str) -> bool:
    normalized = (model or '').lower()
    return any(marker in normalized for marker in ('think', 'reasoner', 'reasoning'))


def _message_content_to_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get('text') or item.get('content')
                if text:
                    parts.append(str(text))
        return ''.join(parts).strip()
    return ''


def _apply_llm_critique(
    plan: dict[str, Any],
    candidates_by_meal: dict[str, list[dict[str, Any]]],
    critique: dict[str, Any],
) -> None:
    meals = critique.get('meals')
    if not isinstance(meals, list):
        raise ValueError('复核 JSON 缺少 meals 数组。')
    meal_payload_map = {item.get('key'): item for item in meals if isinstance(item, dict)}
    for meal in plan.get('meals', []):
        payload = meal_payload_map.get(meal['key'])
        if not payload:
            continue
        replacement = _find_candidate_by_recipe_id(candidates_by_meal.get(meal['key'], []), payload.get('recipe_id'))
        if replacement:
            meal['selected'] = replacement
        if payload.get('summary'):
            meal['summary'] = str(payload['summary'])
        if meal.get('selected') and payload.get('reason'):
            meal['selected'] = dict(meal['selected'])
            meal['selected']['reason'] = str(payload['reason'])
            meal['selected']['llm_reason'] = str(payload['reason'])


def _find_candidate_by_recipe_id(candidates: list[dict[str, Any]], recipe_id) -> dict[str, Any] | None:
    try:
        target_id = int(recipe_id)
    except (TypeError, ValueError):
        return None
    for candidate in candidates:
        if candidate['recipe'].id == target_id:
            return candidate
    return None


def _extract_json_payload(content: str) -> dict[str, Any]:
    text = (content or '').strip()
    if not text:
        raise ValueError('模型没有返回内容。')
    parsed = _extract_json_payload_or_none(text)
    if not isinstance(parsed, dict):
        raise ValueError('模型返回内容中没有 JSON 对象。')
    return parsed


def _extract_json_payload_or_none(content: str) -> dict[str, Any] | None:
    text = (content or '').strip()
    if not text:
        return None
    parsed = _parse_json_candidate(text)
    if parsed is None:
        match = re.search(r'```(?:json)?\s*(.*?)\s*```', text, flags=re.S)
        if match:
            parsed = _parse_json_candidate(match.group(1).strip())
    if parsed is None:
        parsed = _parse_embedded_json(text)
    return parsed if isinstance(parsed, dict) else None


def _parse_json_candidate(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _parse_embedded_json(text: str):
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != '{':
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _normalize_name(name: str) -> str:
    return re.sub(r'[\s·\-—_*#•,，/（）()【】\[\]]+', '', (name or '').lower())


def _is_free_seasoning(name: str, category_name: str) -> bool:
    if category_name in FREE_SEASONING_CATEGORY_NAMES:
        return True
    normalized = _normalize_name(name)
    return any(_normalize_name(keyword) in normalized for keyword in FREE_SEASONING_KEYWORDS)


def _float(value) -> float:
    if value in (None, ''):
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _positive_int(value, default: int, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    number = max(number, minimum)
    if maximum is not None:
        number = min(number, maximum)
    return number
