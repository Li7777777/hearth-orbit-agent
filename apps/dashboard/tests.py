from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import MealPlanSnapshot
from .recommendations import ensure_full_llm_refresh, refresh_full_llm_plan_sync, schedule_full_llm_refresh

ASYNC_SETTINGS = {'enabled': True, 'refresh_minutes': 240}
FULL_LLM_SETTINGS = {'enabled': True, 'reuse_meal_llm_config': False}


class AsyncRecommendationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='async-dashboard-user', password='safe-password')
        self.client.force_login(self.user)

    @override_settings(
        MEAL_AGENT_FULL_LLM=FULL_LLM_SETTINGS,
        MEAL_PLAN_BACKGROUND_REFRESH={'enabled': False, 'refresh_minutes': 240},
    )
    @patch('apps.dashboard.recommendations.build_full_llm_multi_agent_meal_plan')
    def test_dashboard_request_never_calls_recommendation_builders(self, mock_builder):
        with (
            patch('apps.dashboard.views.build_daily_meal_plan') as mock_local_plan,
            patch('apps.dashboard.views.recommend_homepage_recipes') as mock_recipe_recommendations,
        ):
            response = self.client.get(reverse('dashboard:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '后台计算，不阻塞页面')
        mock_builder.assert_not_called()
        mock_local_plan.assert_not_called()
        mock_recipe_recommendations.assert_not_called()

    @override_settings(MEAL_AGENT_FULL_LLM={'enabled': False})
    @patch('apps.dashboard.views.build_daily_meal_plan')
    def test_local_plan_fragment_explicitly_disables_llm(self, mock_local_plan):
        mock_local_plan.return_value = {
            'kicker': 'Local Agent',
            'title': '本地方案',
            'architecture': '本地确定性评分',
            'agent_cards': [],
            'meals': [],
            'llm_status': {'enabled': False, 'used': False, 'provider_label': '本地', 'message': '完成'},
        }

        response = self.client.get(reverse('dashboard:local_meal_plan'))

        self.assertEqual(response.status_code, 200)
        mock_local_plan.assert_called_once_with(limit_per_meal=3, mark_recommended=True, use_llm=False)

    @override_settings(MEAL_AGENT_FULL_LLM=FULL_LLM_SETTINGS, MEAL_PLAN_BACKGROUND_REFRESH=ASYNC_SETTINGS)
    def test_status_endpoint_returns_cached_fragment(self):
        MealPlanSnapshot.objects.create(
            status=MealPlanSnapshot.STATUS_READY,
            rendered_html='<div data-test="cached-plan">缓存推荐</div>',
            generated_for=date.today(),
            generated_at=timezone.now(),
        )

        response = self.client.get(reverse('dashboard:full_llm_plan_status'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '缓存推荐')
        self.assertContains(response, '后台推荐已就绪')

    @override_settings(MEAL_AGENT_FULL_LLM=FULL_LLM_SETTINGS, MEAL_PLAN_BACKGROUND_REFRESH=ASYNC_SETTINGS)
    @patch('apps.dashboard.views.schedule_full_llm_refresh')
    def test_manual_refresh_returns_polling_state(self, mock_schedule):
        snapshot = MealPlanSnapshot.objects.create(status=MealPlanSnapshot.STATUS_REFRESHING)
        mock_schedule.return_value = (snapshot, True)

        response = self.client.post(reverse('dashboard:full_llm_plan_refresh'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '推荐正在后台更新')
        self.assertContains(response, 'hx-trigger="every 4s"')

    @override_settings(MEAL_AGENT_FULL_LLM=FULL_LLM_SETTINGS, MEAL_PLAN_BACKGROUND_REFRESH=ASYNC_SETTINGS)
    @patch('apps.dashboard.recommendations.Thread')
    def test_scheduler_claims_job_and_returns_without_running_inline(self, mock_thread):
        snapshot, started = schedule_full_llm_refresh(force=True)

        self.assertTrue(started)
        self.assertEqual(snapshot.status, MealPlanSnapshot.STATUS_REFRESHING)
        mock_thread.return_value.start.assert_called_once_with()

    @override_settings(MEAL_AGENT_FULL_LLM=FULL_LLM_SETTINGS, MEAL_PLAN_BACKGROUND_REFRESH=ASYNC_SETTINGS)
    @patch('apps.dashboard.recommendations.Thread')
    def test_recent_failure_uses_cooldown_instead_of_retrying_every_page_load(self, mock_thread):
        MealPlanSnapshot.objects.create(
            status=MealPlanSnapshot.STATUS_ERROR,
            last_attempt_at=timezone.now(),
            error_message='模型暂时不可用',
        )

        snapshot = ensure_full_llm_refresh()

        self.assertEqual(snapshot.status, MealPlanSnapshot.STATUS_ERROR)
        mock_thread.assert_not_called()

    @override_settings(MEAL_AGENT_FULL_LLM=FULL_LLM_SETTINGS, MEAL_PLAN_BACKGROUND_REFRESH=ASYNC_SETTINGS)
    @patch('apps.dashboard.recommendations.render_to_string', return_value='<section>生成完成</section>')
    @patch('apps.dashboard.recommendations.build_full_llm_multi_agent_meal_plan')
    def test_sync_refresh_command_path_persists_snapshot(self, mock_builder, _mock_render):
        mock_builder.return_value = {
            'llm_status': {'used': True, 'message': '完成'},
        }

        snapshot = refresh_full_llm_plan_sync(force=True)

        self.assertEqual(snapshot.status, MealPlanSnapshot.STATUS_READY)
        self.assertEqual(snapshot.rendered_html, '<section>生成完成</section>')
        self.assertEqual(snapshot.generated_for, date.today())
        self.assertIsNotNone(snapshot.generated_at)
