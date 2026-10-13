# health-service

基于 Django 与 Django Ninja 的健康服务后端，使用 PostgreSQL 存储数据。
当前提供多渠道账号注册、密码登录与本人账号查询，采用 JWT 认证。

## 快速启动

使用 Python 3.13 和 `uv`。完成首次环境配置、依赖安装及数据库迁移后，在项目根目录运行：

```sh
# 开发：代码变更后自动重启
uv run --locked uvicorn config.asgi:application --app-dir src --host 127.0.0.1 --port 8000 --no-access-log --reload

# 部署：关闭调试模式，不启用自动重载
DJANGO_DEBUG=false uv run --locked uvicorn config.asgi:application --app-dir src --host 127.0.0.1 --port 8000 --no-access-log --workers 1
```

## API 文档

Django Ninja 自动生成交互式 API 文档和 OpenAPI JSON，本地服务启动后可访问：

- [交互式 API 文档（Swagger UI）](http://127.0.0.1:8000/api/v1/docs)
- [OpenAPI JSON](http://127.0.0.1:8000/api/v1/openapi.json)

首次环境配置及详细开发说明见[本地开发指南](docs/development.md)。