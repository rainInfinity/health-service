# Spec Delta

## MODIFIED Requirements

### Requirement: API 与通用应用日志分类输出
系统 MUST 将 API 请求及异常事件写入 `server.log`，将通用应用事件（包括 inference 事件）写入 `application.log`；两个文件中的每条记录 MUST 为单行 JSON。相同事件 MUST 同时写入 stdout，采用无键的日期时间和级别前缀，其余为 `key=value`，保留与文件记录一致的安全字段和值；文件 MUST 保留 `timestamp`、`level` 键。

#### Scenario: API 请求完成
- **WHEN** 一个 API 请求成功或以已知客户端错误结束
- **THEN** 对应的完成记录出现在 stdout 和 `server.log`，文件为 JSON，stdout 包含实际状态与请求标识

#### Scenario: API 请求发生未预期异常
- **WHEN** 一个 API 请求触发未预期异常
- **THEN** 同一请求标识的受限异常记录和完成记录均出现在 stdout 和 `server.log`

#### Scenario: 通用应用事件
- **WHEN** 应用在 API 请求之外写出一个稳定事件
- **THEN** 该事件出现在 stdout 和 `application.log`，且不会伪造请求标识

### Requirement: 控制台日志可读并支持终端高亮
系统 MUST 将 stdout 日志格式化为 `YYYY-MM-DD HH:mm:ss LEVEL` 前缀，日期时间和级别 MUST NOT 带键名或引号；存在 HTTP 状态时 MUST 紧接级别输出 `status=...`，其余安全字段使用单行 `key=value`。stdout MUST 保留 JSON 文件日志的相同安全字段和值；交互终端 MUST 为级别、事件和 HTTP 状态添加 ANSI 高亮，重定向时 MUST NOT 包含 ANSI 控制码。

#### Scenario: 交互终端显示日志
- **WHEN** stdout 连接交互终端并写出日志
- **THEN** 日志使用无键时间和级别前缀及后续键值字段，级别、事件和 HTTP 状态带有 ANSI 高亮

#### Scenario: 日志被重定向或采集
- **WHEN** stdout 未连接交互终端
- **THEN** 日志仍为相同的单行前缀加键值格式，且不含 ANSI 控制码

#### Scenario: 请求完成日志字段顺序
- **WHEN** 输出 HTTP 200 的请求完成事件
- **THEN** 日志采用形如 `2026-10-10 19:19:19 INFO status=200 event=request.completed ...` 的格式，不输出 `timestamp=` 或 `level=`，安全字段仍可独立解析

#### Scenario: 无 HTTP 状态的应用事件
- **WHEN** 输出 inference 就绪或任务执行事件
- **THEN** 时间、级别后直接输出事件及可用安全字段，不虚构 HTTP 状态

### Requirement: API 与应用日志保持敏感信息边界
系统 MUST 只输出稳定事件名及白名单安全字段；MUST NOT 输出请求或响应正文、密码、令牌、Cookie、手机号、用户名、健康资料、原始路径参数、查询字符串、未经验证的转发 IP 或任意异常文本。未预期 API 异常仍 MUST 保留安全的异常类型和受限代码位置。inference 事件 MUST 仅使用专属白名单保留任务关联元数据、耗时及固定诊断，MUST NOT 输出原始输入输出、完整结果、资料快照、存储路径、执行令牌或含凭证的连接地址。

#### Scenario: 敏感输入进入 API 或应用日志
- **WHEN** 请求头、请求正文或异常文本含凭证、个人健康资料或查询哨兵值
- **THEN** stdout、`server.log` 和 `application.log` 均不包含这些原始值

#### Scenario: 应用日志不依赖线程局部上下文
- **WHEN** 异步 API 请求和通用应用事件并发发生
- **THEN** 请求标识只关联所属请求，通用应用事件不继承其他请求的用户或渠道信息

#### Scenario: inference 保留关联但丢弃敏感字段
- **WHEN** inference 记录含合法任务和消息标识，同时附带输入结果、执行令牌、绝对路径及异常正文
- **THEN** stdout 和 `application.log` 保留允许的关联字段和固定诊断，丢弃全部敏感字段，不向普通应用事件开放任意额外字段

## ADDED Requirements

### Requirement: 日志时间统一使用北京时间秒级展示
系统 MUST 将日志事件发生时间转换到 `Asia/Shanghai`，stdout 和 JSON 文件中的时间值 MUST 均为 `YYYY-MM-DD HH:mm:ss`，不带小数秒或时区后缀。同一事件的两个输出时间 MUST 一致，MUST NOT 依赖宿主机时区；本次展示变更 MUST NOT 改变现有按宿主机本地午夜轮转和留存策略。

#### Scenario: UTC 事件转换为北京时间
- **WHEN** 日志事件发生在 `2026-10-10T11:19:19.999Z`
- **THEN** stdout 前缀和文件 `timestamp` 值均为 `2026-10-10 19:19:19`，不四舍五入到下一秒

#### Scenario: 展示时区与轮转时区不同
- **WHEN** 宿主机时区不是 `Asia/Shanghai`
- **THEN** 日志内容仍使用北京时间，文件归档仍遵循现有宿主机本地午夜规则
