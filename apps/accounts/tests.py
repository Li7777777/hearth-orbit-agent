from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class LoginFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='login-flow-user', password='safe-password')

    def test_protected_deep_link_preserves_next_path(self):
        response = self.client.get(reverse('orders:list'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('accounts:login')}?next={reverse('orders:list')}")

    def test_external_next_url_is_rejected_after_login(self):
        response = self.client.post(
            f"{reverse('accounts:login')}?next=https://example.invalid/phish",
            {'username': self.user.username, 'password': 'safe-password'},
        )

        self.assertRedirects(response, reverse('dashboard:index'))
