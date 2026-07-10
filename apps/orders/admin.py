from django.contrib import admin

from .models import DailyDishStatistic, Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['id', 'order_date', 'source', 'total_items', 'total_amount', 'created_at']
    list_filter = ['source', 'order_date']
    inlines = [OrderItemInline]


@admin.register(DailyDishStatistic)
class DailyDishStatisticAdmin(admin.ModelAdmin):
    list_display = ['dish', 'stat_date', 'total_quantity', 'order_count', 'total_amount']
    list_filter = ['stat_date']
