# 运行与 API 运维

## JWT 密钥和过期令牌维护

Access Token 默认有效期为 7 天，Refresh Token 默认有效期为 30 天，可通过[环境配置与服务运行](environment.md)中的 `JWT_*_TOKEN_LIFETIME_SECONDS` 环境变量独立调整，以签发时配置和令牌自身 `exp` 为准。`JWT_SIGNING_KEY` 与 Django 的 `DJANGO_SECRET_KEY` 分开生成并保存；更换 JWT 密钥会使此前签发的令牌无法通过签名校验，这与仅调整有效期不同。

启用账号 API 后，旧 Session 不会转换成 JWT。客户端部署新版本后必须重新登录，并安全保存登录响应中的 Access 与 Refresh；后续按接口契约轮换 Refresh。

Ninja JWT 的黑名单表会记录已签发的 Refresh。部署环境应每日运行以下管理命令，删除已经过期的令牌记录：

```sh
uv run --locked --package health-service python manage.py flushexpiredtokens
```

定时任务须使用应用配置中的 `JWT_SIGNING_KEY` 和数据库连接环境变量；不要在 API 请求中执行清理命令。

## 查看与采集请求日志

API 请求完成和受限的异常定位事件以“无键日期时间、无键级别、其余 `key=value`”的单行格式写入 Web 进程 stdout，并以单行 JSON 写入 `server.log`；`health_service` 通用应用事件同样写入 stdout，并以单行 JSON 写入 `application.log`。API 记录只输出请求上下文白名单；普通应用记录只输出稳定事件名，inference 仅额外保留其专属安全关联字段，均不继承其他请求的标识、用户或渠道。通用事件名必须使用固定的小写点分隔标识，无效名称统一输出为 `application.event`。任意日志消息和异常正文均不序列化。

日志内容统一使用 `Asia/Shanghai` 的 `YYYY-MM-DD HH:mm:ss` 时间，不带小数秒和时区后缀，不依赖宿主机时区。完成记录还包含级别、事件、请求标识、HTTP 方法、路由模板、状态和耗时；只有可信的普通渠道、已鉴权用户 ID 或稳定错误码可用时才包含相应字段。stdout 连接交互终端时会高亮级别、事件和 HTTP 状态；重定向或被采集时不输出 ANSI 颜色码。以下为一个 404 的控制台示例：

```text
2026-10-12 20:00:00 INFO status=404 event=request.completed requestId=0123456789abcdef0123456789abcdef method=GET route=<unmatched> durationMs=1.25 errorCode=NOT_FOUND
```

JSON 文件保留 `timestamp`、`level` 键，时间值与同一事件的 stdout 相同。采集端需先读取固定 19 字符的日期时间，再读取级别与后续键值字段，不能再把整行直接作为 `key=value` 解析。旧 ISO 时间、`timestamp=` 和 `level=` 前缀不再输出；部署时同步切换客户端和采集解析规则，历史日志不重写。Uvicorn 自身启动信息和第三方输出不属于此结构化应用日志格式。

未预期异常另有一条 `request.exception` 事件，包含同一 `requestId`、异常类型及 `module`／`function`／`line` 定位；不包含异常文本、源码行或局部变量。不要为复现故障而把凭证或健康资料写入日志。

`LOG_DIR` 可选，默认是项目根目录下的 `logs/`；相对路径以项目根目录为基准，绝对路径按原值使用。启动时会创建目录；运行进程的操作系统账号必须对该目录有读取、写入和搜索权限，否则启动失败。API 日志写入 `server.log`，通用应用日志写入 `application.log`。

两个文件分别按宿主机本地午夜轮转，每个保留最近 30 个归档，另有一个当前文件。本地午夜由操作系统时区决定，不使用 Django 的 `TIME_ZONE`，因此归档日期可能与内容中的北京时间日期不同。标准库轮转处理器不协调多进程写入；每个 Web worker、inference 消费者或单轮恢复进程必须使用独立日志目录，或改用外部采集方案。上述命令按 `logs/web`、`logs/inference` 分离 Web 与消费者；不要同时运行两个共享 `logs/inference` 的消费进程。

Uvicorn 访问日志已关闭，Django 请求错误和数据库调试日志也不会输出原始路径、查询参数或 SQL。日志不得记录请求／响应正文、密码、令牌、Cookie、手机号、健康资料或未经验证的转发 IP。

## JSON API 回复与错误码

`/api/v1/` 下的 JSON 业务路由使用 `code`、`message`、`data`、`requestId` 四个字段；成功码为字符串 `OK`，无业务载荷时 `data` 为 `null`。HTTP 200／201 表示成功，响应正文的 `requestId` 必须与 `X-Request-ID` 响应头一致。客户端提供的请求标识不会覆盖服务端值。

成功与错误 JSON 响应均使用 UTF-8 编码，直接输出 `message`、`data` 中的中文等非 ASCII 字符，不转义为 `\uXXXX`。API 的 JSON renderer 与公共错误响应统一设置 `ensure_ascii=False`；引号、反斜杠和控制字符仍按 JSON 规则转义，解析后的字段值保持不变。

业务日期时间字段（`createdAt`、`submittedAt`、`startedAt`、`finishedAt`、`uploadedAt`、`publishedAt`）统一转换为北京时间 `Asia/Shanghai`，输出 `YYYY-MM-DD HH:mm:ss`；例如 `2026-10-10T18:00:00Z` 返回 `2026-10-11 02:00:00`。小数秒仅在展示时省略，不四舍五入，数据库时间、排序和租约精度不变；空时间仍为 `null`，`birthDate` 仍为 `YYYY-MM-DD`。任意结果或扩展资料中的字符串及下载文件内容不改写。OpenAPI 声明对应字符串格式、示例和时区，不再将响应日期时间声明为 RFC 3339 `date-time`。客户端必须按北京时间解释，不应依赖本地时区推断。

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
