# Spec Delta

## ADDED Requirements

### Requirement: 管理渠道密码登录与账号创建隔离
系统 MUST 仅允许账号可用、密码正确且当前具有管理权限的用户从 `manager` 登录并获得绑定管理渠道的 Access／Refresh；管理注册入口不得开放。不存在账号、密码错误、账号停用或没有管理权限 MUST 使用统一安全登录失败，不公开账号状态差异。

#### Scenario: 管理人员登录
- **WHEN** 可用管理账号使用正确用户名和密码调用管理渠道登录
- **THEN** 系统签发绑定 manager 的双 Token，沿用既有有效期和安全回复结构

#### Scenario: 普通账号尝试管理登录
- **WHEN** 普通账号使用正确密码调用管理渠道登录
- **THEN** 系统拒绝签发管理令牌，错误与其他无效登录一致，不因此赋予管理权限

#### Scenario: 管理渠道注册
- **WHEN** 客户端向 manager 发送注册请求或在普通注册中提交管理字段
- **THEN** 管理注册不可用，普通注册不能产生或提升管理权限

### Requirement: 管理操作重检渠道与当前权限
系统 MUST 对每次管理业务请求验证有效 Access、manager 渠道、账号可用状态及当前管理权限；此前签发的管理 Token 或普通渠道 Token 不能替代当前权限检查。

#### Scenario: 普通渠道令牌访问管理接口
- **WHEN** 一个具有管理权限的账号使用 android-cn Access 请求 manager 模型管理接口
- **THEN** 请求因渠道不匹配被拒绝，账号具有管理权限不能绕过令牌渠道边界

#### Scenario: 管理权限撤销后使用旧 Access
- **WHEN** 账号已经失去管理权限，但管理 Access 尚未过期
- **THEN** 管理业务请求被拒绝，不依赖令牌到期后才停止权限

#### Scenario: 普通渠道行为不变
- **WHEN** 有效普通渠道身份访问原有本人账号和资料接口
- **THEN** 继续遵循既有普通渠道、账号可用及渠道匹配规则，不额外要求管理权限

### Requirement: 管理 Refresh 轮换复检权限并允许独立登出
系统 MUST 为 manager 提供既有原子 Refresh 轮换和登出语义，刷新要求当前管理权限且不得绕过账号可用或渠道匹配；管理权限被撤销但账号仍可用时，用户仍能撤销自己有效的 manager Refresh。登出不承诺立即撤销 Access。

#### Scenario: 管理 Refresh 正常轮换
- **WHEN** 可用管理账号提交当前有效且匹配 manager 渠道的 Refresh
- **THEN** 系统签发新管理令牌对并撤销旧 Refresh，同一旧令牌并发使用至多成功一次

#### Scenario: 权限撤销后刷新
- **WHEN** 账号管理权限已撤销但其 manager Refresh 尚未过期
- **THEN** 系统拒绝签发新的管理令牌，不把已有 Refresh 当作持续授权

#### Scenario: 权限撤销后登出
- **WHEN** 账号仍可用但已失去管理权限，提交自己的有效 manager Refresh 登出
- **THEN** 系统撤销该 Refresh，不要求仍具有管理权限，也不影响其他独立登录的 Refresh

#### Scenario: 跨渠道刷新或登出
- **WHEN** manager Refresh 被提交到普通渠道，或普通渠道 Refresh 被提交到 manager
- **THEN** 系统拒绝跨渠道操作，不能转换令牌渠道或撤销不匹配的登录
