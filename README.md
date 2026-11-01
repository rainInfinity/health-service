# health-service

基于 Django 与 Django Ninja 的健康服务后端，使用 PostgreSQL 存储数据。
当前提供多渠道账号注册、密码登录与本人账号查询，采用 JWT 认证。
本仓库使用 uv workspace：`packages/health-service` 提供 Django `health_service` 与 `config`，`packages/health-inference` 提供纯计算包。业务成员显式依赖计算成员；根目录共享唯一 `uv.lock` 和默认 `.venv`，Python 基线为 3.13。

## 快速启动

使用 Python 3.13、`uv` 和 Docker。以下首次配置步骤均在仓库根目录执行。

### 1. 同步依赖并配置环境

```sh
uv sync --locked --all-packages --all-extras
# 仅首次且本地尚无 .env 时执行
cp .env.template .env
```

为 `.env` 填写必填密钥和数据库配置，并将 `POSTGRES_PASSWORD` 设为 `local-development-only`（与下方容器配置一致）。密钥生成和其余环境配置见[环境配置与服务运行](docs/development/environment.md)。

### 2. 首次创建并启动 PostgreSQL

首次启动时创建本地数据卷及 PostgreSQL 容器：

```sh
docker volume create health-service-test-postgres-data
docker run --detach \
  --name health-service-test-postgres \
  --restart unless-stopped \
  --publish 127.0.0.1:55432:5432 \
  --mount type=volume,source=health-service-test-postgres-data,target=/var/lib/postgresql/data \
  --env POSTGRES_DB=health_service \
  --env POSTGRES_USER=health_service \
  --env POSTGRES_PASSWORD=local-development-only \
  postgres:16.4-alpine
```

容器已创建但已停止时，后续启动：

```sh
docker start health-service-test-postgres
```

确认数据库已就绪：

```sh
docker exec health-service-test-postgres pg_isready --username=health_service --dbname=health_service
```

数据库保存在具名数据卷中；停止容器不会删除数据。示例密码仅用于本机开发。

### 3. 初始化数据库结构

```sh
uv run --locked --package health-service python manage.py migrate
```

`migrate` 只创建数据库结构，不会自动写入初始渠道、模型版本或指标数据；业务数据维护见[初始数据库数据维护](docs/development/initial-database-data.md)。

### 4. 启动服务

```sh
# 开发：代码变更后自动重启
uv run --locked --package health-service uvicorn config.asgi:application --host 127.0.0.1 --port 8000 --no-access-log --reload

# 部署：关闭调试模式，不启用自动重载
DJANGO_DEBUG=false uv run --locked --package health-service uvicorn config.asgi:application --host 127.0.0.1 --port 8000 --no-access-log --workers 1
```

## API 文档

Django Ninja 自动生成交互式 API 文档和 OpenAPI JSON，本地服务启动后可访问：

- [交互式 API 文档（Swagger UI）](http://127.0.0.1:8000/api/v1/docs)
- [OpenAPI JSON](http://127.0.0.1:8000/api/v1/openapi.json)

首次环境配置及详细开发说明见[本地开发指南](docs/development.md)。