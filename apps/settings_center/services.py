from django.conf import settings
from django.db import OperationalError, ProgrammingError

from .models import RuntimeSettings


def get_runtime_preferences() -> dict:
    full_llm_env = _setting_dict('MEAL_AGENT_FULL_LLM')
    refresh_env = _setting_dict('MEAL_PLAN_BACKGROUND_REFRESH')
    runtime_settings = _load_runtime_settings()

    return {
        'full_llm_enabled': _effective_value(
            runtime_settings,
            'full_llm_enabled_override',
            bool(full_llm_env.get('enabled', False)),
        ),
        'background_refresh_enabled': _effective_value(
            runtime_settings,
            'background_refresh_enabled_override',
            bool(refresh_env.get('enabled', True)),
        ),
        'refresh_minutes': _effective_positive_int(
            runtime_settings,
            'refresh_minutes_override',
            refresh_env.get('refresh_minutes', 240),
            default=240,
            minimum=5,
            maximum=10080,
        ),
        'error_retry_minutes': _effective_positive_int(
            runtime_settings,
            'error_retry_minutes_override',
            refresh_env.get('error_retry_minutes', 30),
            default=30,
            minimum=1,
            maximum=1440,
        ),
        'full_llm_source': _source(runtime_settings, 'full_llm_enabled_override'),
        'background_refresh_source': _source(runtime_settings, 'background_refresh_enabled_override'),
        'refresh_minutes_source': _source(runtime_settings, 'refresh_minutes_override'),
        'error_retry_minutes_source': _source(runtime_settings, 'error_retry_minutes_override'),
        'has_overrides': bool(runtime_settings and any(
            getattr(runtime_settings, field) is not None
            for field in (
                'full_llm_enabled_override',
                'background_refresh_enabled_override',
                'refresh_minutes_override',
                'error_retry_minutes_override',
            )
        )),
    }


def _setting_dict(name: str) -> dict:
    value = getattr(settings, name, {})
    return value if isinstance(value, dict) else {}


def _load_runtime_settings() -> RuntimeSettings | None:
    try:
        return RuntimeSettings.objects.filter(pk=1).first()
    except (OperationalError, ProgrammingError):
        return None


def _effective_value(runtime_settings, field: str, fallback):
    if runtime_settings is None:
        return fallback
    value = getattr(runtime_settings, field)
    return fallback if value is None else value


def _effective_positive_int(
    runtime_settings,
    field: str,
    fallback,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = _effective_value(runtime_settings, field, fallback)
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(min(number, maximum), minimum)


def _source(runtime_settings, field: str) -> str:
    if runtime_settings is not None and getattr(runtime_settings, field) is not None:
        return '设置中心'
    return '.env'
