from django.urls import path
from ninja_extra import NinjaExtraAPI

from health_service.api.exceptions import register_exception_handlers

api = NinjaExtraAPI(title="健康服务 API", version="1.0.0")
register_exception_handlers(api)

urlpatterns = [path("api/v1/", api.urls)]
