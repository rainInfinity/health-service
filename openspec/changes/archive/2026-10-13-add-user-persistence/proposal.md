# Proposal

## Why

当前用户用例直接混合 ORM 查询、行锁、保存及 PostgreSQL 约束识别，且将 ORM 实例返回给 HTTP 层，没有落实架构中已有的持久化与内部数据边界。资料接口还将病史字段的 HTTP 驼峰别名写入 JSONB，与数据库设计和模型测试的下划线命名不一致；应在无存量数据的开发阶段统一实现与契约。

## What Changes

- 为账号与健康资料增加具体的 `infrastructure/persistence/` 模块，集中业务 ORM 操作和已知数据库约束识别；应用层保留校验、事务编排和 PATCH 合并规则。
- 在 `schemas/internal/` 定义最小账号结果和资料数据，使注册及资料读写结果不再向 API 暴露 ORM 实例或惰性查询集。
- 病史已定义字段在内部数据与 `Profile.history_json` 中统一使用 `is_diabetic`、`is_hypertensive`；HTTP 请求和响应继续使用 `isDiabetic`、`isHypertensive`，仅在 HTTP 边界双向转换。
- 保留资料缺席、清空、布尔值、病史按键合并、归属校验及并发更新语义；保留注册账号与空资料的原子创建。
- 同步调整现有行为测试、OpenAPI 病史响应描述及开发和架构文档，区分外部命名与实际存储命名。
- **BREAKING**：内部资料用例调用方与测试数据切换到下划线病史键及内部结果类型，不保留旧驼峰存储键兼容。此项不改变客户端 HTTP 契约。
- 当前处于无存量数据的开发阶段：不增加数据迁移、表结构迁移、双键读写、补偿脚本或数据库清理步骤。

## Capabilities

### New Capabilities

- `user-persistence`：定义账号与健康资料的数据访问边界、内部结果、事务参与、约束错误和并发一致性保证。

### Modified Capabilities

- `health-data-model`：明确已定义病史标记的 JSONB 存储键使用下划线命名，继续区分未知与明确否。
- `user-profile`：明确病史 HTTP 驼峰字段与内部下划线字段的转换，响应不泄漏内部键，PATCH 语义保持不变。

## Impact

- 实现范围：新增用户持久化模块与内部 Schema；调整 `application/accounts.py`、`application/profiles.py`、`api/v1/auth.py`、`api/v1/users.py` 和资料请求／响应 Schema。
- 测试范围：调整现有用户 API 和资料模型行为测试；补足注册回滚、并发首次创建及命名转换的真实回归覆盖。新数据库跨模块测试按职责放在 `tests/integration/users/`。
- 文档范围：更新 `docs/architecture/backend-architecture.md`、`docs/architecture/database-design.md` 和 `docs/development.md`，记录分层职责及内外字段命名。
- 保持不变：URL、响应封装、普通渠道和权限规则、密码认证、Access／Refresh 行为、JWT 黑名单封装、表关系及现有依赖与配置。
- 不扩展到模型目录、推理任务、历史快照或推理结果的持久化；不引入通用 DAO、仓储基类、依赖注入容器或自定义 Unit of Work。
