# Design

## Context

See [proposal.md](proposal.md) for motivation and [application-logging spec](specs/application-logging/spec.md) for observable requirements. 当前 `health_service.api` 只输出白名单 JSON 到 stdout；尚无通用应用日志文件。NXHE 的配置提供了可配置目录、server／scheduler 分类和每日 30 份轮转，但其请求中间件使用 `threading.local`、采信未验证的 `X-Forwarded-For` 并记录用户名等字段，本变更不复制这些实现。当前开发启动命令使用 Uvicorn 默认单 worker。

## Goals / Non-Goals

**Goals:**
- API 请求／异常事件和通用应用事件分别写入 stdout 与对应的本地 JSON 日志文件。
- 用 `LOG_DIR` 控制目录，默认相对项目根目录的 `logs/`；日切轮转并保留 30 份归档。
- 保留现有 API 请求上下文隔离、请求标识关联及安全字段白名单。

**Non-Goals:**
- 不实现尚不存在的调度器或伪造业务事件。
- 不提供多进程共享同一日志目录的轮转协调，也不部署外部日志采集平台。
- 不记录任意日志消息、异常文本或请求／响应正文。

## Decisions

1. **目录由运行配置确定。** `LOG_DIR` 从进程环境或项目根目录 `.env` 读取；未设置时使用项目根目录下的 `logs/`。相对路径锚定到项目根目录，绝对路径保持不变。启动时创建缺失目录；路径不可创建或写入时使服务明确启动失败，避免 stdout 正常而文件持久化悄然缺失。`.env.template` 给出默认值。

2. **按 logger 层级分流，保留双路输出。** `health_service.api` 继续负责 API 请求和异常事件，使用 stdout 与 `server.log` 两个 handler 且不向父 logger 传播；`health_service` 通用应用 logger 使用 stdout 与 `application.log`，API 子 logger 不传播到该文件。两者共用单行 JSON 白名单 formatter；已有 Django 请求／数据库和 Uvicorn access 日志抑制规则保持不变。

3. **使用标准库本地日轮转。** 使用 `TimedRotatingFileHandler`，按主机本地午夜轮转、每个活动文件保留 30 份备份、编码 UTF-8。选择标准库 handler 而非复制 NXHE 的自定义 DST handler，减少维护代码；行为以日切与留存测试固定。

4. **一般应用事件只写结构化安全字段。** 应用 logger 使用稳定 `event` 标识和 formatter 白名单字段；formatter 不格式化任意 `record.msg` 或 `exc_info`。API 的受限异常记录仍保留异常类型和模块／函数／行号，不输出异常文本或原始堆栈。异步请求上下文继续由 `ContextVar` 管理，不用 `threading.local`。

5. **每个日志目录只由一个进程写入。** 标准库轮转 handler 不协调多个进程对同一文件的并发写入和轮转；同一 `LOG_DIR` 必须只对应一个 Uvicorn worker。横向扩容时每个实例使用独立存储目录；部署若必须在同一目录运行多个 worker，应先改用外部采集／轮转方案，不得共享当前文件 handler。

## Risks / Trade-offs

- **日志目录只读或配额不足** → 启动时检查创建和写入能力并明确失败；部署为每个实例提供受限、可写且有容量管理的存储。
- **多 worker 共用日志目录导致轮转竞争** → 文档明确单 worker／单目录约束；多实例分配独立目录，未来多 worker 部署需另行采用进程安全的日志收集方案。
- **文件写入增加同步 I/O** → 保持当前低开销 JSON 字段和单次写入；若吞吐需求改变，再单独评估队列／外部采集方案。
- **本地文件已持久但尚无外部备份** → 文档描述为本地轮转留存，不将其误称为集中采集或灾备。

## Migration Plan

1. 在 `.env.template` 增加 `LOG_DIR=logs`，部署前为每个 Web 实例确认目录挂载、所有者和可写权限。
2. 启用两个文件 handler，同时保留 stdout；启动时验证目录和文件创建成功。
3. 通过实际 ASGI 请求和测试应用事件核对 stdout、`server.log`、`application.log` 的分流、内容脱敏、请求关联和日切留存。
4. 若需回退，移除本地文件 handler 并保留 stdout 配置；既有日志文件不由应用自动删除。
