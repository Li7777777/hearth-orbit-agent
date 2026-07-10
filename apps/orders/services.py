"""Order side-effect services.

Keep aggregate updates in one place so OCR, manual entry, and deletion paths
cannot drift apart.
"""

from decimal import Decimal

from django.db import transaction

from apps.dishes.models import Dish

from .models import DailyDishStatistic, Order, OrderItem

ZERO = Decimal('0')


def _non_negative(value):
    return max(value or ZERO, ZERO)


def apply_order_item_effects(item: OrderItem) -> None:
    """Apply one saved order item to dish totals and daily statistics."""
    if not item.dish_id:
        return

    quantity = item.quantity or ZERO
    amount = item.subtotal or ZERO

    dish = Dish.objects.select_for_update().get(pk=item.dish_id)
    dish.total_ordered = _non_negative(dish.total_ordered + quantity)
    dish.save(update_fields=['total_ordered'])

    stat, _ = DailyDishStatistic.objects.select_for_update().get_or_create(
        dish_id=item.dish_id,
        stat_date=item.order.order_date,
        defaults={'total_quantity': ZERO, 'order_count': 0, 'total_amount': ZERO},
    )
    stat.total_quantity = _non_negative(stat.total_quantity + quantity)
    stat.order_count += 1
    stat.total_amount = _non_negative(stat.total_amount + amount)
    stat.save(update_fields=['total_quantity', 'order_count', 'total_amount'])


def reverse_order_item_effects(item: OrderItem) -> None:
    """Reverse one order item from dish totals and daily statistics."""
    if not item.dish_id:
        return

    quantity = item.quantity or ZERO
    amount = item.subtotal or ZERO

    try:
        dish = Dish.objects.select_for_update().get(pk=item.dish_id)
    except Dish.DoesNotExist:
        dish = None

    if dish:
        dish.total_ordered = _non_negative(dish.total_ordered - quantity)
        dish.save(update_fields=['total_ordered'])

    stat = (
        DailyDishStatistic.objects.select_for_update()
        .filter(dish_id=item.dish_id, stat_date=item.order.order_date)
        .first()
    )
    if not stat:
        return

    stat.total_quantity = _non_negative(stat.total_quantity - quantity)
    stat.order_count = max((stat.order_count or 0) - 1, 0)
    stat.total_amount = _non_negative(stat.total_amount - amount)
    if stat.total_quantity == ZERO and stat.order_count == 0 and stat.total_amount == ZERO:
        stat.delete()
    else:
        stat.save(update_fields=['total_quantity', 'order_count', 'total_amount'])


def reverse_order_effects(order: Order) -> None:
    """Reverse all aggregate side effects for an order before deletion."""
    for item in order.items.select_related('dish').all():
        reverse_order_item_effects(item)


@transaction.atomic
def delete_order_with_effects(order: Order) -> None:
    """Delete an order and keep denormalized aggregates consistent."""
    reverse_order_effects(order)
    order.delete()
