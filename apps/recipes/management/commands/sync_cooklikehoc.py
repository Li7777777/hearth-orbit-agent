from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.recipes.external_source import pull_and_sync, sync_from_repo


class Command(BaseCommand):
    help = '同步 CookLikeHOC 开源仓库到菜谱模块（外部数据源）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--refresh',
            action='store_true',
            help='强制重新拉取远程仓库压缩包（默认仅在本地缺失时拉取）',
        )
        parser.add_argument(
            '--prune',
            action='store_true',
            help='将外部源中已删除的菜谱在本地标记为未发布',
        )
        parser.add_argument(
            '--repo-path',
            type=str,
            default='',
            help='指定本地仓库目录（指定后跳过远程拉取）',
        )

    def handle(self, *args, **options):
        repo_path = options.get('repo_path', '').strip()
        refresh = bool(options.get('refresh'))
        prune = bool(options.get('prune'))

        try:
            if repo_path:
                result = sync_from_repo(Path(repo_path), prune=prune)
                result['repo_path'] = repo_path
            else:
                result = pull_and_sync(refresh=refresh, prune=prune)
        except Exception as exc:
            raise CommandError(f'同步失败: {exc}') from exc

        self.stdout.write(self.style.SUCCESS('CookLikeHOC 同步完成'))
        self.stdout.write(f"仓库路径: {result['repo_path']}")
        self.stdout.write(f"扫描文件: {result['files_total']}")
        self.stdout.write(f"导入菜谱: {result['imported']}")
        self.stdout.write(f"新建: {result['created']} | 更新: {result['updated']}")
        self.stdout.write(f"新建分类: {result['categories_created']}")
        if prune:
            self.stdout.write(f"下线（未发布）: {result['pruned']}")
