# Spec Delta

## MODIFIED Requirements

### Requirement: 普通用户只看到安全失败摘要
系统 MUST 在任务失败详情中向普通用户返回稳定失败代码、安全摘要和完成时间，并仅允许额外公开符合质量拒绝契约的 qualityFailure。场景汇总中的失败成员 MUST 仅公开 algorithmKey、status、空 result、failureCode、failureMessage 和符合质量拒绝契约的可空 qualityFailure。系统 MUST NOT 直接公开内部诊断详情、异常堆栈、原始输入、完整健康资料快照或本地绝对路径；类型化成员结果和质量信息不构成开放任意 failureDetail 的许可。

#### Scenario: 读取计算或结果失败
- **WHEN** 用户查询具有内部失败诊断的本人任务
- **THEN** 返回安全失败码及固定摘要，仅在满足质量拒绝契约时额外返回白名单质量信息，不把诊断中的内部字段直接序列化给用户

#### Scenario: 读取场景内成员失败
- **WHEN** 用户查询本人正常完成汇总的场景，其中某成员失败
- **THEN** 只在对应成员中返回安全失败代码、摘要及合法质量明细，不暴露内部失败诊断，也不把成员失败移到顶层覆盖整个场景

### Requirement: 质量信息只从合法失败记录投影
系统 MUST 只在任务为 FAILED、失败码为 INFERENCE.QUALITY_REJECTED 且原因及所需明细合法时投影非空的任务顶层 qualityFailure；其余情况该顶层字段 MUST 为 null。投影 MUST 显式选择允许公开的字段，历史记录缺失或损坏不得使详情查询失败，也不得补造原因、分数或阈值。场景 result.results 内的成员 qualityFailure MUST 单独遵循对应成员的 FAILED 状态、质量失败码和同一白名单契约，不以任务 SUCCEEDED 为由抹去合法成员拒绝信息。

#### Scenario: 查询其他状态或普通失败
- **WHEN** 用户查询草稿、已提交、运行、成功任务或普通失败任务
- **THEN** 顶层 qualityFailure 为 null；即使普通失败的内部诊断含类似质量字段，也不将其解释为质量拒绝

#### Scenario: 查询缺少或损坏质量明细的历史失败
- **WHEN** 质量拒绝记录没有质量信息、原因未知、原因与明细不匹配、缺少必需数值或数值非法
- **THEN** 详情正常返回已有安全失败摘要，顶层 qualityFailure 为 null，不尝试重新执行或补齐诊断

#### Scenario: 内部质量诊断附带私有字段
- **WHEN** 合法质量原因及明细之外还存在路径、原始数据或其他未声明诊断键
- **THEN** 详情仅投影该原因允许的字段，响应不包含这些额外字段或其内容

#### Scenario: 读取任务列表
- **WHEN** 用户查询本人当前渠道的任务列表
- **THEN** 系统维持摘要响应，不加载或返回质量拒绝明细

#### Scenario: 越权读取质量拒绝
- **WHEN** 用户查询其他账号或其他来源渠道的质量拒绝任务
- **THEN** 系统沿用任务归属规则返回资源不存在，不泄露原因、明细或任务存在性

#### Scenario: 场景成功与成员质量拒绝并存
- **WHEN** 场景正常完成且其中 diabetes 合法质量拒绝
- **THEN** 任务顶层 qualityFailure 为 null，result.results.diabetes.qualityFailure 保留合法原因及白名单明细，不为成功成员生成质量拒绝

## ADDED Requirements

### Requirement: 场景详情区分执行状态与测量结局
场景任务详情 MUST 保留现有响应包装、摘要、固定版本和时间字段。任务 SUCCEEDED MUST 表示场景已完成全部成员调用与合法汇总，而非所有测量成功；result.outcome MUST 明确为 ALL_SUCCEEDED、PARTIALLY_SUCCEEDED 或 ALL_FAILED。正常汇总的顶层 failureCode、failureMessage、qualityFailure MUST 为空。只有场景自身失败时任务为 FAILED 且 result 为空；查询成功仍使用 HTTP 200 和外层业务码 OK。

#### Scenario: 前端展示部分有效结果
- **WHEN** 本人 PPG 场景包含成功的 hr_br、spo2 和失败的 diabetes
- **THEN** 详情返回任务 SUCCEEDED、outcome 为 PARTIALLY_SUCCEEDED，前端可从各成功成员 result 读取数值并从 diabetes 读取失败信息

#### Scenario: 前端识别全部测量失败
- **WHEN** 场景正常汇总但所有成员失败
- **THEN** 详情仍返回任务 SUCCEEDED 和非空 result，outcome 为 ALL_FAILED，各成员失败均可读取，不以任务状态表示测量有效

#### Scenario: 场景任务自身失败
- **WHEN** 场景整体超时或汇总无法通过最终校验
- **THEN** 详情返回 FAILED、result 为 null 和安全任务级失败摘要，不提供未完成成员的中间结果

#### Scenario: 列表保持摘要而不隐含测量结论
- **WHEN** 用户查询包含场景任务的分页列表
- **THEN** 列表仍不加载完整 result；摘要中的 SUCCEEDED 仅表示场景处理完成，不能单独作为全部指标成功或医学有效的依据

### Requirement: 场景通过既有模型与任务接口调用
系统 MUST 通过现有模型管理流程为两个新场景配置对应名称、发布版本和渠道选择；用户 MUST 继续使用该渠道目录返回的 modelId 创建任务、上传 requiredInputs、提交和查询详情，不增加客户端任意成员或版本选择。独立算法目录、已有任务及旧结果格式 MUST 保持，不自动修改现有渠道选择或把历史单算法结果改造成场景结构。

#### Scenario: 配置并调用新场景
- **WHEN** 管理人员分别配置名称为统一PPG场景、统一PPG+ACC场景的合法场景模型并选择到目标渠道
- **THEN** 用户可从原模型目录获得场景 modelId、版本、requiredInputs 和 isExample=false，使用原任务接口完成一次场景测量

#### Scenario: 历史单算法详情不被改写
- **WHEN** 新场景发布后用户读取旧的 HR/BR、血氧、糖尿病风险或血压任务
- **THEN** 其固定模型、版本、状态、原始结果字段和下载逻辑名保持不变，不强制增加 results 或 outcome

#### Scenario: 非本人场景详情不可见
- **WHEN** 其他账号或不匹配来源渠道请求场景任务详情
- **THEN** 按既有任务归属规则拒绝，不泄露任何成员结果、失败信息或任务存在性
