"""食材业务逻辑：模糊匹配、自动分类等"""
import re
from difflib import SequenceMatcher

from .models import Dish, DishCategory

CATEGORY_KEYWORDS = {
    '调料免费': [
        '葱', '小葱', '姜', '蒜', '香菜', '香葱', '生抽', '老抽', '蚝油', '料酒', '米醋',
        '陈醋', '白醋', '盐', '食盐', '白糖', '冰糖', '胡椒', '黑胡椒', '花椒', '八角',
        '桂皮', '香叶', '辣椒粉', '孜然', '香油', '芝麻', '鸡精', '味精', '调料', '佐料',
    ],
    '肉禽蛋类': [
        '五花肉', '里脊', '排骨', '牛腩', '鸡腿', '鸡翅', '鸡胸', '鸭腿', '培根', '火腿',
        '鸡蛋', '鸭蛋', '鹌鹑蛋', '牛肉', '羊肉', '猪肉', '鸡肉', '鸭肉', '鹅肉',
    ],
    '蔬果类': [
        '西兰花', '娃娃菜', '油麦菜', '空心菜', '胡萝卜', '西红柿', '番茄', '黄瓜', '茄子', '土豆',
        '洋葱', '白菜', '生菜', '菠菜', '香菜', '芹菜', '辣椒', '青椒', '南瓜', '冬瓜',
        '火龙果', '猕猴桃', '哈密瓜', '百香果', '蓝莓', '草莓', '葡萄', '橙子', '橘子', '柚子',
        '苹果', '香蕉', '芒果', '西瓜', '梨', '蜜桔',
    ],
    '水产海鲜': [
        '三文鱼', '金枪鱼', '鱿鱼', '章鱼', '扇贝', '生蚝', '蛤蜊', '带鱼', '虾仁', '海带',
        '紫菜', '海参', '鲍鱼', '龙虾', '螃蟹', '大虾', '鱼', '虾', '蟹', '贝', '海鲜',
    ],
    '豆菌类': [
        '小油豆腐', '豆腐', '豆干', '豆皮', '豆泡', '腐竹', '豆浆', '黄豆', '绿豆', '红豆',
        '豆豉', '豆制品',
        '白蘑菇', '金针菇', '杏鲍菇', '香菇', '平菇', '猴头菇', '木耳', '银耳', '蘑菇',
    ],
    '粮油调味': [
        '燕麦', '宽面', '挂面', '面条', '米线', '粉丝', '粉条', '馒头', '面包', '大米',
        '面粉', '糯米', '小米', '食用油', '花生油', '菜籽油', '干货',
        '豆瓣酱', '番茄酱', '辣椒酱', '生抽', '老抽', '蚝油', '料酒', '胡椒', '花椒', '八角',
        '桂皮', '鸡精', '味精', '香油', '芝麻', '白糖', '食盐', '调味品',
    ],
    '乳品饮料': [
        '纯牛奶', '酸奶', '奶酪', '黄油', '矿泉水', '纯净水', '天然水', '饮用水', '苏打水', '果汁',
        '可乐', '雪碧', '椰汁', '牛奶',
    ],
}


def match_dish(parsed_name: str, threshold: float = 0.6):
    """
    将 OCR 识别的食材名模糊匹配到数据库已有食材。
    返回: (dish_id, canonical_name, score) 或 (None, None, 0)
    """
    dishes = Dish.objects.filter(is_active=True).only('id', 'name')

    best_dish = None
    best_score = 0

    for dish in dishes:
        score = SequenceMatcher(None, parsed_name, dish.name).ratio()
        if score > best_score and score >= threshold:
            best_score = score
            best_dish = dish

    if best_dish:
        return best_dish.id, best_dish.name, best_score
    return None, None, 0


def _normalize_name(name: str) -> str:
    return re.sub(r'[\s·\-—_*#•,，/（）()【】\[\]]+', '', (name or '').lower())


def infer_dish_category(dish_name: str):
    """
    根据食材名推断分类，返回 DishCategory 或 None。
    若无法命中关键词，回落到“其他”分类（存在时）。
    """
    normalized = _normalize_name(dish_name)
    if not normalized:
        return None

    target_names = set(CATEGORY_KEYWORDS.keys()) | {'其他'}
    category_map = {
        c.name: c
        for c in DishCategory.objects.filter(name__in=target_names).only('id', 'name')
    }
    seasoning_category = category_map.get('调料免费')
    if seasoning_category:
        for keyword in CATEGORY_KEYWORDS['调料免费']:
            key = _normalize_name(keyword)
            if key and key in normalized:
                return seasoning_category

    best_category_name = None
    best_score = 0
    for category_name, keywords in CATEGORY_KEYWORDS.items():
        if category_name not in category_map:
            continue

        score = 0
        for keyword in keywords:
            key = _normalize_name(keyword)
            if key and key in normalized:
                score = max(score, len(key))

        if score > best_score:
            best_score = score
            best_category_name = category_name

    if best_category_name:
        return category_map.get(best_category_name)

    # “其他”分类缺失时自动补一条，保证自动分类有兜底
    other = category_map.get('其他')
    if other:
        return other
    other, _ = DishCategory.objects.get_or_create(
        name='其他',
        defaults={'icon': '📦', 'sort_order': 99},
    )
    return other
