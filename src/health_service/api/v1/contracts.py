"""定义 v1 业务路由共用的错误响应 Schema。"""

from health_service.api.responses import ApiResponse

ERROR_RESPONSES = {
    401: ApiResponse[None],
    403: ApiResponse[None],
    404: ApiResponse[None],
    405: ApiResponse[None],
    409: ApiResponse[None],
    422: ApiResponse[None],
    429: ApiResponse[None],
    500: ApiResponse[None],
}
