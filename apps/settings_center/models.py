from django.conf import settings
from django.db import models


class RuntimeSettings(models.Model):
    full_llm_enabled_override = models.BooleanField('启用全大模型推荐', null=True, blank=True)
    background_refresh_enabled_override = models.BooleanField('启用后台刷新', null=True, blank=True)
    refresh_minutes_override = models.PositiveIntegerField('刷新周期（分钟）', null=True, blank=True)
    error_retry_minutes_override = models.PositiveIntegerField('失败重试间隔（分钟）', null=True, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='updated_runtime_settings',
        verbose_name='更新者',
    )
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '运行时设置'
        verbose_name_plural = verbose_name

    def __str__(self):
        return '推荐运行时设置'

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def reset_overrides(self, user=None):
        self.full_llm_enabled_override = None
        self.background_refresh_enabled_override = None
        self.refresh_minutes_override = None
        self.error_retry_minutes_override = None
        self.updated_by = user
        self.save()
