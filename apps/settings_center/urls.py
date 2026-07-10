from django.urls import path

from . import views

app_name = 'settings_center'

urlpatterns = [
    path('', views.index, name='index'),
]
