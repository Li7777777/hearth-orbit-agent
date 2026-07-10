import os
import re
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def env_list(name: str, default: list[str] | None = None) -> list[str]:
    value = os.getenv(name, '')
    if not value.strip():
        return list(default or [])
    return [item.strip() for item in value.split(',') if item.strip()]


DEBUG = env_bool('DEBUG', True)
SECRET_KEY = os.getenv('SECRET_KEY', '').strip()
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'django-insecure-dev-key-change-me'
    else:
        raise ImproperlyConfigured('SECRET_KEY must be set when DEBUG is false.')

ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', ['127.0.0.1', 'localhost'] if DEBUG else [])
if not DEBUG and not ALLOWED_HOSTS:
    raise ImproperlyConfigured('ALLOWED_HOSTS must be set when DEBUG is false.')

CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS')

# ── Apps ──────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'widget_tweaks',
    # Local apps
    'apps.accounts',
    'apps.dashboard',
    'apps.ocr',
    'apps.dishes',
    'apps.orders',
    'apps.recipes',
]

# ── Middleware ─────────────────────────────────────────
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.accounts.middleware.LoginRequiredMiddleware',
]

ROOT_URLCONF = 'config.urls'

# ── Templates ─────────────────────────────────────────
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ── Database (MySQL / SQLite) ─────────────────────────
USE_MYSQL = env_bool('USE_MYSQL', False)

if USE_MYSQL:
    try:
        import MySQLdb  # noqa: F401
    except ImportError:
        raise ImproperlyConfigured('USE_MYSQL=true requires mysqlclient to be installed.') from None
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': os.getenv('DB_NAME', 'ai_recipe'),
            'USER': os.getenv('DB_USER', 'root'),
            'PASSWORD': os.getenv('DB_PASSWORD', ''),
            'HOST': os.getenv('DB_HOST', '127.0.0.1'),
            'PORT': os.getenv('DB_PORT', '3306'),
            'OPTIONS': {'charset': 'utf8mb4'},
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# ── Auth ──────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
]

if 'test' in sys.argv:
    PASSWORD_HASHERS = [
        'django.contrib.auth.hashers.MD5PasswordHasher',
    ]

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# ── i18n ──────────────────────────────────────────────
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

# ── Static & Media ────────────────────────────────────
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ── External Recipe Source ───────────────────────────
COOKLIKEHOC_REPO_URL = os.getenv('COOKLIKEHOC_REPO_URL', 'https://github.com/Gar-b-age/CookLikeHOC')
COOKLIKEHOC_ZIP_URL = os.getenv(
    'COOKLIKEHOC_ZIP_URL',
    'https://codeload.github.com/Gar-b-age/CookLikeHOC/zip/refs/heads/main',
)
COOKLIKEHOC_REPO_PATH = Path(os.getenv('COOKLIKEHOC_REPO_PATH', str(BASE_DIR / 'external' / 'CookLikeHOC-main')))

# ── OCR Vision Provider Preset ───────────────────────
VISION_PROVIDER_PRESET = {
    'enabled': env_bool('VISION_PROVIDER_ENABLED', False),
    'provider': os.getenv('VISION_PROVIDER_PROVIDER', '').strip(),
    'provider_name': os.getenv('VISION_PROVIDER_NAME', '').strip(),
    'api_key': os.getenv('VISION_PROVIDER_API_KEY', '').strip(),
    'base_url': os.getenv('VISION_PROVIDER_BASE_URL', '').strip(),
    'model': os.getenv('VISION_PROVIDER_MODEL', '').strip(),
    'prompt': os.getenv('VISION_PROVIDER_PROMPT', '').strip(),
    'timeout_seconds': os.getenv('VISION_PROVIDER_TIMEOUT_SECONDS', '').strip(),
    'requests_per_minute': os.getenv('VISION_PROVIDER_REQUESTS_PER_MINUTE', '').strip(),
}

# ── Daily Meal Multi-Agent LLM Critic ─────────────────
MEAL_AGENT_LLM = {
    'enabled': env_bool('MEAL_AGENT_LLM_ENABLED', False),
    'provider_name': os.getenv('MEAL_AGENT_LLM_PROVIDER_NAME', 'DeepSeek').strip(),
    'api_key': os.getenv('MEAL_AGENT_LLM_API_KEY', '').strip(),
    'base_url': os.getenv('MEAL_AGENT_LLM_BASE_URL', '').strip(),
    'model': os.getenv('MEAL_AGENT_LLM_MODEL', '').strip(),
    'timeout_seconds': os.getenv('MEAL_AGENT_LLM_TIMEOUT_SECONDS', '45').strip(),
    'reuse_vision_config': env_bool('MEAL_AGENT_LLM_REUSE_VISION_CONFIG', True),
}

# ── Daily Meal Full-LLM Multi-Agent Planner ───────────
MEAL_AGENT_FULL_LLM = {
    'enabled': env_bool('MEAL_AGENT_FULL_LLM_ENABLED', False),
    'provider_name': os.getenv('MEAL_AGENT_FULL_LLM_PROVIDER_NAME', '').strip(),
    'api_key': os.getenv('MEAL_AGENT_FULL_LLM_API_KEY', '').strip(),
    'base_url': os.getenv('MEAL_AGENT_FULL_LLM_BASE_URL', '').strip(),
    'model': os.getenv('MEAL_AGENT_FULL_LLM_MODEL', '').strip(),
    'timeout_seconds': os.getenv('MEAL_AGENT_FULL_LLM_TIMEOUT_SECONDS', '60').strip(),
    'reuse_meal_llm_config': env_bool('MEAL_AGENT_FULL_LLM_REUSE_MEAL_LLM_CONFIG', True),
    'max_recipes': os.getenv('MEAL_AGENT_FULL_LLM_MAX_RECIPES', '36').strip(),
    'requests_per_minute': os.getenv('MEAL_AGENT_FULL_LLM_REQUESTS_PER_MINUTE', '5').strip(),
    'max_concurrency': os.getenv('MEAL_AGENT_FULL_LLM_MAX_CONCURRENCY', '4').strip(),
    'expert_concurrency': os.getenv('MEAL_AGENT_FULL_LLM_EXPERT_CONCURRENCY', '4').strip(),
}

# 首页只读取推荐快照；大模型在后台刷新，不阻塞页面请求。
MEAL_PLAN_BACKGROUND_REFRESH = {
    'enabled': env_bool('MEAL_PLAN_BACKGROUND_REFRESH_ENABLED', True),
    'refresh_minutes': os.getenv('MEAL_PLAN_BACKGROUND_REFRESH_MINUTES', '240').strip(),
    'error_retry_minutes': os.getenv('MEAL_PLAN_BACKGROUND_ERROR_RETRY_MINUTES', '30').strip(),
}
if 'test' in sys.argv:
    MEAL_PLAN_BACKGROUND_REFRESH['enabled'] = False

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Logging ──────────────────────────────────────────
_IGNORED_404_URLS = [
    re.compile(r'^/\.well-known/'),
]
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()


def skip_ignorable_404(record):
    if getattr(record, 'status_code', None) != 404:
        return True
    path = record.args[0] if getattr(record, 'args', None) else ''
    return not any(pattern.search(path) for pattern in _IGNORED_404_URLS)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'skip_ignorable_404': {
            '()': 'django.utils.log.CallbackFilter',
            'callback': skip_ignorable_404,
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'filters': ['skip_ignorable_404'],
        },
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'WARNING',
        },
        'apps': {
            'handlers': ['console'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
    },
}
