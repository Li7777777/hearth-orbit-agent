from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.dishes.models import Dish
from apps.orders.models import OrderItem


class OcrConfirmMatchingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='ocr-rematch-user', password='safe-password')
        self.client.force_login(self.user)

    def test_edited_name_does_not_keep_stale_hidden_dish_id(self):
        stale_dish = Dish.objects.create(name='苹果', unit='个', created_by=self.user)

        response = self.client.post(reverse('ocr:confirm'), {
            'order_date': '2026-07-10',
            'dish_name[]': ['香蕉'],
            'quantity[]': ['1'],
            'unit_price[]': ['3.5'],
            'dish_id[]': [str(stale_dish.pk)],
        })

        self.assertEqual(response.status_code, 302)
        item = OrderItem.objects.get()
        self.assertIsNone(item.dish)
        self.assertEqual(item.dish_name, '香蕉')
