from django.urls import path

from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.index, name='index'),
    path('recommendations/local/', views.local_meal_plan, name='local_meal_plan'),
    path('recommendations/recipes/', views.recipe_recommendations, name='recipe_recommendations'),
    path('recommendations/full-llm/status/', views.full_llm_plan_status, name='full_llm_plan_status'),
    path('recommendations/full-llm/refresh/', views.full_llm_plan_refresh, name='full_llm_plan_refresh'),
]
