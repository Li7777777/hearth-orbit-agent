from django.conf import settings
from django.db import models
from django.utils import timezone


class RecipeCategory(models.Model):
    name = models.CharField('分类名称', max_length=50, unique=True)
    sort_order = models.IntegerField('排序', default=0)
    icon = models.CharField('图标', max_length=10, blank=True, default='')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '菜谱分类'
        verbose_name_plural = verbose_name
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.name


class Recipe(models.Model):
    MEDIA_NONE = 'none'
    MEDIA_IMAGE = 'image'
    MEDIA_VIDEO = 'video'

    DIFFICULTY_CHOICES = [
        ('简单', '简单'),
        ('中等', '中等'),
        ('困难', '困难'),
    ]
    MEDIA_TYPE_CHOICES = [
        (MEDIA_NONE, '无'),
        (MEDIA_IMAGE, '外部图片'),
        (MEDIA_VIDEO, '外部视频'),
    ]
    SOURCE_CHOICES = [
        ('local', '本地'),
        ('cooklikehoc', 'CookLikeHOC'),
    ]

    name = models.CharField('菜谱名称', max_length=100)
    category = models.ForeignKey(
        RecipeCategory, on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='分类',
        related_name='recipes'
    )
    dish = models.ForeignKey(
        'dishes.Dish', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='关联食材',
        related_name='recipes'
    )
    description = models.TextField('简介', blank=True, default='')
    servings = models.IntegerField('份数', default=1)
    prep_time_minutes = models.IntegerField('准备时间(分钟)', null=True, blank=True)
    cook_time_minutes = models.IntegerField('烹饪时间(分钟)', null=True, blank=True)
    difficulty = models.CharField('难度', max_length=10, choices=DIFFICULTY_CHOICES, default='中等')
    image = models.ImageField('成品图片', upload_to='recipe_images/', blank=True)
    media_type = models.CharField('媒体类型', max_length=20, choices=MEDIA_TYPE_CHOICES, default=MEDIA_NONE)
    media_title = models.CharField('媒体标题', max_length=120, blank=True, default='')
    media_url = models.URLField('媒体链接', blank=True, default='')
    external_links = models.JSONField('外部菜谱链接', blank=True, default=list)
    tips = models.TextField('小贴士', blank=True, default='')
    source = models.CharField('数据来源', max_length=30, choices=SOURCE_CHOICES, default='local', db_index=True)
    source_id = models.CharField('来源ID', max_length=255, blank=True, default='', db_index=True)
    source_url = models.URLField('来源链接', blank=True, default='')
    is_published = models.BooleanField('已发布', default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='创建者'
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '菜谱'
        verbose_name_plural = verbose_name
        ordering = ['-updated_at']

    def __str__(self):
        return self.name

    @property
    def has_external_media(self):
        return self.media_type != self.MEDIA_NONE and bool(self.media_url)

    @property
    def is_video_media(self):
        return self.media_type == self.MEDIA_VIDEO and bool(self.media_url)

    @property
    def is_image_media(self):
        return self.media_type == self.MEDIA_IMAGE and bool(self.media_url)

    @property
    def display_image_url(self):
        if self.image:
            return self.image.url
        if self.is_image_media:
            return self.media_url
        return ''

    @property
    def display_image_alt(self):
        return self.media_title or self.name

    @property
    def media_display_label(self):
        return dict(self.MEDIA_TYPE_CHOICES).get(self.media_type, '媒体')


class RecipeIngredient(models.Model):
    recipe = models.ForeignKey(
        Recipe, on_delete=models.CASCADE,
        related_name='ingredients', verbose_name='菜谱'
    )
    name = models.CharField('食材名称', max_length=100)
    amount = models.CharField('用量', max_length=50, blank=True, default='')
    is_main = models.BooleanField('主料', default=True)
    sort_order = models.IntegerField('排序', default=0)

    class Meta:
        verbose_name = '菜谱用料'
        verbose_name_plural = verbose_name
        ordering = ['-is_main', 'sort_order']

    def __str__(self):
        return f"{self.name} {self.amount}"


class RecipeStep(models.Model):
    recipe = models.ForeignKey(
        Recipe, on_delete=models.CASCADE,
        related_name='steps', verbose_name='菜谱'
    )
    step_number = models.IntegerField('步骤序号')
    description = models.TextField('步骤描述')
    image = models.ImageField('步骤图片', upload_to='recipe_images/steps/', blank=True)

    class Meta:
        verbose_name = '菜谱步骤'
        verbose_name_plural = verbose_name
        ordering = ['step_number']

    def __str__(self):
        return f"步骤 {self.step_number}"


class RecipeRecommendationHistory(models.Model):
    recipe = models.ForeignKey(
        Recipe, on_delete=models.CASCADE,
        related_name='recommendation_histories', verbose_name='菜谱'
    )
    recommended_date = models.DateField('推荐日期', default=timezone.localdate, db_index=True)
    score = models.FloatField('推荐分', default=0)
    matched_ingredient_count = models.IntegerField('命中食材数', default=0)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '菜谱推荐历史'
        verbose_name_plural = verbose_name
        ordering = ['-recommended_date', '-created_at']
        unique_together = ['recipe', 'recommended_date']

    def __str__(self):
        return f'{self.recipe.name} @ {self.recommended_date}'
