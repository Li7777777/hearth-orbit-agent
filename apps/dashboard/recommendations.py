import ctypes
import logging
import os
import socket
from datetime import timedelta
from threading import Thread

from django.conf import settings
from django.db import close_old_connections, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from apps.recipes.meal_agents import build_full_llm_multi_agent_meal_plan

from .models import MealPlanSnapshot

logger = logging.getLogger(__name__)
_HOSTNAME = socket.gethostname()


def full_llm_enabled() -> bool:
    config = getattr(settings, 'MEAL_AGENT_FULL_LLM', {})
    return bool(config.get('enabled')) if isinstance(config, dict) else False


def background_refresh_enabled() -> bool:
    config = getattr(settings, 'MEAL_PLAN_BACKGROUND_REFRESH', {})
    return bool(config.get('enabled', True)) if isinstance(config, dict) else True


def get_full_llm_snapshot() -> MealPlanSnapshot:
    snapshot, _ = MealPlanSnapshot.objects.get_or_create(key=MealPlanSnapshot.KEY_FULL_LLM)
    return snapshot


def snapshot_is_fresh(snapshot: MealPlanSnapshot, now=None) -> bool:
    now = now or timezone.now()
    if snapshot.status != MealPlanSnapshot.STATUS_READY or not snapshot.generated_at:
        return False
    if snapshot.generated_for != timezone.localdate():
        return False
    return snapshot.generated_at >= now - timedelta(minutes=_refresh_minutes())


def snapshot_in_error_cooldown(snapshot: MealPlanSnapshot, now=None) -> bool:
    if snapshot.status != MealPlanSnapshot.STATUS_ERROR or not snapshot.last_attempt_at:
        return False
    now = now or timezone.now()
    return snapshot.last_attempt_at >= now - timedelta(minutes=_error_retry_minutes())


def ensure_full_llm_refresh() -> MealPlanSnapshot:
    snapshot = get_full_llm_snapshot()
    if (
        not full_llm_enabled()
        or not background_refresh_enabled()
        or snapshot_is_fresh(snapshot)
        or snapshot_in_error_cooldown(snapshot)
    ):
        return snapshot
    snapshot, _ = schedule_full_llm_refresh(force=False)
    return snapshot


def schedule_full_llm_refresh(force: bool = False) -> tuple[MealPlanSnapshot, bool]:
    snapshot = get_full_llm_snapshot()
    if not full_llm_enabled():
        return snapshot, False

    snapshot, claimed = _claim_refresh(force=force)
    if not claimed:
        return snapshot, False

    worker = Thread(
        target=_refresh_worker,
        args=(snapshot.pk,),
        name='meal-plan-refresh',
        daemon=True,
    )
    worker.start()
    return snapshot, True


def refresh_full_llm_plan_sync(force: bool = False) -> MealPlanSnapshot:
    if not full_llm_enabled():
        return get_full_llm_snapshot()
    snapshot, claimed = _claim_refresh(force=force)
    if claimed:
        _generate_snapshot(snapshot.pk)
        snapshot.refresh_from_db()
    return snapshot


def _claim_refresh(force: bool) -> tuple[MealPlanSnapshot, bool]:
    now = timezone.now()
    with transaction.atomic():
        snapshot, _ = MealPlanSnapshot.objects.select_for_update().get_or_create(
            key=MealPlanSnapshot.KEY_FULL_LLM
        )
        active_since = now - _refresh_lock_timeout()
        refresh_is_recent = (
            snapshot.status == MealPlanSnapshot.STATUS_REFRESHING
            and snapshot.refresh_started_at
            and snapshot.refresh_started_at >= active_since
        )
        if refresh_is_recent and _refresh_owner_is_alive(snapshot):
            return snapshot, False
        if not force and snapshot_is_fresh(snapshot, now=now):
            return snapshot, False

        snapshot.status = MealPlanSnapshot.STATUS_REFRESHING
        snapshot.refresh_started_at = now
        snapshot.refresh_owner_pid = os.getpid()
        snapshot.refresh_owner_host = _HOSTNAME
        snapshot.last_attempt_at = now
        snapshot.error_message = ''
        snapshot.save(update_fields=[
            'status',
            'refresh_started_at',
            'refresh_owner_pid',
            'refresh_owner_host',
            'last_attempt_at',
            'error_message',
            'updated_at',
        ])
        return snapshot, True


def _refresh_worker(snapshot_id: int):
    close_old_connections()
    try:
        _generate_snapshot(snapshot_id)
    finally:
        close_old_connections()


def _generate_snapshot(snapshot_id: int):
    try:
        plan = build_full_llm_multi_agent_meal_plan(limit_per_meal=3, mark_recommended=True)
        rendered_html = render_to_string('dashboard/_meal_plan_card.html', {
            'section_title': '全大模型多 Agent 方案',
            'plan': plan,
        })
        llm_status = plan.get('llm_status') or {}
        used = bool(llm_status.get('used'))
        message = str(llm_status.get('message') or '')
        now = timezone.now()

        with transaction.atomic():
            snapshot = MealPlanSnapshot.objects.select_for_update().get(pk=snapshot_id)
            if used:
                snapshot.rendered_html = rendered_html
                snapshot.generated_for = timezone.localdate()
                snapshot.generated_at = now
                snapshot.status = MealPlanSnapshot.STATUS_READY
                snapshot.error_message = ''
            else:
                if not snapshot.rendered_html:
                    snapshot.rendered_html = rendered_html
                snapshot.status = MealPlanSnapshot.STATUS_ERROR
                snapshot.error_message = message or '模型没有生成可用推荐。'
            snapshot.refresh_started_at = None
            snapshot.refresh_owner_pid = None
            snapshot.refresh_owner_host = ''
            snapshot.last_attempt_at = now
            snapshot.save()
    except Exception as exc:  # Background tasks must never break the request process.
        logger.exception('后台生成全大模型三餐推荐失败')
        MealPlanSnapshot.objects.filter(pk=snapshot_id).update(
            status=MealPlanSnapshot.STATUS_ERROR,
            refresh_started_at=None,
            refresh_owner_pid=None,
            refresh_owner_host='',
            last_attempt_at=timezone.now(),
            error_message=str(exc),
        )


def _refresh_minutes() -> int:
    config = getattr(settings, 'MEAL_PLAN_BACKGROUND_REFRESH', {})
    value = config.get('refresh_minutes', 240) if isinstance(config, dict) else 240
    try:
        return max(int(value), 5)
    except (TypeError, ValueError):
        return 240


def _error_retry_minutes() -> int:
    config = getattr(settings, 'MEAL_PLAN_BACKGROUND_REFRESH', {})
    value = config.get('error_retry_minutes', 30) if isinstance(config, dict) else 30
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return 30


def _refresh_lock_timeout() -> timedelta:
    config = getattr(settings, 'MEAL_AGENT_FULL_LLM', {})
    value = config.get('timeout_seconds', 60) if isinstance(config, dict) else 60
    try:
        timeout_seconds = max(int(value), 1)
    except (TypeError, ValueError):
        timeout_seconds = 60
    return timedelta(seconds=max(timeout_seconds * 4 + 60, 600))


def _refresh_owner_is_alive(snapshot: MealPlanSnapshot) -> bool:
    if not snapshot.refresh_owner_pid or not snapshot.refresh_owner_host:
        return False
    if snapshot.refresh_owner_host != _HOSTNAME:
        return True
    return _process_is_alive(snapshot.refresh_owner_pid)


def _process_is_alive(process_id: int) -> bool:
    if os.name == 'nt':
        process_query_limited_information = 0x1000
        try:
            handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
                process_query_limited_information,
                False,
                process_id,
            )
        except (AttributeError, OSError):
            return False
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True
    try:
        os.kill(process_id, 0)
    except OSError:
        return False
    return True
