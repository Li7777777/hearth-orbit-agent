import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ocr', '0004_harden_vision_prompt'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='LLMProviderConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('enabled', models.BooleanField(db_index=True, default=True, verbose_name='启用')),
                ('name', models.CharField(max_length=80, verbose_name='配置名称')),
                ('provider_name', models.CharField(blank=True, default='', max_length=80, verbose_name='服务商名称')),
                ('api_key', models.CharField(blank=True, default='', max_length=512, verbose_name='API Key')),
                ('base_url', models.URLField(blank=True, default='', max_length=300, verbose_name='Base URL')),
                ('model', models.CharField(blank=True, default='', max_length=120, verbose_name='模型')),
                ('priority', models.PositiveIntegerField(db_index=True, default=10, verbose_name='优先级')),
                ('timeout_seconds', models.PositiveIntegerField(default=60, verbose_name='请求超时(秒)')),
                ('requests_per_minute', models.PositiveIntegerField(default=5, verbose_name='RPM限制')),
                ('max_concurrency', models.PositiveIntegerField(default=3, verbose_name='最大并发')),
                ('expert_concurrency', models.PositiveIntegerField(default=3, verbose_name='专家并发')),
                ('notes', models.TextField(blank=True, default='', verbose_name='备注')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                (
                    'created_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='created_llm_provider_configs',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='创建者',
                    ),
                ),
                (
                    'updated_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='updated_llm_provider_configs',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='更新者',
                    ),
                ),
            ],
            options={
                'verbose_name': '大模型配置',
                'verbose_name_plural': '大模型配置',
                'ordering': ['priority', 'id'],
            },
        ),
    ]
