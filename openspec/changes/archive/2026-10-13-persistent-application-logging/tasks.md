# Tasks

## 1. 配置与日志分流

- [x] 1.1 增加可选 `LOG_DIR` 配置，默认解析为项目根目录下的 `logs/`；创建目录并在无法写入时明确失败。更新 `.env.template`，并用单元测试覆盖默认路径、绝对／相对路径和不可写目录。
- [x] 1.2 配置 API logger 写入 stdout 与 `server.log`、通用应用 logger 写入 stdout 与 `application.log`；保留 JSON 白名单和现有 access-log 抑制。用隔离测试验证分类、双路事件一致、无重复及 API 请求标识关联。

## 2. 轮转与安全边界

- [x] 2.1 配置每个日志文件按本地午夜轮转并保留最近 30 份归档；用确定性测试验证切分和最旧归档清理，并更新 `docs/development.md` 中的路径、权限、轮转留存及单 worker 部署约束。
- [x] 2.2 为通用应用事件使用稳定事件名和安全字段白名单，不格式化任意消息或异常文本；用敏感哨兵测试检查 stdout、`server.log` 和 `application.log` 均不泄漏请求正文、凭证、健康资料、原始路径或未验证转发 IP。

## 3. ASGI 集成验收

- [x] 3.1 在隔离测试路由上实际运行 ASGI／Uvicorn 请求链，覆盖成功、404、500 和通用应用事件；核对正文、`X-Request-ID`、完成日志及文件记录，并确认访问日志关闭且没有生产假业务路由。运行相关单元、API 测试及 Django system check。
