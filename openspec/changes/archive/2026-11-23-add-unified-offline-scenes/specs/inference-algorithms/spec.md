# Spec Delta

## MODIFIED Requirements

### Requirement: 正式离线计算遵循统一的阶段与门禁
正式离线计算 MUST 按读取与校验输入、预处理、质量检测、原始推理、结果后处理、成功输出的顺序执行。质量检测和后处理 MUST 使用该固定版本的规则，不设置跨算法默认评分或范围。任一阶段失败 MUST 终止对应单算法调用的后续阶段；未执行质检不得表示质量通过。任务的输入、波形、评分、预测和输出引用 MUST 相互隔离。场景算法 MUST 调用已注册成员的完整计算链，成员失败仅终止该成员，不阻止场景继续执行其他成员，不新增场景通用质量门禁。

#### Scenario: 质量拒绝阻止推理和成功输出
- **WHEN** 质量检测明确拒绝当前单算法输入
- **THEN** 该单算法调用不进入推理、后处理或成功输出，也不留下属于该调用的成功指标或权威输出文件

#### Scenario: 后处理先于结果组织
- **WHEN** 原始预测被固定后处理规则调整
- **THEN** 完整成功结果和实际产生的投影指标均使用同一份处理后数值

#### Scenario: 相邻任务不能继承阶段数据
- **WHEN** 同一实现先后执行两个输入和输出目录不同的任务
- **THEN** 第二个任务不继承第一个任务的评分、波形、预测或文件引用

#### Scenario: 场景成员拒绝不阻止其他成员
- **WHEN** PPG 场景中的一个成员报告质量拒绝，其他成员尚未执行
- **THEN** 拒绝成员不再计算，其余成员仍各自执行完整计算链，场景不沿用失败成员的质量结论

### Requirement: 首个质量拒绝终止本次计算
单算法执行 MUST 在报告首个明确质量拒绝后终止本次调用，不继续预测或生成成功结果，也不为收集更多原因而继续执行该成员的后续检查。质量拒绝仅说明触发终止的原因，不代表已完成所有输入的质量评价。场景中的成员质量拒绝 MUST 作为该成员失败汇总，不要求终止其他成员或将整个场景直接标记为质量拒绝。

#### Scenario: 预测前明确拒绝
- **WHEN** 单算法在预测前报告一个合法质量拒绝
- **THEN** 该调用不再进入预测或后续检查，不返回该成员的部分成功结果

#### Scenario: 场景不为拒绝成员收集额外原因
- **WHEN** 场景成员报告首个质量拒绝
- **THEN** 场景保存其受控原因并继续其他成员，不再次运行该成员或继续其内部检查

## ADDED Requirements

### Requirement: 统一离线场景具有固定成员与明确身份
系统 MUST 提供 `统一PPG场景` 对应的 `ppg.active.offline.all.v1`，成员固定为 `hr_br` 对应 `ppg.active.offline.hr_br.v1`、`spo2` 对应 `ppg.active.offline.spo2.v1`、`diabetes` 对应 `ppg.active.offline.diabetes.v1`；以及 `统一PPG+ACC场景` 对应的 `ppg_acc.active.offline.all.v1`，唯一成员为 `bp` 对应 `ppg_acc.active.offline.bp.v1`。两者 MUST 为非示例正式执行器，`all.v1` 不自动扩充成员。

#### Scenario: PPG 场景执行全部固定成员
- **WHEN** 合法 PPG 场景任务执行并完成汇总
- **THEN** 结果恰好包含 hr_br、spo2、diabetes 三项，不要求客户端指定成员或逐项创建任务

#### Scenario: PPG ACC 场景只测血压
- **WHEN** 合法 PPG ACC 场景任务执行并完成汇总
- **THEN** 结果恰好包含 bp，不额外执行 HR/BR、血氧或糖尿病风险

#### Scenario: 新算法不会自动进入旧场景
- **WHEN** 系统增加其他独立算法或成员的新实现修订
- **THEN** 两个 all.v1 继续使用固定成员与原实现修订，不通过最新版本或注册表枚举改变组合

### Requirement: 场景输入和资源固定于一个发布版本
PPG 场景 MUST 要求 `ppg` CSV 及 `spo2`、`feature_names`、`stable_features`、`diabetes` 制品；PPG ACC 场景 MUST 要求 `ppg`、`acc` CSV 及 `sbp`、`dbp`、`normalize` 制品。制品格式 MUST 沿用对应成员规则，发布时集合完整且文件存在。两个场景 MUST 拒绝非空参数，不要求额外必需资料或客户端元数据；成员只能读取同一次任务的原始文件、冻结快照及场景版本制品，不读取其他模型的渠道当前选择。

#### Scenario: 一次上传供多个成员读取
- **WHEN** 用户为 PPG 场景上传一份原始 PPG 并提交
- **THEN** 三个成员使用该任务的同一原始输入，不额外要求上传副本，不将处理后单通道 PPG 作为其他成员输入

#### Scenario: 版本切换不改变已提交场景
- **WHEN** 场景提交后渠道切换场景版本或独立算法版本
- **THEN** 已提交任务继续使用原场景版本的制品及固定成员实现，不混用其他发布版本

#### Scenario: 发布配置缺少资源
- **WHEN** 待发布场景版本缺少必需制品、物理文件不存在或提供额外制品及非空参数
- **THEN** 发布被拒绝，不以运行时部分成功能力放宽发布完整性要求

#### Scenario: 不提升所有成员的输入门槛
- **WHEN** 原始 PPG 可以被读取，但仅满足部分成员的数据长度或采样条件
- **THEN** 各成员按自身规则成功或失败，场景不因某成员需要更长输入而提前拒绝其余成员，也不补帧、插值或重采样满足门槛

### Requirement: 成员错误只终止对应成员
场景 MUST 隔离成员资源加载、输入校验、质量拒绝、执行异常和结果错误，将其记录为该成员失败并继续其他成员；不可用制品不得自动替换为其他版本。成员调用内部的原子成功规则 MUST 保持，失败成员不返回部分指标或成功文件。场景 MUST NOT 吞掉进程终止或整体硬超时，也不得把无法完成汇总的场景错误伪装成全部成员已完成。

#### Scenario: 制品不兼容仍保留其他成功
- **WHEN** 糖尿病模型无法加载或其维度不匹配，而 HR/BR 与血氧正常
- **THEN** diabetes 记录 INFERENCE.INVALID_ARTIFACT，其他成功成员的完整结果、指标与文件仍被保留

#### Scenario: 发布后成员文件缺失
- **WHEN** 已发布场景的 diabetes 制品在实际使用前缺失，其他资源与原始输入仍可用
- **THEN** diabetes 按制品不可用失败，HR/BR 与血氧仍可执行；场景不回退到其他制品

#### Scenario: 中间成员意外异常不阻止后续成员
- **WHEN** HR/BR 已成功、血氧抛出普通未预期异常、糖尿病尚未执行
- **THEN** spo2 记录安全执行失败，diabetes 仍执行；异常正文不进入汇总

#### Scenario: HR BR 内部仍不可部分成功
- **WHEN** HR 有限但 BR 非有限
- **THEN** hr_br 整项失败，不单独保留 HR；其他场景成员不受该失败影响

### Requirement: 场景返回固定分组结果与测量结局
场景完整计算载荷 MUST 恰好包含 `outcome`、`results`。results MUST 包含全部固定成员，每项恰好含 `algorithmKey`、`status`、`result`、`failureCode`、`failureMessage`、`qualityFailure`。成员 status 只能为 SUCCEEDED 或 FAILED；成功 result 保留对应单算法完整计算载荷，失败字段均为空；失败 result 为空并提供安全失败字段。所有成功时 outcome MUST 为 ALL_SUCCEEDED，部分成功为 PARTIALLY_SUCCEEDED，全部失败为 ALL_FAILED；不返回缺项、处理中成员或跨算法汇总质量分数。

#### Scenario: 混合成功失败的完整汇总
- **WHEN** HR/BR、血氧成功，糖尿病质量拒绝
- **THEN** outcome 为 PARTIALLY_SUCCEEDED，三项均存在，前两项保留其完整载荷，diabetes 的 result 为 null 且只公开安全失败信息

#### Scenario: 所有成员失败仍有可解释结果
- **WHEN** 所有固定成员都完成调用但均失败
- **THEN** outcome 为 ALL_FAILED，每项保留自身失败信息，不以空对象、默认指标或场景级 quality 代替

#### Scenario: 单成员场景的结局
- **WHEN** PPG ACC 场景的 bp 分别成功或失败且汇总正常完成
- **THEN** outcome 分别为 ALL_SUCCEEDED 或 ALL_FAILED，不产生 PARTIALLY_SUCCEEDED

### Requirement: 场景指标与成功成员严格一致
场景 MUST 只合并成功成员的原指标代码、类型、单位和数值：PPG 为 hr、br、spo2、diabetes_probability，PPG ACC 为 sbp、dbp。失败成员不产生指标行或默认值，risk_level 仍只在糖尿病完整结果中。场景 MUST 校验成员集合、实现标识、成功载荷、失败字段、outcome、指标及文件相互一致；伪造或损坏的最终汇总不得被保存为成功结果。

#### Scenario: 只投影成功成员
- **WHEN** HR/BR 与血氧成功，diabetes 失败，版本选中全部四个指标
- **THEN** 只保存 hr、br、spo2，数值等于各成功载荷中的原值，不创建 diabetes_probability 或 risk_level 投影

#### Scenario: 全部失败没有指标
- **WHEN** 场景正常完成并产生 ALL_FAILED
- **THEN** 指标集合为空，所有成员失败仍作为权威完整结果保留

#### Scenario: 汇总被错误修改
- **WHEN** 成员成功状态与载荷不匹配、成员缺失、algorithmKey 不匹配、outcome 不符或指标与成功载荷不一致
- **THEN** 最终汇总以 RESULT.INVALID_OUTPUT 拒绝，不部分保存该损坏汇总
