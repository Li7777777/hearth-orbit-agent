from django.urls import path

from . import views

app_name = 'dishes'

urlpatterns = [
    path('', views.dish_list, name='list'),
    path('create/', views.dish_create, name='create'),
    path('recognize-image/', views.dish_recognize_image, name='recognize_image'),
    path('bulk-mark-eaten/', views.dish_bulk_mark_eaten, name='bulk_mark_eaten'),
    path('bulk-mark-discarded/', views.dish_bulk_mark_discarded, name='bulk_mark_discarded'),
    path('<int:pk>/', views.dish_detail, name='detail'),
    path('<int:pk>/edit/', views.dish_edit, name='edit'),
    path('<int:pk>/delete/', views.dish_delete, name='delete'),
    path('<int:pk>/mark-eaten/', views.dish_mark_eaten, name='mark_eaten'),
    path('<int:pk>/mark-discarded/', views.dish_mark_discarded, name='mark_discarded'),
]
