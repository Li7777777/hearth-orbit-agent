from django.contrib import admin

from .models import Dish, DishCategory


@admin.register(DishCategory)
class DishCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'icon', 'sort_order', 'created_at']
    list_editable = ['sort_order']


@admin.register(Dish)
class DishAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'category', 'unit', 'specification', 'storage', 'default_price',
        'is_active', 'deactivation_reason', 'deactivated_at', 'total_ordered', 'updated_at',
    ]
    list_filter = ['category', 'is_active', 'deactivation_reason', 'storage']
    search_fields = ['name']
    list_editable = ['is_active']
