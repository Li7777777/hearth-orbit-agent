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

from .configuration import (
    apply_llm_config_form,
    apply_vision_config_form,
    coerce_int,
    llm_config_messages,
)
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
    """旧视觉配置入口：保留 POST 兼容并跳转到设置中心。"""
    config = VisionProviderConfig.get_solo()
    settings_url = '/settings/?section=vision'
    if request.method != 'POST':
        return redirect(settings_url)

    action = request.POST.get('action', 'save')
    draft = apply_vision_config_form(config, request, commit=action == 'save')
    check_result = check_vision_config(draft)
    if action == 'save':
        if check_result.ok:
            messages.success(request, '视觉辅助配置已保存')
        else:
            messages.warning(request, '配置已保存，但仍有项目需要补全')
        return redirect(settings_url)

    from apps.settings_center.views import render_settings_center

    return render_settings_center(
        request,
        active_section='vision',
        vision_config=draft,
        vision_check_result=check_result,
    )


def llm_settings(request):
    """旧文本模型入口：保留 POST 兼容并跳转到设置中心。"""
    settings_url = '/settings/?section=llm'
    if request.method != 'POST':
        return redirect(settings_url)

    action = request.POST.get('action', 'save')
    if action == 'delete':
        config_id = coerce_int(request.POST.get('config_id'))
        deleted, _ = LLMProviderConfig.objects.filter(pk=config_id).delete()
        if deleted:
            messages.success(request, '大模型配置已删除')
        else:
            messages.warning(request, '未找到要删除的配置')
        return redirect(settings_url)

    config_id = coerce_int(request.POST.get('config_id'))
    if action == 'create':
        config = LLMProviderConfig(created_by=request.user)
    else:
        config = LLMProviderConfig.objects.filter(pk=config_id).first()
        if not config:
            messages.warning(request, '未找到要保存的配置')
            return redirect(settings_url)
    apply_llm_config_form(config, request, commit=True)
    messages.success(request, '大模型配置已保存')
    return redirect(settings_url)


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
    original_names = request.POST.getlist('original_name[]')

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
            inferred_category = infer_dish_category(name)

            # 名称未编辑时保留结果页的模糊匹配；编辑后只按最终名称精确匹配，
            # 避免隐藏 dish_id 指向旧食材，也避免相似新名称被错误合并。
            dish = None
            is_matched = False
            did = dish_ids[i] if i < len(dish_ids) and dish_ids[i] else None
            original_name = original_names[i].strip() if i < len(original_names) else ''
            if did and original_name and name == original_name:
                try:
                    dish = Dish.objects.get(id=int(did), is_active=True)
                except (Dish.DoesNotExist, ValueError):
                    dish = None
            if dish is None:
                dish = Dish.objects.filter(name=name, is_active=True).first()
            is_matched = dish is not None

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


def _parse_decimal(value, default):
    if value in (None, ''):
        return default
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return default
