from decimal import Decimal, InvalidOperation

from .models import LLMProviderConfig, VisionProviderConfig


def apply_vision_config_form(config, request, commit=False):
    target = config if commit else VisionProviderConfig(
        enabled=config.enabled,
        provider=config.provider,
        provider_name=config.provider_name,
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        prompt=config.prompt,
        timeout_seconds=config.timeout_seconds,
        requests_per_minute=config.requests_per_minute,
    )
    target.enabled = request.POST.get('enabled') == 'on'
    target.provider = request.POST.get('provider', VisionProviderConfig.PROVIDER_OPENAI)
    target.provider_name = request.POST.get('provider_name', '').strip()
    if target.provider != VisionProviderConfig.PROVIDER_OPENAI_COMPATIBLE:
        target.provider_name = ''
    api_key = request.POST.get('api_key', '').strip()
    if request.POST.get('clear_api_key') == 'on':
        target.api_key = ''
    elif api_key:
        target.api_key = api_key
    target.base_url = request.POST.get('base_url', '').strip()
    if target.provider != VisionProviderConfig.PROVIDER_OPENAI_COMPATIBLE:
        target.base_url = ''
    target.model = request.POST.get('model', '').strip()
    target.prompt = request.POST.get('prompt', '').strip()
    timeout_value = _parse_decimal(request.POST.get('timeout_seconds'), Decimal('60')) or Decimal('60')
    target.timeout_seconds = max(int(timeout_value), 1)
    rpm_value = _parse_decimal(request.POST.get('requests_per_minute'), Decimal('5')) or Decimal('5')
    target.requests_per_minute = max(int(rpm_value), 1)
    if commit:
        if not target.created_by_id:
            target.created_by = request.user
        target.updated_by = request.user
        target.save()
    return target


def apply_llm_config_form(config: LLMProviderConfig, request, commit=False):
    config.enabled = request.POST.get('enabled') == 'on'
    config.name = request.POST.get('name', '').strip() or '未命名模型'
    config.provider_name = request.POST.get('provider_name', '').strip()
    api_key = request.POST.get('api_key', '').strip()
    if request.POST.get('clear_api_key') == 'on':
        config.api_key = ''
    elif api_key:
        config.api_key = api_key
    config.base_url = request.POST.get('base_url', '').strip().rstrip('/')
    config.model = request.POST.get('model', '').strip()
    config.priority = positive_int_from_post(request.POST.get('priority'), default=10, minimum=1, maximum=9999)
    config.timeout_seconds = positive_int_from_post(
        request.POST.get('timeout_seconds'), default=60, minimum=5, maximum=600
    )
    config.requests_per_minute = positive_int_from_post(
        request.POST.get('requests_per_minute'), default=5, minimum=1, maximum=120
    )
    config.max_concurrency = positive_int_from_post(
        request.POST.get('max_concurrency'), default=3, minimum=1, maximum=12
    )
    config.expert_concurrency = positive_int_from_post(
        request.POST.get('expert_concurrency'), default=3, minimum=1, maximum=3
    )
    config.notes = request.POST.get('notes', '').strip()
    if commit:
        if not config.created_by_id:
            config.created_by = request.user
        config.updated_by = request.user
        config.save()
    return config


def llm_config_messages(config: LLMProviderConfig) -> list[str]:
    validation_messages = []
    if not config.enabled:
        validation_messages.append('当前未启用。')
    if not config.has_api_key:
        validation_messages.append('请填写 API Key。')
    if not config.base_url.strip():
        validation_messages.append('请填写 Base URL。')
    elif not config.base_url.startswith(('http://', 'https://')):
        validation_messages.append('Base URL 必须以 http:// 或 https:// 开头。')
    if not config.model.strip():
        validation_messages.append('请填写模型名称。')
    if config.requests_per_minute < 1:
        validation_messages.append('RPM 必须至少为 1。')
    if config.max_concurrency < 1:
        validation_messages.append('最大并发必须至少为 1。')
    return validation_messages


def positive_int_from_post(value, default: int, minimum: int, maximum: int) -> int:
    number = _parse_decimal(value, Decimal(default)) or Decimal(default)
    return max(min(int(number), maximum), minimum)


def coerce_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_decimal(value, default):
    if value in (None, ''):
        return default
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return default
