# user-auth Specification

## Purpose

为五个普通渠道提供共用账号的注册、密码认证及渠道绑定的双 Token 身份凭证，使客户端能够安全访问本人接口，同时明确刷新轮换、登出和无效令牌的边界。

## Requirements

### Requirement: 普通渠道共用账号注册
系统 MUST 允许在 `android-cn`、`android-intl`、`ios-cn`、`ios-intl` 和 `wxapp` 使用全局唯一用户名与符合密码规则的密码注册同一个账号；注册不得赋予管理权限或要求未验证的手机号。

#### Scenario: 从一个普通渠道注册后在另一渠道登录
- **WHEN** 用户在 `android-cn` 完成密码注册，随后在 `ios-intl` 使用同一用户名和密码登录
- **THEN** 两次操作指向同一个账号 ID，且注册时密码只以哈希形式保存、手机号保持未绑定

#### Scenario: 重复用户名或无效注册信息
- **WHEN** 使用已有用户名注册，或提交不符合密码规则的注册信息
- **THEN** 系统拒绝创建第二个账号，对重复用户名返回冲突错误，对无效输入返回校验错误

#### Scenario: 越权注册字段与管理渠道
- **WHEN** 普通渠道注册请求包含 `is_staff` 或其他权限字段，或向 `manager` 渠道发送注册请求
- **THEN** 请求不能据此创建管理账号，且 `manager` 注册接口不可用

### Requirement: 密码登录签发渠道绑定的双 Token
系统 MUST 在用户名和密码验证成功且账号可用时签发具有失效时间的 Access Token 和 Refresh Token；登录失败 MUST 不透露用户名是否存在或账号是否停用。

#### Scenario: 分别从两个普通渠道登录
- **WHEN** 同一账号依次在 `android-cn` 和 `wxapp` 输入正确密码登录
- **THEN** 系统对同一账号签发各自绑定登录渠道的 Access 和 Refresh，不创建两个账号或两份健康资料

#### Scenario: 无效凭证与停用账号
- **WHEN** 用户名不存在、密码错误，或账号已停用
- **THEN** 系统拒绝签发令牌，并向客户端返回相同的通用凭证错误文案

### Requirement: 受保护的本人账号访问
系统 MUST 只接受 `Authorization: Bearer` 中有效且类型为 Access、渠道与请求路径一致的令牌访问普通渠道的本人账号接口；账号停用时 MUST 拒绝现存 Access。

#### Scenario: 读取本人账号
- **WHEN** 有效 `android-cn` Access 调用该渠道的 `GET /users/me`
- **THEN** 返回该令牌所属账号的公开字段，不返回密码哈希或其他账号的数据

#### Scenario: 无效令牌、错用类型与跨渠道访问
- **WHEN** 请求缺少 Access、携带过期 Access、以 Refresh 当作 Access，或以 `android-cn` Access 访问 `wxapp` 路径
- **THEN** 前三种请求被拒绝为未认证，渠道不匹配的请求被拒绝且不返回本人资料

### Requirement: Refresh 轮换和撤销
系统 MUST 仅在专用刷新接口接收有效 Refresh，并核对其渠道与账号可用状态；刷新成功 MUST 同时返回新的 Access 和 Refresh，并使旧 Refresh 不再可用，包括旧令牌的重复或并发使用。

#### Scenario: Access 过期后刷新
- **WHEN** Access 已过期但 Refresh 有效，账号可用且刷新路径渠道匹配
- **THEN** 刷新无需有效 Access 即可成功，并返回该渠道的新 Access 和新 Refresh

#### Scenario: 旧 Refresh 重放或渠道不符
- **WHEN** 已轮换的 Refresh 再次被提交，或 Refresh 被提交到其他普通渠道的刷新路径
- **THEN** 系统拒绝签发新的令牌；同一旧 Refresh 的并发刷新至多成功一次

#### Scenario: 账号停用后的刷新
- **WHEN** 持有尚未到期 Refresh 的账号被停用
- **THEN** 系统拒绝刷新，不因已有令牌继续签发新的访问凭证

### Requirement: 当前 Refresh 登出
系统 MUST 允许持有有效 Refresh 的用户在其绑定渠道撤销这一枚 Refresh，无需提供尚未过期的 Access；登出不承诺立即撤销已签发的 Access，也不撤销其他登录的 Refresh。

#### Scenario: Access 过期后登出
- **WHEN** Access 已过期且当前 Refresh 仍有效，用户提交该 Refresh 登出
- **THEN** 登出成功，旧 Refresh 随后不能再刷新；此前 Access 是否可用仍以其自身有效期及账号状态为准

#### Scenario: 其他渠道的登录保持独立
- **WHEN** 账号在 `android-cn` 登出，同时还持有从 `ios-cn` 登录得到的独立 Refresh
- **THEN** `android-cn` 的当前 Refresh 失效，`ios-cn` 的 Refresh 不因该次登出而失效
