from datetime import date

from django.conf import settings
from django.db.models import Sum
from django.shortcuts import render

from apps.dishes.models import Dish
from apps.orders.models import DailyDishStatistic, Order
from apps.recipes.meal_agents import build_daily_meal_plan, build_full_llm_multi_agent_meal_plan
from apps.recipes.models import Recipe
from apps.recipes.recommendation import recommend_homepage_recipes


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
    daily_meal_plan = build_daily_meal_plan(limit_per_meal=3, mark_recommended=True)
    full_llm_meal_plan = None
    if getattr(settings, 'MEAL_AGENT_FULL_LLM', {}).get('enabled'):
        full_llm_meal_plan = build_full_llm_multi_agent_meal_plan(limit_per_meal=3, mark_recommended=True)
    recommended_recipes = recommend_homepage_recipes(limit=5, mark_recommended=True)

    return render(request, 'dashboard/index.html', {
        'today_orders': today_orders,
        'total_dishes': total_dishes,
        'total_recipes': total_recipes,
        'today_quantity': int(today_quantity),
        'top_dishes': top_list,
        'daily_meal_plan': daily_meal_plan,
        'full_llm_meal_plan': full_llm_meal_plan,
        'recommended_recipes': recommended_recipes,
    })
