import json
from datetime import date, timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render

from apps.dishes.models import Dish

from .models import DailyDishStatistic, Order
from .services import delete_order_with_effects


def order_list(request):
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    # 默认筛选：最近30天
    if not date_from and not date_to and 'page' not in request.GET:
        today = date.today()
        date_from = (today - timedelta(days=30)).isoformat()
        date_to = today.isoformat()

    orders = Order.objects.all()
    if date_from:
        orders = orders.filter(order_date__gte=date_from)
    if date_to:
        orders = orders.filter(order_date__lte=date_to)

    paginator = Paginator(orders, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'orders/list.html', {
        'page_obj': page_obj,
        'date_from': date_from,
        'date_to': date_to,
    })


def order_create(request):
    # 手动创建在 Phase 2 实现，先跳转到 OCR
    return redirect('ocr:upload')


def order_detail(request, pk):
    order = get_object_or_404(Order.objects.prefetch_related('items__dish'), pk=pk)
    return render(request, 'orders/detail.html', {'order': order})


def order_delete(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if request.method == 'POST':
        delete_order_with_effects(order)
        messages.success(request, '订单已删除，关联统计已同步更新')
    return redirect('orders:list')


def statistics(request):
    today = date.today()
    try:
        days = int(request.GET.get('days', 7))
        if days not in (7, 14, 30):
            days = 7
    except (ValueError, TypeError):
        days = 7
    start_date = today - timedelta(days=days - 1)

    stats_qs = DailyDishStatistic.objects.filter(stat_date__gte=start_date)
    order_qs = Order.objects.filter(order_date__gte=start_date)

    # 每日汇总：菜量 + 金额
    daily_stats = list(
        stats_qs
        .values('stat_date')
        .annotate(total_qty=Sum('total_quantity'), total_amt=Sum('total_amount'))
        .order_by('stat_date')
    )
    stats_map = {s['stat_date']: s for s in daily_stats}

    labels = []
    data_qty = []
    data_amt = []
    for d in range(days):
        dt = start_date + timedelta(days=d)
        labels.append(dt.strftime('%m/%d'))
        found = stats_map.get(dt)
        data_qty.append(float(found['total_qty']) if found and found.get('total_qty') else 0)
        data_amt.append(float(found['total_amt']) if found and found.get('total_amt') else 0)

    # TOP 食材（按菜量）
    top_dishes = (
        stats_qs
        .values('dish__name')
        .annotate(total=Sum('total_quantity'))
        .order_by('-total')[:10]
    )

    # TOP 食材（按金额）
    top_amount_dishes = list(
        stats_qs
        .values('dish__name')
        .annotate(total_amount=Sum('total_amount'))
        .order_by('-total_amount')[:10]
    )

    # 分类金额分布
    category_amount_rows = (
        stats_qs
        .values('dish__category__name')
        .annotate(total_amount=Sum('total_amount'))
        .order_by('-total_amount')
    )
    category_amount_labels = []
    category_amount_data = []
    for row in category_amount_rows:
        amount = float(row['total_amount'] or 0)
        if amount <= 0:
            continue
        category_amount_labels.append(row['dish__category__name'] or '未分类')
        category_amount_data.append(amount)

    # 来源金额分布
    source_amount_rows = (
        order_qs
        .values('source')
        .annotate(total_amount=Sum('total_amount'), order_count=Count('id'))
        .order_by('-total_amount')
    )
    source_label_map = dict(Order.SOURCE_CHOICES)
    source_amount_labels = []
    source_amount_data = []
    for row in source_amount_rows:
        source_amount_labels.append(source_label_map.get(row['source'], row['source']))
        source_amount_data.append(float(row['total_amount'] or 0))

    # 金额总览指标
    amount_summary = order_qs.aggregate(total_amount=Sum('total_amount'), total_orders=Count('id'))
    quantity_summary = stats_qs.aggregate(total_qty=Sum('total_quantity'))
    period_total_amount = float(amount_summary['total_amount'] or 0)
    period_total_orders = int(amount_summary['total_orders'] or 0)
    period_total_quantity = float(quantity_summary['total_qty'] or 0)
    avg_order_amount = round(period_total_amount / period_total_orders, 2) if period_total_orders else 0
    avg_daily_amount = round(period_total_amount / days, 2) if days else 0

    price_dimension_payload = {
        'category': {
            'labels': category_amount_labels,
            'data': category_amount_data,
        },
        'source': {
            'labels': source_amount_labels,
            'data': source_amount_data,
        },
        'dish': {
            'labels': [row['dish__name'] for row in top_amount_dishes],
            'data': [float(row['total_amount'] or 0) for row in top_amount_dishes],
        },
    }

    # 食材丢弃分析
    discard_qs = Dish.objects.filter(
        is_active=False,
        deactivation_reason='discarded',
        deactivated_at__gte=start_date,
        deactivated_at__lte=today,
    )
    discard_daily_rows = (
        discard_qs
        .values('deactivated_at')
        .annotate(total=Count('id'), total_amount=Sum('default_price'))
        .order_by('deactivated_at')
    )
    discard_daily_map = {row['deactivated_at']: int(row['total'] or 0) for row in discard_daily_rows}
    discard_daily_amount_map = {row['deactivated_at']: float(row['total_amount'] or 0) for row in discard_daily_rows}
    discard_daily_data = []
    discard_amount_daily_data = []
    for d in range(days):
        dt = start_date + timedelta(days=d)
        discard_daily_data.append(discard_daily_map.get(dt, 0))
        discard_amount_daily_data.append(discard_daily_amount_map.get(dt, 0))

    discard_total = discard_qs.count()
    discard_total_amount = float(discard_qs.aggregate(total_amount=Sum('default_price'))['total_amount'] or 0)
    discard_avg_amount = round(discard_total_amount / discard_total, 2) if discard_total else 0
    discard_category_rows = (
        discard_qs
        .values('category__name')
        .annotate(total=Count('id'))
        .order_by('-total')
    )
    discard_category_labels = []
    discard_category_data = []
    for row in discard_category_rows:
        count = int(row['total'] or 0)
        if count <= 0:
            continue
        discard_category_labels.append(row['category__name'] or '未分类')
        discard_category_data.append(count)

    discard_category_amount_rows = (
        discard_qs
        .values('category__name')
        .annotate(total_amount=Sum('default_price'))
        .order_by('-total_amount')
    )
    discard_category_amount_labels = []
    discard_category_amount_data = []
    for row in discard_category_amount_rows:
        amount = float(row['total_amount'] or 0)
        if amount <= 0:
            continue
        discard_category_amount_labels.append(row['category__name'] or '未分类')
        discard_category_amount_data.append(amount)

    discarded_items = (
        discard_qs
        .select_related('category')
        .order_by('-deactivated_at', '-id')[:10]
    )

    return render(request, 'orders/statistics.html', {
        'chart_labels': json.dumps(labels),
        'chart_data': json.dumps(data_qty),
        'chart_amount_data': json.dumps(data_amt),
        'price_dimension_payload': json.dumps(price_dimension_payload, ensure_ascii=False),
        'top_dishes': top_dishes,
        'top_amount_dishes': top_amount_dishes,
        'days': days,
        'period_total_amount': period_total_amount,
        'period_total_orders': period_total_orders,
        'period_total_quantity': period_total_quantity,
        'avg_order_amount': avg_order_amount,
        'avg_daily_amount': avg_daily_amount,
        'discard_total': discard_total,
        'discard_total_amount': discard_total_amount,
        'discard_avg_amount': discard_avg_amount,
        'discard_daily_data': json.dumps(discard_daily_data),
        'discard_amount_daily_data': json.dumps(discard_amount_daily_data),
        'discard_category_payload': json.dumps({
            'labels': discard_category_labels,
            'data': discard_category_data,
        }, ensure_ascii=False),
        'discard_category_amount_payload': json.dumps({
            'labels': discard_category_amount_labels,
            'data': discard_category_amount_data,
        }, ensure_ascii=False),
        'discarded_items': discarded_items,
    })
