# Spec Delta

## ADDED Requirements

### Requirement: 记录账号首次注册的普通渠道
系统 MUST 将新账号绑定到注册请求路径中的普通 API 渠道。来源 MUST 由服务端从已校验的路径渠道确定，不能由请求体指定；后续跨渠道登录 MUST NOT 改写该来源，普通账号响应 MUST NOT 暴露该字段。

#### Scenario: 从普通渠道注册账号
- **WHEN** 用户通过 `android-cn` 注册有效账号
- **THEN** 系统将 `android-cn` 记录为账号首次注册来源，注册响应仍只返回既有公开账号字段

#### Scenario: 后续从其他渠道登录
- **WHEN** 账号最初通过 `android-cn` 注册，之后通过 `ios-intl` 登录
- **THEN** 账号首次注册来源仍为 `android-cn`

#### Scenario: 请求体试图伪造来源
- **WHEN** 注册请求路径为 `android-cn`，请求体另行提供 `sourceChannel` 或其他来源值
- **THEN** 客户端提供的值不能覆盖路径来源，未声明字段按注册请求契约拒绝
