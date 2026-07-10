
from django.contrib import admin

from .models import LLMProviderConfig, VisionProviderConfig


@admin.register(VisionProviderConfig)
class VisionProviderConfigAdmin(admin.ModelAdmin):
    list_display = ['provider', 'provider_name', 'model', 'requests_per_minute', 'enabled', 'updated_at']
    list_filter = ['provider', 'enabled']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(LLMProviderConfig)
class LLMProviderConfigAdmin(admin.ModelAdmin):
    list_display = [
        'name',
        'provider_name',
        'model',
        'priority',
        'requests_per_minute',
        'max_concurrency',
        'enabled',
        'updated_at',
    ]
    list_filter = ['enabled', 'provider_name']
    readonly_fields = ['created_at', 'updated_at']
