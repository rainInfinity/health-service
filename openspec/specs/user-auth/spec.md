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

### Requirement: Access 与 Refresh 有效期可独立配置
系统 MUST 允许通过 `JWT_ACCESS_TOKEN_LIFETIME_SECONDS` 和 `JWT_REFRESH_TOKEN_LIFETIME_SECONDS` 分别配置 Access 与 Refresh 的有效期，单位为正整数秒；未设置时 MUST 分别使用 `604800`（7 天）和 `2592000`（30 天）。普通渠道与 manager MUST 使用同一组配置，登录及成功的 Refresh 轮换 MUST 为新令牌使用各自配置的期限，不改变既有渠道绑定、权限检查和撤销规则。

#### Scenario: 缺省配置保持原有期限
- **WHEN** 两个有效期环境变量均未设置，合法用户登录或成功刷新
- **THEN** 新 Access 的有效期为 7 天，新 Refresh 的有效期为 30 天，回复结构保持不变

#### Scenario: 独立设置其中一种有效期
- **WHEN** 仅将 Access 有效期设置为 `900` 秒，合法用户登录
- **THEN** 新 Access 的 `exp - iat` 为 `900`，新 Refresh 仍采用默认 `2592000` 秒，不要求同时设置两项

#### Scenario: 普通渠道和管理渠道采用配置期限
- **WHEN** 两个有效期分别设置为 `900` 和 `7200` 秒，普通账号和具有管理权限的账号分别在所属渠道成功登录
- **THEN** 两个渠道新签发 Access 的 `exp - iat` 均为 `900`，Refresh 均为 `7200`，令牌仍绑定各自渠道

#### Scenario: Refresh 轮换使用当前配置
- **WHEN** 合法 Refresh 在正确渠道成功轮换
- **THEN** 新 Access 与 Refresh 分别采用当前签发配置的期限，旧 Refresh 按既有原子撤销规则失效，不通过保留旧失效时间绕过新配置

### Requirement: 有效期配置沿用环境优先级并拒绝非法值
系统 MUST 在启动时读取有效期配置，进程环境 MUST 优先于项目 `.env`；显式空值、非整数、零或负数 MUST 导致明确指出对应配置项的启动错误，MUST NOT 静默回退到默认值或低优先级来源。两项配置 MUST 允许分别省略，并分别应用对应默认值。

#### Scenario: 进程环境覆盖文件配置
- **WHEN** `.env` 设置 Access 为 `1800` 秒、Refresh 为 `14400` 秒，而进程环境分别设置为 `900` 和 `7200` 秒
- **THEN** 新签发期限采用进程环境的 `900` 和 `7200` 秒

#### Scenario: 空进程值不得回退
- **WHEN** `.env` 为某个有效期提供合法值，但同名进程环境变量显式为空
- **THEN** 启动失败并指出该配置项，不采用 `.env` 值或默认值

#### Scenario: 非法有效期配置拒绝启动
- **WHEN** 任一有效期配置为 `0`、负数、小数或非数字文本
- **THEN** 系统明确拒绝启动并指出对应配置项，不签发采用静默替代期限的令牌

### Requirement: 有效期配置只影响重启后的新签发
调整有效期配置 MUST 在重启签发服务后影响新签发令牌；已有令牌 MUST 继续使用各自的 `exp`，仍受既有签名、撤销、账号状态及权限校验约束。配置变更 MUST NOT 追溯延长或缩短已有令牌的期限。JWT 的 `exp`、`iat` MUST 保持标准数值格式，MUST NOT 被日志或 API 日期时间展示规则转换为北京时间字符串。

#### Scenario: 缩短期限不提前撤销已有令牌
- **WHEN** 签发服务将有效期缩短并重启，旧 Access 和旧 Refresh 尚未达到各自原有 `exp`，且通过其他既有校验
- **THEN** 旧令牌仍按原期限有效，新签发令牌采用缩短后的期限

#### Scenario: 延长期限不复活旧过期令牌
- **WHEN** 签发服务将有效期延长并重启，而旧令牌已达到自身 `exp`
- **THEN** 旧令牌仍被拒绝，不因新配置获得额外有效时间

#### Scenario: 时间展示调整不修改 JWT 声明
- **WHEN** API 业务日期时间已经改为北京时间字符串，用户成功登录或刷新
- **THEN** Access 与 Refresh 的 `exp`、`iat` 仍为标准数值，签名及到期验证继续遵循 JWT 契约

