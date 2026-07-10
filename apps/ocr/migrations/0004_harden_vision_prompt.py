from django.db import migrations, models

OLD_DEFAULT_PROMPT = (
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

NEW_DEFAULT_PROMPT = (
    '你是订单截图结构化识别助手，专门处理买菜、外卖、超市、小票和团购订单截图。'
    '请只提取真实购买的商品/食材明细行，忽略订单号、地址、手机号、配送费、包装费、优惠、红包、'
    '满减、合计、实付、支付方式、配送状态、售后按钮、推荐商品和广告。'
    '如果商品名称包含规格、口味或重量，请保留有助于区分商品的简短信息，删除无意义营销词。'
    '数量优先读取截图中的数量；无法判断数量时填 1。单价和小计只填数字，不带货币符号；'
    '如果只能看到总价，可把 total/subtotal 填入 subtotal，unit_price 填 null。'
    '只返回一个 JSON 对象，不要 Markdown，不要解释，不要多余文字。'
    '不要输出 <think>、推理过程、分析步骤或任何 JSON 之外的前后缀。格式严格为：'
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
        ('ocr', '0003_update_vision_default_prompt'),
    ]

    operations = [
        migrations.AlterField(
            model_name='visionproviderconfig',
            name='prompt',
            field=models.TextField(blank=True, default=NEW_DEFAULT_PROMPT, verbose_name='识别提示词'),
        ),
        migrations.RunPython(update_default_prompt, restore_old_default_prompt),
    ]
