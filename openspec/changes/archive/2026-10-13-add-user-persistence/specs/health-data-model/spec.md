# Spec Delta

## MODIFIED Requirements

### Requirement: 健康资料区分未知与实测值
系统 MUST 支持姓名、出生日期、身高、体重、病史文字和结构化病史逐步填写；未知测量值使用 `NULL`，性别 `0` 表示未知，已知结构化病史标记缺失时不得默认为 `false`。已定义病史标记 MUST 在内部数据和持久化 JSON 中使用 `is_diabetic`、`is_hypertensive`，不得将 HTTP 的 `isDiabetic`、`isHypertensive` 别名作为这些字段的存储键。

#### Scenario: 部分资料未填写
- **WHEN** 账号资料未提供姓名、出生日期、身高、体重或结构化病史标记
- **THEN** 可选字段保持 `NULL` 或缺少对应 JSON 键，性别可为 `0`，系统不将未知身高体重写成 `0`

#### Scenario: 无效资料值
- **WHEN** 写入不在 `0`、`1`、`2` 范围内的性别，或非空且不大于零的身高／体重
- **THEN** 数据库拒绝该值

#### Scenario: 已定义病史标记以内部键持久化
- **WHEN** 业务资料更新保存糖尿病为 `true`、高血压为 `false`
- **THEN** 持久化病史为 `is_diabetic: true`、`is_hypertensive: false`，这两个已定义字段不同时生成对应驼峰存储键

#### Scenario: 未提供标记不制造默认值
- **WHEN** 业务资料更新只保存糖尿病标记且此前没有高血压标记
- **THEN** 持久化病史包含 `is_diabetic`，不产生 `is_hypertensive`，不会把缺失标记写成 `null` 或 `false`
