# 设计

## 背景与约束

动机见[提案](proposal.md)，对外格式与错误语义见 [api-response 规格](specs/api-response/spec.md)。当前 `config.urls` 仅装配一个 `NinjaExtraAPI`，无业务路由和统一响应层。旧项目将成功值暗中包装、部分失败返回 HTTP 200，不能作为新 HTTP 契约。`structured-application-logging` 负责生成每次请求的 `requestId`，本变更负责让它出现在 JSON 回复中。

## 目标与非目标

**目标：** 一个 API JSON 契约同时覆盖成功、业务失败、框架校验失败和未知故障，HTTP 状态与实际结果一致，公开响应结构与 OpenAPI 一致。

**非目标：** 不包装 Django admin、OpenAPI 页面、流式或文件内容；不为本变更建设日志存储、不记录原始异常详情到客户端、不维持旧接口的数字业务码或旧 URL。

## 技术决策

### 1. 声明式成功 Schema 与单一异常映射

在 `health_service.schemas.responses` 定义公共外层 `code/message/data/requestId` 和各路由的具体 `data` 载荷 Schema；路由显式声明包裹后的响应 Schema、HTTP 状态及字段别名，成功结果经公共响应构造函数填入外层。无载荷的成功由该函数返回 HTTP 200 和 `data: null`。相较于旧项目的全局 renderer 自动给任意返回值加外壳，这能保证 OpenAPI 与实际 JSON 相符，避免异常处理返回值被重复包装。新 API 只有一个 `NinjaExtraAPI` 实例，集中注册异常映射。

受控业务异常携带内部枚举业务码和预定义对外文案；全局映射按类型确定 HTTP 状态，不能把任意 `HttpError` 的 `str(exc)`、数据库异常或第三方库消息原样输出。建议固定 `OK`、`INVALID_CREDENTIALS`／`INVALID_TOKEN`、`CHANNEL_FORBIDDEN`、`NOT_FOUND`、`METHOD_NOT_ALLOWED`、`USERNAME_EXISTS`、`INVALID_INPUT`、`INTERNAL_ERROR` 等字符串码；已知数据库用户名冲突由用户用例精确转换，其余 `IntegrityError` 不被推断为客户端冲突。状态使用 200／201 或 401／403／404／405／409／422／500，不用 HTTP 200 承载错误。

### 2. 覆盖 Ninja 与 Django 路由边界

优先为 `ninja.errors.ValidationError`、认证及权限异常、`django.http.Http404`、受控业务异常和未知 `Exception` 注册专门的 API 处理器。框架校验错误返回固定 `INVALID_INPUT` 与通用文案，不传递 `pydantic` 的 `input`、异常详情或原始字段值；错误 `data` 统一为 `null`。异常处理无论 `DEBUG` 值如何均返回固定的通用 500，不让 Ninja 在调试时输出文本堆栈；真实异常只交给内部日志边界记录一次。

Django 路由分派在 Ninja 之外也可能产生 404／405 或 500。仅对 `/api/v1/` 前缀使用路径限定的响应兜底，补齐尚未进入 API handler 的错误；对已由 API 处理器形成的四字段回复不再包装，Django admin 和文档路径保持框架原样。部署仍保持 `DEBUG=False`，但 API 错误保护不以该设置为唯一保证。

### 3. 请求标识与日志协作

使用日志变更在请求入口生成的不可由未可信客户端任意覆写的 `requestId`；公共回复构造器把它写入正文，入口中间件把相同值写入 `X-Request-ID`，异步异常也使用同一标识。错误码／HTTP 状态由响应层作为安全元数据传给日志层，日志层不解析或缓存响应正文。若请求尚未进入业务 handler，入口仍已建立关联上下文，以便返回可查的 404／500。

## 风险与权衡

- **隐式 renderer 包装导致 OpenAPI 声明与实际结构不符** → 显式声明成功载荷包裹 Schema 和错误响应，并由 HTTP 契约测试比对实际解析结果。
- **Ninja 捕获之外的 Django 404／500 泄漏 HTML 或调试细节** → 仅限 `/api/v1/` 的兜底处理并验证匹配和未匹配的请求，不改变管理后台行为。
- **用异常文本构造客户端文案导致敏感信息泄漏** → 可枚举码和固定消息白名单，未知异常一律通用 500；日志单独保存受限的内部诊断。
- **外层与日志两边各自生成请求标识** → 唯一请求入口拥有标识，所有响应只读取该值，不重新生成。

## 部署与回退

先与日志变更统一 `requestId` 的生成／消费边界，启用响应层后再发布用户接口；已有部署没有需要兼容的业务 API。部署前用真实 ASGI 请求覆盖成功、校验失败、未知异常及未知路径，确认 `DEBUG=True` 也不在 JSON API 中暴露堆栈。回退业务路由时可停用新增响应层，但不得让已经发布且依赖四字段结构的客户端重新接收不一致格式。