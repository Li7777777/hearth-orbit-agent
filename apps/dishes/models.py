from django.conf import settings
from django.db import models
from django.utils import timezone


class DishCategory(models.Model):
    name = models.CharField('分类名称', max_length=50, unique=True)
    sort_order = models.IntegerField('排序', default=0)
    icon = models.CharField('图标', max_length=10, blank=True, default='')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '食材分类'
        verbose_name_plural = verbose_name
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.name


class Dish(models.Model):
    STORAGE_CHOICES = [
        ('常温', '常温'),
        ('冷藏', '冷藏'),
        ('冷冻', '冷冻'),
    ]
    DEACTIVATION_REASON_CHOICES = [
        ('eaten', '吃完了'),
        ('discarded', '丢掉了'),
    ]

    name = models.CharField('食材名称', max_length=100, unique=True, db_index=True)
    category = models.ForeignKey(
        DishCategory, on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='分类',
        related_name='dishes'
    )
    unit = models.CharField('计量单位', max_length=20, default='斤')
    specification = models.CharField('规格', max_length=100, blank=True, default='',
                                     help_text='如: 500g/袋、1kg/盒')
    default_price = models.DecimalField(
        '参考单价', max_digits=10, decimal_places=2, null=True, blank=True
    )
    storage = models.CharField('储存方式', max_length=10, choices=STORAGE_CHOICES,
                               default='常温')
    stock_in_date = models.DateField('入库日期', default=timezone.localdate, db_index=True)
    image = models.ImageField('食材图片', upload_to='dish_images/', blank=True)
    description = models.TextField('描述', blank=True, default='')
    is_active = models.BooleanField('启用', default=True, db_index=True)
    deactivation_reason = models.CharField(
        '停用原因',
        max_length=20,
        choices=DEACTIVATION_REASON_CHOICES,
        blank=True,
        default='',
        db_index=True,
    )
    deactivated_at = models.DateField('停用日期', null=True, blank=True, db_index=True)
    total_ordered = models.DecimalField('累计用量', max_digits=12, decimal_places=2, default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='创建者'
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '食材'
        verbose_name_plural = verbose_name
        ordering = ['-updated_at']

    def __str__(self):
        return self.name

    @property
    def days_in_stock(self):
        """已买入天数（从入库日期到今天）"""
        if not self.stock_in_date:
            return 0
        return max((timezone.localdate() - self.stock_in_date).days, 0)
