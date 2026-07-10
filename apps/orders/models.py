from django.conf import settings
from django.db import models


class Order(models.Model):
    SOURCE_CHOICES = [
        ('ocr', 'OCR识别'),
        ('manual', '手动录入'),
    ]

    order_date = models.DateField('订单日期', db_index=True)
    source = models.CharField('数据来源', max_length=20, choices=SOURCE_CHOICES, default='ocr')
    ocr_image = models.ImageField('OCR截图', upload_to='ocr_uploads/', blank=True)
    ocr_raw_text = models.TextField('OCR原始文本', blank=True, default='')
    note = models.TextField('备注', blank=True, default='')
    total_items = models.IntegerField('总食材数', default=0)
    total_amount = models.DecimalField('总金额', max_digits=10, decimal_places=2, default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='创建者'
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '订单'
        verbose_name_plural = verbose_name
        ordering = ['-order_date', '-created_at']

    def __str__(self):
        return f"订单 {self.id} ({self.order_date})"


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE,
        related_name='items', verbose_name='订单'
    )
    dish = models.ForeignKey(
        'dishes.Dish', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='关联食材',
        related_name='order_items'
    )
    dish_name = models.CharField('食材名称(原始)', max_length=100)
    quantity = models.DecimalField('数量', max_digits=10, decimal_places=2, default=1)
    unit_price = models.DecimalField('单价', max_digits=10, decimal_places=2, null=True, blank=True)
    subtotal = models.DecimalField('小计', max_digits=10, decimal_places=2, null=True, blank=True)
    is_matched = models.BooleanField('已匹配', default=False)

    class Meta:
        verbose_name = '订单明细'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.dish_name} x{self.quantity}"


class DailyDishStatistic(models.Model):
    dish = models.ForeignKey(
        'dishes.Dish', on_delete=models.CASCADE,
        related_name='daily_stats', verbose_name='食材'
    )
    stat_date = models.DateField('统计日期', db_index=True)
    total_quantity = models.DecimalField('总量', max_digits=10, decimal_places=2, default=0)
    order_count = models.IntegerField('订单数', default=0)
    total_amount = models.DecimalField('总金额', max_digits=10, decimal_places=2, default=0)

    class Meta:
        verbose_name = '每日食材统计'
        verbose_name_plural = verbose_name
        unique_together = ['dish', 'stat_date']
        ordering = ['-stat_date']

    def __str__(self):
        return f"{self.dish.name} - {self.stat_date}"
