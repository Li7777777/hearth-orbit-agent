"""
订单文本解析器
支持外卖平台（美团/饿了么）及超市/电商（沃尔玛/京东/盒马等）订单截图格式
"""
import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ParsedOrderItem:
    dish_name: str
    quantity: float = 1.0
    unit_price: Optional[float] = None
    subtotal: Optional[float] = None
    raw_text: str = ''


# ── 正则模式 ──────────────────────────────────
QTY_PATTERNS = [
    re.compile(r'[xX×]\s*(\d+\.?\d*)'),              # x3, X3, ×3
    re.compile(r'(\d+\.?\d*)\s*[份碗盘杯瓶箱袋包罐盒](?!装)'), # 3份, 2袋 (不匹配"3个装")
    re.compile(r'(\d+\.?\d*)\s*个(?!装|入)'),          # 3个 (不匹配"3个装"/"3个入")
    re.compile(r'数量[：:]\s*(\d+\.?\d*)'),             # 数量：3
    re.compile(r'[*＊]\s*(\d+\.?\d*)'),                # *3
]

PRICE_PATTERNS = [
    # (regex, is_subtotal) — is_subtotal=True 表示匹配到的价格是总价而非单价
    (re.compile(r'(?:实付|小计)[：:]?\s*[¥￥]\s*(\d+\.?\d*)'), True),   # 实付 ¥8.69 / 小计：¥84
    (re.compile(r'[¥￥]\s*(\d+\.?\d*)'), False),                        # ¥28.00
    (re.compile(r'(\d+\.\d{2})\s*元'), False),                          # 28.00元
]

# ── 元数据行检测（价格/数量/标签，用于多行合并） ──
# 纯价格行: "¥7.50", "¥7.50 x1", "实付 ¥8.69"
_META_PRICE_RE = re.compile(
    r'^[\s]*(?:实付|原价|售价|划线价)?[\s]*[¥￥]\s*\d+\.?\d*'
    r'(?:\s*[xX×]\s*\d+\.?\d*)?[\s]*$'
)
# 纯数量行: "x1", "×2"
_META_QTY_RE = re.compile(r'^[\s]*[xX×]\s*\d+\.?\d*[\s]*$')
_META_SPEC_RE = re.compile(
    r'^[\s]*(?:\d+\.?\d*\s*(?:[mM]?[lL]|[kK]?[gG]|斤|两|克|千克|毫升|升|袋|盒|瓶|包)(?:装)?|\d+\s*个装)'
    r'(?:\s*[xX×]\s*\d+\.?\d*)?[\s]*$'
)
_HAS_NAME_CHAR_RE = re.compile(r'[\u4e00-\u9fffA-Za-z]')
_TIME_LINE_RE = re.compile(r'^\d{1,2}[:：]\d{2}$')
_UNIT_ONLY_RE = re.compile(
    r'^(?:[mM]?[lL]|[kK]?[gG]|斤|两|克|千克|毫升|升|个装|袋装|盒装|瓶装)$'
)
_INVALID_DISH_RE = re.compile(
    r'^(?:冷藏|冷冻|常温|保鲜|沃集鲜)$|商品金额|实际支付|再次购买|立即购买'
)

# 需要跳过的行
SKIP_PATTERNS = [
    re.compile(p) for p in [
        # ── 价格汇总 ──
        r'合计|总计|共计|总价|小计|预估|估计',
        r'实付款|实付金额|应付',
        r'优惠|折扣|满\d*减\d*|红包|券|立省|省\d',
        # ── 订单信息 ──
        r'订单编号|订单号|交易号|流水号',
        r'下单时间|送达时间|期望送达|预计送达',
        # ── 配送/包装 ──
        r'配送费|运费|打包费|餐具费|包装费|免运费',
        # ── 联系信息 ──
        r'备注|地址|电话|联系|收货',
        # ── 商家信息 ──
        r'商家|店铺|门店|感谢|好评|评价',
        r'发票|开票|税',
        # ── 订单状态 ──
        r'已送达|待配送|配送中|已完成|已取消|已签收',
        r'极速达|配达',
        # ── 支付方式 ──
        r'微信|支付宝|在线支付|货到付款',
        # ── 存储/品牌碎片 ──
        r'^(?:冷藏|冷冻|常温|保鲜)$',
        r'^沃集鲜(?:\s*[xX×]\s*\d+)?$',
        # ── 平台名/店名行（用精确匹配避免误杀商品名中的品牌前缀）──
        r'^沃尔玛|^京东|^淘宝|^天猫|^拼多多|^美团|^饿了么',
        r'^山姆会员|盒马鲜生|^叮咚买菜|^朴朴|^每日优鲜|^大润发',
        r'闪购|自营',
        # ── UI元素 ──
        r'购物车|去结算|结算|去支付|提交订单|加入购物车',
        r'换购|凑单|推荐|猜你喜欢|看了又看',
        r'全选|编辑|删除|移入收藏',
        r'收起|展开|共\d+件',
        r'价格明细|费用明细|账单明细',
        # ── 售后/标签 ──
        r'无理由|退货|退换|退款|售后',
        r'客服|在线客服|联系客服',
        r'支持7天|不支持7天|退换|退货',
        r'换为极速达|长期降价|为您节省|已省|猜您喜欢',
        r'入会咨询|点击跳转|绑定会员卡|办理会员|仅山姆会员',
        r'^NEW$|^新品$|^首页$|^分类$|^发现$|^我的$',
        r'^.{0,4}服务$',
        r'^\d{1,2}[:：]\d{2}$',
    ]
]

# 纯数字/价格行（不含食材名）
PURE_NUMBER_RE = re.compile(r'^[\d\s.,¥￥+\-=()（）]+$')
# 太短的文本（1个字符）
TOO_SHORT_RE = re.compile(r'^.{0,1}$')


def _should_skip(text: str) -> bool:
    """判断是否为非食材行"""
    if TOO_SHORT_RE.match(text):
        return True
    if PURE_NUMBER_RE.match(text):
        return True
    for pattern in SKIP_PATTERNS:
        if pattern.search(text):
            return True
    return False


def _is_metadata_line(text: str) -> bool:
    """判断是否为纯价格/数量行（不含食材名）"""
    text = text.strip()
    return bool(_META_PRICE_RE.match(text) or _META_QTY_RE.match(text) or _META_SPEC_RE.match(text))


def _has_price_token(text: str) -> bool:
    return any(pattern.search(text) for pattern, _ in PRICE_PATTERNS)


def _has_quantity_token(text: str) -> bool:
    return any(pattern.search(text) for pattern in QTY_PATTERNS)


def _has_position_data(ocr_lines: list) -> bool:
    return any('x_min' in line and 'x_max' in line for line in ocr_lines)


def _line_x_center(line_data: dict) -> float:
    if 'x_center' in line_data:
        return float(line_data.get('x_center') or 0)
    return (float(line_data.get('x_min') or 0) + float(line_data.get('x_max') or 0)) / 2


def _image_width(lines: list) -> float:
    return max((float(line.get('x_max') or 0) for line in lines), default=0) or 1


def _looks_like_price_anchor(line_data: dict, image_width: float) -> bool:
    text = line_data['text'].strip()
    if not _has_price_token(text):
        return False
    if '→' in text or '省' in text or '满' in text or '免' in text:
        return False
    return _line_x_center(line_data) >= image_width * 0.34


def _is_position_noise(text: str) -> bool:
    text = text.strip()
    if not text:
        return True
    if _should_skip(text):
        return True
    if _META_QTY_RE.match(text) or _has_price_token(text):
        return True
    if re.match(r'^\d+\.?\d*\s*元$', text):
        return True
    if re.match(r'^[A-Za-z]{1,4}$', text):
        return True
    if re.match(r'^[A-Z][A-Z\s\-/]{4,}$', text) and not re.search(r'[\u4e00-\u9fff]', text):
        return True
    return False


def _find_explicit_quantity(lines: list, anchor: dict, image_width: float) -> float | None:
    anchor_y = float(anchor.get('y_pos') or 0)
    for line in lines:
        text = line['text'].strip()
        if not _META_QTY_RE.match(text):
            continue
        if _line_x_center(line) < image_width * 0.74:
            continue
        y_pos = float(line.get('y_pos') or 0)
        if anchor_y - 25 <= y_pos <= anchor_y + 170:
            quantity, _ = _extract_quantity(text)
            return quantity
    return None


def _positioned_name_candidates(lines: list, anchor: dict, image_width: float) -> list:
    anchor_y = float(anchor.get('y_pos') or 0)
    anchor_x = _line_x_center(anchor)
    anchor_left = float(anchor.get('x_min') or 0)
    text_left_limit = image_width * 0.28
    if anchor_x >= image_width * 0.70:
        y_min = anchor_y - 90
        y_max = anchor_y + 145
    else:
        y_min = anchor_y - 320
        y_max = anchor_y + 55
    candidates = []

    for line in lines:
        if line is anchor:
            continue
        text = line['text'].strip()
        y_pos = float(line.get('y_pos') or 0)
        if y_pos < y_min or y_pos > y_max:
            continue
        if float(line.get('x_min') or 0) < text_left_limit:
            continue
        if anchor_x > image_width * 0.70 and float(line.get('x_min') or 0) >= anchor_left - 8:
            continue
        if _line_x_center(line) > image_width * 0.86:
            continue
        if float(line.get('confidence') or 0) < 0.45:
            continue
        if _is_position_noise(text):
            continue
        candidates.append(line)

    return sorted(candidates, key=lambda item: (float(item.get('y_pos') or 0), float(item.get('x_min') or 0)))


def _sort_by_visual_lines(candidates: list) -> list:
    groups = []
    for line in sorted(candidates, key=lambda item: (float(item.get('y_pos') or 0), float(item.get('x_min') or 0))):
        y_pos = float(line.get('y_pos') or 0)
        for group in groups:
            if abs(group['y'] - y_pos) <= 24:
                group['items'].append(line)
                group['y'] = (group['y'] + y_pos) / 2
                break
        else:
            groups.append({'y': y_pos, 'items': [line]})

    ordered = []
    for group in sorted(groups, key=lambda item: item['y']):
        ordered.extend(sorted(group['items'], key=lambda item: float(item.get('x_min') or 0)))
    return ordered


def _clean_positioned_name(candidates: list) -> str:
    parts = []
    seen = set()
    for line in _sort_by_visual_lines(candidates):
        text = line['text'].strip()
        normalized = re.sub(r'\s+', '', text).lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        parts.append(text)

    text = ' '.join(parts)
    text = re.sub(r'(?<=\d)\s+(?=\d+\s*(?:g|克|[mM]?[lL]|斤|袋|盒|包))', '', text)
    text = re.sub(r'([0-9])\s+([mM])\s+([lL])', r'\1\2\3', text)
    text = re.sub(r'([mM])\s+([lL]\*)', r'\1\2', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return _clean_dish_name(text)


def _parse_positioned_order_text(ocr_lines: list) -> list[ParsedOrderItem]:
    lines = [dict(line, text=str(line.get('text') or '').strip()) for line in ocr_lines]
    lines = [line for line in lines if line['text']]
    lines.sort(key=lambda item: (float(item.get('y_pos') or 0), float(item.get('x_min') or 0)))
    image_width = _image_width(lines)
    anchors = [line for line in lines if _looks_like_price_anchor(line, image_width)]

    items = []
    used_names = set()
    for anchor in anchors:
        price, _, _ = _extract_price(anchor['text'])
        if price is None:
            continue

        candidates = _positioned_name_candidates(lines, anchor, image_width)
        if not candidates:
            continue

        dish_name = _clean_positioned_name(candidates)
        if not _is_valid_dish_name(dish_name):
            continue

        dedupe_key = re.sub(r'\s+', '', dish_name).lower()
        if dedupe_key in used_names:
            continue
        used_names.add(dedupe_key)

        quantity = _find_explicit_quantity(lines, anchor, image_width) or 1.0
        subtotal = round(price * quantity, 2) if quantity > 1 else price
        raw_text = ' '.join([line['text'] for line in candidates] + [anchor['text']])
        items.append(ParsedOrderItem(
            dish_name=dish_name,
            quantity=quantity,
            unit_price=price,
            subtotal=subtotal,
            raw_text=raw_text,
        ))

    return items


def _is_near_previous(prev_line: dict, line_data: dict, max_gap: float = 90) -> bool:
    prev_y = float(prev_line.get('y_pos', 0))
    curr_y = float(line_data.get('y_pos', 0))
    return abs(curr_y - prev_y) <= max_gap


def _metadata_merge_gap(text: str) -> float:
    """不同元数据行采用不同合并距离阈值。"""
    if _META_QTY_RE.match(text):
        return 220
    if _META_SPEC_RE.match(text):
        return 140
    return 95


def _extract_quantity(text: str) -> tuple:
    """从文本中提取数量，返回 (quantity, cleaned_text)"""
    for pattern in QTY_PATTERNS:
        m = pattern.search(text)
        if m:
            qty = float(m.group(1))
            cleaned = pattern.sub('', text)
            return qty, cleaned
    return 1.0, text


def _extract_price(text: str) -> tuple:
    """从文本中提取价格，返回 (price, is_subtotal, cleaned_text)"""
    for pattern, is_sub in PRICE_PATTERNS:
        m = pattern.search(text)
        if m:
            price = float(m.group(1))
            cleaned = pattern.sub('', text)
            return price, is_sub, cleaned
    return None, False, text


def _clean_dish_name(text: str) -> str:
    """清理食材名：去除多余符号和空白"""
    # 去除残留的价格数字
    text = re.sub(r'[¥￥]\s*\d+\.?\d*', '', text)
    # 去除【标签】前缀（如"【水果菜】"、"【桶装水】"）
    text = re.sub(r'【[^】]*】', '', text)
    # 去除行首/尾的特殊符号
    text = re.sub(r'^[\s·\-—_*#•\d.、]+', '', text)
    text = re.sub(r'[\s·\-—_*#•]+$', '', text)
    # 去除尾部孤立数字(可能是残留的数量)
    text = re.sub(r'\s+\d+\.?\d*$', '', text)
    return text.strip()


def _has_unclosed_bracket(text: str) -> bool:
    """判断文本是否存在未闭合括号，用于跨行合并菜名。"""
    parentheses_open = text.count('(') + text.count('（')
    parentheses_close = text.count(')') + text.count('）')
    square_open = text.count('[') + text.count('【')
    square_close = text.count(']') + text.count('】')
    return parentheses_open > parentheses_close or square_open > square_close


def _should_append_to_previous(prev_line: dict, line_data: dict) -> bool:
    """判断当前行是否应并入上一行（常见于被截断的商品名）。"""
    prev_text = prev_line['text']
    text = line_data['text']

    if _has_unclosed_bracket(prev_text):
        return True
    return bool(re.match(r'^[)）】]', text))


def _should_merge_continuation(prev_line: dict, line_data: dict) -> bool:
    """判断是否为换行续写（同一商品被拆成两行）。"""
    if not _is_near_previous(prev_line, line_data, max_gap=72):
        return False

    prev_text = prev_line['text']
    text = line_data['text']

    if _should_append_to_previous(prev_line, line_data):
        return True

    # 典型场景：上一行已有价格，下一行是描述+数量（无价格） -> 同一商品续行
    if _has_price_token(prev_text) and _has_quantity_token(text) and not _has_price_token(text):
        return True

    # 典型场景：上一行已有价格，下一行是紧邻说明文案（后续常跟 x1/x2）
    if _has_price_token(prev_text) and not _has_price_token(text) and _is_near_previous(prev_line, line_data, max_gap=60):
        return True

    return False


def _is_valid_dish_name(name: str) -> bool:
    """最终食材名有效性校验，避免时间/服务文案误入。"""
    if len(name) < 2:
        return False
    if _TIME_LINE_RE.match(name):
        return False
    if not _HAS_NAME_CHAR_RE.search(name):
        return False
    if _UNIT_ONLY_RE.match(name):
        return False
    if _INVALID_DISH_RE.search(name):
        return False
    if not re.search(r'[\u4e00-\u9fff]', name) and len(name) <= 3:
        return False
    if re.search(r'配达|服务', name):
        return False
    return True


def _merge_lines(ocr_lines: list) -> list:
    """
    预处理：
    1. 先过滤掉明确的跳过行（平台名、UI元素等）
    2. 将纯价格/数量行合并到最近的食材行
    适用于多行格式（盒马/沃尔玛等，食材名和价格分多行显示）
    """
    merged = []

    for line_data in ocr_lines:
        text = line_data['text'].strip()
        if not text:
            continue

        # 纯价格/数量行 → 合并到最近的食材行
        if _is_metadata_line(text) and merged and _is_near_previous(merged[-1], line_data, _metadata_merge_gap(text)):
            prev = merged[-1]
            merged[-1] = {
                'text': prev['text'] + ' ' + text,
                'confidence': min(prev.get('confidence', 0), line_data.get('confidence', 0)),
                'y_pos': prev.get('y_pos', 0),
            }
            continue

        # 先跳过明确的非食材行
        if _should_skip(text):
            continue

        if merged and _should_merge_continuation(merged[-1], line_data):
            prev = merged[-1]
            merged[-1] = {
                'text': prev['text'] + ' ' + text,
                'confidence': min(prev.get('confidence', 0), line_data.get('confidence', 0)),
                'y_pos': prev.get('y_pos', 0),
            }
        else:
            merged.append(dict(line_data))

    return merged


def parse_order_text(ocr_lines: list) -> List[ParsedOrderItem]:
    """
    解析 OCR 识别结果为结构化订单数据。

    参数:
        ocr_lines: engine.recognize_image() 返回的行列表
                   每项 {'text': str, 'confidence': float, 'y_pos': float}
    返回:
        ParsedOrderItem 列表
    """
    if _has_position_data(ocr_lines):
        positioned_items = _parse_positioned_order_text(ocr_lines)
        if positioned_items:
            return positioned_items

    # 1. 预处理：过滤 + 多行合并
    lines = _merge_lines(ocr_lines)

    items = []
    for line_data in lines:
        text = line_data['text']

        # 提取数量
        quantity, text_after_qty = _extract_quantity(text)

        # 提取价格
        price, price_is_subtotal, text_after_price = _extract_price(text_after_qty)

        # 清理食材名
        dish_name = _clean_dish_name(text_after_price)

        if not _is_valid_dish_name(dish_name):
            continue

        # 计算小计与单价
        subtotal = None
        if price is not None:
            if price_is_subtotal:
                # "实付"/"小计" 标注的价格就是总价
                subtotal = price
                if quantity > 1:
                    price = round(price / quantity, 2)
            elif quantity > 1:
                per_unit = price / quantity
                if per_unit == int(per_unit) or (per_unit * 2) == int(per_unit * 2):
                    subtotal = price
                    price = per_unit
                else:
                    subtotal = round(price * quantity, 2)
            else:
                subtotal = price

        items.append(ParsedOrderItem(
            dish_name=dish_name,
            quantity=quantity,
            unit_price=price,
            subtotal=subtotal,
            raw_text=line_data['text'],
        ))

    return items
