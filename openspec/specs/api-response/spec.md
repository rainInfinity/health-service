# api-response Specification

## Purpose

为版本化 JSON 业务接口提供一致且可验证的成功与失败回复，使客户端能依靠稳定业务码、正确 HTTP 状态和请求标识处理结果，同时不从错误消息获得服务器内部信息。

## Requirements

### Requirement: 成功与失败采用同一回复结构
`/api/v1/` 下的 JSON 业务回复 MUST 同时包含 `code`、`message`、`data`、`requestId`；`code` MUST 为稳定字符串业务码，成功时为 `OK`，HTTP 状态 MUST 准确表达请求结果。无业务载荷时 `data` MUST 为 `null` 且仍返回响应体。成功与错误 JSON 回复 MUST 使用 UTF-8 编码，直接输出中文等非 ASCII 字符而不转义为 `\uXXXX`；引号、反斜杠和控制字符仍 MUST 按 JSON 规则转义。

#### Scenario: 带载荷的成功
- **WHEN** 客户端成功读取本人账号或创建账号
- **THEN** 响应包含四个固定字段，`data` 为对应的公开载荷，读取使用 HTTP 200、创建使用 HTTP 201，`code` 为 `OK`

#### Scenario: 无载荷的成功
- **WHEN** 客户端成功登出且无业务数据需要返回
- **THEN** 响应使用 HTTP 200 并包含 `code: "OK"`、`data: null`，不以无响应体的 204 代替统一结构

#### Scenario: 成功与错误响应直接输出中文
- **WHEN** 成功或错误回复的 `message` 或 `data` 包含中文等非 ASCII 字符
- **THEN** 原始响应以 UTF-8 直接包含这些字符而非对应的 `\uXXXX` 转义，解析后的字段值保持不变，JSON 必需的字符转义仍正确保留

### Requirement: 可识别错误具有稳定状态和安全消息
系统 MUST 将可识别的认证失败、权限拒绝、资源或方法不存在、数据冲突、输入校验失败映射为相应 HTTP 401、403、404、405、409、422 和稳定业务码；不得用 HTTP 200 包装失败。

#### Scenario: 未认证和渠道不匹配
- **WHEN** 请求没有有效凭证，或凭证所属渠道与路径不符
- **THEN** 分别返回 HTTP 401 或 403，保持统一回复结构，消息不泄露凭证声明

#### Scenario: 重复用户名与输入错误
- **WHEN** 注册用户名已存在，或资料字段无效
- **THEN** 分别返回 HTTP 409 或 422；校验响应最多指出安全的字段名，不回显提交值、密码或健康资料

#### Scenario: 未知路径和不支持的方法
- **WHEN** 请求 `/api/v1/` 下不存在的 JSON API 路径，或以未支持的方法调用现有接口
- **THEN** 分别返回 HTTP 404 或 405，且仍符合相同回复结构

### Requirement: 未预期错误不暴露服务器细节
系统 MUST 将未预期的 API 异常转换为 HTTP 500、稳定的 `INTERNAL_ERROR` 业务码和固定对外文案；调试模式也不得在该 JSON API 响应中输出异常信息、堆栈、SQL、文件路径、配置或敏感输入。

#### Scenario: 服务内部抛出异常
- **WHEN** 已匹配的 JSON API handler 发生未预期异常
- **THEN** 客户端仅收到统一结构的通用 500 回复和请求标识，而不是异常内容或调试堆栈

### Requirement: 回复与日志使用同一请求标识
系统 MUST 在 JSON 回复的 `requestId` 与 `X-Request-ID` 响应头中使用相同的服务端请求标识，该值 MUST 能用于关联同次请求的服务器日志。

#### Scenario: 正常请求与失败请求关联
- **WHEN** 一个成功请求和一个失败请求分别完成
- **THEN** 各自的正文 `requestId` 等于本次响应头及相应请求日志的标识，不因成功或失败改变结构

### Requirement: 统一结构的范围和对外契约
系统 MUST 让 JSON API 的声明响应结构与实际回复一致；Django admin、API 文档页面以及非 JSON 的流式或文件响应 MUST NOT 被 JSON 回复包装。

#### Scenario: 文档所述结构与实际一致
- **WHEN** 客户端按公布的接口契约调用返回账号数据或校验错误的 JSON API
- **THEN** 两种响应均可按公布的四字段结构解析，不存在文档声明裸数据而实际包裹数据的情况

#### Scenario: 非 JSON 路径不受影响
- **WHEN** 请求 Django admin、API 文档页面或未来的文件下载
- **THEN** 系统不强制将页面或文件内容转换为四字段 JSON 业务回复
