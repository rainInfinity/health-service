"""装配版本化 API 路由与统一 JSON 序列化。"""

from django.urls import path
from health_service.api.exceptions import register_exception_handlers
from health_service.api.v1.auth import router as account_auth_router
from health_service.api.v1.catalog import router as catalog_router
from health_service.api.v1.tasks import router as task_router
from health_service.api.v1.users import router as account_users_router
from ninja.renderers import JSONRenderer
from ninja_extra import NinjaExtraAPI

json_renderer = JSONRenderer()
json_renderer.json_dumps_params = {"ensure_ascii": False}
api = NinjaExtraAPI(title="健康服务 API", version="1.0.0", renderer=json_renderer)
register_exception_handlers(api)
api.add_router("", account_auth_router)
api.add_router("", account_users_router)
api.add_router("", catalog_router)
api.add_router("", task_router)

urlpatterns = [path("api/v1/", api.urls)]
