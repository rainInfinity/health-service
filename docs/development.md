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
uv run --locked python -c 'import secrets; print(secrets.token_urlsafe(50))'
```

将第一条命令输出填写到 `.env` 的 `DJANGO_SECRET_KEY`，第二条填写到独立的 `JWT_SIGNING_KEY`；不要复用密钥。再为 `POSTGRES_PASSWORD` 填入本地数据库密码。上面的 PostgreSQL 容器示例使用 `local-development-only`，仅用于本地开发，不要用于生产环境。

Django 从项目根目录读取可选的 `.env`，不受启动时工作目录影响。进程环境变量优先于 `.env`；已设置为空的必填进程变量仍会报错，不会回退到文件值。`.env` 已加入 Git 忽略规则，不要提交真实密钥或数据库密码。

`DJANGO_SECRET_KEY`、`JWT_SIGNING_KEY`、`DJANGO_ALLOWED_HOSTS`、`POSTGRES_DB`、`POSTGRES_USER`、`POSTGRES_PASSWORD` 和 `POSTGRES_HOST` 必须显式提供。模板中的 `DJANGO_DEBUG` 默认设为 `true`，未设置时默认为关闭；`POSTGRES_PORT` 未设置时默认为 `5432`。

### 仅使用进程环境变量

没有 `.env` 时，也可以在启动 Django 的同一 shell 中提供全部必填配置：

```sh
export DJANGO_SECRET_KEY="$(uv run --locked python -c 'import secrets; print(secrets.token_urlsafe(50))')"
export JWT_SIGNING_KEY="$(uv run --locked python -c 'import secrets; print(secrets.token_urlsafe(50))')"
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

当前提供五个普通渠道的账号注册、密码登录与本人账号查询；上传、推理业务接口尚未提供。

## 账号注册、密码登录与本人账号查询
普通渠道为 `android-cn`、`ios-cn`、`android-intl`、`ios-intl` 和 `wxapp`；示例中的 `{channel}` 使用其中一个值，`manager` 不开放这些入口。账号是全局共用的，但签发的令牌绑定登录渠道。

注册时提交 `username` 和 `password`：

```sh
curl --include --request POST \
  http://127.0.0.1:8000/api/v1/android-cn/auth/register \
  --header 'Content-Type: application/json' \
  --data '{"username":"local-user","password":"Example-passphrase-483!"}'
```

注册成功返回 HTTP 201。登录使用同一请求字段，成功时返回 HTTP 200：

```sh
curl --include --request POST \
  http://127.0.0.1:8000/api/v1/android-cn/auth/login \
  --header 'Content-Type: application/json' \
  --data '{"username":"local-user","password":"Example-passphrase-483!"}'
```

登录响应中的公开字段使用 `accessToken` 和 `refreshToken` 别名：

```json
{
  "code": "OK",
  "message": "成功",
  "data": {
    "accessToken": "<access-token>",
    "refreshToken": "<refresh-token>"
  },
  "requestId": "<server-request-id>"
}
```

使用 Access Token 查询当前渠道的本人账号；只返回 `userId` 和 `username`，不返回手机号或资料：

```sh
ACCESS_TOKEN='<从登录响应的 data.accessToken 复制>'
curl --include \
  http://127.0.0.1:8000/api/v1/android-cn/users/me \
  --header "Authorization: Bearer ${ACCESS_TOKEN}"
```

成功响应仍使用统一字段，账号 ID 的公开别名为 `userId`：

```json
{
  "code": "OK",
  "message": "成功",
  "data": {"userId": 42, "username": "local-user"},
  "requestId": "<server-request-id>"
}
```

Access Token 仅适用于签发它的渠道，过期、缺失或无效令牌均不能读取账号。错误响应的结构与统一错误码见下文。

## 本人健康资料读取与部分更新

资料接口只从当前渠道的 Access 确定账号，不接受请求指定 `userId`。`GET /api/v1/<channel>/users/me/profile` 不创建缺失的 Profile；未填写值返回 `null`，性别以 `0` 表示未知：

```json
{
  "code": "OK",
  "message": "成功",
  "data": {
    "name": null,
    "gender": 0,
    "birthDate": null,
    "heightCm": null,
    "weightKg": null,
    "medicalHistory": null,
    "historyJson": null
  },
  "requestId": "<server-request-id>"
}
```

使用 `PATCH` 只提交要修改的字段；未提交字段保持原值，可空字段显式设为 `null` 时清空。`gender` 不可为 `null`，`0` 表示未知；身高和体重非空时必须为正数：

```sh
ACCESS_TOKEN='<当前渠道的 Access Token>'
curl --include --request PATCH \
  http://127.0.0.1:8000/api/v1/android-cn/users/me/profile \
  --header 'Content-Type: application/json' \
  --header "Authorization: Bearer ${ACCESS_TOKEN}" \
  --data '{"name":"示例用户","gender":0,"heightCm":null,"historyJson":{"isHypertensive":false}}'
```

`historyJson` 对象按键合并：键值 `false` 会保留为明确否，键值 `null` 删除该键，整个字段为 `null` 则清空病史对象。未知入参（包括 `userId`）会被拒绝；PATCH 成功返回 HTTP 200 和更新后的完整资料。

## Refresh 轮换与登出

刷新和登出只在请求体中接收当前渠道的 `refreshToken`，不要求有效 Access；不要把 Refresh 放入 `Authorization` 头。成功刷新返回 HTTP 200，`data` 使用与登录相同的 `accessToken`、`refreshToken` 字段：

```sh
REFRESH_TOKEN='<从最近一次成功响应的 data.refreshToken 复制>'
curl --include --request POST \
  http://127.0.0.1:8000/api/v1/android-cn/auth/refresh \
  --header 'Content-Type: application/json' \
  --data "{\"refreshToken\":\"${REFRESH_TOKEN}\"}"
```

每次成功刷新都会原子撤销旧 Refresh 并签发新令牌对。客户端应安全保存新对，并在一次成功处理后同时替换本地 Access 和 Refresh；旧 Refresh 不可重用。刷新旧令牌、已登出令牌或渠道不匹配的令牌均不会产生新令牌。

登出只撤销请求中的那枚 Refresh。客户端应提交当前最新的 Refresh，并在收到成功响应后立即清除本地保存的令牌：

```sh
REFRESH_TOKEN='<当前渠道最新的 data.refreshToken>'
curl --include --request POST \
  http://127.0.0.1:8000/api/v1/android-cn/auth/logout \
  --header 'Content-Type: application/json' \
  --data "{\"refreshToken\":\"${REFRESH_TOKEN}\"}"
```

登出成功返回 HTTP 200，`data` 为 `null`；响应仍包含服务端生成的 `requestId`：

```json
{
  "code": "OK",
  "message": "成功",
  "data": null,
  "requestId": "<server-request-id>"
}
```

登出不会撤销已签发的 Access；在其 10 分钟有效期内，只要账号仍可用且渠道匹配，该 Access 仍能访问本人接口。客户端登出后应立即丢弃它；服务端不承诺提前使其失效。其他渠道的独立 Refresh 不受本次登出影响。

## JWT 密钥和过期令牌维护

Access Token 有效期为 10 分钟，Refresh Token 有效期为 7 天。`JWT_SIGNING_KEY` 与 Django 的 `DJANGO_SECRET_KEY` 分开生成并保存；更换 JWT 密钥会使此前签发的令牌无法通过签名校验。

启用账号 API 后，旧 Session 不会转换成 JWT。客户端部署新版本后必须重新登录，并安全保存登录响应中的 Access 与 Refresh；后续按接口契约轮换 Refresh。

Ninja JWT 的黑名单表会记录已签发的 Refresh。部署环境应每日运行以下管理命令，删除已经过期的令牌记录：

```sh
uv run --locked python manage.py flushexpiredtokens
```

定时任务须使用应用配置中的 `JWT_SIGNING_KEY` 和数据库连接环境变量；不要在 API 请求中执行清理命令。

## 查看与采集请求日志

API 请求完成和受限的异常定位事件以单行 `key=value` 格式写入 Web 进程标准输出，并以单行 JSON 写入 `server.log`；`health_service` 通用应用事件同样写入标准输出，并以单行 JSON 写入 `application.log`。API 记录只输出请求上下文白名单；通用应用记录只输出 `event`，不继承请求标识、用户或渠道。通用事件名必须使用固定的小写点分隔标识，无效名称统一输出为 `application.event`。任意日志消息和异常正文均不序列化。

完成记录包含 UTC 时间、级别、事件、请求标识、HTTP 方法、路由模板、状态和耗时；只有可信的普通渠道、已鉴权用户 ID 或稳定错误码可用时才包含相应字段。标准输出连接交互终端时会高亮级别、事件和 HTTP 状态；重定向或被采集时不输出 ANSI 颜色码。以下为一个 404 的控制台示例：

```text
timestamp=2026-10-12T12:00:00.000Z level=INFO event=request.completed requestId=0123456789abcdef0123456789abcdef method=GET route=<unmatched> status=404 durationMs=1.25 errorCode=NOT_FOUND
```

未预期异常另有一条 `request.exception` 事件，包含同一 `requestId`、异常类型及 `module`／`function`／`line` 定位；不包含异常文本、源码行或局部变量。不要为复现故障而把凭证或健康资料写入日志。

`LOG_DIR` 可选，默认是项目根目录下的 `logs/`；相对路径以项目根目录为基准，绝对路径按原值使用。启动时会创建目录；运行进程的操作系统账号必须对该目录有读取、写入和搜索权限，否则启动失败。API 日志写入 `server.log`，通用应用日志写入 `application.log`。

两个文件分别按宿主机本地午夜轮转，每个保留最近 30 个归档，另有一个当前文件。本地午夜由操作系统时区决定，不使用 Django 的 `TIME_ZONE`。标准库轮转处理器不协调多进程写入；共享同一个 `LOG_DIR` 时只能运行一个 Web worker。启动命令显式使用 `--workers 1`；多进程部署必须为每个进程配置独立日志目录，或改用外部采集方案。

Uvicorn 访问日志已关闭，Django 请求错误和数据库调试日志也不会输出原始路径、查询参数或 SQL。日志不得记录请求／响应正文、密码、令牌、Cookie、手机号、健康资料或未经验证的转发 IP。

## JSON API 回复与错误码

`/api/v1/` 下的 JSON 业务路由使用 `code`、`message`、`data`、`requestId` 四个字段；成功码为字符串 `OK`，无业务载荷时 `data` 为 `null`。HTTP 200／201 表示成功，响应正文的 `requestId` 必须与 `X-Request-ID` 响应头一致。客户端提供的请求标识不会覆盖服务端值。

成功与错误 JSON 响应均使用 UTF-8 编码，直接输出 `message`、`data` 中的中文等非 ASCII 字符，不转义为 `\uXXXX`。API 的 JSON renderer 与公共错误响应统一设置 `ensure_ascii=False`；引号、反斜杠和控制字符仍按 JSON 规则转义，解析后的字段值保持不变。

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