# Spec Delta

## ADDED Requirements

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
