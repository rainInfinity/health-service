# application-logging Specification

## Purpose

为 API 请求和通用应用事件提供安全、可关联的结构化日志，并将记录写入标准输出及可配置、受轮转留存约束的本地文件。

## Requirements

### Requirement: API 与通用应用日志分类输出
系统 MUST 将 API 请求及异常事件写入 `server.log`，将通用应用事件写入 `application.log`；两个文件中的每条记录 MUST 为单行 JSON。相同事件 MUST 同时以单行 `key=value` 结构化记录写入标准输出，保留与文件记录一致的安全字段和值。

#### Scenario: API 请求完成
- **WHEN** 一个 API 请求成功或以已知客户端错误结束
- **THEN** 对应的完成记录出现在标准输出和 `server.log`，文件为 JSON，标准输出包含实际状态与请求标识

#### Scenario: API 请求发生未预期异常
- **WHEN** 一个 API 请求触发未预期异常
- **THEN** 同一请求标识的受限异常记录和完成记录均出现在标准输出和 `server.log`

#### Scenario: 通用应用事件
- **WHEN** 应用在 API 请求之外写出一个稳定事件
- **THEN** 该事件出现在标准输出和 `application.log`，且不会伪造请求标识

### Requirement: 控制台日志可读并支持终端高亮
系统 MUST 将标准输出日志格式化为单行 `key=value` 文本，并保留 JSON 文件日志的相同安全字段。标准输出连接交互终端时 MUST 为级别、事件和 HTTP 状态添加 ANSI 高亮；标准输出被重定向时 MUST NOT 包含 ANSI 控制码。

#### Scenario: 交互终端显示日志
- **WHEN** 标准输出连接交互终端并写出日志
- **THEN** 日志使用可读的键值格式，级别、事件和 HTTP 状态带有 ANSI 高亮

#### Scenario: 日志被重定向或采集
- **WHEN** 标准输出未连接交互终端
- **THEN** 日志仍为单行键值格式，且不含 ANSI 控制码

### Requirement: 日志目录可配置且写入失败显式
系统 MUST 允许通过 `LOG_DIR` 配置日志目录；未设置时 MUST 使用项目根目录下的 `logs/`，相对路径相对于项目根目录解析，绝对路径按原值使用。服务 MUST 创建缺失目录；当目录不能创建或写入时 MUST 明确报告启动错误，不得静默丢弃文件日志。

#### Scenario: 默认日志目录
- **WHEN** 未设置 `LOG_DIR` 且默认目录不存在
- **THEN** 服务在启动时创建项目根目录下的 `logs/`，并在其中写入日志文件

#### Scenario: 使用绝对路径
- **WHEN** `LOG_DIR` 设置为可写的绝对路径
- **THEN** 两类日志文件均写入该目录

#### Scenario: 日志目录不可写
- **WHEN** 服务无法创建或写入配置的日志目录
- **THEN** 启动以明确错误失败，而不是仅保留 stdout 并隐瞒持久化缺失

### Requirement: 日志文件每日轮转并限制留存
系统 MUST 在本地时区每日午夜轮转每个日志文件，并为每个日志文件最多保留最近 30 份归档；超出数量的最旧归档 MUST 被删除。

#### Scenario: 日切轮转
- **WHEN** 当前时间到达本地午夜且日志文件存在
- **THEN** 当前文件归档，后续记录写入新的活动文件

#### Scenario: 归档数量达到上限
- **WHEN** 某日志文件的归档数量超过 30 份
- **THEN** 最旧归档被删除，活动文件和最近 30 份归档保留

### Requirement: API 与应用日志保持敏感信息边界
系统 MUST 只输出稳定事件名及白名单安全字段；MUST NOT 输出请求或响应正文、密码、令牌、Cookie、手机号、用户名、健康资料、原始路径参数、查询字符串、未经验证的转发 IP 或任意异常文本。未预期 API 异常仍 MUST 保留安全的异常类型和受限代码位置。

#### Scenario: 敏感输入进入 API 或应用日志
- **WHEN** 请求头、请求正文或异常文本含凭证、个人健康资料或查询哨兵值
- **THEN** stdout、`server.log` 和 `application.log` 均不包含这些原始值

#### Scenario: 应用日志不依赖线程局部上下文
- **WHEN** 异步 API 请求和通用应用事件并发发生
- **THEN** 请求标识只关联所属请求，通用应用事件不继承其他请求的用户或渠道信息
