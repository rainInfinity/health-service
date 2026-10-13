# user-profile Specification

## Purpose

让同一账号在五个普通渠道安全地读取和逐步完善自己的当前健康资料，且不会把未填写的健康信息误记为实测值或明确否认，也不会让请求访问其他人的敏感资料。

## Requirements

### Requirement: 本人健康资料读取
系统 MUST 根据有效且渠道匹配的 Access 确定当前账号，向本人返回其健康资料；账号尚无 `Profile` 时 MUST 返回语义等同于未填写的资料，而不是暴露异常或创建无授权记录。

#### Scenario: 跨普通渠道读取同一资料
- **WHEN** 一个账号在 `android-cn` 更新资料，然后持有该账号的有效 `wxapp` Access 读取 `GET /users/me/profile`
- **THEN** 两个渠道读到同一份当前资料，不按渠道建立副本

#### Scenario: 已有账号未建立资料
- **WHEN** 一个已有账号尚无资料记录并调用本人资料读取
- **THEN** 返回姓名、出生日期、身高、体重、病史等未填写值及未知性别 `0`，不会返回其他用户的资料

### Requirement: 部分更新与明确清空
系统 MUST 允许本人以 `PATCH /users/me/profile` 部分更新资料；未提供的字段 MUST 保持原值，可空字段显式为 `null` 时 MUST 清空，性别使用 `0` 表示未知且不可用 `null` 替代。

#### Scenario: 只修改姓名
- **WHEN** 用户只提交新的 `name`
- **THEN** 系统只更新姓名，不清空既有的出生日期、身高、体重或病史

#### Scenario: 清空可空测量值与重置性别
- **WHEN** 用户提交 `heightCm: null` 和 `gender: 0`
- **THEN** 身高变为未填写，性别变为未知；其他缺席字段保持原值

#### Scenario: 未建立资料时首次更新
- **WHEN** 现有账号没有资料记录且本人提交有效的资料 PATCH
- **THEN** 系统为该账号创建一份以其账号 ID 为主键的资料，并保存所提交的值

### Requirement: 健康资料数值与病史含义
系统 MUST 拒绝无效性别和非正数的非空身高／体重；结构化病史 MUST 区分键未提供、值为 `false` 与值为 `null`，不可把缺失的健康标记默认成 `false`。

#### Scenario: 拒绝无效性别或测量值
- **WHEN** 用户提交 `gender: 3`、`heightCm: 0` 或负数 `weightKg`
- **THEN** 更新被拒绝，原有资料保持不变，客户端收到安全的校验错误

#### Scenario: 局部修改结构化病史
- **WHEN** 已保存 `isDiabetic: true`，用户仅提交 `historyJson: {"isHypertensive": false}`
- **THEN** 已有的 `isDiabetic` 不变，`isHypertensive` 被明确保存为 `false`

#### Scenario: 清空结构化病史键
- **WHEN** 用户提交 `historyJson: {"isDiabetic": null}` 或 `historyJson: null`
- **THEN** 前者只移除该键并保留其他键，后者清空整个结构化病史；两者均不将未知标记转为 `false`

### Requirement: 敏感资料仅对本人开放
系统 MUST 从 Access 中识别资料归属，不能允许客户端通过请求体或路径指定其他用户；无效令牌、停用账号或不匹配的渠道均不能读取或修改资料。

#### Scenario: 越权访问尝试
- **WHEN** 客户端尝试携带其他用户的 `userId`，或使用另一渠道的 Access 请求本人资料接口
- **THEN** 系统不会读取或改写指定的其他用户资料，渠道不匹配的请求被拒绝
