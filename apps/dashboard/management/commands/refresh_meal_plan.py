from django.core.management.base import BaseCommand, CommandError

from apps.dashboard.models import MealPlanSnapshot
from apps.dashboard.recommendations import full_llm_enabled, refresh_full_llm_plan_sync


class Command(BaseCommand):
    help = '在独立进程中刷新全大模型三餐推荐快照，可由 cron 或 Windows 任务计划定时调用。'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='忽略快照有效期，立即重新生成。',
        )

    def handle(self, *args, **options):
        if not full_llm_enabled():
            raise CommandError('MEAL_AGENT_FULL_LLM_ENABLED 未启用。')

        snapshot = refresh_full_llm_plan_sync(force=options['force'])
        if snapshot.status == MealPlanSnapshot.STATUS_READY:
            self.stdout.write(self.style.SUCCESS(
                f'推荐快照已就绪：{snapshot.generated_at:%Y-%m-%d %H:%M:%S}'
            ))
            return
        if snapshot.status == MealPlanSnapshot.STATUS_REFRESHING:
            self.stdout.write(self.style.WARNING('已有推荐任务正在运行，本次未重复启动。'))
            return
        raise CommandError(snapshot.error_message or '推荐快照生成失败。')
