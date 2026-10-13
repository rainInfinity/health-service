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
uv run --locked uvicorn config.asgi:application --app-dir src --host 127.0.0.1 --port 8000 --no-access-log --workers 1
```

Uvicorn 默认只提供 Web 依赖，不安装或加载推理算法运行时。当前仓库尚未提供登录、上传或推理业务接口。

## 查看与采集请求日志

API 请求完成和受限的异常定位事件以单行 JSON 同时写入 Web 进程的标准输出与 `server.log`；`health_service` 通用应用事件同时写入标准输出与 `application.log`。API 记录只输出请求上下文白名单；通用应用记录只输出 `event`，不继承请求标识、用户或渠道。通用事件名必须使用固定的小写点分隔标识，无效名称统一输出为 `application.event`。任意日志消息和异常正文均不序列化。

完成记录包含 UTC 时间、级别、事件、请求标识、HTTP 方法、路由模板、状态和耗时；只有可信的普通渠道、已鉴权用户 ID 或稳定错误码可用时才包含相应字段。以下是 404 示例：

```json
{"timestamp":"2026-10-12T12:00:00.000Z","level":"INFO","event":"request.completed","requestId":"0123456789abcdef0123456789abcdef","method":"GET","route":"<unmatched>","status":404,"durationMs":1.25,"errorCode":"NOT_FOUND"}
```

未预期异常另有一条 `request.exception` 事件，包含同一 `requestId`、异常类型及 `module`／`function`／`line` 定位；不包含异常文本、源码行或局部变量。不要为复现故障而把凭证或健康资料写入日志。

`LOG_DIR` 可选，默认是项目根目录下的 `logs/`；相对路径以项目根目录为基准，绝对路径按原值使用。启动时会创建目录；运行进程的操作系统账号必须对该目录有读取、写入和搜索权限，否则启动失败。API 日志写入 `server.log`，通用应用日志写入 `application.log`。

两个文件分别按宿主机本地午夜轮转，每个保留最近 30 个归档，另有一个当前文件。本地午夜由操作系统时区决定，不使用 Django 的 `TIME_ZONE`。标准库轮转处理器不协调多进程写入；共享同一个 `LOG_DIR` 时只能运行一个 Web worker。启动命令显式使用 `--workers 1`；多进程部署必须为每个进程配置独立日志目录，或改用外部采集方案。

Uvicorn 访问日志已关闭，Django 请求错误和数据库调试日志也不会输出原始路径、查询参数或 SQL。日志不得记录请求／响应正文、密码、令牌、Cookie、手机号、健康资料或未经验证的转发 IP。

## JSON API 回复与错误码

`/api/v1/` 下的 JSON 业务路由使用 `code`、`message`、`data`、`requestId` 四个字段；成功码为字符串 `OK`，无业务载荷时 `data` 为 `null`。HTTP 200／201 表示成功，响应正文的 `requestId` 必须与 `X-Request-ID` 响应头一致。客户端提供的请求标识不会覆盖服务端值。

以下 JSON 示例为 HTTP 200；创建成功使用 HTTP 201，回复结构相同。

```json
{
  "code": "OK",
  "message": "成功",
  "data": {"value": "业务结果"},
  "requestId": "0123456789abcdef0123456789abcdef"
}
```

业务路由应声明包含实际载荷的回复 Schema，并使用公共 `success_response` 构造器；Schema 的别名应以 `requestId` 等公开字段名生成 OpenAPI。错误响应的 `data` 固定为 `null`，状态与业务码如下：

| HTTP 状态 | 业务码 | 对外文案 |
|---:|---|---|
| 401 | `INVALID_CREDENTIALS` | 用户名或密码错误 |
| 401 | `INVALID_TOKEN` | 认证凭证无效 |
| 403 | `CHANNEL_FORBIDDEN` | 无权访问此资源 |
| 404 | `NOT_FOUND` | 接口不存在 |
| 405 | `METHOD_NOT_ALLOWED` | 请求方法不支持 |
| 409 | `USERNAME_EXISTS` | 用户名已存在 |
| 409 | `CONFLICT` | 请求冲突 |
| 422 | `INVALID_INPUT` | 请求参数无效 |
| 429 | `RATE_LIMITED` | 请求过于频繁 |
| 500 | `INTERNAL_ERROR` | 服务器内部错误 |

错误文案来自固定映射，不回显提交值、令牌、任意异常文本、堆栈或 SQL；即使 `DEBUG=True`，API 客户端也只收到通用 500。Django 管理页、API 文档页及非 JSON 流／文件响应不使用此 JSON 包装。