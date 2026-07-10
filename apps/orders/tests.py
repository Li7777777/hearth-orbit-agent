from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Order


class OrderListFilterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='order-filter-user', password='safe-password')
        self.client.force_login(self.user)
        Order.objects.create(order_date=date.today(), source='manual', created_by=self.user)
        Order.objects.create(
            order_date=date.today() - timedelta(days=90),
            source='manual',
            created_by=self.user,
        )

    def test_default_recent_range_is_kept_when_page_parameter_exists(self):
        response = self.client.get(reverse('orders:list'), {'page': 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_obj'].paginator.count, 1)

    def test_invalid_date_input_does_not_raise_server_error(self):
        response = self.client.get(reverse('orders:list'), {'date_from': 'not-a-date'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '开始日期格式无效')
