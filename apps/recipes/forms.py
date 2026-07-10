from django import forms

from .models import Recipe


class RecipeForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['media_type'].required = False

    class Meta:
        model = Recipe
        fields = [
            'name', 'category', 'dish', 'description', 'servings',
            'prep_time_minutes', 'cook_time_minutes', 'difficulty',
            'image', 'media_type', 'media_title', 'media_url', 'tips', 'is_published',
        ]
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'ios-form-input-full', 'placeholder': '菜谱名称',
            }),
            'description': forms.Textarea(attrs={
                'class': 'ios-form-input-full', 'placeholder': '简单介绍这道菜...', 'rows': 3,
            }),
            'servings': forms.NumberInput(attrs={
                'class': 'ios-form-input', 'min': 1,
            }),
            'prep_time_minutes': forms.NumberInput(attrs={
                'class': 'ios-form-input', 'placeholder': '分钟', 'min': 0,
            }),
            'cook_time_minutes': forms.NumberInput(attrs={
                'class': 'ios-form-input', 'placeholder': '分钟', 'min': 0,
            }),
            'tips': forms.Textarea(attrs={
                'class': 'ios-form-input-full', 'placeholder': '小贴士（可选）', 'rows': 2,
            }),
            'media_title': forms.TextInput(attrs={
                'class': 'ios-form-input-full', 'placeholder': '媒体标题',
            }),
            'media_url': forms.URLInput(attrs={
                'class': 'ios-form-input-full', 'placeholder': 'https://example.com/media',
            }),
        }

    def clean(self):
        cleaned = super().clean()
        media_type = cleaned.get('media_type') or Recipe.MEDIA_NONE
        media_url = (cleaned.get('media_url') or '').strip()
        if media_type in {Recipe.MEDIA_IMAGE, Recipe.MEDIA_VIDEO} and not media_url:
            self.add_error('media_url', '选择外部图片或视频时需要填写媒体链接。')
        if media_type == Recipe.MEDIA_NONE:
            cleaned['media_type'] = Recipe.MEDIA_NONE
            cleaned['media_title'] = ''
            cleaned['media_url'] = ''
        return cleaned
