from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard:index')
    if request.method == 'POST':
        username = request.POST.get('username', '')
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            requested_next = request.POST.get('next') or request.GET.get('next', '')
            next_url = requested_next if url_has_allowed_host_and_scheme(
                requested_next,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ) else reverse('dashboard:index')
            return redirect(next_url)
        else:
            messages.error(request, '用户名或密码错误')
    return render(request, 'accounts/login.html')


def logout_view(request):
    logout(request)
    return redirect('accounts:login')
