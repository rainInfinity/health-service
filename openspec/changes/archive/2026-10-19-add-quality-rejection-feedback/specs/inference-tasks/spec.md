# Spec Delta

## MODIFIED Requirements

### Requirement: 普通用户只看到安全失败摘要
系统 MUST 在失败详情中向普通用户返回稳定失败代码、安全摘要和完成时间，并仅允许额外公开符合质量拒绝契约的 `qualityFailure`。系统 MUST NOT 直接公开内部诊断详情、异常堆栈、原始输入、完整健康资料快照或本地绝对路径；类型化质量信息不构成开放任意 `failureDetail` 的许可。

#### Scenario: 读取计算或结果失败
- **WHEN** 用户查询具有内部失败诊断的本人任务
- **THEN** 返回安全失败码及固定摘要，仅在满足质量拒绝契约时额外返回白名单质量信息，不把诊断中的内部字段直接序列化给用户

## ADDED Requirements

### Requirement: 质量拒绝详情按原因公开明细
本人任务详情 MUST 提供可空的 `qualityFailure`。合法质量拒绝的该对象 MUST 含稳定原因码 `reason` 和对应的 `details`；`LOW_SCORE` 的明细仅公开实际 `score`、`threshold`，`NO_VALID_SIGNAL` 的明细为 `null`。质量拒绝任务仍为 `FAILED`，`result` 为 `null`；详情查询成功仍使用 HTTP 200 和外层业务码 `OK`。

#### Scenario: 查看低分拒绝
- **WHEN** 用户成功查询本人当前渠道的合法 `LOW_SCORE` 质量拒绝任务
- **THEN** 详情返回 `INFERENCE.QUALITY_REJECTED`、安全提示和 `qualityFailure`，其中原因是 `LOW_SCORE`，明细为本次实际分数及阈值，查询不重新计算

#### Scenario: 查看无评分拒绝
- **WHEN** 用户成功查询本人当前渠道的合法 `NO_VALID_SIGNAL` 质量拒绝任务
- **THEN** 详情返回该原因且 `details` 为 `null`，不提供虚构分数或默认阈值

### Requirement: 质量信息只从合法失败记录投影
系统 MUST 只在任务为 `FAILED`、失败码为 `INFERENCE.QUALITY_REJECTED` 且原因及所需明细合法时投影非空 `qualityFailure`；其余情况 MUST 返回 `null`。投影 MUST 显式选择允许公开的字段，历史记录缺失或损坏不得使详情查询失败，也不得补造原因、分数或阈值。

#### Scenario: 查询其他状态或普通失败
- **WHEN** 用户查询草稿、已提交、运行、成功任务或普通失败任务
- **THEN** `qualityFailure` 为 `null`；即使普通失败的内部诊断含类似质量字段，也不将其解释为质量拒绝

#### Scenario: 查询缺少或损坏质量明细的历史失败
- **WHEN** 质量拒绝记录没有质量信息、原因未知、原因与明细不匹配、缺少必需数值或数值非法
- **THEN** 详情正常返回已有安全失败摘要，`qualityFailure` 为 `null`，不尝试重新执行或补齐诊断

#### Scenario: 内部质量诊断附带私有字段
- **WHEN** 合法质量原因及明细之外还存在路径、原始数据或其他未声明诊断键
- **THEN** 详情仅投影该原因允许的字段，响应不包含这些额外字段或其内容

#### Scenario: 读取任务列表
- **WHEN** 用户查询本人当前渠道的任务列表
- **THEN** 系统维持摘要响应，不加载或返回质量拒绝明细

#### Scenario: 越权读取质量拒绝
- **WHEN** 用户查询其他账号或其他来源渠道的质量拒绝任务
- **THEN** 系统沿用任务归属规则返回资源不存在，不泄露原因、明细或任务存在性
