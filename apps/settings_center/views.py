from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.db import connection
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.dashboard.recommendations import full_llm_enabled, get_full_llm_snapshot, schedule_full_llm_refresh
from apps.ocr.configuration import (
    apply_llm_config_form,
    apply_vision_config_form,
    coerce_int,
    llm_config_messages,
    positive_int_from_post,
)
from apps.ocr.models import LLMProviderConfig, VisionProviderConfig
from apps.ocr.vision import check_vision_config

from .models import RuntimeSettings
from .services import get_runtime_preferences

VALID_SECTIONS = {'overview', 'vision', 'llm', 'recommendation', 'system'}


def index(request):
    active_section = _valid_section(request.GET.get('section') or request.POST.get('section'))
    if request.method == 'POST':
        return _handle_post(request, active_section)
    return render_settings_center(request, active_section=active_section)


def render_settings_center(
    request,
    *,
    active_section='overview',
    vision_config=None,
    vision_check_result=None,
):
    active_section = _valid_section(active_section)
    vision_config = vision_config or VisionProviderConfig.get_solo()
    vision_check_result = vision_check_result or check_vision_config(vision_config)
    configs = list(LLMProviderConfig.objects.order_by('priority', 'id'))
    config_cards = [
        {
            'config': config,
            'check_messages': llm_config_messages(config),
        }
        for config in configs
    ]
    active_complete_count = sum(
        1 for card in config_cards if card['config'].enabled and not card['check_messages']
    )
    runtime_settings = RuntimeSettings.objects.filter(pk=1).first()
    runtime_preferences = get_runtime_preferences()
    snapshot = get_full_llm_snapshot()

    context = {
        'active_section': active_section,
        'vision_config': vision_config,
        'vision_check_result': vision_check_result,
        'provider_choices': VisionProviderConfig.PROVIDER_CHOICES,
        'config_cards': config_cards,
        'llm_config_count': len(config_cards),
        'active_complete_count': active_complete_count,
        'runtime_settings': runtime_settings,
        'runtime_preferences': runtime_preferences,
        'snapshot': snapshot,
        'full_llm_enabled': full_llm_enabled(),
        'system_info': _system_info(),
    }
    return render(request, 'settings_center/index.html', context)


def _handle_post(request, active_section):
    action = request.POST.get('action', '')

    if action in {'vision_check', 'vision_save'}:
        config = VisionProviderConfig.get_solo()
        should_save = action == 'vision_save'
        draft = apply_vision_config_form(config, request, commit=should_save)
        check_result = check_vision_config(draft)
        if not should_save:
            return render_settings_center(
                request,
                active_section='vision',
                vision_config=draft,
                vision_check_result=check_result,
            )
        if check_result.ok:
            messages.success(request, '视觉辅助配置已保存')
        else:
            messages.warning(request, '配置已保存，但仍有项目需要补全')
        return redirect(_section_url('vision'))

    if action in {'llm_create', 'llm_save'}:
        config_id = coerce_int(request.POST.get('config_id'))
        if action == 'llm_create':
            config = LLMProviderConfig(created_by=request.user)
        else:
            config = LLMProviderConfig.objects.filter(pk=config_id).first()
            if not config:
                messages.warning(request, '未找到要保存的模型配置')
                return redirect(_section_url('llm'))
        apply_llm_config_form(config, request, commit=True)
        messages.success(request, '大模型配置已保存')
        return redirect(_section_url('llm'))

    if action == 'llm_delete':
        config_id = coerce_int(request.POST.get('config_id'))
        deleted, _ = LLMProviderConfig.objects.filter(pk=config_id).delete()
        if deleted:
            messages.success(request, '大模型配置已删除')
        else:
            messages.warning(request, '未找到要删除的模型配置')
        return redirect(_section_url('llm'))

    if action == 'runtime_save':
        runtime_settings = RuntimeSettings.get_solo()
        runtime_settings.full_llm_enabled_override = request.POST.get('full_llm_enabled') == 'on'
        runtime_settings.background_refresh_enabled_override = request.POST.get('background_refresh_enabled') == 'on'
        runtime_settings.refresh_minutes_override = positive_int_from_post(
            request.POST.get('refresh_minutes'), default=240, minimum=5, maximum=10080
        )
        runtime_settings.error_retry_minutes_override = positive_int_from_post(
            request.POST.get('error_retry_minutes'), default=30, minimum=1, maximum=1440
        )
        runtime_settings.updated_by = request.user
        runtime_settings.save()
        messages.success(request, '推荐调度设置已保存')
        return redirect(_section_url('recommendation'))

    if action == 'runtime_reset':
        RuntimeSettings.get_solo().reset_overrides(user=request.user)
        messages.success(request, '推荐调度已恢复为环境配置')
        return redirect(_section_url('recommendation'))

    if action == 'runtime_refresh':
        if not full_llm_enabled():
            messages.warning(request, '请先启用全大模型推荐')
        else:
            _, started = schedule_full_llm_refresh(force=True)
            if started:
                messages.success(request, '后台推荐刷新已启动')
            else:
                messages.info(request, '推荐任务已在运行')
        return redirect(_section_url('recommendation'))

    messages.warning(request, '未识别的设置操作')
    return redirect(_section_url(active_section))


def _valid_section(value):
    return value if value in VALID_SECTIONS else 'overview'


def _section_url(section):
    return f'{reverse("settings_center:index")}?section={_valid_section(section)}'


def _system_info():
    media_root = Path(settings.MEDIA_ROOT)
    recipe_repo_path = Path(settings.COOKLIKEHOC_REPO_PATH)
    engine = settings.DATABASES['default']['ENGINE'].rsplit('.', 1)[-1]
    engine_labels = {
        'sqlite3': 'SQLite',
        'mysql': 'MySQL',
        'postgresql': 'PostgreSQL',
    }
    return {
        'database': engine_labels.get(engine, connection.vendor.title()),
        'debug': settings.DEBUG,
        'timezone': settings.TIME_ZONE,
        'media_root': str(media_root),
        'media_ready': media_root.exists(),
        'recipe_repo_url': settings.COOKLIKEHOC_REPO_URL,
        'recipe_repo_path': str(recipe_repo_path),
        'recipe_repo_ready': recipe_repo_path.exists(),
    }
