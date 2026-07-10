from django.db import migrations

OLD_DEFAULT_PROMPT = (
    '你是订单截图结构化助手。请从图片中提取真实商品/食材行，忽略订单号、地址、配送费、优惠、'
    '合计、支付方式、按钮文案和售后文案。只返回 JSON，不要输出解释。格式：'
    '{"items":[{"dish_name":"食材名称","quantity":1,"unit_price":12.3,"subtotal":12.3}],'
    '"raw_text":"可选，概括识别到的关键文本"}。数量无法判断时填 1，价格无法判断时填 null。'
)

NEW_DEFAULT_PROMPT = (
    '你是订单截图结构化识别助手，专门处理买菜、外卖、超市、小票和团购订单截图。'
    '请只提取真实购买的商品/食材明细行，忽略订单号、地址、手机号、配送费、包装费、优惠、红包、'
    '满减、合计、实付、支付方式、配送状态、售后按钮、推荐商品和广告。'
    '如果商品名称包含规格、口味或重量，请保留有助于区分商品的简短信息，删除无意义营销词。'
    '数量优先读取截图中的数量；无法判断数量时填 1。单价和小计只填数字，不带货币符号；'
    '如果只能看到总价，可把 total/subtotal 填入 subtotal，unit_price 填 null。'
    '只返回一个 JSON 对象，不要 Markdown，不要解释，不要多余文字。格式严格为：'
    '{"items":[{"dish_name":"商品或食材名称","quantity":1,"unit_price":12.3,"subtotal":12.3}],'
    '"raw_text":"简要记录你在截图中看到的关键商品文本"}。'
    '没有识别到商品时返回 {"items":[],"raw_text":"未识别到商品明细"}。'
)


def update_default_prompt(apps, schema_editor):
    VisionProviderConfig = apps.get_model('ocr', 'VisionProviderConfig')
    VisionProviderConfig.objects.filter(prompt='').update(prompt=NEW_DEFAULT_PROMPT)
    VisionProviderConfig.objects.filter(prompt=OLD_DEFAULT_PROMPT).update(prompt=NEW_DEFAULT_PROMPT)


def restore_old_default_prompt(apps, schema_editor):
    VisionProviderConfig = apps.get_model('ocr', 'VisionProviderConfig')
    VisionProviderConfig.objects.filter(prompt=NEW_DEFAULT_PROMPT).update(prompt=OLD_DEFAULT_PROMPT)


class Migration(migrations.Migration):

    dependencies = [
        ('ocr', '0002_visionproviderconfig_requests_per_minute_and_more'),
    ]

    operations = [
        migrations.RunPython(update_default_prompt, restore_old_default_prompt),
    ]
