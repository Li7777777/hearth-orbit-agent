from django.contrib import admin

from .models import Recipe, RecipeCategory, RecipeIngredient, RecipeRecommendationHistory, RecipeStep


@admin.register(RecipeCategory)
class RecipeCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'icon', 'sort_order']
    list_editable = ['sort_order']


class RecipeIngredientInline(admin.TabularInline):
    model = RecipeIngredient
    extra = 1


class RecipeStepInline(admin.TabularInline):
    model = RecipeStep
    extra = 1


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'source', 'media_type', 'difficulty', 'is_published', 'updated_at']
    list_filter = ['source', 'media_type', 'category', 'difficulty', 'is_published']
    search_fields = ['name', 'media_title', 'media_url', 'source_url']
    inlines = [RecipeIngredientInline, RecipeStepInline]


@admin.register(RecipeRecommendationHistory)
class RecipeRecommendationHistoryAdmin(admin.ModelAdmin):
    list_display = ['recipe', 'recommended_date', 'score', 'matched_ingredient_count', 'created_at']
    list_filter = ['recommended_date']
    search_fields = ['recipe__name']
