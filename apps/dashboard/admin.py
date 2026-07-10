from django.contrib import admin

from .models import MealPlanSnapshot


@admin.register(MealPlanSnapshot)
class MealPlanSnapshotAdmin(admin.ModelAdmin):
    list_display = ('key', 'status', 'generated_for', 'generated_at', 'last_attempt_at', 'updated_at')
    readonly_fields = (
        'key',
        'status',
        'rendered_html',
        'generated_for',
        'generated_at',
        'refresh_started_at',
        'refresh_owner_pid',
        'refresh_owner_host',
        'last_attempt_at',
        'error_message',
        'updated_at',
    )
