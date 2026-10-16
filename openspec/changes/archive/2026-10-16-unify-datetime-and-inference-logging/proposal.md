# Proposal

## Why

日志和 JSON API 的时间目前以 UTC ISO 8601 展示，与业务使用的北京时间不一致，控制台的 `timestamp=`、`level=` 前缀也增加阅读负担。独立推理消费者没有启动及任务处理日志，运行时无法从终端判断是否就绪、接取任务或执行完成。

Access 与 Refresh Token 的有效期目前硬编码在设置中，部署环境无法独立调整；本次同时将其提取为配置，保持未配置时的现有有效期。

## What Changes

- **BREAKING**：日志和 JSON 业务回复中的日期时间统一转换到 `Asia/Shanghai`，输出 `YYYY-MM-DD HH:mm:ss`，不带毫秒和时区后缀；数据库和内部日期时间精度、时区及租约计算不变。纯日期、`null` 和任意业务字符串不改写。
- **BREAKING**：stdout 改为 `2026-10-10 19:19:19 INFO status=200 event=request.completed ...`，时间、级别不带键，其余字段保留 `key=value`。文件仍是单行 JSON，保留 `timestamp`、`level` 键；保留终端高亮和非终端无颜色行为。
- 为 inference 补充启动、就绪、消息接收、执行领取、持久化成功或失败、失租、基础设施故障与停止事件，同时写入 stdout 和 `application.log`；使用任务专属安全字段白名单，不输出健康数据、执行令牌或任意异常正文。
- 提取 `JWT_ACCESS_TOKEN_LIFETIME_SECONDS` 和 `JWT_REFRESH_TOKEN_LIFETIME_SECONDS` 两个可选环境变量，单位为正整数秒，默认分别为 `604800`（7 天）、`2592000`（30 天）。沿用进程环境优先于 `.env` 的规则，非法值启动时报错；重启后仅影响新签发令牌，不追溯修改已有令牌的 `exp`。
- 同步 OpenAPI、已有日志解析测试和开发文档，不提供新旧格式双写或兼容开关。除上述两个 Token 配置外不新增配置项；不新增依赖、数据库迁移、重试机制或监控服务，不改变任务消费和确认语义。JWT 的 `exp`、`iat` 保持标准数值格式，不参与北京时间字符串展示。

## Capabilities

### New Capabilities

无；沿用既有能力边界。

### Modified Capabilities

- `api-response`：明确 JSON 业务回复日期时间的北京时间、秒级展示契约及文档一致性。
- `api-request-logging`：将请求日志的 UTC 要求改为北京时间，统一 stdout 格式，并消除与既有文件日志能力冲突的过时输出约束。
- `application-logging`：修改控制台格式和时间展示，允许 inference 专属白名单字段，维持安全、分类和双通道输出。
- `inference-runtime`：增加可验证的消费者生命周期与任务处理日志，保持现有租约、终态及消息确认边界。
- `user-auth`：允许独立配置 Access 与 Refresh 有效期，明确默认值、配置优先级、错误处理及新旧令牌的生效边界，普通渠道和 manager 沿用同一组配置。

## Impact

- 日志格式器 `src/health_service/api/structured_logging.py`；现有 `src/config/settings.py` 日志路由继续复用。
- JSON 序列化装配 `src/config/urls.py`、`src/health_service/api/` 及任务、目录的公开响应 schema；内部 schema 和数据库模型不转换为字符串。
- 消费命令 `src/health_service/management/commands/run_inference.py` 及必要的派发、执行结果调用边界；不改变正常退出、错误退出和补偿投递行为。
- `tests/integration/api/`、`tests/integration/workflows/` 中受影响的行为测试，以及 `docs/development.md` 的时间、日志、推理运行和部署说明。
- `src/config/settings.py` 的 JWT 有效期配置、`.env.template`，以及配置加载和真实认证签发的行为验证；同步开发文档中固定“7 天／30 天”的描述，不修改实际 `.env` 或签名密钥。
- API 客户端和外部日志采集端必须同步切换解析规则；历史日志不重写。多进程文件轮转仍沿用现有部署限制，Web 与 inference 应使用进程独立的 `LOG_DIR`。
