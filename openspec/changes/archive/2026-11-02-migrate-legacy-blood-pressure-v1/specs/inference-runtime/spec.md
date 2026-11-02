# Spec Delta

## ADDED Requirements

### Requirement: 推理执行受独立硬时限约束
消费者 MUST 对所有算法执行使用正整数配置 `INFERENCE_EXECUTION_TIMEOUT_SECONDS`（默认 300 秒）施加硬时限；该时限独立于数据库租约和心跳，续约 MUST NOT 延长执行时限。

#### Scenario: 算法超过执行时限
- **WHEN** 算法执行超过配置时限且任务仍持有有效执行权
- **THEN** 消费者 MUST 强制终止该次计算，将任务以固定安全失败码 `INFERENCE.EXECUTION_TIMEOUT` 提交为 `FAILED`，不得保存成功结果、指标或权威输出；仅在失败终态提交后确认消息

#### Scenario: 超时失败未能提交
- **WHEN** 强制终止后保存超时失败遭遇数据库错误或执行权失效
- **THEN** 消费者 MUST NOT 确认消息或伪造失败终态；任务恢复继续遵循既有租约到期和补发规则

#### Scenario: 执行在时限内完成
- **WHEN** 算法在配置时限内完成且执行租约有效
- **THEN** 任务继续遵循既有结果校验、成功事务、发布和确认语义

#### Scenario: 超时配置无效
- **WHEN** `INFERENCE_EXECUTION_TIMEOUT_SECONDS` 不是正整数
- **THEN** 服务启动 MUST 明确失败，不得以无限时限或静默默认值继续运行
