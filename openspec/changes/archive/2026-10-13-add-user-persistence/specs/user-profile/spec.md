# Spec Delta

## MODIFIED Requirements

### Requirement: 健康资料数值与病史含义
系统 MUST 拒绝无效性别和非正数的非空身高／体重；结构化病史 MUST 区分键未提供、值为 `false` 与值为 `null`，不可把缺失的健康标记默认成 `false`。已定义病史字段 MUST 在 HTTP 边界完成驼峰字段与内部下划线字段的转换，转换不得改变按键合并及清空含义。

#### Scenario: 拒绝无效性别或测量值
- **WHEN** 用户提交 `gender: 3`、`heightCm: 0` 或负数 `weightKg`
- **THEN** 更新被拒绝，原有资料保持不变，客户端收到安全的校验错误

#### Scenario: 局部修改结构化病史
- **WHEN** 已保存 `is_diabetic: true`，用户仅提交 `historyJson: {"isHypertensive": false}`
- **THEN** 已有的 `is_diabetic` 不变，`is_hypertensive` 被明确保存为 `false`，响应仍使用 `isDiabetic`、`isHypertensive`

#### Scenario: 清空结构化病史键
- **WHEN** 用户提交 `historyJson: {"isDiabetic": null}` 或 `historyJson: null`
- **THEN** 前者只移除内部的 `is_diabetic` 键并保留其他键，后者清空整个结构化病史；两者均不将未知标记转为 `false`

## ADDED Requirements

### Requirement: 病史对外命名与存储命名分离
资料 API MUST 在请求和响应中继续使用已定义的 `isDiabetic`、`isHypertensive` 字段，并在内部数据与存储中使用对应的 `is_diabetic`、`is_hypertensive`。GET 与 PATCH 响应及 OpenAPI 描述 MUST 使用外部命名，不泄漏这两个内部键，不为缺席标记制造默认值；未涉及的扩展内容 MUST 保持原值。

#### Scenario: 写入后读取病史
- **WHEN** 用户提交 `historyJson: {"isDiabetic": true, "isHypertensive": false}`，随后读取本人资料
- **THEN** 数据库保存对应下划线键，PATCH 和 GET 的 `historyJson` 均返回 `isDiabetic: true`、`isHypertensive: false`，不返回这两个内部键

#### Scenario: 缺失病史键不补值
- **WHEN** 已保存的病史只包含 `is_diabetic: false`，用户读取本人资料
- **THEN** 响应病史只包含对应的 `isDiabetic: false`，不添加 `isHypertensive: null` 或 `isHypertensive: false`

#### Scenario: 保留未涉及的扩展内容
- **WHEN** 已有病史包含其他扩展内容，用户仅更新一个已定义病史标记
- **THEN** 保存和响应均保留原扩展内容，不递归重命名、删除或改写其键和值

#### Scenario: 客户端查询病史契约
- **WHEN** 客户端查看资料响应的 OpenAPI 描述
- **THEN** 已定义病史标记展示为可选的 `isDiabetic`、`isHypertensive`，不以 `is_diabetic`、`is_hypertensive` 作为这两个标记的公开属性
