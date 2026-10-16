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


def _nonempty_env(name: str, default: str) -> str:
    """读取非空配置，并去除首尾空白。"""
    value = os.environ.get(name, default).strip()
    if not value:
        raise ImproperlyConfigured(f"{name} must not be empty")
    return value


def _positive_integer_env(name: str, default: int) -> int:
    """读取严格为正整数的环境配置。"""
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as error:
        raise ImproperlyConfigured(f"{name} must be a positive integer") from error
    if value <= 0:
        raise ImproperlyConfigured(f"{name} must be a positive integer")
    return value


def _configured_directory(name: str, default: str) -> Path:
    """解析并验证项目可读写的本地目录配置。"""
    value = _nonempty_env(name, default)
    configured = Path(value)
    directory = configured if configured.is_absolute() else PROJECT_ROOT / configured
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ImproperlyConfigured(f"{name} must be a writable directory") from error
    if not directory.is_dir() or not os.access(directory, os.R_OK | os.W_OK | os.X_OK):
        raise ImproperlyConfigured(f"{name} must be a writable directory")
    return directory


LOG_DIR = _configured_directory("LOG_DIR", "logs")
TASK_DATA_ROOT = _configured_directory("TASK_DATA_ROOT", "var/tasks")
MODEL_ARTIFACT_ROOT = _configured_directory("MODEL_ARTIFACT_ROOT", "var/model-artifacts")

MAX_UPLOAD_BYTES = _positive_integer_env("MAX_UPLOAD_BYTES", 52_428_800)
REDIS_URL = _nonempty_env("REDIS_URL", "redis://127.0.0.1:6379/0")
INFERENCE_STREAM = _nonempty_env("INFERENCE_STREAM", "health:inference")
INFERENCE_GROUP = _nonempty_env("INFERENCE_GROUP", "health-inference")
INFERENCE_LEASE_SECONDS = _positive_integer_env("INFERENCE_LEASE_SECONDS", 60)
INFERENCE_HEARTBEAT_SECONDS = _positive_integer_env("INFERENCE_HEARTBEAT_SECONDS", 10)
INFERENCE_DISPATCH_INTERVAL_SECONDS = _positive_integer_env(
    "INFERENCE_DISPATCH_INTERVAL_SECONDS",
    30,
)
INFERENCE_MAX_ATTEMPTS = _positive_integer_env("INFERENCE_MAX_ATTEMPTS", 3)
INFERENCE_POLL_MILLISECONDS = _positive_integer_env("INFERENCE_POLL_MILLISECONDS", 1000)

if INFERENCE_HEARTBEAT_SECONDS * 3 >= INFERENCE_LEASE_SECONDS:
    raise ImproperlyConfigured(
        "INFERENCE_HEARTBEAT_SECONDS must be less than one third of INFERENCE_LEASE_SECONDS"
    )



SECRET_KEY = _required_env("DJANGO_SECRET_KEY")
JWT_SIGNING_KEY = _required_env("JWT_SIGNING_KEY")
DEBUG = os.environ.get("DJANGO_DEBUG", "false").casefold() in {"1", "true", "yes", "on"}
ALLOWED_HOSTS = [
    host.strip() for host in _required_env("DJANGO_ALLOWED_HOSTS").split(",") if host.strip()
]
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must contain at least one host")


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
    "ACCESS_TOKEN_LIFETIME": timedelta(
        seconds=_positive_integer_env("JWT_ACCESS_TOKEN_LIFETIME_SECONDS", 604800),
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(
        seconds=_positive_integer_env("JWT_REFRESH_TOKEN_LIFETIME_SECONDS", 2592000),
    ),
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
