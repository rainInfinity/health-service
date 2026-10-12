# 本地开发

## 前置条件

- Python 3.13，由 `uv` 管理解释器与依赖。
- Docker，以及 PostgreSQL 16 或兼容的 PostgreSQL 服务。
- 已提交的 `uv.lock`；日常安装使用 `uv sync --locked`，不要单独升级依赖而不更新锁文件。

## 启动 PostgreSQL

可使用已有 PostgreSQL 实例，也可用仓库外部的临时容器：

```sh
docker run --rm --detach \
  --name health-service-postgres \
  --publish 127.0.0.1:55432:5432 \
  --env POSTGRES_DB=health_service \
  --env POSTGRES_USER=health_service \
  --env POSTGRES_PASSWORD=local-development-only \
  postgres:16.4-alpine
```

容器停止后会自动删除。不要在生产环境使用示例密码。

## 配置环境变量

### 使用根目录 `.env`

推荐先复制模板，再填写本地配置：

```sh
cp .env.template .env
uv run --locked python -c 'import secrets; print(secrets.token_urlsafe(50))'
```

将命令输出填写到 `.env` 的 `DJANGO_SECRET_KEY`，并为 `POSTGRES_PASSWORD` 填入本地数据库密码。上面的 PostgreSQL 容器示例使用 `local-development-only`，仅用于本地开发，不要用于生产环境。

Django 从项目根目录读取可选的 `.env`，不受启动时工作目录影响。进程环境变量优先于 `.env`；已设置为空的必填进程变量仍会报错，不会回退到文件值。`.env` 已加入 Git 忽略规则，不要提交真实密钥或数据库密码。

`DJANGO_SECRET_KEY`、`DJANGO_ALLOWED_HOSTS`、`POSTGRES_DB`、`POSTGRES_USER`、`POSTGRES_PASSWORD` 和 `POSTGRES_HOST` 必须显式提供。模板中的 `DJANGO_DEBUG` 默认设为 `true`，未设置时默认为关闭；`POSTGRES_PORT` 未设置时默认为 `5432`。

### 仅使用进程环境变量

没有 `.env` 时，也可以在启动 Django 的同一 shell 中提供全部必填配置：

```sh
export DJANGO_SECRET_KEY="$(uv run --locked python -c 'import secrets; print(secrets.token_urlsafe(50))')"
export DJANGO_DEBUG=true
export DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1"
export POSTGRES_DB=health_service
export POSTGRES_USER=health_service
export POSTGRES_PASSWORD=local-development-only
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=55432
```

## 安装、迁移与启动

```sh
uv python install 3.13
uv sync --locked
uv run --locked python manage.py check
uv run --locked python manage.py migrate
uv run --locked uvicorn config.asgi:application --app-dir src --host 127.0.0.1 --port 8000
```

Uvicorn 默认只提供 Web 依赖，不安装或加载推理算法运行时。当前变更只建立服务入口和数据模型，不提供登录、上传或推理业务接口。