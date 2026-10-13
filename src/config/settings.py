"""Django 项目设置及环境配置。"""

import os
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _required_env(name: str) -> str:
    """读取必需环境变量，缺失或空值时明确失败。"""
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ImproperlyConfigured(f"Missing required environment variable: {name}")
    return value


SECRET_KEY = _required_env("DJANGO_SECRET_KEY")
JWT_SIGNING_KEY = _required_env("JWT_SIGNING_KEY")
DEBUG = os.environ.get("DJANGO_DEBUG", "false").casefold() in {"1", "true", "yes", "on"}
ALLOWED_HOSTS = [
    host.strip() for host in _required_env("DJANGO_ALLOWED_HOSTS").split(",") if host.strip()
]
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must contain at least one host")

_log_dir_value = os.environ.get("LOG_DIR", "logs")
if not _log_dir_value.strip():
    raise ImproperlyConfigured("LOG_DIR must not be empty")

_configured_log_dir = Path(_log_dir_value)
LOG_DIR = (
    _configured_log_dir if _configured_log_dir.is_absolute() else PROJECT_ROOT / _configured_log_dir
)
try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
except OSError as error:
    raise ImproperlyConfigured("LOG_DIR must be a writable directory") from error
if not LOG_DIR.is_dir() or not os.access(
    LOG_DIR,
    os.R_OK | os.W_OK | os.X_OK,
):
    raise ImproperlyConfigured("LOG_DIR must be a writable directory")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "ninja_jwt.token_blacklist",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "health_service.apps.HealthServiceConfig",
]

MIDDLEWARE = [
    "health_service.api.middleware.api_request_middleware",
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

NINJA_JWT = {
    "SIGNING_KEY": JWT_SIGNING_KEY,
    "AUTH_TOKEN_CLASSES": ("ninja_jwt.tokens.AccessToken",),
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=10),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
}
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

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "console": {
            "()": "health_service.api.structured_logging.ConsoleApiFormatter",
        },
        "api_json": {
            "()": "health_service.api.structured_logging.StructuredApiFormatter",
        },
    },
    "handlers": {
        "structured_stdout": {
            "class": "health_service.api.structured_logging.StandardOutputHandler",
            "formatter": "console",
        },
        "api_file": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "filename": str(LOG_DIR / "server.log"),
            "formatter": "api_json",
            "encoding": "utf-8",
            "when": "midnight",
            "interval": 1,
            "backupCount": 30,
            "utc": False,
        },
        "application_file": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "filename": str(LOG_DIR / "application.log"),
            "formatter": "api_json",
            "encoding": "utf-8",
            "when": "midnight",
            "interval": 1,
            "backupCount": 30,
            "utc": False,
        },
    },
    "loggers": {
        "health_service": {
            "handlers": ["structured_stdout", "application_file"],
            "level": "INFO",
            "propagate": False,
        },
        "health_service.api": {
            "handlers": ["structured_stdout", "api_file"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": [],
            "level": "CRITICAL",
            "propagate": False,
        },
        "django.db.backends": {
            "handlers": [],
            "level": "CRITICAL",
            "propagate": False,
        },
        "uvicorn.access": {
            "handlers": [],
            "level": "CRITICAL",
            "propagate": False,
        },
    },
}
