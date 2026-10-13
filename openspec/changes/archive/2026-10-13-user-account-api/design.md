# 设计

## 背景与约束

动机见 [提案](proposal.md)，对外行为见 [用户认证规格](specs/user-auth/spec.md)和[健康资料规格](specs/user-profile/spec.md)。现有 `User` 继承 `AbstractUser`，`Profile` 共享用户主键且允许缺失；`config.urls` 只有空的 `NinjaExtraAPI`，未安装 JWT 库，也未初始化渠道目录行。架构要求普通渠道共用用例、ASGI 异步入口与同步事务边界，并禁止用 Django Session 代替 API 的用户认证。

## 目标与非目标

**目标：** 在现有模型上完成五个普通渠道可用的账号、双 Token 和本人资料流程；通过独立的统一响应和日志变更取得相同的 `requestId` 与安全错误文案。

**非目标：** 不为渠道复制用户记录，不创建 `manager` 路由、短信服务、找回密码、修改用户名／密码或任务 API；不将旧 Session 或旧接口自动迁移为新凭证。

## 技术决策

### 1. 单组路由和明确的普通渠道边界

在 `health_service.api.v1` 装配单组异步 handler，并只允许五个普通渠道 label；`manager` 不在这组路由中。注册、登录、刷新、登出使用 `/api/v1/<channel>/auth/register|login|refresh|logout`，本人账号和资料使用 `/api/v1/<channel>/users/me`、`/users/me/profile`。渠道白名单表示 HTTP 接入范围，不依赖目前未灌入任何行的 `Channel` 表；将来任务创建仍须按任务规则检验渠道目录。比每渠道复制 controller 更易保证相同契约，且不会将管理端入口悄然开放。

公开注册只接受 `username` 和 `password` 的 JSON；使用 Django 用户名规范化、`validate_password`、`create_user`，在单个同步 `transaction.atomic()` 中建立用户和空资料，数据库唯一约束作为并发冲突的最终保证。手机号保持 `NULL`，`is_staff` 与 `is_superuser` 不对外赋值；重复用户名仅转译已知唯一约束，其他数据库错误不伪装成冲突。注册成功返回账号基本字段并要求后续单独登录。密码登录使用 Django 认证能力，用户名不存在、密码错误和停用账号返回同一种对外凭证错误。

### 2. JWT 适配、生命周期与并发刷新

优先验证 `django-ninja-jwt` 与锁定的 Django 5.2、Django Ninja 1.7.1、Ninja Extra 0.31.7、Pydantic v2 在异步鉴权和 Refresh 黑名单迁移上的行为；只有验证通过才锁定 Web 依赖与 `uv.lock`。不注册默认 obtain／refresh／verify 路由，而在 `infrastructure.auth` 对库的发证、校验和黑名单做薄适配，并确保签发令牌的用户 ID、渠道、类型、有效期与待撤销令牌记录一致。拟定 Access 10 分钟、Refresh 7 天，并用独立 `JWT_SIGNING_KEY` 而非公开或硬编码密钥；新增配置同步 `.env.template`。有效期可由设置调整，对外仍承诺 Access 短于 Refresh。

业务请求只接受 Access：验证签名、过期及类型后读取当前用户，核对 `is_active` 与请求渠道；刷新和登出只接收请求体里的 Refresh，不依赖有效 Access。渠道不符统一为权限错误；不能以刷新令牌访问业务路由，也不能把令牌中的 `is_staff` 快照当成实时权限。登出只撤销提交的当前 Refresh，已签发 Access 自然到期；账号停用由鉴权时查当前用户立即阻断。

库自带的“轮换＋黑名单”不能未经验证就视为并发原子操作。刷新及登出进入同一同步事务，按旧 Refresh 的 `jti` 锁定其 outstanding 记录，在锁内重新检验黑名单与用户状态，然后撤销旧令牌并签发该渠道的新令牌；先在 PostgreSQL 中验证同一旧令牌的并发刷新至多成功一次。如果所选库不能暴露完成该事务所需的可靠原语，先修订认证设计与依赖选择，不退化为可重放刷新。过期黑名单记录按所选库的受支持命令定期清理，部署说明列明这项维护责任。

### 3. 健康资料的读取与部分更新

`schemas.requests` 限制外部资料字段并禁止额外字段，`schemas.responses` 声明公开的账号与资料字段并由统一响应层包装。对外使用 `birthDate`、`heightCm`、`weightKg`、`medicalHistory`、`historyJson` 等明确别名，内部 ORM 字段名保持原状；身高／体重按十进制数值处理，不以零表示未知。账号 ID 只能来自鉴权结果，不接受请求体或路径指定目标用户。

资料 GET 不因关联缺失而触发写入，直接投影一份姓名等为 `null`、性别为 `0` 的空资料。PATCH 根据实际提供的字段构造更改集：缺席不变、可空字段 `null` 清空、性别 `null` 拒绝且 `0` 为未知。`historyJson` 缺席不变、顶层 `null` 清空、对象按已明确的 `isDiabetic`／`isHypertensive` 键合并；键值 `null` 删除该键，`false` 保留为明确否，未改动的已有键（包括未在本阶段开放编辑的其他键）仍保留。拒绝未知外部键以免写入未定义的医疗语义。

PATCH 在一个同步事务内锁定用户行，串行化首建资料与读改写 JSON，保存后返回最新的公开投影；请求值先完成校验，数据库检查约束仍保留为最终防线。异步 HTTP handler 使用 `sync_to_async(..., thread_sensitive=True)` 调用包含事务、密码哈希与同步 ORM 的用例，不在事件循环中直接执行同步数据库流程。

### 4. 跨变更契约

`unified-api-responses` 定义 `code/message/data/requestId`、HTTP 状态与异常映射，用户 handler 只返回业务结果或受控业务错误。`structured-application-logging` 提供服务端 `requestId`、脱敏日志上下文；JWT 适配只提供安全的用户 ID、渠道和业务结果标记，绝不让令牌或病史进入日志。先建立共享底座再将五个渠道路由暴露给客户端。

## 风险与权衡

- **库与异步栈的兼容性、黑名单轮换并发语义未经当前项目验证** → 首个实施步骤用真实 PostgreSQL 和 HTTP 路径验证；不兼容时修改方案而不是放松单次刷新要求。
- **事务外发证或普通轮换产生可重放令牌** → 锁定旧 `jti`，锁内重检、撤销与签发，并在并发测试中检验唯一成功。
- **登出后 Access 仍可能短时有效** → 维持 10 分钟短有效期并在客户端清除凭证；若未来需要立即失效须另设计，不宣称本阶段完成。
- **健康资料中存在未定义的历史 JSON 键** → 只开放已确定键的写入，部分更新保留未触及键，避免无意擦除旧数据。
- **单账号跨渠道登录可能被误当作跨渠道令牌通行** → 测试相同账号可分别登录、不同渠道 Access／Refresh 不可互用。

## 部署与回退

先落地共享响应与日志能力，再引入认证依赖、独立签名密钥、黑名单迁移及用户路由；迁移后在已有账号、空资料和多渠道情况下验证请求。发布时客户端必须重新登录；部署环境提供 `JWT_SIGNING_KEY` 并安排过期令牌清理。若回退接口，先停止签发新令牌，等待短期 Access 到期；不得在仍需验证 Refresh 时直接删除黑名单表或复用失效签名密钥。