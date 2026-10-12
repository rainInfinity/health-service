import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ImproperlyConfigured(f"Missing required environment variable: {name}")
    return value


SECRET_KEY = _required_env("DJANGO_SECRET_KEY")
DEBUG = os.environ.get("DJANGO_DEBUG", "false").casefold() in {"1", "true", "yes", "on"}
ALLOWED_HOSTS = [
    host.strip()
    for host in _required_env("DJANGO_ALLOWED_HOSTS").split(",")
    if host.strip()
]
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must contain at least one host")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "health_service.apps.HealthServiceConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _required_env("POSTGRES_DB"),
        "USER": _required_env("POSTGRES_USER"),
        "PASSWORD": _required_env("POSTGRES_PASSWORD"),
        "HOST": _required_env("POSTGRES_HOST"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

AUTH_USER_MODEL = "health_service.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
