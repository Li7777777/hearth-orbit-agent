from django.urls import path

from . import views

app_name = 'recipes'

urlpatterns = [
    path('', views.recipe_list, name='list'),
    path('sync-external/', views.sync_external_recipes, name='sync_external'),
    path('create/', views.recipe_create, name='create'),
    path('<int:pk>/', views.recipe_detail, name='detail'),
    path('<int:pk>/edit/', views.recipe_edit, name='edit'),
    path('<int:pk>/delete/', views.recipe_delete, name='delete'),
]
