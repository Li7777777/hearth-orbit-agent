from django.conf import settings
from django.shortcuts import redirect


class LoginRequiredMiddleware:
    """全局登录保护中间件：未登录用户自动跳转到登录页"""

    EXEMPT_URLS = [
        settings.LOGIN_URL,
        '/admin/',
        '/accounts/login/',
        '/accounts/register/',
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            path = request.path_info
            if not any(path.startswith(url) for url in self.EXEMPT_URLS):
                return redirect(settings.LOGIN_URL)
        return self.get_response(request)
