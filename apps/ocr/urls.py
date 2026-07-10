from django.urls import path

from . import views

app_name = 'ocr'

urlpatterns = [
    path('upload/', views.upload, name='upload'),
    path('process/', views.process, name='process'),
    path('vision-settings/', views.vision_settings, name='vision_settings'),
    path('llm-settings/', views.llm_settings, name='llm_settings'),
    path('confirm/', views.confirm, name='confirm'),
]
