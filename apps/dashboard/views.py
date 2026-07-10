import logging
from datetime import date

from django.db.models import Sum
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from apps.dishes.models import Dish
from apps.orders.models import DailyDishStatistic, Order
from apps.recipes.meal_agents import build_daily_meal_plan
from apps.recipes.models import Recipe
from apps.recipes.recommendation import recommend_homepage_recipes

from .recommendations import (
    ensure_full_llm_refresh,
    full_llm_enabled,
    get_full_llm_snapshot,
    schedule_full_llm_refresh,
)

logger = logging.getLogger(__name__)


def index(request):
    today = date.today()

    # 汇总数据
    today_orders = Order.objects.filter(order_date=today).count()
    total_dishes = Dish.objects.filter(is_active=True).count()
    total_recipes = Recipe.objects.filter(is_published=True).count()

    today_stats = DailyDishStatistic.objects.filter(stat_date=today)
    today_quantity = today_stats.aggregate(total=Sum('total_quantity'))['total'] or 0

    # 今日 TOP5
    top_dishes = (
        today_stats
        .values('dish__name')
        .annotate(quantity=Sum('total_quantity'))
        .order_by('-quantity')[:5]
    )
    top_list = [{'name': t['dish__name'], 'quantity': float(t['quantity'])} for t in top_dishes]
    full_llm_snapshot = ensure_full_llm_refresh() if full_llm_enabled() else None

    return render(request, 'dashboard/index.html', {
        'today_orders': today_orders,
        'total_dishes': total_dishes,
        'total_recipes': total_recipes,
        'today_quantity': int(today_quantity),
        'top_dishes': top_list,
        'full_llm_snapshot': full_llm_snapshot,
        'full_llm_enabled': full_llm_enabled(),
    })


@require_GET
def local_meal_plan(request):
    try:
        plan = build_daily_meal_plan(limit_per_meal=3, mark_recommended=True, use_llm=False)
        error_message = ''
    except Exception:
        logger.exception('生成本地三餐推荐失败')
        plan = None
        error_message = '本地推荐暂时无法生成，请稍后重试。'
    return render(request, 'dashboard/_local_plan_region.html', {
        'daily_meal_plan': plan,
        'local_plan_error': error_message,
    })


@require_GET
def recipe_recommendations(request):
    try:
        recommendations = recommend_homepage_recipes(limit=5, mark_recommended=True)
        error_message = ''
    except Exception:
        logger.exception('生成首页菜谱推荐失败')
        recommendations = []
        error_message = '菜谱匹配暂时不可用，请稍后重试。'
    return render(request, 'dashboard/_recipe_recommendations_region.html', {
        'recommended_recipes': recommendations,
        'recommendations_loaded': True,
        'recommendations_error': error_message,
    })


@require_GET
def full_llm_plan_status(request):
    snapshot = ensure_full_llm_refresh() if full_llm_enabled() else get_full_llm_snapshot()
    return render(request, 'dashboard/_async_plan_region.html', {
        'full_llm_snapshot': snapshot,
        'full_llm_enabled': full_llm_enabled(),
    })


@require_POST
def full_llm_plan_refresh(request):
    snapshot, _ = schedule_full_llm_refresh(force=True)
    return render(request, 'dashboard/_async_plan_region.html', {
        'full_llm_snapshot': snapshot,
        'full_llm_enabled': full_llm_enabled(),
    })
