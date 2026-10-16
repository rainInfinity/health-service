# Spec Delta

## MODIFIED Requirements

### Requirement: 结构化请求结果日志
系统 MUST 为 API 请求输出可解析的结构化完成记录，至少包含 `Asia/Shanghai` 的 `YYYY-MM-DD HH:mm:ss` 时间、级别、`requestId`、HTTP 方法、可用的路由模板、HTTP 状态及耗时；鉴权后可安全记录用户 ID 和普通渠道，业务错误码可用时记录其稳定值。秒级时间展示 MUST NOT 降低耗时字段的现有精度。

#### Scenario: 匿名请求与鉴权请求
- **WHEN** 一个匿名登录请求和一个有效 Access 的本人资料请求完成
- **THEN** 两者各有一次可关联的请求完成记录；前者不虚构用户 ID，后者可记录用户 ID 与实际普通渠道

#### Scenario: 未匹配路由或已知客户端错误
- **WHEN** 客户端请求未知 API 路径或触发已知校验错误
- **THEN** 日志记录真实的 HTTP 404 或 422 与请求标识，不需要解析响应正文才能判断状态

#### Scenario: 时间转换不依赖宿主机时区
- **WHEN** API 请求在 `2026-10-10T11:19:19Z` 完成，宿主机配置为 UTC 或其他时区
- **THEN** 文件与 stdout 中的记录时间均为 `2026-10-10 19:19:19`，耗时仍由实际执行时长计算

### Requirement: 应用日志输出到标准输出
系统 MUST 将 Web API 应用日志输出到标准输出以供运行环境采集，同时按应用日志分类规则写入可配置的本地文件。stdout MUST 采用无键的日期时间和级别前缀，后接安全键值字段；文件 MUST 保持单行 JSON。文件目录可写性、轮转和留存 MUST 遵循应用日志契约，不得将本地文件留存等同于外部集中采集或备份。

#### Scenario: Web 进程输出日志
- **WHEN** 以 ASGI 方式启动服务并调用一个 API 请求
- **THEN** 可从进程 stdout 读取结构化请求日志，并从配置目录的 `server.log` 读取同一事件的 JSON 记录，两者使用相同请求标识和北京时间

#### Scenario: 请求完成记录采用新前缀
- **WHEN** 一个 API 请求以 HTTP 200 完成
- **THEN** stdout 以形如 `2026-10-10 19:19:19 INFO status=200` 的前缀开始，后续包含稳定事件名、请求标识及其他可用安全字段，不再输出 `timestamp=` 或 `level=`
