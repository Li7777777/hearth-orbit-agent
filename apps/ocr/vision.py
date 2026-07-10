"""Large-model visual assistance for OCR fallback."""

from __future__ import annotations

import base64
import json
import mimetypes
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import VisionProviderConfig
from .parser import ParsedOrderItem

RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_WINDOWS: dict[str, deque[float]] = defaultdict(deque)


class VisionConfigError(ValueError):
    """Raised when the configured vision provider is incomplete."""


class VisionProviderError(RuntimeError):
    """Raised when the remote provider cannot return parseable data."""


@dataclass
class VisionRecognitionResult:
    items: list[ParsedOrderItem]
    raw_text: str
    provider_label: str


@dataclass
class VisionDishRecognitionResult:
    name: str
    category: str
    unit: str
    specification: str
    default_price: float | None
    storage: str
    description: str
    raw_text: str
    provider_label: str
    confidence: float | None = None


@dataclass
class VisionConfigCheck:
    ok: bool
    messages: list[str]


DISH_RECOGNITION_PROMPT = (
    '你是食材照片结构化识别助手，专门识别用户拍摄的单个食材、包装食材或购物照片。'
    '请识别画面中最主要、最适合作为库存食材登记的对象；如果有多个食材，只选择最显眼或用户最可能要登记的一个。'
    '不要把餐具、背景、品牌广告、价格标签标题或无关文字当成食材名。'
    '请尽量给出适合库存表单的字段：name 为简洁食材名，category 只能从'
    '“肉禽蛋类、蔬果类、水产海鲜、豆菌类、粮油调味、乳品饮料、其他”中选择；'
    'unit 是常用计量单位，如斤、个、袋、盒、瓶、包；specification 是图片中可见的规格/净含量/包装，'
    'default_price 只在图片中明确看到价格时填写数字，否则为 null；'
    'storage 只能是“常温”“冷藏”“冷冻”或 null；description 用一句话记录可见状态、包装或识别依据。'
    '只返回一个 JSON 对象，不要 Markdown，不要解释，不要多余文字。'
    '不要输出 <think>、推理过程、分析步骤或任何 JSON 之外的前后缀。格式严格为：'
    '{"name":"食材名称","category":"蔬果类","unit":"斤","specification":"500g/盒",'
    '"default_price":12.3,"storage":"冷藏","description":"一句简短描述","confidence":0.85,'
    '"raw_text":"图片中看到的关键文字"}。'
    '无法可靠识别时返回 {"name":"","category":"其他","unit":"","specification":"",'
    '"default_price":null,"storage":null,"description":"未能可靠识别","confidence":0,"raw_text":""}。'
)


def check_vision_config(config: VisionProviderConfig) -> VisionConfigCheck:
    messages = []
    if not config.enabled:
        messages.append('视觉辅助未启用。')
    allowed_providers = {choice[0] for choice in VisionProviderConfig.PROVIDER_CHOICES}
    if not config.provider:
        messages.append('请选择服务商。')
    elif config.provider not in allowed_providers:
        messages.append('请选择受支持的服务商。')
    if not config.has_api_key:
        messages.append('请填写 API Key。')
    model = (config.model or '').strip()
    if not model:
        messages.append('请填写模型名称。')
    elif config.provider == VisionProviderConfig.PROVIDER_ANTHROPIC and model.startswith(('gpt-', 'o')):
        messages.append('Anthropic 需要填写 Claude 视觉模型名称。')
    elif config.provider in (
        VisionProviderConfig.PROVIDER_OPENAI,
        VisionProviderConfig.PROVIDER_OPENAI_COMPATIBLE,
    ) and model.startswith('claude-'):
        messages.append('OpenAI-compatible 接口需要填写兼容 Chat Completions 的视觉模型名称。')
    if config.provider == VisionProviderConfig.PROVIDER_OPENAI_COMPATIBLE and not config.base_url.strip():
        messages.append('第三方 OpenAI-compatible 服务需要填写 Base URL。')
    if config.provider in (VisionProviderConfig.PROVIDER_OPENAI, VisionProviderConfig.PROVIDER_ANTHROPIC) and config.base_url:
        messages.append('官方服务商通常不需要填写 Base URL；如需自定义地址请改选第三方 OpenAI-compatible。')
    if config.base_url and not config.base_url.startswith(('http://', 'https://')):
        messages.append('Base URL 必须以 http:// 或 https:// 开头。')
    if config.timeout_seconds < 5:
        messages.append('请求超时建议不低于 5 秒。')
    if config.requests_per_minute < 1:
        messages.append('RPM 限制必须至少为 1。')
    if not (config.prompt or '').strip():
        messages.append('请填写识别提示词。')
    return VisionConfigCheck(ok=not messages, messages=messages or ['本地配置项完整，可以用于视觉辅助识别。'])


def recognize_order_image_with_vision(config: VisionProviderConfig, image_path: str | Path) -> VisionRecognitionResult:
    check = check_vision_config(config)
    if not check.ok:
        raise VisionConfigError('；'.join(check.messages))

    _enforce_rate_limit(config)

    if config.provider == VisionProviderConfig.PROVIDER_ANTHROPIC:
        content = _call_anthropic(config, image_path)
    elif config.provider in (VisionProviderConfig.PROVIDER_OPENAI, VisionProviderConfig.PROVIDER_OPENAI_COMPATIBLE):
        content = _call_openai_compatible(config, image_path)
    else:
        raise VisionConfigError('不支持的视觉服务商。')

    payload = _extract_json_payload(content)
    items = _normalize_items(payload)
    raw_text = str(payload.get('raw_text') or payload.get('text') or content)
    return VisionRecognitionResult(items=items, raw_text=raw_text, provider_label=_provider_label(config))


def recognize_dish_image_with_vision(config: VisionProviderConfig, image_path: str | Path) -> VisionDishRecognitionResult:
    check = check_vision_config(config)
    if not check.ok:
        raise VisionConfigError('；'.join(check.messages))

    _enforce_rate_limit(config)

    if config.provider == VisionProviderConfig.PROVIDER_ANTHROPIC:
        content = _call_anthropic(config, image_path, prompt=DISH_RECOGNITION_PROMPT)
    elif config.provider in (VisionProviderConfig.PROVIDER_OPENAI, VisionProviderConfig.PROVIDER_OPENAI_COMPATIBLE):
        content = _call_openai_compatible(config, image_path, prompt=DISH_RECOGNITION_PROMPT)
    else:
        raise VisionConfigError('不支持的视觉服务商。')

    payload = _extract_json_payload(content)
    return _normalize_dish(payload, content, _provider_label(config))


def _provider_label(config: VisionProviderConfig) -> str:
    if config.provider == VisionProviderConfig.PROVIDER_OPENAI_COMPATIBLE and config.provider_name:
        return config.provider_name
    return config.get_provider_display()


def _enforce_rate_limit(config: VisionProviderConfig):
    limit = max(int(config.requests_per_minute or 5), 1)
    now = monotonic()
    window = _RATE_LIMIT_WINDOWS[_rate_limit_key(config)]
    while window and now - window[0] >= RATE_LIMIT_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= limit:
        raise VisionProviderError(f'视觉服务请求过于频繁：当前限制为每分钟 {limit} 次，请稍后再试或在设置中调整 RPM。')
    window.append(now)


def _rate_limit_key(config: VisionProviderConfig) -> str:
    return '|'.join([
        config.provider,
        config.effective_base_url,
        config.model.strip(),
    ])


def _image_data(image_path: str | Path) -> tuple[str, str]:
    path = Path(image_path)
    mime_type = mimetypes.guess_type(path.name)[0] or 'image/png'
    encoded = base64.b64encode(path.read_bytes()).decode('ascii')
    return mime_type, encoded


def _call_openai_compatible(config: VisionProviderConfig, image_path: str | Path, prompt: str | None = None) -> str:
    mime_type, encoded = _image_data(image_path)
    payload = {
        'model': config.model.strip(),
        'messages': [
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': (prompt or config.prompt).strip()},
                    {'type': 'image_url', 'image_url': {'url': f'data:{mime_type};base64,{encoded}'}},
                ],
            }
        ],
        'temperature': 0,
    }
    response = _post_json(
        f'{config.effective_base_url}/chat/completions',
        payload,
        headers={
            'Authorization': f'Bearer {config.api_key.strip()}',
            'Accept': 'application/json',
        },
        timeout=config.timeout_seconds,
    )
    try:
        return response['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError) as exc:
        raise VisionProviderError('视觉服务返回格式不符合 OpenAI-compatible 响应。') from exc


def _call_anthropic(config: VisionProviderConfig, image_path: str | Path, prompt: str | None = None) -> str:
    mime_type, encoded = _image_data(image_path)
    payload = {
        'model': config.model.strip(),
        'max_tokens': 2048,
        'temperature': 0,
        'messages': [
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': (prompt or config.prompt).strip()},
                    {'type': 'image', 'source': {'type': 'base64', 'media_type': mime_type, 'data': encoded}},
                ],
            }
        ],
    }
    response = _post_json(
        f'{config.effective_base_url}/messages',
        payload,
        headers={
            'Accept': 'application/json',
            'x-api-key': config.api_key.strip(),
            'anthropic-version': '2023-06-01',
        },
        timeout=config.timeout_seconds,
    )
    try:
        return '\n'.join(block.get('text', '') for block in response.get('content', []) if block.get('type') == 'text')
    except AttributeError as exc:
        raise VisionProviderError('视觉服务返回格式不符合 Anthropic 响应。') from exc


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json', **headers},
        method='POST',
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except HTTPError as exc:
        body = exc.read().decode('utf-8', errors='ignore')[:500]
        raise VisionProviderError(f'视觉服务请求失败：HTTP {exc.code} {body}') from exc
    except URLError as exc:
        raise VisionProviderError(f'视觉服务网络连接失败：{exc.reason}') from exc
    except json.JSONDecodeError as exc:
        raise VisionProviderError('视觉服务返回了非 JSON 响应。') from exc


def _extract_json_payload(content: str) -> dict[str, Any]:
    text = (content or '').strip()
    if not text:
        raise VisionProviderError('视觉服务没有返回内容。')
    parsed = _parse_json_candidate(text)
    if parsed is None:
        match = re.search(r'```(?:json)?\s*(.*?)\s*```', text, flags=re.S)
        if match:
            parsed = _parse_json_candidate(match.group(1).strip())
    if parsed is None:
        parsed = _parse_embedded_json(text)
    if parsed is None:
        raise VisionProviderError('视觉服务返回内容中没有可解析的 JSON。')
    if isinstance(parsed, list):
        return {'items': parsed, 'raw_text': text}
    if isinstance(parsed, dict):
        return parsed
    raise VisionProviderError('视觉服务 JSON 顶层必须是对象或数组。')


def _parse_json_candidate(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _parse_embedded_json(text: str):
    decoder = json.JSONDecoder()
    candidates = []
    for index, char in enumerate(text):
        if char not in '{[':
            continue
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict | list):
            candidates.append((value, end))
    for value, _ in candidates:
        if isinstance(value, dict) and any(key in value for key in ('items', 'products', 'dishes')):
            return value
    return max(candidates, key=lambda candidate: candidate[1])[0] if candidates else None


def _normalize_items(payload: dict[str, Any]) -> list[ParsedOrderItem]:
    source_items = payload.get('items') or payload.get('products') or payload.get('dishes') or []
    if not isinstance(source_items, list):
        raise VisionProviderError('视觉服务 JSON 中的 items 必须是数组。')

    items = []
    for raw in source_items:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get('dish_name') or raw.get('name') or raw.get('item_name') or raw.get('商品名称') or '').strip()
        if not name:
            continue
        quantity = _to_float(raw.get('quantity') or raw.get('qty') or raw.get('数量'), 1.0)
        unit_price = _optional_float(raw.get('unit_price') or raw.get('price') or raw.get('单价'))
        subtotal = _optional_float(raw.get('subtotal') or raw.get('amount') or raw.get('total') or raw.get('小计'))
        if subtotal is None and unit_price is not None:
            subtotal = round(unit_price * quantity, 2)
        items.append(ParsedOrderItem(
            dish_name=name,
            quantity=quantity,
            unit_price=unit_price,
            subtotal=subtotal,
            raw_text=json.dumps(raw, ensure_ascii=False),
        ))
    return items


def _normalize_dish(payload: dict[str, Any], content: str, provider_label: str) -> VisionDishRecognitionResult:
    raw = _first_dish_payload(payload)
    name = _string_value(raw, 'name', 'dish_name', 'ingredient_name', 'item_name', '食材名称', '名称', '商品名称')
    if not name:
        raise VisionProviderError('视觉服务没有识别出食材名称。')

    return VisionDishRecognitionResult(
        name=name,
        category=_string_value(raw, 'category', 'category_name', '分类'),
        unit=_string_value(raw, 'unit', '单位', '计量单位'),
        specification=_string_value(raw, 'specification', 'spec', 'package', 'weight', '规格', '净含量'),
        default_price=_optional_float(_first_present(raw, 'default_price', 'price', 'unit_price', '参考单价', '单价', '价格')),
        storage=_normalize_storage(_string_value(raw, 'storage', 'storage_method', '储存方式', '保存方式')),
        description=_string_value(raw, 'description', 'desc', 'note', 'notes', '描述', '说明'),
        raw_text=str(raw.get('raw_text') or payload.get('raw_text') or payload.get('text') or content),
        provider_label=provider_label,
        confidence=_optional_float(_first_present(raw, 'confidence', 'score', '置信度')),
    )


def _first_dish_payload(payload: dict[str, Any]) -> dict[str, Any]:
    source = payload.get('item') or payload.get('dish') or payload.get('ingredient')
    if isinstance(source, dict):
        return source

    items = payload.get('items') or payload.get('products') or payload.get('dishes') or payload.get('ingredients')
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                return item
    return payload


def _string_value(data: dict[str, Any], *keys: str) -> str:
    value = _first_present(data, *keys)
    if value in (None, ''):
        return ''
    return str(value).strip()


def _first_present(data: dict[str, Any], *keys: str):
    for key in keys:
        value = data.get(key)
        if value not in (None, ''):
            return value
    return None


def _normalize_storage(value: str) -> str:
    text = (value or '').strip()
    if not text:
        return ''
    if '冷冻' in text or '速冻' in text or text == '冻':
        return '冷冻'
    if '冷藏' in text or '保鲜' in text or '冰箱' in text:
        return '冷藏'
    if '常温' in text or '室温' in text:
        return '常温'
    if text in {'常温', '冷藏', '冷冻'}:
        return text
    return ''


def _to_float(value, default: float) -> float:
    parsed = _optional_float(value)
    return default if parsed is None else parsed


def _optional_float(value) -> float | None:
    if value in (None, ''):
        return None
    try:
        return float(str(value).replace('¥', '').replace('￥', '').replace(',', '').strip())
    except (TypeError, ValueError):
        return None
