
from django.db import models


class MealPlanSnapshot(models.Model):
    KEY_FULL_LLM = 'full_llm'

    STATUS_IDLE = 'idle'
    STATUS_REFRESHING = 'refreshing'
    STATUS_READY = 'ready'
    STATUS_ERROR = 'error'
    STATUS_CHOICES = [
        (STATUS_IDLE, '等待生成'),
        (STATUS_REFRESHING, '后台生成中'),
        (STATUS_READY, '可用'),
        (STATUS_ERROR, '生成失败'),
    ]

    key = models.CharField('快照标识', max_length=40, unique=True, default=KEY_FULL_LLM)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default=STATUS_IDLE, db_index=True)
    rendered_html = models.TextField('渲染结果', blank=True, default='')
    generated_for = models.DateField('推荐日期', null=True, blank=True, db_index=True)
    generated_at = models.DateTimeField('生成时间', null=True, blank=True)
    refresh_started_at = models.DateTimeField('刷新开始时间', null=True, blank=True)
    refresh_owner_pid = models.PositiveIntegerField('刷新进程 PID', null=True, blank=True)
    refresh_owner_host = models.CharField('刷新主机', max_length=255, blank=True, default='')
    last_attempt_at = models.DateTimeField('最近尝试时间', null=True, blank=True)
    error_message = models.TextField('错误信息', blank=True, default='')
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '三餐推荐快照'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f'{self.key}: {self.get_status_display()}'
