"""CookLikeHOC 外部菜谱源：拉取 + 解析 + 同步到本地 Recipe 模型。"""

from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, unquote, urlparse
from urllib.request import urlopen

from django.conf import settings
from django.db import transaction

from .models import Recipe, RecipeCategory, RecipeIngredient, RecipeStep

SOURCE_NAME = 'cooklikehoc'
DEFAULT_REPO_URL = 'https://github.com/Gar-b-age/CookLikeHOC'
DEFAULT_ZIP_URL = 'https://codeload.github.com/Gar-b-age/CookLikeHOC/zip/refs/heads/main'
DEFAULT_REPO_PATH = Path('external') / 'CookLikeHOC-main'
TOP_LEVEL_SKIP_DIRS = {'.git', '.github', '.vitepress', 'docs', 'docker_support', 'images'}
SIMPLIFIED_CATEGORIES = [
    {'name': '家常热菜', 'icon': '🍳', 'sort_order': 1},
    {'name': '主食早餐', 'icon': '🍚', 'sort_order': 2},
    {'name': '汤粥锅煲', 'icon': '🍲', 'sort_order': 3},
    {'name': '凉菜卤味', 'icon': '🥗', 'sort_order': 4},
    {'name': '炸烤小吃', 'icon': '🍤', 'sort_order': 5},
    {'name': '饮品配料', 'icon': '🥤', 'sort_order': 6},
    {'name': '其他', 'icon': '📦', 'sort_order': 99},
]

LEGACY_TO_SIMPLIFIED_CATEGORY = {
    '炒菜': '家常热菜',
    '炖菜': '家常热菜',
    '蒸菜': '家常热菜',
    '烫菜': '家常热菜',
    '家常菜': '家常热菜',
    '川菜': '家常热菜',
    '粤菜': '家常热菜',
    '西餐': '家常热菜',
    '主食': '主食早餐',
    '早餐': '主食早餐',
    '汤': '汤粥锅煲',
    '煮锅': '汤粥锅煲',
    '砂锅菜': '汤粥锅煲',
    '凉拌': '凉菜卤味',
    '凉菜': '凉菜卤味',
    '卤菜': '凉菜卤味',
    '炸品': '炸烤小吃',
    '烤类': '炸烤小吃',
    '烘焙': '炸烤小吃',
    '饮品': '饮品配料',
    '配料': '饮品配料',
    '其他': '其他',
}

_HEADING_RE = re.compile(r'^\s*##+\s*(.+?)\s*$')
_TITLE_RE = re.compile(r'^\s*#\s+(.+?)\s*$')
_BULLET_RE = re.compile(r'^\s*[-*+]\s+(.+?)\s*$')
_NUMBERED_RE = re.compile(r'^\s*\d+[\.\)．、]\s*(.+?)\s*$')
_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\((.+)\)')
_LINK_RE = re.compile(r'(?<!!)\[([^\]]+)\]\(([^)]+)\)')


@dataclass
class ParsedRecipe:
    source_id: str
    source_url: str
    category_name: str
    name: str
    description: str
    ingredients: list[str]
    steps: list[str]
    image_url: str = ''
    image_title: str = ''
    external_links: list[dict[str, str]] | None = None


def _setting_path(name: str, fallback: Path) -> Path:
    value = getattr(settings, name, None)
    if value is None:
        return Path(settings.BASE_DIR) / fallback
    return Path(value)


def _setting_str(name: str, fallback: str) -> str:
    return str(getattr(settings, name, fallback))


def get_repo_path() -> Path:
    return _setting_path('COOKLIKEHOC_REPO_PATH', DEFAULT_REPO_PATH)


def simplify_recipe_category(raw_name: str) -> str:
    normalized = (raw_name or '').strip()
    if not normalized:
        return '其他'
    return LEGACY_TO_SIMPLIFIED_CATEGORY.get(normalized, '其他')


def _ensure_simplified_categories() -> tuple[dict[str, RecipeCategory], int]:
    category_map: dict[str, RecipeCategory] = {}
    created_count = 0
    for cat in SIMPLIFIED_CATEGORIES:
        obj, created = RecipeCategory.objects.get_or_create(
            name=cat['name'],
            defaults={'icon': cat['icon'], 'sort_order': cat['sort_order']},
        )
        if created:
            created_count += 1
        updated = False
        if obj.icon != cat['icon']:
            obj.icon = cat['icon']
            updated = True
        if obj.sort_order != cat['sort_order']:
            obj.sort_order = cat['sort_order']
            updated = True
        if updated:
            obj.save(update_fields=['icon', 'sort_order'])
        category_map[obj.name] = obj
    return category_map, created_count


def pull_repo(refresh: bool = False, timeout: int = 60) -> Path:
    """
    拉取（下载 zip）CookLikeHOC 仓库到本地目录。
    """
    repo_path = get_repo_path()
    if repo_path.exists() and not refresh:
        return repo_path

    zip_url = _setting_str('COOKLIKEHOC_ZIP_URL', DEFAULT_ZIP_URL)
    parent = repo_path.parent
    parent.mkdir(parents=True, exist_ok=True)

    with urlopen(zip_url, timeout=timeout) as response:
        payload = response.read()

    with zipfile.ZipFile(BytesIO(payload)) as archive:
        names = archive.namelist()
        if not names:
            raise RuntimeError('CookLikeHOC 压缩包为空')
        root_name = names[0].split('/')[0]
        extracted_root = parent / root_name
        if repo_path.exists():
            shutil.rmtree(repo_path, ignore_errors=True)
        if extracted_root.exists():
            shutil.rmtree(extracted_root, ignore_errors=True)
        archive.extractall(parent)

    if extracted_root != repo_path:
        extracted_root.rename(repo_path)

    return repo_path


def _iter_recipe_markdown_files(repo_root: Path) -> Iterable[Path]:
    top_dirs = [
        p for p in repo_root.iterdir()
        if p.is_dir() and p.name not in TOP_LEVEL_SKIP_DIRS and not p.name.startswith('.')
    ]
    for category_dir in sorted(top_dirs, key=lambda p: p.name):
        for md in sorted(category_dir.glob('*.md'), key=lambda p: p.name):
            if md.name.lower() == 'readme.md':
                continue
            yield md


def _section_type(title: str) -> str | None:
    normalized = title.strip().replace('：', '').replace(':', '')
    if any(token in normalized for token in ('配料', '食材', '原料', '材料')):
        return 'ingredients'
    if any(token in normalized for token in ('步骤', '做法', '制作')):
        return 'steps'
    return None


def _clean_inline_markdown(text: str) -> str:
    value = text.strip()
    value = value.replace('`', '').replace('**', '').replace('__', '').replace('*', '')
    value = re.sub(r'\[(.*?)\]\([^)]+\)', r'\1', value)
    value = re.sub(r'\s+', ' ', value).strip()
    return value


def _extract_markdown_list(lines: list[str], strip_leading_number: bool = False) -> list[str]:
    items: list[str] = []
    for raw in lines:
        line = raw.rstrip()
        bullet = _BULLET_RE.match(line)
        numbered = _NUMBERED_RE.match(line)
        if bullet:
            content = _clean_inline_markdown(bullet.group(1))
            if strip_leading_number:
                content = re.sub(r'^\d+[\.\)．、]?\s*', '', content).strip()
            if content:
                items.append(content)
            continue
        if numbered:
            content = _clean_inline_markdown(numbered.group(1))
            if content:
                items.append(content)
            continue
        text = _clean_inline_markdown(line)
        if text and items:
            items[-1] = f"{items[-1]} {text}".strip()
    return [item for item in items if item]


def _build_source_url(relative_path: str) -> str:
    base = _setting_str('COOKLIKEHOC_REPO_URL', DEFAULT_REPO_URL).rstrip('/')
    encoded = '/'.join(quote(part) for part in Path(relative_path).parts)
    return f'{base}/blob/main/{encoded}'


def _build_raw_url(relative_path: str) -> str:
    repo_url = _setting_str('COOKLIKEHOC_REPO_URL', DEFAULT_REPO_URL).rstrip('/')
    encoded = '/'.join(quote(part) for part in Path(relative_path).parts)
    match = re.match(r'^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$', repo_url)
    if match:
        owner, repo = match.groups()
        return f'https://raw.githubusercontent.com/{owner}/{repo}/main/{encoded}'
    return f'{repo_url}/raw/main/{encoded}'


def _markdown_url_target(value: str) -> str:
    target = (value or '').strip().strip('<>')
    if not target:
        return ''
    if ' ' in target:
        target = target.split()[0].strip('<>')
    return target


def _resolve_repo_relative_path(repo_root: Path, file_path: Path, target: str) -> str:
    target = _markdown_url_target(target)
    if not target:
        return ''

    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return ''

    raw_path = unquote(parsed.path or '')
    if not raw_path:
        return ''

    if raw_path.startswith('/'):
        relative_posix = raw_path.lstrip('/')
    else:
        parent = file_path.parent.relative_to(repo_root).as_posix()
        relative_posix = f'{parent}/{raw_path}' if parent else raw_path

    normalized = Path(relative_posix).as_posix()
    parts = []
    for part in normalized.split('/'):
        if not part or part == '.':
            continue
        if part == '..':
            if not parts:
                return ''
            parts.pop()
            continue
        parts.append(part)

    if not parts:
        return ''

    relative_path = Path(*parts)
    try:
        candidate = (repo_root / relative_path).resolve()
        candidate.relative_to(repo_root.resolve())
    except (OSError, ValueError):
        return ''

    return relative_path.as_posix()


def _resolve_markdown_image_url(repo_root: Path, file_path: Path, target: str) -> str:
    target = _markdown_url_target(target)
    parsed = urlparse(target)
    if parsed.scheme in {'http', 'https'} and parsed.netloc:
        return target

    relative_path = _resolve_repo_relative_path(repo_root, file_path, target)
    if not relative_path:
        return ''
    return _build_raw_url(relative_path)


def _extract_recipe_links(repo_root: Path, file_path: Path, line: str) -> list[dict[str, str]]:
    links = []
    current_source_id = file_path.relative_to(repo_root).as_posix()
    for match in _LINK_RE.finditer(line):
        text = _clean_inline_markdown(match.group(1))
        target_source_id = _resolve_repo_relative_path(repo_root, file_path, match.group(2))
        if not text or not target_source_id or target_source_id == current_source_id:
            continue
        if not target_source_id.lower().endswith('.md'):
            continue
        links.append({
            'text': text,
            'source_id': target_source_id,
            'source_url': _build_source_url(target_source_id),
        })
    return links


def _dedupe_links(links: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    unique_links = []
    for link in links:
        key = (link.get('source_id', ''), link.get('text', ''))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        unique_links.append(link)
    return unique_links


def parse_recipe_markdown(repo_root: Path, file_path: Path) -> ParsedRecipe:
    text = file_path.read_text(encoding='utf-8', errors='ignore')
    lines = text.splitlines()

    name = file_path.stem
    image_url = ''
    image_title = ''
    external_links: list[dict[str, str]] = []
    description_lines: list[str] = []
    ingredient_lines: list[str] = []
    step_lines: list[str] = []
    current_section = None

    for line in lines:
        stripped = line.strip()
        title_match = _TITLE_RE.match(line)
        if title_match:
            candidate = title_match.group(1).strip()
            if candidate:
                name = candidate
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            current_section = _section_type(heading_match.group(1))
            continue

        image_match = _IMAGE_RE.search(line)
        if image_match:
            if not image_url:
                image_title = _clean_inline_markdown(image_match.group(1)) or name
                image_url = _resolve_markdown_image_url(repo_root, file_path, image_match.group(2))
            continue

        external_links.extend(_extract_recipe_links(repo_root, file_path, line))

        if current_section == 'ingredients':
            ingredient_lines.append(line)
            continue
        if current_section == 'steps':
            step_lines.append(line)
            continue

        if not stripped or stripped.startswith('![') or stripped == '---':
            continue
        if stripped.startswith('layout:') or stripped.startswith('hero:') or stripped.startswith('features:'):
            continue
        description_lines.append(stripped)

    ingredients = _extract_markdown_list(ingredient_lines)
    steps = _extract_markdown_list(step_lines, strip_leading_number=True)
    description = '\n'.join(description_lines).strip() or '来源于 CookLikeHOC 开源菜谱。'

    relative_path = file_path.relative_to(repo_root).as_posix()
    category_name = file_path.parent.name
    return ParsedRecipe(
        source_id=relative_path,
        source_url=_build_source_url(relative_path),
        category_name=category_name,
        name=name,
        description=description,
        ingredients=ingredients,
        steps=steps,
        image_url=image_url,
        image_title=image_title,
        external_links=_dedupe_links(external_links),
    )


@transaction.atomic
def sync_from_repo(repo_root: Path, prune: bool = False) -> dict[str, int]:
    if not repo_root.exists():
        raise FileNotFoundError(f'仓库目录不存在: {repo_root}')

    created = 0
    updated = 0
    category_map, categories_created = _ensure_simplified_categories()
    files_total = 0
    imported = 0
    seen_source_ids: set[str] = set()

    for md_file in _iter_recipe_markdown_files(repo_root):
        files_total += 1
        parsed = parse_recipe_markdown(repo_root, md_file)
        seen_source_ids.add(parsed.source_id)

        simplified_category_name = simplify_recipe_category(parsed.category_name)
        category = category_map.get(simplified_category_name) or category_map['其他']

        defaults = {
            'name': parsed.name,
            'category': category,
            'description': parsed.description,
            'difficulty': '中等',
            'is_published': True,
            'source_url': parsed.source_url,
            'external_links': parsed.external_links or [],
            'media_type': Recipe.MEDIA_IMAGE if parsed.image_url else Recipe.MEDIA_NONE,
            'media_title': parsed.image_title if parsed.image_url else '',
            'media_url': parsed.image_url,
        }

        recipe, was_created = Recipe.objects.update_or_create(
            source=SOURCE_NAME,
            source_id=parsed.source_id,
            defaults=defaults,
        )
        if was_created:
            created += 1
        else:
            updated += 1

        recipe.ingredients.all().delete()
        recipe.steps.all().delete()

        ingredient_objs = [
            RecipeIngredient(
                recipe=recipe,
                name=ingredient,
                amount='',
                is_main=True,
                sort_order=index,
            )
            for index, ingredient in enumerate(parsed.ingredients)
        ]
        if ingredient_objs:
            RecipeIngredient.objects.bulk_create(ingredient_objs)

        step_objs = [
            RecipeStep(
                recipe=recipe,
                step_number=index + 1,
                description=step,
            )
            for index, step in enumerate(parsed.steps)
        ]
        if step_objs:
            RecipeStep.objects.bulk_create(step_objs)

        imported += 1

    pruned = 0
    if prune:
        pruned = Recipe.objects.filter(source=SOURCE_NAME).exclude(source_id__in=seen_source_ids).update(
            is_published=False
        )

    return {
        'files_total': files_total,
        'imported': imported,
        'created': created,
        'updated': updated,
        'categories_created': categories_created,
        'pruned': pruned,
    }


def pull_and_sync(refresh: bool = False, prune: bool = False) -> dict[str, int]:
    repo_path = pull_repo(refresh=refresh)
    result = sync_from_repo(repo_path, prune=prune)
    result['repo_path'] = str(repo_path)
    return result
