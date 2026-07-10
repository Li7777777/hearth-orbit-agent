import json
import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.core.files.storage import default_storage
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils.text import get_valid_filename

from apps.dishes.models import Dish
from apps.dishes.services import infer_dish_category, match_dish
from apps.orders.models import Order, OrderItem
from apps.orders.services import apply_order_item_effects

from .models import LLMProviderConfig, VisionProviderConfig
from .vision import VisionConfigError, VisionProviderError, check_vision_config, recognize_order_image_with_vision

logger = logging.getLogger(__name__)


def upload(request):
    """OCR 上传页面"""
    recent_orders = Order.objects.filter(
        source='ocr', created_by=request.user
    ).order_by('-created_at')[:5]
    vision_config = VisionProviderConfig.get_solo()
    vision_check = check_vision_config(vision_config)
    return render(request, 'ocr/upload.html', {
        'recent_orders': recent_orders,
        'vision_config': vision_config,
        'vision_check': vision_check,
    })


def process(request):
    """处理上传的截图：OCR 识别 → 必要时视觉辅助 → 匹配 → 返回结果页"""
    if request.method != 'POST':
        return redirect('ocr:upload')

    image_file = request.FILES.get('image')
    if not image_file:
        messages.error(request, '请选择要识别的图片')
        return redirect('ocr:upload')

    path, full_path = _save_uploaded_image(image_file)

    vision_config = VisionProviderConfig.get_solo()
    vision_check = check_vision_config(vision_config)
    ocr_lines = []
    ocr_error = None

    try:
        from .engine import recognize_image
        ocr_lines = recognize_image(full_path)
    except Exception as exc:
        ocr_error = exc
        logger.error(f"OCR 识别失败: {exc}")

    from .parser import parse_order_text
    parsed_items = parse_order_text(ocr_lines) if ocr_lines else []
    raw_text = '\n'.join(line['text'] for line in ocr_lines)

    if (ocr_error or _should_use_vision_fallback(ocr_lines, parsed_items)) and vision_check.ok:
        try:
            result = recognize_order_image_with_vision(vision_config, full_path)
        except (VisionConfigError, VisionProviderError) as exc:
            logger.error(f"视觉辅助自动回退失败: {exc}")
            if ocr_error:
                messages.error(request, f'OCR 识别失败，视觉辅助也未成功：{exc}')
                return redirect('ocr:upload')
            messages.warning(request, f'OCR 结果较少，视觉辅助未成功：{exc}')
        else:
            if ocr_error:
                messages.info(request, 'OCR 不可用，已自动改用视觉辅助识别')
            elif not parsed_items:
                messages.info(request, 'OCR 未解析出食材，已自动改用视觉辅助识别')
            else:
                messages.info(request, 'OCR 置信度较低，已自动改用视觉辅助识别')
            if not result.items:
                messages.warning(request, '视觉辅助没有识别出食材，可在结果页手动添加')
            context = _build_result_context(
                parsed_items=result.items,
                image_path=path,
                raw_text=result.raw_text,
                recognition_method=f'{result.provider_label} 视觉辅助（自动）',
            )
            return render(request, 'ocr/result.html', context)

    if ocr_error:
        if not vision_check.ok:
            messages.error(request, f'OCR 识别失败：{ocr_error}。视觉辅助配置未完整，暂不能自动接管。')
        else:
            messages.error(request, f'OCR 识别失败：{ocr_error}')
        return redirect('ocr:upload')

    if not parsed_items:
        if vision_check.ok:
            messages.warning(request, 'OCR 未解析出食材，视觉辅助也未能生成可用结果，可在设置中调整模型或提示词后重试')
        else:
            messages.warning(request, 'OCR 未解析出食材，可在视觉设置中启用大模型辅助后再试')

    context = _build_result_context(
        parsed_items=parsed_items,
        image_path=path,
        raw_text=raw_text,
        recognition_method='PaddleOCR',
    )
    return render(request, 'ocr/result.html', context)


def vision_settings(request):
    """视觉辅助配置页。本地检查不调用第三方 API。"""
    config = VisionProviderConfig.get_solo()
    check_result = None

    if request.method == 'POST':
        action = request.POST.get('action', 'save')
        draft = _apply_vision_config_form(config, request, commit=action == 'save')
        check_result = check_vision_config(draft)
        if action == 'save':
            if check_result.ok:
                messages.success(request, '视觉辅助配置已保存')
            else:
                messages.warning(request, '配置已保存，但仍有项目需要补全')
            return redirect('ocr:vision_settings')
        config = draft

    if check_result is None:
        check_result = check_vision_config(config)

    return render(request, 'ocr/vision_settings.html', {
        'config': config,
        'check_result': check_result,
        'provider_choices': VisionProviderConfig.PROVIDER_CHOICES,
    })


def llm_settings(request):
    """Text model provider management. Local checks only; no remote API call."""
    if request.method == 'POST':
        action = request.POST.get('action', 'save')
        if action == 'delete':
            config_id = _coerce_int(request.POST.get('config_id'))
            deleted, _ = LLMProviderConfig.objects.filter(pk=config_id).delete()
            if deleted:
                messages.success(request, '大模型配置已删除')
            else:
                messages.warning(request, '未找到要删除的配置')
            return redirect('ocr:llm_settings')

        config_id = _coerce_int(request.POST.get('config_id'))
        if action == 'create':
            config = LLMProviderConfig(created_by=request.user)
        else:
            config = LLMProviderConfig.objects.filter(pk=config_id).first()
            if not config:
                messages.warning(request, '未找到要保存的配置')
                return redirect('ocr:llm_settings')
        _apply_llm_config_form(config, request, commit=True)
        messages.success(request, '大模型配置已保存')
        return redirect('ocr:llm_settings')

    configs = list(LLMProviderConfig.objects.order_by('priority', 'id'))
    config_cards = [
        {
            'config': config,
            'check_messages': _llm_config_messages(config),
        }
        for config in configs
    ]
    active_complete_count = len([
        card
        for card in config_cards
        if card['config'].enabled and not card['check_messages']
    ])
    return render(request, 'ocr/llm_settings.html', {
        'config_cards': config_cards,
        'active_complete_count': active_complete_count,
    })


def confirm(request):
    """确认 OCR 结果，创建订单和订单明细"""
    if request.method != 'POST':
        return redirect('ocr:upload')

    order_date_str = request.POST.get('order_date', '')
    image_path = request.POST.get('image_path', '')
    raw_text = request.POST.get('raw_text', '')
    auto_create = request.POST.get('auto_create_dish') == 'on'

    # 解析所有食材行
    dish_names = request.POST.getlist('dish_name[]')
    quantities = request.POST.getlist('quantity[]')
    unit_prices = request.POST.getlist('unit_price[]')
    dish_ids = request.POST.getlist('dish_id[]')

    if not dish_names:
        messages.error(request, '没有要保存的食材')
        return redirect('ocr:upload')

    try:
        order_date = date.fromisoformat(order_date_str)
    except (ValueError, TypeError):
        order_date = date.today()

    with transaction.atomic():
        # 创建订单
        order = Order.objects.create(
            order_date=order_date,
            source='ocr',
            ocr_image=image_path,
            ocr_raw_text=raw_text,
            created_by=request.user,
        )

        total_items = 0
        total_amount = Decimal('0')

        for i, name in enumerate(dish_names):
            name = name.strip()
            if not name:
                continue

            qty = _parse_decimal(quantities[i] if i < len(quantities) else '', Decimal('1'))
            price_str = unit_prices[i] if i < len(unit_prices) else ''
            price = _parse_decimal(price_str, None)
            did = dish_ids[i] if i < len(dish_ids) and dish_ids[i] else None
            inferred_category = infer_dish_category(name)

            # 尝试匹配食材
            dish = None
            is_matched = False
            if did:
                try:
                    dish = Dish.objects.get(id=int(did))
                    is_matched = True
                except (Dish.DoesNotExist, ValueError):
                    pass

            # 自动创建新食材
            if not dish and auto_create and len(name) >= 2:
                dish, _ = Dish.objects.get_or_create(
                    name=name,
                    defaults={
                        'default_price': price,
                        'category': inferred_category,
                        'stock_in_date': order_date,
                        'created_by': request.user,
                    }
                )
                is_matched = True

            # 对已存在但未分类的食材补齐自动分类（不覆盖人工已有分类）
            if dish and dish.category_id is None and inferred_category:
                dish.category = inferred_category
                dish.save(update_fields=['category'])

            subtotal = (price * qty) if price is not None else None

            order_item = OrderItem.objects.create(
                order=order,
                dish=dish,
                dish_name=name,
                quantity=qty,
                unit_price=price,
                subtotal=subtotal,
                is_matched=is_matched,
            )

            total_items += 1
            if subtotal:
                total_amount += subtotal

            apply_order_item_effects(order_item)

        # 更新订单汇总
        order.total_items = total_items
        order.total_amount = total_amount
        order.save()

    messages.success(request, f'订单已保存！共 {total_items} 道食材')
    return redirect('orders:detail', pk=order.pk)


def _save_uploaded_image(image_file):
    original_name = get_valid_filename(Path(image_file.name).name) or 'upload.png'
    path = default_storage.save(f'ocr_uploads/{uuid4().hex}_{original_name}', image_file)
    return path, default_storage.path(path)


def _build_result_context(parsed_items, image_path, raw_text, recognition_method):
    matched_items = []
    for item in parsed_items:
        dish_id, matched_name, score = match_dish(item.dish_name)
        matched_items.append({
            'dish_name': item.dish_name,
            'quantity': float(item.quantity),
            'unit_price': float(item.unit_price) if item.unit_price is not None else '',
            'subtotal': float(item.subtotal) if item.subtotal is not None else '',
            'dish_id': dish_id or '',
            'matched_name': matched_name or '',
            'match_score': round(score, 2),
            'is_matched': dish_id is not None,
            'raw_text': item.raw_text,
        })

    image_url = settings.MEDIA_URL + image_path

    return {
        'items': matched_items,
        'items_json': json.dumps(matched_items, ensure_ascii=False),
        'image_url': image_url,
        'image_path': image_path,
        'raw_text': raw_text,
        'order_date': date.today().isoformat(),
        'item_count': len(matched_items),
        'recognition_method': recognition_method,
    }


def _should_use_vision_fallback(ocr_lines, parsed_items):
    if not ocr_lines or not parsed_items:
        return True
    confidences = [float(line.get('confidence', 0)) for line in ocr_lines if line.get('confidence') is not None]
    if not confidences:
        return True
    return sum(confidences) / len(confidences) < 0.55


def _apply_vision_config_form(config, request, commit=False):
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


def _apply_llm_config_form(config: LLMProviderConfig, request, commit=False):
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
    config.priority = _positive_int_from_post(request.POST.get('priority'), default=10, minimum=1, maximum=9999)
    config.timeout_seconds = _positive_int_from_post(
        request.POST.get('timeout_seconds'), default=60, minimum=5, maximum=600
    )
    config.requests_per_minute = _positive_int_from_post(
        request.POST.get('requests_per_minute'), default=5, minimum=1, maximum=120
    )
    config.max_concurrency = _positive_int_from_post(
        request.POST.get('max_concurrency'), default=3, minimum=1, maximum=12
    )
    config.expert_concurrency = _positive_int_from_post(
        request.POST.get('expert_concurrency'), default=3, minimum=1, maximum=3
    )
    config.notes = request.POST.get('notes', '').strip()
    if commit:
        if not config.created_by_id:
            config.created_by = request.user
        config.updated_by = request.user
        config.save()
    return config


def _llm_config_messages(config: LLMProviderConfig) -> list[str]:
    messages = []
    if not config.enabled:
        messages.append('当前未启用。')
    if not config.has_api_key:
        messages.append('请填写 API Key。')
    if not config.base_url.strip():
        messages.append('请填写 Base URL。')
    elif not config.base_url.startswith(('http://', 'https://')):
        messages.append('Base URL 必须以 http:// 或 https:// 开头。')
    if not config.model.strip():
        messages.append('请填写模型名称。')
    if config.requests_per_minute < 1:
        messages.append('RPM 必须至少为 1。')
    if config.max_concurrency < 1:
        messages.append('最大并发必须至少为 1。')
    return messages


def _positive_int_from_post(value, default: int, minimum: int, maximum: int) -> int:
    number = _parse_decimal(value, Decimal(default)) or Decimal(default)
    return max(min(int(number), maximum), minimum)


def _coerce_int(value) -> int | None:
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
