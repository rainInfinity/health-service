"""为真实 Uvicorn 集成测试提供隔离的 ASGI 应用。"""

import os


def _create_application():
    """在 Django setup 前配置测试 URLconf 并创建 ASGI 应用。"""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.conf import settings
    from django.core.asgi import get_asgi_application

    settings.ROOT_URLCONF = "tests.integration.api.urls"
    settings.ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
    return get_asgi_application()


application = _create_application()
