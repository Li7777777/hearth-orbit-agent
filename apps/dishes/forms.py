from django import forms

from .models import Dish


class DishForm(forms.ModelForm):
    class Meta:
        model = Dish
        fields = [
            'name', 'category', 'stock_in_date', 'unit', 'specification',
            'default_price', 'storage', 'image', 'description', 'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'ios-form-input-full', 'placeholder': '食材名称'}),
            'stock_in_date': forms.DateInput(attrs={'class': 'ios-form-input-full', 'type': 'date'}),
            'unit': forms.TextInput(attrs={'class': 'ios-form-input-full', 'placeholder': '斤'}),
            'specification': forms.TextInput(attrs={'class': 'ios-form-input-full', 'placeholder': '如: 500g/袋'}),
            'default_price': forms.NumberInput(attrs={'class': 'ios-form-input-full', 'placeholder': '0.00', 'step': '0.01'}),
            'description': forms.Textarea(attrs={'class': 'ios-form-input-full', 'placeholder': '食材描述（可选）', 'rows': 2}),
        }
