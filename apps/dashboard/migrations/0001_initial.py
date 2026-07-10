from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='MealPlanSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(default='full_llm', max_length=40, unique=True, verbose_name='快照标识')),
                ('status', models.CharField(
                    choices=[
                        ('idle', '等待生成'),
                        ('refreshing', '后台生成中'),
                        ('ready', '可用'),
                        ('error', '生成失败'),
                    ],
                    db_index=True,
                    default='idle',
                    max_length=20,
                    verbose_name='状态',
                )),
                ('rendered_html', models.TextField(blank=True, default='', verbose_name='渲染结果')),
                ('generated_for', models.DateField(blank=True, db_index=True, null=True, verbose_name='推荐日期')),
                ('generated_at', models.DateTimeField(blank=True, null=True, verbose_name='生成时间')),
                ('refresh_started_at', models.DateTimeField(blank=True, null=True, verbose_name='刷新开始时间')),
                ('last_attempt_at', models.DateTimeField(blank=True, null=True, verbose_name='最近尝试时间')),
                ('error_message', models.TextField(blank=True, default='', verbose_name='错误信息')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': '三餐推荐快照',
                'verbose_name_plural': '三餐推荐快照',
            },
        ),
    ]
