# Proposal

## Why

当前 Web API 日志只写入进程标准输出，应用没有可配置的本地持久日志目录，也没有轮转留存。运维需要在保留 stdout 采集能力的同时，能配置本地日志路径并按日轮转；服务日志还应覆盖通用应用事件，而不只是 API 请求。

## What Changes

- **BREAKING** 将应用日志输出契约从 stdout-only 扩展为 stdout 与本地轮转文件双写；运行环境必须提供可写日志目录。
- 增加 `LOG_DIR` 配置，默认使用项目根目录下的 `logs/`，并在 `.env.template` 与开发文档中说明路径语义和权限要求。
- API 请求及异常记录写入 `server.log`；通用应用日志写入 `application.log`。两类日志保留结构化 JSON 和安全字段白名单。
- 本地文件在每日午夜轮转并保留 30 份；stdout 保持可供容器或部署采集器读取。
- 不新增虚构的调度器事件；未来调度器可通过通用应用日志记录真实事件。

## Capabilities

### New Capabilities
- `application-logging`: 为 API 与通用应用事件提供安全、可配置且有轮转留存的结构化日志输出。

### Modified Capabilities
- None. 当前主规格中没有已发布的应用日志能力；本变更建立统一的持久日志契约。

## Impact

- 配置：`src/config/settings.py`、`.env.template`。
- 日志实现：`src/health_service/api/structured_logging.py` 及应用 logger 配置。
- 验收：API 与应用日志路由、路径配置、每日轮转和保留行为测试。
- 文档：`docs/development.md`。
- 运行环境新增对 `LOG_DIR` 可写的要求；保留 stdout 输出，不改变 API 响应格式。
