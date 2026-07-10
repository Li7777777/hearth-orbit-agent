from django.contrib import admin

from .models import RuntimeSettings


@admin.register(RuntimeSettings)
class RuntimeSettingsAdmin(admin.ModelAdmin):
    list_display = (
        'full_llm_enabled_override',
        'background_refresh_enabled_override',
        'refresh_minutes_override',
        'error_retry_minutes_override',
        'updated_by',
        'updated_at',
    )
    readonly_fields = ('updated_at',)

    def has_add_permission(self, request):
        return not RuntimeSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
