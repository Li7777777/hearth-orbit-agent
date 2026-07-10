"""
AI菜量菜谱 - 综合测试套件
覆盖所有接口：认证、仪表盘、食材CRUD、订单CRUD、OCR、菜谱、统计
"""
import json
import os
import tempfile
import threading
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.dashboard.models import MealPlanSnapshot
from apps.dishes.models import Dish, DishCategory
from apps.orders.models import DailyDishStatistic, Order, OrderItem
from apps.recipes.models import (
    Recipe,
    RecipeCategory,
    RecipeIngredient,
    RecipeRecommendationHistory,
    RecipeStep,
)

os.environ.setdefault('PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK', 'True')


def _make_image(fmt='PNG', size=(100, 100)):
    """生成一个测试用的小图片"""
    buf = BytesIO()
    Image.new('RGB', size, color='red').save(buf, format=fmt)
    buf.seek(0)
    return buf


# ════════════════════════════════════════════════════════════
# 1. 认证系统
# ════════════════════════════════════════════════════════════
class AuthTestCase(TestCase):
    """测试登录/登出及中间件"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='tester', password='test1234')

    # ── 登录页面 ──
    def test_login_page_loads(self):
        resp = self.client.get(reverse('accounts:login'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '登录')

    def test_login_success(self):
        resp = self.client.post(reverse('accounts:login'), {
            'username': 'tester', 'password': 'test1234'
        })
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, '/')

    def test_login_success_with_next(self):
        resp = self.client.post(reverse('accounts:login') + '?next=/dish/', {
            'username': 'tester', 'password': 'test1234'
        })
        self.assertRedirects(resp, '/dish/')

    def test_login_fail(self):
        resp = self.client.post(reverse('accounts:login'), {
            'username': 'tester', 'password': 'wrong'
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '用户名或密码错误')

    def test_login_redirect_when_authenticated(self):
        self.client.login(username='tester', password='test1234')
        resp = self.client.get(reverse('accounts:login'))
        self.assertRedirects(resp, reverse('dashboard:index'))

    # ── 登出 ──
    def test_logout(self):
        self.client.login(username='tester', password='test1234')
        resp = self.client.get(reverse('accounts:logout'))
        self.assertRedirects(resp, reverse('accounts:login'))

    # ── 中间件：未登录重定向 ──
    def test_middleware_redirects_unauthenticated(self):
        urls = ['/', '/dish/', '/order/', '/recipe/', '/ocr/upload/']
        for url in urls:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, f'{url} should redirect')
            self.assertIn('/accounts/login/', resp.url)

    def test_middleware_allows_login_page(self):
        resp = self.client.get('/accounts/login/')
        self.assertEqual(resp.status_code, 200)


# ════════════════════════════════════════════════════════════
# 2. 仪表盘
# ════════════════════════════════════════════════════════════
@override_settings(
    MEAL_AGENT_LLM={'enabled': False, 'reuse_vision_config': True},
    MEAL_AGENT_FULL_LLM={'enabled': False},
    MEAL_PLAN_BACKGROUND_REFRESH={'enabled': False, 'refresh_minutes': 240},
)
class DashboardTestCase(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='tester', password='test1234')
        self.client.login(username='tester', password='test1234')

    def test_dashboard_loads_empty(self):
        resp = self.client.get(reverse('dashboard:index'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'AI菜量菜谱')

    def test_dashboard_with_data(self):
        cat = DishCategory.objects.create(name='荤菜')
        dish = Dish.objects.create(name='红烧肉', category=cat, created_by=self.user)
        Order.objects.create(
            order_date=date.today(), source='ocr', created_by=self.user,
            total_items=1, total_amount=Decimal('28.00')
        )
        DailyDishStatistic.objects.create(
            dish=dish, stat_date=date.today(),
            total_quantity=3, order_count=1, total_amount=Decimal('84.00')
        )
        resp = self.client.get(reverse('dashboard:index'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '红烧肉')

    def test_dashboard_shows_recommended_recipes(self):
        Recipe.objects.create(name='无食材菜谱', created_by=self.user)
        Dish.objects.create(name='土豆', stock_in_date=date.today() - timedelta(days=6), created_by=self.user)
        Dish.objects.create(name='牛肉', stock_in_date=date.today() - timedelta(days=8), created_by=self.user)

        recipe = Recipe.objects.create(name='土豆炖牛肉', created_by=self.user)
        RecipeIngredient.objects.create(recipe=recipe, name='土豆', amount='300g', is_main=True, sort_order=0)
        RecipeIngredient.objects.create(recipe=recipe, name='牛肉', amount='200g', is_main=True, sort_order=1)

        initial_resp = self.client.get(reverse('dashboard:index'))
        self.assertEqual(initial_resp.status_code, 200)
        self.assertContains(initial_resp, '今日推荐菜谱')
        self.assertNotContains(initial_resp, '命中2/2种食材')

        resp = self.client.get(reverse('dashboard:recipe_recommendations'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '土豆炖牛肉')
        self.assertContains(resp, '命中 2/2 种食材')

        self.assertTrue(
            RecipeRecommendationHistory.objects.filter(recipe=recipe, recommended_date=date.today()).exists()
        )

    def test_dashboard_shows_daily_meal_multi_agent_plan(self):
        dish_category, _ = DishCategory.objects.get_or_create(name='蔬果类')
        breakfast_category, _ = RecipeCategory.objects.get_or_create(name='主食早餐')
        lunch_category, _ = RecipeCategory.objects.get_or_create(name='家常热菜')
        dinner_category, _ = RecipeCategory.objects.get_or_create(name='汤粥锅煲')

        egg = Dish.objects.create(
            name='鸡蛋',
            category=dish_category,
            default_price=Decimal('3.00'),
            stock_in_date=date.today() - timedelta(days=2),
            created_by=self.user,
        )
        potato = Dish.objects.create(
            name='土豆',
            category=dish_category,
            default_price=Decimal('5.00'),
            stock_in_date=date.today() - timedelta(days=6),
            created_by=self.user,
        )
        greens = Dish.objects.create(
            name='青菜',
            category=dish_category,
            default_price=Decimal('4.00'),
            storage='冷藏',
            stock_in_date=date.today() - timedelta(days=8),
            created_by=self.user,
        )
        Dish.objects.create(
            name='过期青菜',
            category=dish_category,
            default_price=Decimal('4.00'),
            is_active=False,
            deactivation_reason='discarded',
            deactivated_at=date.today(),
            created_by=self.user,
        )
        Order.objects.create(
            order_date=date.today(),
            source='ocr',
            created_by=self.user,
            total_items=1,
            total_amount=Decimal('15.00'),
        )
        DailyDishStatistic.objects.create(
            dish=potato,
            stat_date=date.today(),
            total_quantity=3,
            order_count=1,
            total_amount=Decimal('15.00'),
        )

        breakfast = Recipe.objects.create(name='鸡蛋饼', category=breakfast_category, created_by=self.user)
        lunch = Recipe.objects.create(name='土豆炖牛肉', category=lunch_category, created_by=self.user)
        dinner = Recipe.objects.create(name='青菜豆腐汤', category=dinner_category, created_by=self.user)
        RecipeIngredient.objects.create(recipe=breakfast, name=egg.name, is_main=True, sort_order=0)
        RecipeIngredient.objects.create(recipe=lunch, name=potato.name, is_main=True, sort_order=0)
        RecipeIngredient.objects.create(recipe=dinner, name=greens.name, is_main=True, sort_order=0)

        initial_resp = self.client.get(reverse('dashboard:index'))
        self.assertEqual(initial_resp.status_code, 200)
        self.assertContains(initial_resp, '今日三餐智能计划')
        self.assertNotContains(initial_resp, '鸡蛋饼')

        resp = self.client.get(reverse('dashboard:local_meal_plan'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '早餐')
        self.assertContains(resp, '午餐')
        self.assertContains(resp, '晚餐')
        self.assertContains(resp, '鸡蛋饼')
        self.assertContains(resp, '土豆炖牛肉')
        self.assertContains(resp, '青菜豆腐汤')
        self.assertContains(resp, '库存保鲜 Agent')
        self.assertContains(resp, '行为记忆 Agent')
        self.assertContains(resp, '订单价格 Agent')
        self.assertContains(resp, '采购提醒 Agent')
        self.assertContains(resp, '近期曾丢弃')
        self.assertContains(resp, '近 30 天订单 1 笔')
        self.assertContains(resp, '本地多 Agent 确定性评分')

    @override_settings(MEAL_AGENT_FULL_LLM={'enabled': True, 'reuse_meal_llm_config': False})
    def test_full_llm_multi_agent_plan_reports_missing_config(self):
        from apps.recipes.meal_agents import build_full_llm_multi_agent_meal_plan

        plan = build_full_llm_multi_agent_meal_plan(mark_recommended=False)

        self.assertFalse(plan['llm_status']['used'])
        self.assertIn('配置', plan['llm_status']['message'])

    @override_settings(MEAL_AGENT_FULL_LLM={
        'enabled': True,
        'provider_name': '测试模型',
        'api_key': 'test-key',
        'base_url': 'https://llm.example.com/v1',
        'model': 'test-model',
        'timeout_seconds': '10',
        'reuse_meal_llm_config': False,
        'max_recipes': '12',
        'requests_per_minute': '5',
        'max_concurrency': '4',
        'expert_concurrency': '4',
    })
    @patch('apps.recipes.meal_agents._call_openai_compatible_messages')
    def test_full_llm_multi_agent_plan_uses_model_agents(self, mock_chat):
        dish_category, _ = DishCategory.objects.get_or_create(name='蔬果类')
        breakfast_category, _ = RecipeCategory.objects.get_or_create(name='主食早餐')
        lunch_category, _ = RecipeCategory.objects.get_or_create(name='家常热菜')
        dinner_category, _ = RecipeCategory.objects.get_or_create(name='汤粥锅煲')
        egg = Dish.objects.create(
            name='鸡蛋', category=dish_category, stock_in_date=date.today(), created_by=self.user
        )
        potato = Dish.objects.create(
            name='土豆', category=dish_category, stock_in_date=date.today(), created_by=self.user
        )
        greens = Dish.objects.create(
            name='青菜', category=dish_category, stock_in_date=date.today(), created_by=self.user
        )
        breakfast = Recipe.objects.create(name='鸡蛋饼', category=breakfast_category, created_by=self.user)
        lunch = Recipe.objects.create(name='土豆炖牛肉', category=lunch_category, created_by=self.user)
        dinner = Recipe.objects.create(name='青菜豆腐汤', category=dinner_category, created_by=self.user)
        RecipeIngredient.objects.create(recipe=breakfast, name=egg.name, is_main=True, sort_order=0)
        RecipeIngredient.objects.create(recipe=lunch, name=potato.name, is_main=True, sort_order=0)
        RecipeIngredient.objects.create(recipe=dinner, name=greens.name, is_main=True, sort_order=0)

        def fake_chat(_config, messages, **_kwargs):
            prompt = '\n'.join(str(message.get('content', '')) for message in messages)
            if '三餐协调 Agent' in prompt:
                return json.dumps({
                    'overall_note': '测试模型已完成全大模型三餐编排',
                    'meals': [
                        {
                            'key': 'breakfast',
                            'recipe_id': breakfast.id,
                            'score': 91,
                            'summary': '早餐推荐鸡蛋饼',
                            'reason': '鸡蛋命中库存，制作快。',
                            'reason_details': ['鸡蛋在当前库存中可用', '早餐时段适合快手主食'],
                            'alternatives': [],
                            'agent_votes': [{'name': '库存LLM', 'score': 91, 'label': '库存命中'}],
                        },
                        {
                            'key': 'lunch',
                            'recipe_id': lunch.id,
                            'score': 89,
                            'summary': '午餐推荐土豆炖牛肉',
                            'reason': '午餐更饱腹。',
                            'alternatives': [],
                            'agent_votes': [{'name': '记忆LLM', 'score': 89, 'label': '搭配稳定'}],
                        },
                        {
                            'key': 'dinner',
                            'recipe_id': dinner.id,
                            'score': 92,
                            'summary': '晚餐推荐青菜豆腐汤',
                            'reason': '晚餐清爽，减少青菜积压。',
                            'reason_details': ['青菜命中库存', '晚餐口味更清爽'],
                            'alternatives': [],
                            'agent_votes': [{'name': '成本LLM', 'score': 92, 'label': '避浪费'}],
                        },
                    ],
                })
            if '库存策略 Agent' in prompt:
                return json.dumps({
                    'findings': ['库存优先处理鸡蛋和青菜'],
                    'priorities': [{'recipe_id': breakfast.id, 'score': 88, 'reason': '早餐快手'}],
                })
            if '偏好记忆 Agent' in prompt:
                return json.dumps({
                    'findings': ['近期没有重复推荐压力'],
                    'priorities': [{'recipe_id': lunch.id, 'score': 86, 'reason': '午餐饱腹'}],
                })
            if '成本风险 Agent' in prompt:
                return json.dumps({
                    'findings': ['青菜适合降低浪费风险'],
                'priorities': [{'recipe_id': dinner.id, 'score': 90, 'reason': '晚餐清爽'}],
            })
            if '采购提醒 Agent' in prompt:
                return json.dumps({
                    'findings': ['鸡蛋近期消耗快，建议核对余量'],
                    'priorities': [{'recipe_id': breakfast.id, 'score': 82, 'reason': '采购提醒支持早餐'}],
                })
            raise AssertionError(prompt)

        mock_chat.side_effect = fake_chat
        from apps.recipes.meal_agents import build_full_llm_multi_agent_meal_plan

        plan = build_full_llm_multi_agent_meal_plan(mark_recommended=False)

        self.assertTrue(plan['llm_status']['used'])
        self.assertEqual(mock_chat.call_count, 5)
        self.assertContainsPlanRecipe(plan, '早餐', '鸡蛋饼')
        self.assertContainsPlanRecipe(plan, '午餐', '土豆炖牛肉')
        self.assertContainsPlanRecipe(plan, '晚餐', '青菜豆腐汤')
        breakfast_meal = next(item for item in plan['meals'] if item['label'] == '早餐')
        lunch_meal = next(item for item in plan['meals'] if item['label'] == '午餐')
        self.assertEqual(breakfast_meal['selected']['reason_details'][0], '鸡蛋在当前库存中可用')
        self.assertIn('偏好记忆 Agent：午餐饱腹', lunch_meal['selected']['reason_details'])
        self.assertIn('库存策略 Agent', [agent['name'] for agent in plan['agent_cards']])
        self.assertIn('采购提醒 Agent', [agent['name'] for agent in plan['agent_cards']])
        self.assertIn('三餐协调 Agent', [agent['name'] for agent in plan['agent_cards']])

    @patch('apps.recipes.meal_agents._call_openai_compatible_messages')
    def test_full_llm_expert_agents_run_in_parallel(self, mock_chat):
        barrier = threading.Barrier(4)

        def fake_chat(_config, messages, **_kwargs):
            prompt = '\n'.join(str(message.get('content', '')) for message in messages)
            barrier.wait(timeout=2)
            if '库存策略 Agent' in prompt:
                return json.dumps({
                    'findings': ['库存专家已启动'],
                    'priorities': [{'recipe_id': 1, 'score': 88, 'reason': '库存理由'}],
                })
            if '偏好记忆 Agent' in prompt:
                return json.dumps({
                    'findings': ['记忆专家已启动'],
                    'priorities': [{'recipe_id': 1, 'score': 86, 'reason': '记忆理由'}],
                })
            if '成本风险 Agent' in prompt:
                return json.dumps({
                    'findings': ['成本专家已启动'],
                    'priorities': [{'recipe_id': 1, 'score': 84, 'reason': '成本理由'}],
                })
            if '采购提醒 Agent' in prompt:
                return json.dumps({
                    'findings': ['采购专家已启动'],
                    'priorities': [{'recipe_id': 1, 'score': 82, 'reason': '采购理由'}],
                })
            raise AssertionError(prompt)

        mock_chat.side_effect = fake_chat
        from apps.recipes.meal_agents import _run_full_llm_expert_agents

        reports = _run_full_llm_expert_agents(
            {'expert_concurrency': 4},
            {
                'date': str(date.today()),
                'meal_slots': [],
                'context_notes': [],
                'inventory': [],
                'history': {},
                'orders': {},
                'recipes': [{'recipe_id': 1, 'name': '测试菜谱'}],
                '_recipes_by_id': {1: {'recipe_id': 1, 'name': '测试菜谱'}},
            },
        )

        self.assertEqual(mock_chat.call_count, 4)
        self.assertEqual([report['key'] for report in reports], ['inventory', 'memory', 'cost', 'purchase'])

    @override_settings(MEAL_AGENT_FULL_LLM={
        'enabled': True,
        'provider_name': 'Full Override',
        'model': 'DeepSeek-V4-Flash-think',
        'timeout_seconds': '150',
        'reuse_meal_llm_config': True,
        'requests_per_minute': '5',
        'max_concurrency': '3',
        'expert_concurrency': '3',
    })
    @override_settings(MEAL_AGENT_LLM={
        'enabled': True,
        'provider_name': 'Meal Base',
        'api_key': 'test-key',
        'base_url': 'https://llm.example.com/v1',
        'model': 'qwen3-vl-plus',
        'timeout_seconds': '60',
        'reuse_vision_config': False,
    })
    def test_full_llm_config_overrides_reused_model_and_timeout(self):
        from apps.recipes.meal_agents import _resolve_full_llm_config

        config = _resolve_full_llm_config()

        self.assertEqual(config['provider_label'], 'Full Override')
        self.assertEqual(config['model'], 'DeepSeek-V4-Flash-think')
        self.assertEqual(config['timeout_seconds'], 150)
        self.assertEqual(config['requests_per_minute'], 5)

    @override_settings(
        MEAL_AGENT_FULL_LLM={'enabled': True},
        MEAL_PLAN_BACKGROUND_REFRESH={'enabled': False, 'refresh_minutes': 240},
    )
    def test_dashboard_renders_cached_full_llm_plan_without_waiting_for_model(self):
        MealPlanSnapshot.objects.create(
            key=MealPlanSnapshot.KEY_FULL_LLM,
            status=MealPlanSnapshot.STATUS_READY,
            rendered_html=(
                '<section><h2>全大模型多 Agent 方案</h2>'
                '<p>全大模型三餐方案</p><p>详细理由会提供给前端</p></section>'
            ),
            generated_for=date.today(),
        )

        with patch('apps.dashboard.recommendations.build_full_llm_multi_agent_meal_plan') as mock_builder:
            resp = self.client.get(reverse('dashboard:index'))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '全大模型多 Agent 方案')
        self.assertContains(resp, '全大模型三餐方案')
        self.assertContains(resp, '详细理由会提供给前端')
        mock_builder.assert_not_called()

    @patch('apps.recipes.meal_agents.urlopen')
    def test_reasoning_model_uses_larger_generation_budget(self, mock_urlopen):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps({
                    'choices': [{
                        'message': {
                            'content': '{"ok": true}',
                            'reasoning': '思考内容不用于页面展示。',
                        },
                    }],
                }).encode('utf-8')

        mock_urlopen.return_value = FakeResponse()
        from apps.recipes.meal_agents import _call_openai_compatible_messages

        content = _call_openai_compatible_messages(
            {
                'provider_label': 'Deepseek',
                'api_key': 'test-key',
                'base_url': 'https://llm.example.com/v1',
                'model': 'DeepSeek-V4-Pro-think',
                'timeout_seconds': 25,
            },
            [{'role': 'user', 'content': 'ping'}],
            max_tokens=32,
            temperature=0,
        )
        request = mock_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode('utf-8'))

        self.assertEqual(content, '{"ok": true}')
        self.assertEqual(payload['max_tokens'], 8192)
        self.assertEqual(mock_urlopen.call_args.kwargs['timeout'], 90)

    @patch('apps.recipes.meal_agents.urlopen')
    def test_reasoning_model_extracts_json_from_reasoning_when_content_empty(self, mock_urlopen):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps({
                    'choices': [{
                        'message': {
                            'content': '',
                            'reasoning_content': (
                                '内部分析不应展示。最终 JSON：'
                                '{"findings":["可用"],"priorities":[{"recipe_id":1,"score":88,"reason":"命中库存"}]}'
                            ),
                        },
                    }],
                }).encode('utf-8')

        mock_urlopen.return_value = FakeResponse()
        from apps.recipes.meal_agents import _call_openai_compatible_messages

        content = _call_openai_compatible_messages(
            {
                'provider_label': 'Deepseek',
                'api_key': 'test-key',
                'base_url': 'https://llm.example.com/v1',
                'model': 'DeepSeek-V4-Flash-think',
                'timeout_seconds': 25,
            },
            [{'role': 'user', 'content': 'ping'}],
            max_tokens=32,
            temperature=0,
        )

        self.assertNotIn('内部分析', content)
        self.assertEqual(json.loads(content)['priorities'][0]['recipe_id'], 1)

    @patch('apps.recipes.meal_agents.urlopen')
    def test_reasoning_model_without_final_json_reports_config_hint(self, mock_urlopen):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps({
                    'choices': [{
                        'message': {
                            'content': '',
                            'reasoning': '这里只是思考内容，没有最终 JSON。',
                        },
                    }],
                }).encode('utf-8')

        mock_urlopen.return_value = FakeResponse()
        from apps.recipes.meal_agents import _call_openai_compatible_messages

        with self.assertRaisesMessage(RuntimeError, '模型只返回了思考内容'):
            _call_openai_compatible_messages(
                {
                    'provider_label': 'Deepseek',
                    'api_key': 'test-key',
                    'base_url': 'https://llm.example.com/v1',
                    'model': 'DeepSeek-V4-Flash-think',
                    'timeout_seconds': 25,
                },
                [{'role': 'user', 'content': 'ping'}],
                max_tokens=32,
                temperature=0,
            )

    @patch('apps.recipes.meal_agents.urlopen')
    def test_model_call_obeys_configured_rate_limit(self, mock_urlopen):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps({
                    'choices': [{'message': {'content': '{"ok": true}'}}],
                }).encode('utf-8')

        mock_urlopen.return_value = FakeResponse()
        from apps.recipes import meal_agents

        meal_agents._MODEL_RATE_TIMESTAMPS.clear()
        original_window = meal_agents.MODEL_RATE_WINDOW_SECONDS
        meal_agents.MODEL_RATE_WINDOW_SECONDS = 1.0
        config = {
            'provider_label': '测试模型',
            'api_key': 'test-key',
            'base_url': 'https://llm.example.com/v1',
            'model': 'test-model',
            'timeout_seconds': 10,
            'requests_per_minute': 1,
            'max_concurrency': 1,
        }
        try:
            with (
                patch('apps.recipes.meal_agents.time.monotonic', side_effect=[0.0, 0.1, 1.0]),
                patch('apps.recipes.meal_agents.time.sleep') as mock_sleep,
            ):
                meal_agents._call_openai_compatible_messages(
                    config,
                    [{'role': 'user', 'content': 'ping'}],
                    max_tokens=32,
                    temperature=0,
                )
                meal_agents._call_openai_compatible_messages(
                    config,
                    [{'role': 'user', 'content': 'ping'}],
                    max_tokens=32,
                    temperature=0,
                )
        finally:
            meal_agents.MODEL_RATE_WINDOW_SECONDS = original_window
            meal_agents._MODEL_RATE_TIMESTAMPS.clear()

        self.assertEqual(mock_urlopen.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)
        self.assertAlmostEqual(mock_sleep.call_args.args[0], 0.9)

    @patch('apps.recipes.meal_agents.urlopen')
    def test_model_call_retries_once_after_remote_rate_limit(self, mock_urlopen):
        from urllib.error import HTTPError

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps({
                    'choices': [{'message': {'content': '{"ok": true}'}}],
                }).encode('utf-8')

        class ErrorBody:
            def read(self):
                return b'{"error":{"message":"rate limited"}}'

        mock_urlopen.side_effect = [
            HTTPError(
                url='https://llm.example.com/v1/chat/completions',
                code=429,
                msg='Too Many Requests',
                hdrs={'Retry-After': '2'},
                fp=ErrorBody(),
            ),
            FakeResponse(),
        ]
        from apps.recipes import meal_agents

        meal_agents._MODEL_RATE_TIMESTAMPS.clear()
        config = {
            'provider_label': '测试模型',
            'api_key': 'test-key',
            'base_url': 'https://llm.example.com/v1',
            'model': 'test-model',
            'timeout_seconds': 10,
            'requests_per_minute': 5,
            'max_concurrency': 1,
        }
        try:
            with patch('apps.recipes.meal_agents.time.sleep') as mock_sleep:
                content = meal_agents._call_openai_compatible_messages(
                    config,
                    [{'role': 'user', 'content': 'ping'}],
                    max_tokens=32,
                    temperature=0,
                )
        finally:
            meal_agents._MODEL_RATE_TIMESTAMPS.clear()

        self.assertEqual(content, '{"ok": true}')
        self.assertEqual(mock_urlopen.call_count, 2)
        mock_sleep.assert_called_once_with(2.0)

    def assertContainsPlanRecipe(self, plan, meal_label, recipe_name):
        meal = next(item for item in plan['meals'] if item['label'] == meal_label)
        self.assertEqual(meal['selected']['recipe'].name, recipe_name)

    def test_dashboard_recommendation_penalizes_history(self):
        Dish.objects.create(name='鸡蛋', stock_in_date=date.today() - timedelta(days=5), created_by=self.user)
        Dish.objects.create(name='番茄', stock_in_date=date.today() - timedelta(days=5), created_by=self.user)

        old_recipe = Recipe.objects.create(name='历史常推菜谱', created_by=self.user)
        new_recipe = Recipe.objects.create(name='新鲜候选菜谱', created_by=self.user)
        for idx, ing_name in enumerate(['鸡蛋', '番茄']):
            RecipeIngredient.objects.create(recipe=old_recipe, name=ing_name, is_main=True, sort_order=idx)
            RecipeIngredient.objects.create(recipe=new_recipe, name=ing_name, is_main=True, sort_order=idx)

        for delta in [1, 2, 3]:
            RecipeRecommendationHistory.objects.create(
                recipe=old_recipe,
                recommended_date=date.today() - timedelta(days=delta),
                score=20,
                matched_ingredient_count=2,
            )

        resp = self.client.get(reverse('dashboard:recipe_recommendations'))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        self.assertIn('新鲜候选菜谱', content)
        self.assertIn('历史常推菜谱', content)
        self.assertLess(content.index('新鲜候选菜谱'), content.index('历史常推菜谱'))


# ════════════════════════════════════════════════════════════
# 3. 食材管理 CRUD
# ════════════════════════════════════════════════════════════
class DishTestCase(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='tester', password='test1234')
        self.client.login(username='tester', password='test1234')
        self.cat = DishCategory.objects.create(name='荤菜', icon='🥩')

    # ── 列表 ──
    def test_list_empty(self):
        resp = self.client.get(reverse('dishes:list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '暂无食材')

    def test_list_with_dishes(self):
        Dish.objects.create(name='宫保鸡丁', category=self.cat, created_by=self.user)
        resp = self.client.get(reverse('dishes:list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '宫保鸡丁')

    def test_list_category_filter(self):
        Dish.objects.create(name='宫保鸡丁', category=self.cat, created_by=self.user)
        cat2 = DishCategory.objects.create(name='素菜', icon='🥬')
        Dish.objects.create(name='清炒时蔬', category=cat2, created_by=self.user)
        resp = self.client.get(reverse('dishes:list') + f'?category={self.cat.id}')
        self.assertContains(resp, '宫保鸡丁')
        self.assertNotContains(resp, '清炒时蔬')

    def test_list_search_is_frontend_only(self):
        Dish.objects.create(name='宫保鸡丁', category=self.cat, created_by=self.user)
        Dish.objects.create(name='水煮鱼', category=self.cat, created_by=self.user)
        resp = self.client.get(reverse('dishes:list') + '?q=鸡丁')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '宫保鸡丁')
        self.assertContains(resp, '水煮鱼')
        self.assertContains(resp, 'id="dish-search-input"')
        self.assertContains(resp, 'data-search-text=')
        self.assertNotContains(resp, 'name="q"')
        self.assertNotContains(resp, 'type="submit" class="btn btn-primary btn-sm">搜索')

    def test_list_htmx_partial(self):
        Dish.objects.create(name='宫保鸡丁', category=self.cat, created_by=self.user)
        resp = self.client.get(reverse('dishes:list'), HTTP_HX_REQUEST='true')
        self.assertEqual(resp.status_code, 200)
        # HTMX 局部返回不含 base 模板的 html/head
        self.assertNotContains(resp, '<!DOCTYPE html>')

    def test_list_show_inactive(self):
        Dish.objects.create(name='已停菜', category=self.cat, is_active=False, created_by=self.user)
        resp = self.client.get(reverse('dishes:list'))
        self.assertNotContains(resp, '已停菜')
        resp = self.client.get(reverse('dishes:list') + '?inactive=1')
        self.assertContains(resp, '已停菜')

    def test_list_shows_days_in_stock(self):
        Dish.objects.create(
            name='列表页入库天数',
            category=self.cat,
            stock_in_date=date.today() - timedelta(days=4),
            created_by=self.user,
        )
        resp = self.client.get(reverse('dishes:list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '已买入4天')

    # ── 创建 ──
    def test_create_get(self):
        resp = self.client.get(reverse('dishes:create'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '新增食材')
        self.assertContains(resp, 'AI拍照识别')
        self.assertContains(resp, reverse('dishes:recognize_image'))
        self.assertContains(resp, '拍照上传')
        self.assertContains(resp, '从相册选择')
        self.assertContains(resp, 'accept="image/*"')

    def test_create_post_success(self):
        resp = self.client.post(reverse('dishes:create'), {
            'name': '鱼香肉丝',
            'category': self.cat.id,
            'stock_in_date': date.today().isoformat(),
            'unit': '斤',
            'storage': '冷藏',
            'is_active': 'on',
        })
        self.assertRedirects(resp, reverse('dishes:list'))
        self.assertTrue(Dish.objects.filter(name='鱼香肉丝').exists())
        dish = Dish.objects.get(name='鱼香肉丝')
        self.assertEqual(dish.created_by, self.user)
        self.assertEqual(dish.stock_in_date, date.today())

    def test_create_post_with_image_upload(self):
        upload = SimpleUploadedFile('dish.png', _make_image().getvalue(), content_type='image/png')
        resp = self.client.post(reverse('dishes:create'), {
            'name': '带图食材',
            'category': self.cat.id,
            'stock_in_date': date.today().isoformat(),
            'unit': '斤',
            'storage': '冷藏',
            'is_active': 'on',
            'image': upload,
        })
        self.assertRedirects(resp, reverse('dishes:list'))
        dish = Dish.objects.get(name='带图食材')
        self.assertTrue(dish.image.name.startswith('dish_images/'))

    def test_recognize_image_prefill_payload(self):
        from apps.ocr.models import VisionProviderConfig
        from apps.ocr.vision import VisionDishRecognitionResult

        category, _ = DishCategory.objects.get_or_create(name='蔬果类')
        VisionProviderConfig.objects.create(
            pk=1,
            enabled=True,
            provider=VisionProviderConfig.PROVIDER_OPENAI,
            api_key='test-key',
            model='gpt-4o-mini',
            prompt=VisionProviderConfig.DEFAULT_PROMPT,
        )
        upload = SimpleUploadedFile('tomato.png', _make_image().getvalue(), content_type='image/png')
        result = VisionDishRecognitionResult(
            name='西红柿',
            category='蔬果类',
            unit='斤',
            specification='约500g',
            default_price=6.5,
            storage='冷藏',
            description='成熟番茄',
            raw_text='西红柿 500g',
            provider_label='OpenAI',
            confidence=0.92,
        )

        with patch('apps.dishes.views.recognize_dish_image_with_vision', return_value=result) as mock_recognize:
            resp = self.client.post(reverse('dishes:recognize_image'), {'image': upload})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['fields']['name'], '西红柿')
        self.assertEqual(data['fields']['category_id'], category.id)
        self.assertEqual(data['fields']['unit'], '斤')
        self.assertEqual(data['fields']['specification'], '约500g')
        self.assertEqual(data['fields']['default_price'], '6.5')
        self.assertEqual(data['fields']['storage'], '冷藏')
        self.assertEqual(data['fields']['description'], '成熟番茄')
        self.assertEqual(mock_recognize.call_count, 1)

    @override_settings(VISION_PROVIDER_PRESET={})
    def test_recognize_image_requires_vision_config(self):
        upload = SimpleUploadedFile('dish.png', _make_image().getvalue(), content_type='image/png')
        resp = self.client.post(reverse('dishes:recognize_image'), {'image': upload})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['ok'])
        self.assertIn('视觉辅助配置未完整', resp.json()['error'])

    def test_recognize_image_rejects_missing_file(self):
        resp = self.client.post(reverse('dishes:recognize_image'))
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['ok'])

    def test_create_post_duplicate_name(self):
        Dish.objects.create(name='鱼香肉丝', created_by=self.user)
        resp = self.client.post(reverse('dishes:create'), {
            'name': '鱼香肉丝',
            'stock_in_date': date.today().isoformat(),
            'unit': '份',
            'is_active': 'on',
        })
        self.assertEqual(resp.status_code, 200)  # 表单重新渲染
        self.assertEqual(Dish.objects.filter(name='鱼香肉丝').count(), 1)

    def test_create_post_missing_name(self):
        resp = self.client.post(reverse('dishes:create'), {
            'unit': '份',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Dish.objects.count(), 0)

    def test_create_post_with_stock_in_date(self):
        custom_date = date.today() - timedelta(days=5)
        resp = self.client.post(reverse('dishes:create'), {
            'name': '测试入库日期食材',
            'category': self.cat.id,
            'stock_in_date': custom_date.isoformat(),
            'unit': '斤',
            'storage': '常温',
            'is_active': 'on',
        })
        self.assertRedirects(resp, reverse('dishes:list'))
        dish = Dish.objects.get(name='测试入库日期食材')
        self.assertEqual(dish.stock_in_date, custom_date)

    def test_days_in_stock_property(self):
        dish = Dish.objects.create(
            name='存放三天食材',
            category=self.cat,
            stock_in_date=date.today() - timedelta(days=3),
            created_by=self.user,
        )
        self.assertEqual(dish.days_in_stock, 3)

    # ── 详情 ──
    def test_detail(self):
        dish = Dish.objects.create(name='宫保鸡丁', category=self.cat, created_by=self.user)
        resp = self.client.get(reverse('dishes:detail', args=[dish.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '宫保鸡丁')

    def test_detail_404(self):
        resp = self.client.get(reverse('dishes:detail', args=[99999]))
        self.assertEqual(resp.status_code, 404)

    def test_detail_with_orders(self):
        dish = Dish.objects.create(name='宫保鸡丁', category=self.cat, created_by=self.user)
        order = Order.objects.create(order_date=date.today(), created_by=self.user)
        OrderItem.objects.create(order=order, dish=dish, dish_name='宫保鸡丁', quantity=2)
        resp = self.client.get(reverse('dishes:detail', args=[dish.pk]))
        self.assertContains(resp, '宫保鸡丁')

    def test_detail_shows_days_in_stock(self):
        dish = Dish.objects.create(
            name='详情页入库天数',
            category=self.cat,
            stock_in_date=date.today() - timedelta(days=2),
            created_by=self.user,
        )
        resp = self.client.get(reverse('dishes:detail', args=[dish.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '已买入天数')
        self.assertContains(resp, str(dish.days_in_stock))

    def test_detail_shows_direct_deactivation_buttons(self):
        dish = Dish.objects.create(name='按钮测试食材', category=self.cat, is_active=True, created_by=self.user)
        resp = self.client.get(reverse('dishes:detail', args=[dish.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '吃完了')
        self.assertContains(resp, '丢掉了')
        self.assertNotContains(resp, '停用此食材')

    # ── 编辑 ──
    def test_edit_get(self):
        dish = Dish.objects.create(name='宫保鸡丁', category=self.cat, created_by=self.user)
        resp = self.client.get(reverse('dishes:edit', args=[dish.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '编辑食材')
        self.assertContains(resp, '宫保鸡丁')

    def test_edit_post(self):
        dish = Dish.objects.create(name='宫保鸡丁', category=self.cat, created_by=self.user)
        resp = self.client.post(reverse('dishes:edit', args=[dish.pk]), {
            'name': '宫保鸡丁改',
            'category': self.cat.id,
            'stock_in_date': (date.today() - timedelta(days=1)).isoformat(),
            'unit': '斤',
            'storage': '冷藏',
            'is_active': 'on',
        })
        self.assertRedirects(resp, reverse('dishes:detail', args=[dish.pk]))
        dish.refresh_from_db()
        self.assertEqual(dish.name, '宫保鸡丁改')

    def test_edit_404(self):
        resp = self.client.get(reverse('dishes:edit', args=[99999]))
        self.assertEqual(resp.status_code, 404)

    # ── 删除(停用) ──
    def test_delete_post(self):
        dish = Dish.objects.create(name='宫保鸡丁', category=self.cat, is_active=True, created_by=self.user)
        resp = self.client.post(reverse('dishes:delete', args=[dish.pk]))
        self.assertRedirects(resp, reverse('dishes:list'))
        dish.refresh_from_db()
        self.assertFalse(dish.is_active)
        self.assertEqual(dish.deactivation_reason, 'eaten')
        self.assertEqual(dish.deactivated_at, date.today())

    def test_mark_discarded_post(self):
        dish = Dish.objects.create(name='过期牛奶', category=self.cat, is_active=True, created_by=self.user)
        resp = self.client.post(reverse('dishes:mark_discarded', args=[dish.pk]))
        self.assertRedirects(resp, reverse('dishes:list'))
        dish.refresh_from_db()
        self.assertFalse(dish.is_active)
        self.assertEqual(dish.deactivation_reason, 'discarded')
        self.assertEqual(dish.deactivated_at, date.today())

    def test_bulk_mark_eaten_post(self):
        dish1 = Dish.objects.create(name='批量吃完A', category=self.cat, is_active=True, created_by=self.user)
        dish2 = Dish.objects.create(name='批量吃完B', category=self.cat, is_active=True, created_by=self.user)
        resp = self.client.post(reverse('dishes:bulk_mark_eaten'), {
            'dish_ids': f'{dish1.pk},{dish2.pk}',
            'next': reverse('dishes:list'),
        })
        self.assertRedirects(resp, reverse('dishes:list'))
        dish1.refresh_from_db()
        dish2.refresh_from_db()
        self.assertFalse(dish1.is_active)
        self.assertFalse(dish2.is_active)
        self.assertEqual(dish1.deactivation_reason, 'eaten')
        self.assertEqual(dish2.deactivation_reason, 'eaten')
        self.assertEqual(dish1.deactivated_at, date.today())

    def test_bulk_mark_discarded_post(self):
        dish1 = Dish.objects.create(name='批量丢弃A', category=self.cat, is_active=True, created_by=self.user)
        dish2 = Dish.objects.create(name='批量丢弃B', category=self.cat, is_active=True, created_by=self.user)
        resp = self.client.post(reverse('dishes:bulk_mark_discarded'), {
            'dish_ids': f'{dish1.pk},{dish2.pk}',
            'next': reverse('dishes:list'),
        })
        self.assertRedirects(resp, reverse('dishes:list'))
        dish1.refresh_from_db()
        dish2.refresh_from_db()
        self.assertFalse(dish1.is_active)
        self.assertFalse(dish2.is_active)
        self.assertEqual(dish1.deactivation_reason, 'discarded')
        self.assertEqual(dish2.deactivation_reason, 'discarded')
        self.assertEqual(dish1.deactivated_at, date.today())

    def test_bulk_mark_without_ids_keeps_data(self):
        dish = Dish.objects.create(name='批量空选择', category=self.cat, is_active=True, created_by=self.user)
        resp = self.client.post(reverse('dishes:bulk_mark_eaten'), {
            'dish_ids': '',
            'next': reverse('dishes:list'),
        })
        self.assertRedirects(resp, reverse('dishes:list'))
        dish.refresh_from_db()
        self.assertTrue(dish.is_active)

    def test_delete_get_no_action(self):
        dish = Dish.objects.create(name='宫保鸡丁', category=self.cat, is_active=True, created_by=self.user)
        resp = self.client.get(reverse('dishes:delete', args=[dish.pk]))
        self.assertRedirects(resp, reverse('dishes:list'))
        dish.refresh_from_db()
        self.assertTrue(dish.is_active)

    def test_search_api_removed(self):
        Dish.objects.create(name='宫保鸡丁', created_by=self.user)
        resp = self.client.get('/dish/search/?q=鸡丁')
        self.assertEqual(resp.status_code, 404)

    def test_list_contains_bulk_selection_ui(self):
        Dish.objects.create(name='长按测试食材', category=self.cat, created_by=self.user)
        resp = self.client.get(reverse('dishes:list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '长按食材进入多选模式')
        self.assertContains(resp, '批量吃完了')
        self.assertContains(resp, '批量丢掉了')


# ════════════════════════════════════════════════════════════
# 4. 订单管理
# ════════════════════════════════════════════════════════════
class OrderTestCase(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='tester', password='test1234')
        self.client.login(username='tester', password='test1234')
        self.cat = DishCategory.objects.create(name='荤菜')

    def _create_order(self, **kwargs):
        defaults = {
            'order_date': date.today(),
            'source': 'ocr',
            'created_by': self.user,
            'total_items': 2,
            'total_amount': Decimal('50.00'),
        }
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    # ── 列表 ──
    def test_list_empty(self):
        resp = self.client.get(reverse('orders:list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '暂无订单')

    def test_list_with_orders(self):
        self._create_order()
        resp = self.client.get(reverse('orders:list'))
        self.assertEqual(resp.status_code, 200)
        # Django zh-hans 本地化日期格式为 "2026年2月19日"
        self.assertContains(resp, '道食材')

    def test_list_date_filter(self):
        self._create_order(order_date=date(2025, 1, 1))
        self._create_order(order_date=date(2025, 6, 1))
        resp = self.client.get(reverse('orders:list') + '?date_from=2025-06-01')
        self.assertContains(resp, '2025-06-01')
        self.assertNotContains(resp, '2025-01-01')

    def test_list_pagination(self):
        for i in range(25):
            self._create_order(order_date=date.today() - timedelta(days=i))
        resp = self.client.get(reverse('orders:list'))
        self.assertEqual(resp.status_code, 200)
        # page 2
        resp = self.client.get(reverse('orders:list') + '?page=2')
        self.assertEqual(resp.status_code, 200)

    # ── 详情 ──
    def test_detail(self):
        order = self._create_order()
        dish = Dish.objects.create(name='宫保鸡丁', created_by=self.user)
        OrderItem.objects.create(
            order=order, dish=dish, dish_name='宫保鸡丁',
            quantity=2, unit_price=Decimal('25'), subtotal=Decimal('50')
        )
        resp = self.client.get(reverse('orders:detail', args=[order.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '宫保鸡丁')
        self.assertContains(resp, '订单详情')

    def test_detail_404(self):
        resp = self.client.get(reverse('orders:detail', args=[99999]))
        self.assertEqual(resp.status_code, 404)

    # ── 删除 ──
    def test_delete_post(self):
        order = self._create_order()
        resp = self.client.post(reverse('orders:delete', args=[order.pk]))
        self.assertRedirects(resp, reverse('orders:list'))
        self.assertFalse(Order.objects.filter(pk=order.pk).exists())

    def test_delete_post_reverses_statistics_and_dish_total(self):
        dish = Dish.objects.create(
            name='统计回滚食材',
            total_ordered=Decimal('3.50'),
            created_by=self.user,
        )
        order = self._create_order(total_items=1, total_amount=Decimal('25.00'))
        OrderItem.objects.create(
            order=order,
            dish=dish,
            dish_name=dish.name,
            quantity=Decimal('1.50'),
            unit_price=Decimal('10.00'),
            subtotal=Decimal('15.00'),
            is_matched=True,
        )
        DailyDishStatistic.objects.create(
            dish=dish,
            stat_date=order.order_date,
            total_quantity=Decimal('1.50'),
            order_count=1,
            total_amount=Decimal('15.00'),
        )

        resp = self.client.post(reverse('orders:delete', args=[order.pk]))

        self.assertRedirects(resp, reverse('orders:list'))
        self.assertFalse(Order.objects.filter(pk=order.pk).exists())
        dish.refresh_from_db()
        self.assertEqual(dish.total_ordered, Decimal('2.00'))
        self.assertFalse(DailyDishStatistic.objects.filter(dish=dish, stat_date=order.order_date).exists())

    def test_delete_get_no_action(self):
        order = self._create_order()
        resp = self.client.get(reverse('orders:delete', args=[order.pk]))
        self.assertRedirects(resp, reverse('orders:list'))
        # GET 请求不应该删除
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())

    # ── 创建（跳转 OCR） ──
    def test_create_redirects_to_ocr(self):
        resp = self.client.get(reverse('orders:create'))
        self.assertRedirects(resp, reverse('ocr:upload'))


# ════════════════════════════════════════════════════════════
# 5. 统计分析
# ════════════════════════════════════════════════════════════
class StatisticsTestCase(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='tester', password='test1234')
        self.client.login(username='tester', password='test1234')

    def test_statistics_empty(self):
        resp = self.client.get(reverse('orders:statistics'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '统计分析')
        self.assertContains(resp, '暂无统计数据')

    def test_statistics_with_data(self):
        dish = Dish.objects.create(name='红烧肉', created_by=self.user)
        DailyDishStatistic.objects.create(
            dish=dish, stat_date=date.today(),
            total_quantity=5, order_count=2, total_amount=Decimal('100.00')
        )
        resp = self.client.get(reverse('orders:statistics'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '红烧肉')

    def test_statistics_price_dimensions(self):
        category, _ = DishCategory.objects.get_or_create(name='蔬果类')
        dish = Dish.objects.create(name='香菜', category=category, created_by=self.user)
        DailyDishStatistic.objects.create(
            dish=dish,
            stat_date=date.today(),
            total_quantity=3,
            order_count=1,
            total_amount=Decimal('12.30'),
        )
        Order.objects.create(
            order_date=date.today(),
            source='ocr',
            created_by=self.user,
            total_items=1,
            total_amount=Decimal('12.30'),
        )
        Order.objects.create(
            order_date=date.today(),
            source='manual',
            created_by=self.user,
            total_items=1,
            total_amount=Decimal('15.00'),
        )

        resp = self.client.get(reverse('orders:statistics'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '金额趋势')
        self.assertContains(resp, '价格维度分析')
        self.assertContains(resp, '"category"')
        self.assertContains(resp, '"source"')
        self.assertContains(resp, '"dish"')
        self.assertContains(resp, '香菜')

    def test_statistics_day_range(self):
        for days_param in [7, 14, 30]:
            resp = self.client.get(reverse('orders:statistics') + f'?days={days_param}')
            self.assertEqual(resp.status_code, 200)

    def test_statistics_discard_analysis(self):
        category, _ = DishCategory.objects.get_or_create(name='乳品饮料')
        Dish.objects.create(
            name='过期酸奶',
            category=category,
            default_price=Decimal('8.80'),
            is_active=False,
            deactivation_reason='discarded',
            deactivated_at=date.today(),
            created_by=self.user,
        )
        resp = self.client.get(reverse('orders:statistics'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '食材丢弃趋势')
        self.assertContains(resp, '食材丢弃金额趋势')
        self.assertContains(resp, '丢弃分类分布')
        self.assertContains(resp, '丢弃分类金额分布')
        self.assertContains(resp, '丢弃金额(估算)')
        self.assertContains(resp, '过期酸奶')
        self.assertContains(resp, 'discardCategoryPayload')
        self.assertContains(resp, 'discardAmountDailyData')
        self.assertContains(resp, 'discardCategoryAmountPayload')

    def test_statistics_invalid_days(self):
        resp = self.client.get(reverse('orders:statistics') + '?days=abc')
        self.assertEqual(resp.status_code, 200)  # 应回退为 7 天


# ════════════════════════════════════════════════════════════
# 6. OCR 模块
# ════════════════════════════════════════════════════════════
class OcrTestCase(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='tester', password='test1234')
        self.client.login(username='tester', password='test1234')

    # ── 上传页 ──
    def test_upload_page(self):
        resp = self.client.get(reverse('ocr:upload'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '订单识别')
        self.assertContains(resp, '开始识别')
        self.assertContains(resp, 'OCR识别与辅助判断')
        self.assertNotContains(resp, '使用视觉辅助识别')
        self.assertNotContains(resp, 'vision-process')

    def test_vision_settings_page(self):
        resp = self.client.get(reverse('ocr:vision_settings'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '视觉辅助设置')
        self.assertContains(resp, '检查只验证本地字段完整性')
        self.assertContains(resp, 'RPM')
        self.assertNotContains(resp, '本地预置配置')
        self.assertNotContains(resp, '应用本地预置')

    def test_vision_settings_preloads_env_preset(self):
        from apps.ocr.models import VisionProviderConfig

        with override_settings(VISION_PROVIDER_PRESET={
            'enabled': True,
            'provider': VisionProviderConfig.PROVIDER_OPENAI_COMPATIBLE,
            'provider_name': 'Env 测试服务',
            'api_key': 'env-test-key',
            'base_url': 'https://api.env.test/v1',
            'model': 'env-vision-model',
            'prompt': '只返回 JSON',
            'timeout_seconds': 45,
            'requests_per_minute': 4,
        }):
            resp = self.client.get(reverse('ocr:vision_settings'))

        self.assertEqual(resp.status_code, 200)
        config = VisionProviderConfig.objects.get(pk=1)
        self.assertTrue(config.enabled)
        self.assertEqual(config.provider, VisionProviderConfig.PROVIDER_OPENAI_COMPATIBLE)
        self.assertEqual(config.provider_name, 'Env 测试服务')
        self.assertEqual(config.api_key, 'env-test-key')
        self.assertEqual(config.base_url, 'https://api.env.test/v1')
        self.assertEqual(config.model, 'env-vision-model')
        self.assertEqual(config.timeout_seconds, 45)
        self.assertEqual(config.requests_per_minute, 4)

    def test_vision_settings_saved_config_overrides_env_preset(self):
        from apps.ocr.models import VisionProviderConfig

        with override_settings(VISION_PROVIDER_PRESET={
            'enabled': True,
            'provider': VisionProviderConfig.PROVIDER_OPENAI_COMPATIBLE,
            'provider_name': 'Env 测试服务',
            'api_key': 'env-test-key',
            'base_url': 'https://api.env.test/v1',
            'model': 'env-vision-model',
            'prompt': 'env prompt',
            'timeout_seconds': 45,
            'requests_per_minute': 4,
        }):
            VisionProviderConfig.get_solo()
            resp = self.client.post(reverse('ocr:vision_settings'), {
                'action': 'save',
                'enabled': 'on',
                'provider': VisionProviderConfig.PROVIDER_OPENAI_COMPATIBLE,
                'provider_name': '页面保存服务',
                'api_key': 'page-test-key',
                'base_url': 'https://api.page.test/v1',
                'model': 'page-vision-model',
                'prompt': VisionProviderConfig.DEFAULT_PROMPT,
                'timeout_seconds': '30',
                'requests_per_minute': '7',
            })
            self.assertRedirects(resp, reverse('ocr:vision_settings'))
            config = VisionProviderConfig.get_solo()

        self.assertEqual(config.provider_name, '页面保存服务')
        self.assertEqual(config.api_key, 'page-test-key')
        self.assertEqual(config.base_url, 'https://api.page.test/v1')
        self.assertEqual(config.model, 'page-vision-model')
        self.assertEqual(config.timeout_seconds, 30)
        self.assertEqual(config.requests_per_minute, 7)

    def test_vision_settings_save_openai_compatible(self):
        from apps.ocr.models import VisionProviderConfig

        resp = self.client.post(reverse('ocr:vision_settings'), {
            'action': 'save',
            'enabled': 'on',
            'provider': VisionProviderConfig.PROVIDER_OPENAI_COMPATIBLE,
            'provider_name': '测试第三方',
            'api_key': 'test-key',
            'base_url': 'https://api.example.com/v1',
            'model': 'gpt-4o-mini',
            'prompt': VisionProviderConfig.DEFAULT_PROMPT,
            'timeout_seconds': '30',
            'requests_per_minute': '6',
        })

        self.assertRedirects(resp, reverse('ocr:vision_settings'))
        config = VisionProviderConfig.get_solo()
        self.assertTrue(config.enabled)
        self.assertEqual(config.provider, VisionProviderConfig.PROVIDER_OPENAI_COMPATIBLE)
        self.assertEqual(config.provider_name, '测试第三方')
        self.assertEqual(config.base_url, 'https://api.example.com/v1')
        self.assertEqual(config.api_key, 'test-key')
        self.assertEqual(config.requests_per_minute, 6)

    def test_vision_settings_check_anthropic_model_hint(self):
        from apps.ocr.models import VisionProviderConfig

        resp = self.client.post(reverse('ocr:vision_settings'), {
            'action': 'check',
            'enabled': 'on',
            'provider': VisionProviderConfig.PROVIDER_ANTHROPIC,
            'api_key': 'test-key',
            'base_url': '',
            'model': 'gpt-4o-mini',
            'prompt': VisionProviderConfig.DEFAULT_PROMPT,
            'timeout_seconds': '30',
            'requests_per_minute': '5',
        })

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Anthropic 需要填写 Claude 视觉模型名称')

    def test_vision_rate_limit_blocks_after_configured_rpm(self):
        from apps.ocr.models import VisionProviderConfig
        from apps.ocr.vision import _RATE_LIMIT_WINDOWS, VisionProviderError, recognize_order_image_with_vision

        _RATE_LIMIT_WINDOWS.clear()
        config = VisionProviderConfig(
            enabled=True,
            provider=VisionProviderConfig.PROVIDER_OPENAI,
            api_key='test-key',
            model='gpt-4o-mini',
            prompt=VisionProviderConfig.DEFAULT_PROMPT,
            timeout_seconds=30,
            requests_per_minute=5,
        )
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            tmp.write(_make_image().getvalue())
            image_path = Path(tmp.name)

        response = {'choices': [{'message': {'content': '{"items":[],"raw_text":"ok"}'}}]}
        try:
            with patch('apps.ocr.vision._post_json', return_value=response) as mock_post:
                for _ in range(5):
                    recognize_order_image_with_vision(config, image_path)
                with self.assertRaisesMessage(VisionProviderError, '每分钟 5 次'):
                    recognize_order_image_with_vision(config, image_path)
            self.assertEqual(mock_post.call_count, 5)
        finally:
            _RATE_LIMIT_WINDOWS.clear()
            image_path.unlink(missing_ok=True)

    def test_vision_extracts_json_after_think_prefix(self):
        from apps.ocr.models import VisionProviderConfig
        from apps.ocr.vision import _RATE_LIMIT_WINDOWS, recognize_order_image_with_vision

        _RATE_LIMIT_WINDOWS.clear()
        config = VisionProviderConfig(
            enabled=True,
            provider=VisionProviderConfig.PROVIDER_OPENAI,
            api_key='test-key',
            model='gpt-4o-mini',
            prompt=VisionProviderConfig.DEFAULT_PROMPT,
            timeout_seconds=30,
            requests_per_minute=5,
        )
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            tmp.write(_make_image().getvalue())
            image_path = Path(tmp.name)

        content = (
            '<think>先分析截图，但这些内容不应该影响 JSON 提取。</think>\n'
            '{"items":[{"dish_name":"虾仁","quantity":1,"unit_price":99.9,"subtotal":99.9}],'
            '"raw_text":"虾仁 99.9"}'
        )
        response = {'choices': [{'message': {'content': content}}]}
        try:
            with patch('apps.ocr.vision._post_json', return_value=response):
                result = recognize_order_image_with_vision(config, image_path)
        finally:
            _RATE_LIMIT_WINDOWS.clear()
            image_path.unlink(missing_ok=True)

        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].dish_name, '虾仁')
        self.assertEqual(result.items[0].subtotal, 99.9)

    def test_vision_recognizes_single_dish_fields(self):
        from apps.ocr.models import VisionProviderConfig
        from apps.ocr.vision import _RATE_LIMIT_WINDOWS, recognize_dish_image_with_vision

        _RATE_LIMIT_WINDOWS.clear()
        config = VisionProviderConfig(
            enabled=True,
            provider=VisionProviderConfig.PROVIDER_OPENAI,
            api_key='test-key',
            model='gpt-4o-mini',
            prompt=VisionProviderConfig.DEFAULT_PROMPT,
            timeout_seconds=30,
            requests_per_minute=5,
        )
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            tmp.write(_make_image().getvalue())
            image_path = Path(tmp.name)

        content = (
            '{"name":"虾仁","category":"水产海鲜","unit":"袋","specification":"300g/袋",'
            '"default_price":19.9,"storage":"冷冻","description":"冷冻虾仁","confidence":0.86,'
            '"raw_text":"虾仁 300g"}'
        )
        response = {'choices': [{'message': {'content': content}}]}
        try:
            with patch('apps.ocr.vision._post_json', return_value=response) as mock_post:
                result = recognize_dish_image_with_vision(config, image_path)
        finally:
            _RATE_LIMIT_WINDOWS.clear()
            image_path.unlink(missing_ok=True)

        self.assertEqual(result.name, '虾仁')
        self.assertEqual(result.category, '水产海鲜')
        self.assertEqual(result.unit, '袋')
        self.assertEqual(result.specification, '300g/袋')
        self.assertEqual(result.default_price, 19.9)
        self.assertEqual(result.storage, '冷冻')
        self.assertEqual(result.confidence, 0.86)
        payload = mock_post.call_args.args[1]
        self.assertIn('食材照片结构化识别助手', payload['messages'][0]['content'][0]['text'])

    def test_manual_vision_process_endpoint_removed(self):
        upload = SimpleUploadedFile('order.png', _make_image().getvalue(), content_type='image/png')
        resp = self.client.post('/ocr/vision-process/', {'image': upload})
        self.assertEqual(resp.status_code, 404)

    def test_process_auto_falls_back_to_vision_when_ocr_empty(self):
        from apps.ocr.models import VisionProviderConfig
        from apps.ocr.parser import ParsedOrderItem
        from apps.ocr.vision import VisionRecognitionResult

        VisionProviderConfig.objects.create(
            pk=1,
            enabled=True,
            provider=VisionProviderConfig.PROVIDER_OPENAI,
            api_key='test-key',
            model='gpt-4o-mini',
            prompt=VisionProviderConfig.DEFAULT_PROMPT,
        )
        upload = SimpleUploadedFile('order.png', _make_image().getvalue(), content_type='image/png')
        result = VisionRecognitionResult(
            items=[ParsedOrderItem(dish_name='土豆', quantity=1, unit_price=4, subtotal=4)],
            raw_text='{"items":[{"dish_name":"土豆"}]}',
            provider_label='OpenAI',
        )

        with patch('apps.ocr.engine.recognize_image', return_value=[]), \
                patch('apps.ocr.views.recognize_order_image_with_vision', return_value=result) as mock_recognize:
            resp = self.client.post(reverse('ocr:process'), {'image': upload})

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'OpenAI 视觉辅助（自动）')
        self.assertContains(resp, '土豆')
        self.assertEqual(mock_recognize.call_count, 1)

    # ── process: GET 重定向 ──
    def test_process_get_redirects(self):
        resp = self.client.get(reverse('ocr:process'))
        self.assertRedirects(resp, reverse('ocr:upload'))

    # ── process: 无图片 ──
    def test_process_no_image(self):
        resp = self.client.post(reverse('ocr:process'))
        self.assertRedirects(resp, reverse('ocr:upload'))

    # ── confirm: GET 重定向 ──
    def test_confirm_get_redirects(self):
        resp = self.client.get(reverse('ocr:confirm'))
        self.assertRedirects(resp, reverse('ocr:upload'))

    # ── confirm: 空表单 ──
    def test_confirm_empty(self):
        resp = self.client.post(reverse('ocr:confirm'), {
            'order_date': date.today().isoformat(),
            'image_path': '',
            'raw_text': '',
        })
        self.assertRedirects(resp, reverse('ocr:upload'))

    # ── confirm: 正常提交 ──
    def test_confirm_creates_order(self):
        dish = Dish.objects.create(name='宫保鸡丁', created_by=self.user)
        resp = self.client.post(reverse('ocr:confirm'), {
            'order_date': date.today().isoformat(),
            'image_path': '',
            'raw_text': '测试文本',
            'dish_name[]': ['宫保鸡丁', '水煮鱼'],
            'quantity[]': ['2', '1'],
            'unit_price[]': ['25', '35'],
            'dish_id[]': [str(dish.pk), ''],
        })
        self.assertEqual(resp.status_code, 302)
        order = Order.objects.first()
        self.assertIsNotNone(order)
        self.assertEqual(order.total_items, 2)
        self.assertEqual(order.items.count(), 2)

    def test_confirm_with_auto_create(self):
        resp = self.client.post(reverse('ocr:confirm'), {
            'order_date': date.today().isoformat(),
            'image_path': '',
            'raw_text': '',
            'auto_create_dish': 'on',
            'dish_name[]': ['全新食材A', '全新食材B'],
            'quantity[]': ['1', '3'],
            'unit_price[]': ['20', '15'],
            'dish_id[]': ['', ''],
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Dish.objects.filter(name='全新食材A').exists())
        self.assertTrue(Dish.objects.filter(name='全新食材B').exists())

    def test_confirm_auto_create_uses_order_date_as_stock_in_date(self):
        order_date = date.today() - timedelta(days=6)
        resp = self.client.post(reverse('ocr:confirm'), {
            'order_date': order_date.isoformat(),
            'image_path': '',
            'raw_text': '',
            'auto_create_dish': 'on',
            'dish_name[]': ['按订单日期入库食材'],
            'quantity[]': ['1'],
            'unit_price[]': ['9.9'],
            'dish_id[]': [''],
        })
        self.assertEqual(resp.status_code, 302)
        dish = Dish.objects.get(name='按订单日期入库食材')
        self.assertEqual(dish.stock_in_date, order_date)

    def test_confirm_auto_create_assigns_category(self):
        DishCategory.objects.get_or_create(name='蔬果类', defaults={'icon': '🥬', 'sort_order': 2})
        DishCategory.objects.get_or_create(name='调料免费', defaults={'icon': '🧂', 'sort_order': 7})
        DishCategory.objects.get_or_create(name='乳品饮料', defaults={'icon': '🥛', 'sort_order': 9})

        resp = self.client.post(reverse('ocr:confirm'), {
            'order_date': date.today().isoformat(),
            'image_path': '',
            'raw_text': '',
            'auto_create_dish': 'on',
            'dish_name[]': ['香菜100g', '纯牛奶250ml'],
            'quantity[]': ['1', '1'],
            'unit_price[]': ['4.5', '12.8'],
            'dish_id[]': ['', ''],
        })
        self.assertEqual(resp.status_code, 302)

        coriander = Dish.objects.get(name='香菜100g')
        milk = Dish.objects.get(name='纯牛奶250ml')
        self.assertIsNotNone(coriander.category)
        self.assertIsNotNone(milk.category)
        self.assertEqual(coriander.category.name, '调料免费')
        self.assertEqual(milk.category.name, '乳品饮料')

    def test_confirm_fill_category_for_uncategorized_existing_dish(self):
        DishCategory.objects.get_or_create(name='蔬果类', defaults={'icon': '🥬', 'sort_order': 2})
        DishCategory.objects.get_or_create(name='调料免费', defaults={'icon': '🧂', 'sort_order': 7})
        dish = Dish.objects.create(name='香菜', category=None, created_by=self.user)

        resp = self.client.post(reverse('ocr:confirm'), {
            'order_date': date.today().isoformat(),
            'image_path': '',
            'raw_text': '',
            'dish_name[]': ['香菜'],
            'quantity[]': ['2'],
            'unit_price[]': ['3.5'],
            'dish_id[]': [str(dish.pk)],
        })
        self.assertEqual(resp.status_code, 302)

        dish.refresh_from_db()
        self.assertIsNotNone(dish.category)
        self.assertEqual(dish.category.name, '调料免费')

    def test_confirm_updates_statistics(self):
        dish = Dish.objects.create(name='红烧肉', created_by=self.user)
        self.client.post(reverse('ocr:confirm'), {
            'order_date': date.today().isoformat(),
            'image_path': '',
            'raw_text': '',
            'dish_name[]': ['红烧肉'],
            'quantity[]': ['3'],
            'unit_price[]': ['28'],
            'dish_id[]': [str(dish.pk)],
        })
        stat = DailyDishStatistic.objects.get(dish=dish, stat_date=date.today())
        self.assertEqual(stat.total_quantity, Decimal('3'))
        self.assertEqual(stat.order_count, 1)
        dish.refresh_from_db()
        self.assertEqual(dish.total_ordered, Decimal('3.00'))

    def test_confirm_preserves_fractional_total_ordered(self):
        dish = Dish.objects.create(name='半份食材', created_by=self.user)
        self.client.post(reverse('ocr:confirm'), {
            'order_date': date.today().isoformat(),
            'image_path': '',
            'raw_text': '',
            'dish_name[]': ['半份食材'],
            'quantity[]': ['0.5'],
            'unit_price[]': ['8'],
            'dish_id[]': [str(dish.pk)],
        })

        dish.refresh_from_db()
        stat = DailyDishStatistic.objects.get(dish=dish, stat_date=date.today())
        self.assertEqual(dish.total_ordered, Decimal('0.50'))
        self.assertEqual(stat.total_quantity, Decimal('0.50'))

    def test_confirm_invalid_date_uses_today(self):
        resp = self.client.post(reverse('ocr:confirm'), {
            'order_date': 'invalid-date',
            'image_path': '',
            'raw_text': '',
            'dish_name[]': ['测试菜'],
            'quantity[]': ['1'],
            'unit_price[]': ['10'],
            'dish_id[]': [''],
            'auto_create_dish': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        order = Order.objects.first()
        self.assertEqual(order.order_date, date.today())

    def test_ocr_engine_uses_safe_cpu_config(self):
        from apps.ocr import engine
        with patch('paddleocr.PaddleOCR') as mock_ocr:
            engine._ocr_instance = None
            try:
                instance = engine.get_ocr_engine()
            finally:
                engine._ocr_instance = None

        self.assertIs(instance, mock_ocr.return_value)
        mock_ocr.assert_called_once_with(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            ocr_version='PP-OCRv4',
            enable_mkldnn=False,
        )


# ════════════════════════════════════════════════════════════
# 7. OCR 文本解析器（单元测试）
# ════════════════════════════════════════════════════════════
class ParserTestCase(TestCase):

    def test_parse_basic_items(self):
        from apps.ocr.parser import parse_order_text
        lines = [
            {'text': '宫保鸡丁 x2 ¥28.00', 'confidence': 0.95, 'y_pos': 100},
            {'text': '水煮鱼 x1 ¥35.00', 'confidence': 0.92, 'y_pos': 200},
        ]
        items = parse_order_text(lines)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].dish_name, '宫保鸡丁')
        self.assertEqual(items[0].quantity, 2.0)
        self.assertEqual(items[1].dish_name, '水煮鱼')

    def test_parse_skips_metadata(self):
        from apps.ocr.parser import parse_order_text
        lines = [
            {'text': '订单编号: 12345678', 'confidence': 0.9, 'y_pos': 10},
            {'text': '配送费 ¥5.00', 'confidence': 0.9, 'y_pos': 20},
            {'text': '合计 ¥63.00', 'confidence': 0.9, 'y_pos': 30},
            {'text': '宫保鸡丁 x2 ¥28.00', 'confidence': 0.9, 'y_pos': 100},
            {'text': '地址：某街道', 'confidence': 0.9, 'y_pos': 400},
        ]
        items = parse_order_text(lines)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].dish_name, '宫保鸡丁')

    def test_parse_quantity_formats(self):
        from apps.ocr.parser import parse_order_text
        cases = [
            ('麻婆豆腐 x3', 3.0),
            ('红烧排骨 X2', 2.0),
            ('清炒时蔬 ×5', 5.0),
            ('米饭 3份', 3.0),
            ('可乐 *2', 2.0),
        ]
        for text, expected_qty in cases:
            lines = [{'text': text, 'confidence': 0.9, 'y_pos': 0}]
            items = parse_order_text(lines)
            self.assertEqual(len(items), 1, f'Failed for: {text}')
            self.assertEqual(items[0].quantity, expected_qty, f'Wrong qty for: {text}')

    def test_parse_empty_input(self):
        from apps.ocr.parser import parse_order_text
        items = parse_order_text([])
        self.assertEqual(len(items), 0)

    def test_parse_short_text_skipped(self):
        from apps.ocr.parser import parse_order_text
        lines = [
            {'text': '饭', 'confidence': 0.9, 'y_pos': 0},   # 太短
            {'text': '', 'confidence': 0.9, 'y_pos': 0},     # 空
            {'text': '¥28', 'confidence': 0.9, 'y_pos': 0},  # 纯价格
        ]
        items = parse_order_text(lines)
        self.assertEqual(len(items), 0)

    def test_parse_filters_time_and_delivery_slogan(self):
        from apps.ocr.parser import parse_order_text
        lines = [
            {'text': '09:11', 'confidence': 0.9, 'y_pos': 0},
            {'text': '简家日配达，', 'confidence': 0.9, 'y_pos': 1},
            {'text': '香菜100g 实付￥4.38', 'confidence': 0.9, 'y_pos': 2},
        ]
        items = parse_order_text(lines)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].dish_name, '香菜100g')

    def test_parse_merges_continued_name_with_unclosed_bracket(self):
        from apps.ocr.parser import parse_order_text
        lines = [
            {'text': '火锅组合（包', 'confidence': 0.9, 'y_pos': 0},
            {'text': '浆豆腐+油干片) X1 实付￥8.69', 'confidence': 0.9, 'y_pos': 1},
            {'text': '香菜100g 实付￥4.38', 'confidence': 0.9, 'y_pos': 2},
        ]
        items = parse_order_text(lines)
        self.assertEqual(len(items), 2)
        self.assertIn('火锅组合', items[0].dish_name)
        self.assertIn('浆豆腐', items[0].dish_name)
        self.assertEqual(items[1].dish_name, '香菜100g')

    def test_parse_filters_unit_or_payment_noise(self):
        from apps.ocr.parser import parse_order_text
        lines = [
            {'text': '50mL x1', 'confidence': 0.9, 'y_pos': 0},
            {'text': '冷藏', 'confidence': 0.9, 'y_pos': 1},
            {'text': '商品金额', 'confidence': 0.9, 'y_pos': 2},
            {'text': '实际支付￥52.67', 'confidence': 0.9, 'y_pos': 3},
            {'text': '再次购买', 'confidence': 0.9, 'y_pos': 4},
            {'text': '香菜100g 实付￥4.38', 'confidence': 0.9, 'y_pos': 5},
        ]
        items = parse_order_text(lines)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].dish_name, '香菜100g')

    def test_parse_merges_wrapped_spec_line(self):
        from apps.ocr.parser import parse_order_text
        lines = [
            {'text': '沃集鲜A2β-酪蛋白鲜牛奶9', 'confidence': 0.9, 'y_pos': 100},
            {'text': '冷藏', 'confidence': 0.9, 'y_pos': 103},
            {'text': '￥12.99', 'confidence': 0.9, 'y_pos': 101},
            {'text': '50mL x1', 'confidence': 0.9, 'y_pos': 145},
        ]
        items = parse_order_text(lines)
        self.assertEqual(len(items), 1)
        self.assertIn('50mL', items[0].dish_name)
        self.assertEqual(items[0].unit_price, 12.99)

    def test_parse_merges_wrapped_description_with_qty(self):
        from apps.ocr.parser import parse_order_text
        lines = [
            {'text': '【桶装水】盒马深层天然水5L', 'confidence': 0.9, 'y_pos': 300},
            {'text': '实付￥11.42', 'confidence': 0.9, 'y_pos': 302},
            {'text': '天然矿物质清冽解渴矿泉水 X2', 'confidence': 0.9, 'y_pos': 346},
        ]
        items = parse_order_text(lines)
        self.assertEqual(len(items), 1)
        self.assertIn('盒马深层天然水5L', items[0].dish_name)
        self.assertIn('矿泉水', items[0].dish_name)
        self.assertEqual(items[0].quantity, 2.0)

    def test_parse_positioned_walmart_order_rows(self):
        from apps.ocr.parser import parse_order_text
        lines = [
            {'text': '沃集鲜有机豆乳250mL*3', 'confidence': 0.99, 'x_min': 392, 'x_max': 965, 'x_center': 678, 'y_pos': 233},
            {'text': '￥3.99', 'confidence': 0.94, 'x_min': 1085, 'x_max': 1248, 'x_center': 1166, 'y_pos': 232},
            {'text': 'x1', 'confidence': 0.80, 'x_min': 1186, 'x_max': 1241, 'x_center': 1213, 'y_pos': 323},
            {'text': 'MARKETSIDE', 'confidence': 0.99, 'x_min': 94, 'x_max': 171, 'x_center': 132, 'y_pos': 218},
            {'text': '沃集鲜水牛纯牛奶', 'confidence': 0.99, 'x_min': 392, 'x_max': 800, 'x_center': 596, 'y_pos': 959},
            {'text': '200m', 'confidence': 0.99, 'x_min': 778, 'x_max': 930, 'x_center': 854, 'y_pos': 959},
            {'text': '￥ 29.99', 'confidence': 0.90, 'x_min': 1066, 'x_max': 1235, 'x_center': 1150, 'y_pos': 959},
            {'text': 'L*10盒', 'confidence': 0.99, 'x_min': 389, 'x_max': 535, 'x_center': 462, 'y_pos': 1028},
            {'text': '新旧包装随机发..', 'confidence': 0.97, 'x_min': 580, 'x_max': 957, 'x_center': 768, 'y_pos': 1027},
            {'text': 'X1', 'confidence': 0.92, 'x_min': 1187, 'x_max': 1236, 'x_center': 1211, 'y_pos': 1051},
        ]
        items = parse_order_text(lines)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].dish_name, '沃集鲜有机豆乳250mL*3')
        self.assertEqual(items[0].quantity, 1.0)
        self.assertEqual(items[0].unit_price, 3.99)
        self.assertIn('沃集鲜水牛纯牛奶', items[1].dish_name)
        self.assertIn('200mL*10盒', items[1].dish_name)
        self.assertEqual(items[1].unit_price, 29.99)

    def test_parse_positioned_sam_cart_rows(self):
        from apps.ocr.parser import parse_order_text
        lines = [
            {'text': 'MM进口生冷冻大虾仁908g', 'confidence': 0.99, 'x_min': 458, 'x_max': 984, 'x_center': 721, 'y_pos': 540},
            {'text': '换为极速达', 'confidence': 0.99, 'x_min': 476, 'x_max': 652, 'x_center': 564, 'y_pos': 620},
            {'text': '￥1049→99.9', 'confidence': 0.91, 'x_min': 240, 'x_max': 383, 'x_center': 311, 'y_pos': 739},
            {'text': '￥99.9', 'confidence': 0.92, 'x_min': 453, 'x_max': 579, 'x_center': 516, 'y_pos': 782},
            {'text': 'MM澳洲谷饲牛腩排约700g', 'confidence': 0.99, 'x_min': 458, 'x_max': 980, 'x_center': 719, 'y_pos': 926},
            {'text': '￥104.8', 'confidence': 0.93, 'x_min': 456, 'x_max': 604, 'x_center': 530, 'y_pos': 1170},
        ]
        items = parse_order_text(lines)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].dish_name, 'MM进口生冷冻大虾仁908g')
        self.assertEqual(items[0].unit_price, 99.9)
        self.assertEqual(items[1].dish_name, 'MM澳洲谷饲牛腩排约700g')
        self.assertEqual(items[1].unit_price, 104.8)


# ════════════════════════════════════════════════════════════
# 8. 食材匹配（单元测试）
# ════════════════════════════════════════════════════════════
class DishMatchTestCase(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='tester', password='test1234')

    def test_exact_match(self):
        from apps.dishes.services import match_dish
        Dish.objects.create(name='宫保鸡丁', created_by=self.user)
        did, name, score = match_dish('宫保鸡丁')
        self.assertIsNotNone(did)
        self.assertEqual(name, '宫保鸡丁')
        self.assertEqual(score, 1.0)

    def test_fuzzy_match(self):
        from apps.dishes.services import match_dish
        Dish.objects.create(name='宫保鸡丁', created_by=self.user)
        did, name, score = match_dish('宫爆鸡丁')  # OCR 可能识别为 "爆"
        self.assertIsNotNone(did)
        self.assertGreaterEqual(score, 0.6)

    def test_no_match(self):
        from apps.dishes.services import match_dish
        Dish.objects.create(name='宫保鸡丁', created_by=self.user)
        did, name, score = match_dish('可口可乐')
        self.assertIsNone(did)

    def test_inactive_dish_not_matched(self):
        from apps.dishes.services import match_dish
        Dish.objects.create(name='宫保鸡丁', is_active=False, created_by=self.user)
        did, name, score = match_dish('宫保鸡丁')
        self.assertIsNone(did)

    def test_match_empty_database(self):
        from apps.dishes.services import match_dish
        did, name, score = match_dish('任何食材')
        self.assertIsNone(did)
        self.assertEqual(score, 0)

    def test_infer_dish_category_with_keyword(self):
        from apps.dishes.services import infer_dish_category
        DishCategory.objects.get_or_create(name='蔬果类', defaults={'icon': '🥬', 'sort_order': 2})
        cat = infer_dish_category('菠菜100g')
        self.assertIsNotNone(cat)
        self.assertEqual(cat.name, '蔬果类')

    def test_infer_dish_category_free_seasoning(self):
        from apps.dishes.services import infer_dish_category
        DishCategory.objects.get_or_create(name='蔬果类', defaults={'icon': '🥬', 'sort_order': 2})
        DishCategory.objects.get_or_create(name='调料免费', defaults={'icon': '🧂', 'sort_order': 7})
        cat = infer_dish_category('香菜100g')
        self.assertIsNotNone(cat)
        self.assertEqual(cat.name, '调料免费')

    def test_infer_dish_category_aquatic(self):
        from apps.dishes.services import infer_dish_category
        DishCategory.objects.get_or_create(name='水产海鲜', defaults={'icon': '🐟', 'sort_order': 3})
        cat = infer_dish_category('三文鱼排')
        self.assertIsNotNone(cat)
        self.assertEqual(cat.name, '水产海鲜')

    def test_infer_dish_category_bean_or_mushroom(self):
        from apps.dishes.services import infer_dish_category
        DishCategory.objects.get_or_create(name='豆菌类', defaults={'icon': '🍄', 'sort_order': 4})
        cat = infer_dish_category('白蘑菇200g')
        self.assertIsNotNone(cat)
        self.assertEqual(cat.name, '豆菌类')

    def test_infer_dish_category_fallback_other(self):
        from apps.dishes.services import infer_dish_category
        DishCategory.objects.get_or_create(name='其他', defaults={'icon': '📦', 'sort_order': 99})
        cat = infer_dish_category('神秘食材XYZ')
        self.assertIsNotNone(cat)
        self.assertEqual(cat.name, '其他')


# ════════════════════════════════════════════════════════════
# 9. 菜谱模块（占位页面）
# ════════════════════════════════════════════════════════════
class RecipeTestCase(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='tester', password='test1234')
        self.client.login(username='tester', password='test1234')
        self.recipe = Recipe.objects.create(
            name='测试菜谱', difficulty='中等', created_by=self.user,
        )

    def test_list(self):
        resp = self.client.get(reverse('recipes:list'))
        self.assertEqual(resp.status_code, 200)

    def test_list_prioritizes_recipes_with_more_matched_ingredients(self):
        Dish.objects.create(name='鸡丁', created_by=self.user)
        Dish.objects.create(name='花生米', created_by=self.user)

        r_more = Recipe.objects.create(name='匹配更多菜谱', difficulty='中等', created_by=self.user)
        r_less = Recipe.objects.create(name='匹配较少菜谱', difficulty='中等', created_by=self.user)
        r_none = Recipe.objects.create(name='不匹配菜谱', difficulty='中等', created_by=self.user)

        RecipeIngredient.objects.create(recipe=r_more, name='鸡丁', amount='100g', is_main=True, sort_order=0)
        RecipeIngredient.objects.create(recipe=r_more, name='花生米', amount='50g', is_main=True, sort_order=1)
        RecipeIngredient.objects.create(recipe=r_less, name='鸡丁', amount='100g', is_main=True, sort_order=0)
        RecipeIngredient.objects.create(recipe=r_less, name='未知原料X', amount='30g', is_main=False, sort_order=1)
        RecipeIngredient.objects.create(recipe=r_none, name='神秘原料', amount='10g', is_main=True, sort_order=0)

        resp = self.client.get(reverse('recipes:list'))
        self.assertEqual(resp.status_code, 200)
        names = [r.name for r in resp.context['page_obj'].object_list]
        self.assertLess(names.index('匹配更多菜谱'), names.index('匹配较少菜谱'))
        self.assertLess(names.index('匹配较少菜谱'), names.index('不匹配菜谱'))

    def test_list_search_matches_all_recipe_fields(self):
        cat = RecipeCategory.objects.create(name='测试分类')
        linked_dish = Dish.objects.create(name='番茄', created_by=self.user)
        full_recipe = Recipe.objects.create(
            name='全字段搜索菜谱',
            category=cat,
            dish=linked_dish,
            description='超级下饭',
            tips='少盐更健康',
            difficulty='简单',
            servings=3,
            prep_time_minutes=12,
            cook_time_minutes=18,
            media_type=Recipe.MEDIA_VIDEO,
            media_title='示范视频',
            media_url='https://example.com/full-recipe-video',
            created_by=self.user,
        )
        RecipeIngredient.objects.create(
            recipe=full_recipe,
            name='蒜末',
            amount='2勺',
            is_main=False,
            sort_order=0,
        )
        RecipeStep.objects.create(
            recipe=full_recipe,
            step_number=1,
            description='大火爆香蒜末后翻炒',
        )
        Recipe.objects.create(name='无关菜谱', created_by=self.user)

        hit_queries = [
            '超级下饭', '少盐', '蒜末', '爆香', '测试分类', '番茄',
            '12', '18', '3', '示范视频', 'full-recipe-video',
        ]
        for query in hit_queries:
            resp = self.client.get(reverse('recipes:list') + f'?q={query}')
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, '全字段搜索菜谱')
            self.assertNotContains(resp, '无关菜谱')

    def test_create(self):
        resp = self.client.get(reverse('recipes:create'))
        self.assertEqual(resp.status_code, 200)

    def test_detail(self):
        resp = self.client.get(reverse('recipes:detail', args=[self.recipe.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_edit(self):
        resp = self.client.get(reverse('recipes:edit', args=[self.recipe.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_delete(self):
        resp = self.client.post(reverse('recipes:delete', args=[self.recipe.pk]))
        self.assertRedirects(resp, reverse('recipes:list'))

    def test_create_with_ingredients_and_steps(self):
        resp = self.client.post(reverse('recipes:create'), {
            'name': '红烧肉',
            'difficulty': '中等',
            'servings': 4,
            'is_published': 'on',
            'ingredient_name[]': ['五花肉', '生抽', '老抽'],
            'ingredient_amount[]': ['500g', '2勺', '1勺'],
            'ingredient_is_main[]': ['1', '0', '0'],
            'step_desc[]': ['五花肉切块焯水', '加调料炖煮'],
        })
        self.assertEqual(resp.status_code, 302)
        recipe = Recipe.objects.get(name='红烧肉')
        self.assertEqual(recipe.ingredients.count(), 3)
        self.assertEqual(recipe.steps.count(), 2)

    def test_create_with_video_media_source(self):
        resp = self.client.post(reverse('recipes:create'), {
            'name': '视频菜谱',
            'difficulty': '简单',
            'servings': 2,
            'is_published': 'on',
            'media_type': Recipe.MEDIA_VIDEO,
            'media_title': '做法视频',
            'media_url': 'https://example.com/watch/video-recipe',
        })
        self.assertEqual(resp.status_code, 302)
        recipe = Recipe.objects.get(name='视频菜谱')
        self.assertTrue(recipe.has_external_media)
        self.assertTrue(recipe.is_video_media)
        self.assertEqual(recipe.media_title, '做法视频')

        detail = self.client.get(reverse('recipes:detail', args=[recipe.pk]))
        self.assertContains(detail, '打开视频')
        self.assertContains(detail, 'https://example.com/watch/video-recipe')

    def test_create_with_external_image_media_source(self):
        resp = self.client.post(reverse('recipes:create'), {
            'name': '图片菜谱',
            'difficulty': '简单',
            'servings': 2,
            'is_published': 'on',
            'media_type': Recipe.MEDIA_IMAGE,
            'media_title': '参考图片',
            'media_url': 'https://example.com/recipe-image.jpg',
        })
        self.assertEqual(resp.status_code, 302)
        recipe = Recipe.objects.get(name='图片菜谱')
        self.assertTrue(recipe.has_external_media)
        self.assertTrue(recipe.is_image_media)

        detail = self.client.get(reverse('recipes:detail', args=[recipe.pk]))
        self.assertContains(detail, '打开图片')
        self.assertContains(detail, 'https://example.com/recipe-image.jpg')
        self.assertContains(detail, 'external-media-image')

    def test_recipe_media_requires_url_when_enabled(self):
        resp = self.client.post(reverse('recipes:create'), {
            'name': '缺少媒体链接菜谱',
            'difficulty': '简单',
            'servings': 2,
            'is_published': 'on',
            'media_type': Recipe.MEDIA_IMAGE,
            'media_title': '参考图片',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '选择外部图片或视频时需要填写媒体链接')
        self.assertFalse(Recipe.objects.filter(name='缺少媒体链接菜谱').exists())

    def test_detail_marks_fuzzy_matched_ingredient(self):
        Dish.objects.create(name='鸡丁', created_by=self.user)
        RecipeIngredient.objects.create(
            recipe=self.recipe,
            name='鸡丁（去骨鸡腿肉）',
            amount='200g',
            is_main=True,
            sort_order=0,
        )
        RecipeIngredient.objects.create(
            recipe=self.recipe,
            name='神秘配料',
            amount='5g',
            is_main=False,
            sort_order=1,
        )

        resp = self.client.get(reverse('recipes:detail', args=[self.recipe.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'ingredient-match-ok', count=1)
        self.assertContains(resp, '已匹配食材：鸡丁')

    def test_sync_external_recipes_view(self):
        with patch('apps.recipes.views.pull_and_sync') as mock_sync:
            mock_sync.return_value = {
                'files_total': 12,
                'imported': 12,
                'created': 5,
                'updated': 7,
            }
            resp = self.client.post(reverse('recipes:sync_external'))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('recipes:list'))
        mock_sync.assert_called_once_with(refresh=True, prune=True)

    def test_sync_cooklikehoc_command_imports_recipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            images_dir = repo_root / 'images'
            images_dir.mkdir(parents=True, exist_ok=True)
            cat_dir = repo_root / '炒菜'
            cat_dir.mkdir(parents=True, exist_ok=True)
            seasoning_dir = repo_root / '配料'
            seasoning_dir.mkdir(parents=True, exist_ok=True)
            (cat_dir / 'README.md').write_text('# 炒菜\n', encoding='utf-8')
            (seasoning_dir / '鸡油料.md').write_text(
                (
                    '# 鸡油料\n\n'
                    '## 配料\n'
                    '- 鸡油\n\n'
                    '## 步骤\n'
                    '- 熬出鸡油备用。\n'
                ),
                encoding='utf-8',
            )
            (cat_dir / '宫保鸡丁.md').write_text(
                (
                    '# 宫保鸡丁\n\n'
                    '![宫保鸡丁](../images/宫保鸡丁.jpg)\n\n'
                    '## 配料\n'
                    '- 鸡丁\n'
                    '- [鸡油料](/配料/鸡油料.md)\n'
                    '- 花生米\n\n'
                    '## 步骤\n'
                    '- 1. 鸡丁下锅翻炒。\n'
                    '- 2. 参考[鸡油料](/配料/鸡油料.md)，加入花生米出锅。\n'
                ),
                encoding='utf-8',
            )

            call_command('sync_cooklikehoc', repo_path=str(repo_root))

        recipe = Recipe.objects.get(source='cooklikehoc', source_id='炒菜/宫保鸡丁.md')
        self.assertEqual(recipe.name, '宫保鸡丁')
        self.assertEqual(recipe.category.name, '家常热菜')
        self.assertTrue(recipe.source_url.startswith('https://github.com/Gar-b-age/CookLikeHOC/blob/main/'))
        self.assertEqual(recipe.media_type, Recipe.MEDIA_IMAGE)
        self.assertEqual(recipe.media_title, '宫保鸡丁')
        self.assertIn('raw.githubusercontent.com/Gar-b-age/CookLikeHOC/main/images/', recipe.media_url)
        self.assertIn('%E5%AE%AB%E4%BF%9D%E9%B8%A1%E4%B8%81.jpg', recipe.media_url)
        self.assertEqual(recipe.external_links[0]['text'], '鸡油料')
        self.assertEqual(recipe.external_links[0]['source_id'], '配料/鸡油料.md')
        self.assertEqual(recipe.ingredients.count(), 3)
        self.assertEqual(recipe.steps.count(), 2)

        list_resp = self.client.get(reverse('recipes:list'))
        self.assertContains(list_resp, recipe.media_url)

        detail = self.client.get(reverse('recipes:detail', args=[recipe.pk]))
        self.assertContains(detail, recipe.media_url)
        self.assertContains(detail, 'recipe-inline-link')
        self.assertContains(detail, 'linked-recipe-previews')
        self.assertContains(detail, '鸡油料')
        self.assertContains(detail, '查看完整菜谱')


# ════════════════════════════════════════════════════════════
# 10. 边界条件和安全测试
# ════════════════════════════════════════════════════════════
class EdgeCaseTestCase(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='tester', password='test1234')
        self.client.login(username='tester', password='test1234')

    def test_order_delete_get_should_not_delete(self):
        """GET 请求不应删除订单"""
        order = Order.objects.create(
            order_date=date.today(), created_by=self.user
        )
        resp = self.client.get(reverse('orders:delete', args=[order.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())

    def test_dish_delete_get_should_not_deactivate(self):
        """GET 请求不应停用食材"""
        dish = Dish.objects.create(name='测试菜', is_active=True, created_by=self.user)
        self.client.get(reverse('dishes:delete', args=[dish.pk]))
        dish.refresh_from_db()
        self.assertTrue(dish.is_active)

    def test_confirm_with_empty_names_skipped(self):
        """空食材名应被跳过"""
        resp = self.client.post(reverse('ocr:confirm'), {
            'order_date': date.today().isoformat(),
            'image_path': '',
            'raw_text': '',
            'auto_create_dish': 'on',
            'dish_name[]': ['', '  ', '有效食材名'],
            'quantity[]': ['1', '1', '1'],
            'unit_price[]': ['10', '10', '10'],
            'dish_id[]': ['', '', ''],
        })
        self.assertEqual(resp.status_code, 302)
        order = Order.objects.first()
        self.assertEqual(order.total_items, 1)  # 只有最后一个有效

    def test_statistics_chart_data_format(self):
        """确保统计页的 chart_labels/chart_data 是合法 JSON"""
        resp = self.client.get(reverse('orders:statistics'))
        content = resp.content.decode('utf-8')
        # chart_labels 和 chart_data 应该在模板中正确渲染为 JSON 数组
        self.assertIn('labels:', content)

    def test_dashboard_trend_chart_removed(self):
        """确保仪表盘已移除近7日趋势图"""
        resp = self.client.get(reverse('dashboard:index'))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        self.assertIn('今日推荐菜谱', content)
        self.assertNotIn('trendChart', content)
        self.assertNotIn('近7日菜量趋势', content)
