from django.conf import settings
from django.db import models


class VisionProviderConfig(models.Model):
    PROVIDER_OPENAI = 'openai'
    PROVIDER_ANTHROPIC = 'anthropic'
    PROVIDER_OPENAI_COMPATIBLE = 'openai_compatible'
    DEFAULT_MODELS = {
        PROVIDER_OPENAI: 'gpt-4o-mini',
        PROVIDER_ANTHROPIC: 'claude-sonnet-4-20250514',
        PROVIDER_OPENAI_COMPATIBLE: 'gpt-4o-mini',
    }

    PROVIDER_CHOICES = [
        (PROVIDER_OPENAI, 'OpenAI'),
        (PROVIDER_ANTHROPIC, 'Anthropic（A社 / Claude）'),
        (PROVIDER_OPENAI_COMPATIBLE, '第三方 OpenAI-compatible'),
    ]

    DEFAULT_PROMPT = (
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

    enabled = models.BooleanField('启用视觉辅助', default=False)
    provider = models.CharField('服务商', max_length=32, choices=PROVIDER_CHOICES, default=PROVIDER_OPENAI)
    provider_name = models.CharField('第三方名称', max_length=80, blank=True, default='')
    api_key = models.CharField('API Key', max_length=512, blank=True, default='')
    base_url = models.URLField('Base URL', max_length=300, blank=True, default='')
    model = models.CharField('模型', max_length=120, blank=True, default='gpt-4o-mini')
    prompt = models.TextField('识别提示词', blank=True, default=DEFAULT_PROMPT)
    timeout_seconds = models.PositiveIntegerField('请求超时(秒)', default=60)
    requests_per_minute = models.PositiveIntegerField('RPM限制', default=5)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='创建者',
        related_name='created_vision_configs',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='更新者',
        related_name='updated_vision_configs',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '视觉辅助配置'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f'{self.get_provider_display()} - {self.model or "未配置模型"}'

    @classmethod
    def get_solo(cls):
        defaults = cls.env_preset_defaults()
        obj, created = cls.objects.get_or_create(pk=1, defaults=defaults)
        if not created and defaults and obj.can_apply_env_preset:
            changed_fields = []
            for field, value in defaults.items():
                if getattr(obj, field) != value:
                    setattr(obj, field, value)
                    changed_fields.append(field)
            if changed_fields:
                obj.save(update_fields=changed_fields + ['updated_at'])
        return obj

    @classmethod
    def env_preset_defaults(cls):
        data = getattr(settings, 'VISION_PROVIDER_PRESET', {})
        if not isinstance(data, dict):
            return {}

        allowed_providers = {choice[0] for choice in cls.PROVIDER_CHOICES}
        provider = str(data.get('provider') or cls.PROVIDER_OPENAI).strip()
        if provider not in allowed_providers:
            provider = cls.PROVIDER_OPENAI

        defaults = {
            'enabled': _coerce_bool(data.get('enabled'), False),
            'provider': provider,
            'provider_name': str(data.get('provider_name') or '').strip(),
            'api_key': str(data.get('api_key') or '').strip(),
            'base_url': str(data.get('base_url') or '').strip(),
            'model': str(
                data.get('model') or cls.DEFAULT_MODELS.get(provider, cls.DEFAULT_MODELS[cls.PROVIDER_OPENAI])
            ).strip(),
            'prompt': str(data.get('prompt') or cls.DEFAULT_PROMPT).strip(),
            'timeout_seconds': _coerce_timeout(data.get('timeout_seconds'), 60),
            'requests_per_minute': _coerce_positive_int(data.get('requests_per_minute'), 5),
        }
        if provider != cls.PROVIDER_OPENAI_COMPATIBLE:
            defaults['provider_name'] = ''
            defaults['base_url'] = ''
        return defaults

    @property
    def has_api_key(self):
        return bool(self.api_key.strip())

    @property
    def masked_api_key(self):
        value = self.api_key.strip()
        if not value:
            return ''
        if len(value) <= 8:
            return '*' * len(value)
        return f'{value[:4]}...{value[-4:]}'

    @property
    def effective_base_url(self):
        if self.base_url:
            return self.base_url.rstrip('/')
        if self.provider == self.PROVIDER_OPENAI:
            return 'https://api.openai.com/v1'
        if self.provider == self.PROVIDER_ANTHROPIC:
            return 'https://api.anthropic.com/v1'
        return ''

    @property
    def provider_default_model(self):
        return self.DEFAULT_MODELS.get(self.provider, self.DEFAULT_MODELS[self.PROVIDER_OPENAI])

    @property
    def is_unmodified_default(self):
        return (
            self.created_by_id is None
            and self.updated_by_id is None
            and self.enabled is False
            and self.provider == self.PROVIDER_OPENAI
            and self.provider_name == ''
            and self.api_key == ''
            and self.base_url == ''
            and self.model == self.DEFAULT_MODELS[self.PROVIDER_OPENAI]
            and self.prompt == self.DEFAULT_PROMPT
            and self.timeout_seconds == 60
            and self.requests_per_minute == 5
        )

    @property
    def can_apply_env_preset(self):
        return (
            self.created_by_id is None
            and self.updated_by_id is None
            and (self.is_unmodified_default or not self.has_api_key)
        )


class LLMProviderConfig(models.Model):
    enabled = models.BooleanField('启用', default=True, db_index=True)
    name = models.CharField('配置名称', max_length=80)
    provider_name = models.CharField('服务商名称', max_length=80, blank=True, default='')
    api_key = models.CharField('API Key', max_length=512, blank=True, default='')
    base_url = models.URLField('Base URL', max_length=300, blank=True, default='')
    model = models.CharField('模型', max_length=120, blank=True, default='')
    priority = models.PositiveIntegerField('优先级', default=10, db_index=True)
    timeout_seconds = models.PositiveIntegerField('请求超时(秒)', default=60)
    requests_per_minute = models.PositiveIntegerField('RPM限制', default=5)
    max_concurrency = models.PositiveIntegerField('最大并发', default=3)
    expert_concurrency = models.PositiveIntegerField('专家并发', default=3)
    notes = models.TextField('备注', blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='创建者',
        related_name='created_llm_provider_configs',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='更新者',
        related_name='updated_llm_provider_configs',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        ordering = ['priority', 'id']
        verbose_name = '大模型配置'
        verbose_name_plural = '大模型配置'

    def __str__(self):
        return f'{self.display_label} - {self.model or "未配置模型"}'

    @property
    def display_label(self):
        return self.name or self.provider_name or '大模型'

    @property
    def has_api_key(self):
        return bool(self.api_key.strip())

    @property
    def masked_api_key(self):
        value = self.api_key.strip()
        if not value:
            return ''
        if len(value) <= 8:
            return '*' * len(value)
        return f'{value[:4]}...{value[-4:]}'

    @property
    def is_complete(self):
        return bool(self.has_api_key and self.base_url.strip() and self.model.strip())

    @classmethod
    def active_complete(cls):
        return [
            config
            for config in cls.objects.filter(enabled=True).order_by('priority', 'id')
            if config.is_complete
        ]


def _coerce_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _coerce_timeout(value, default=60):
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return default


def _coerce_positive_int(value, default=5):
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return default
