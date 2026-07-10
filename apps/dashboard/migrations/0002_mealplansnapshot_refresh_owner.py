from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('dashboard', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='mealplansnapshot',
            name='refresh_owner_host',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='刷新主机'),
        ),
        migrations.AddField(
            model_name='mealplansnapshot',
            name='refresh_owner_pid',
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name='刷新进程 PID'),
        ),
    ]
