# Spec Delta

## ADDED Requirements

### Requirement: 血压 v1 接受 PPG 与加速度离线输入
正式算法 `ppg_acc.active.offline.bp.v1` MUST 要求 `ppg`、`acc` 两个 CSV，不要求 frame 文件或客户端元数据。PPG MUST 使用毫秒 `timestampFrame` 和恰好一组完整 RGB 或 YUV 通道，每帧单行，可选 blockIndex 仅为整数 0。ACC MUST 包含毫秒 `timestampEvent` 及 accX、accY、accZ。必需数值 MUST 有限，各时间轴 MUST 严格递增且满足执行所需数据长度与采样率；无效输入 MUST 明确失败，不排序、选块、插值或补齐。

#### Scenario: 使用现有双传感器采集文件
- **WHEN** 合法单块 PPG 和 ACC 文件满足执行条件，没有 frame 文件和客户端元数据
- **THEN** 血压算法允许执行，不额外要求其他传感器文件

#### Scenario: 拒绝多块或损坏输入
- **WHEN** PPG 同帧多行、blockIndex 非整数 0、通道组不完整，或任一路必需数值非有限、时间轴不递增、采样率或数据长度不支持处理
- **THEN** 算法报告 `INFERENCE.INVALID_INPUT` 及明确受控原因，不生成默认血压或质量结论

### Requirement: 血压 v1 固定双模型与归一化制品
血压 v1 MUST 绑定 `sbp`、`dbp`、`normalize` 三个固定版本制品，后缀分别为 joblib、joblib、csv，拒绝非空参数。两个模型 MUST 各接受六维特征，归一化参数 MUST 提供 X1、age、pr、bmi、height 的有限均值与有限非零标准差。不兼容资源 MUST 按制品错误失败，不扫描 latest 或使用默认权重。可执行对象制品 MUST 限于已核验来源的受信发布，不将后缀检查宣称为安全反序列化。

#### Scenario: 缺失或不兼容制品
- **WHEN** 版本缺少任一制品，归一化参数缺列、标准差为零，或模型不支持六维特征
- **THEN** 系统拒绝发布配置或在实际加载时报告 `INFERENCE.INVALID_ARTIFACT`，不选择其他版本资源替代

#### Scenario: 固定计算规则不可由参数覆盖
- **WHEN** 版本提供阈值、滤波、峰配对或其他非空参数
- **THEN** 配置被拒绝，不覆盖血压 v1 的固定解释

### Requirement: 血压 v1 使用冻结资料并保留旧版缺失解释
血压 v1 MUST 只从任务提交快照读取 age_years、height_cm、weight_kg、gender，映射到旧版年龄、厘米身高、千克体重和 sex。年龄、身高、体重缺失 MUST 保留旧版零值路径，不能设为通用必需资料而提前拒绝。常规快照 gender 的 0、1、2 MUST 原样用于 sex，不新增编码或按后续资料更新重算。

#### Scenario: 缺失数值资料仍进入旧计算路径
- **WHEN** 已冻结资料中的年龄、身高或体重为空，gender 为 0
- **THEN** 对应数值资料按旧规则补零，sex 保留 0，执行不因缺少这些资料被通用必需资料检查拒绝

#### Scenario: 提交后更新资料
- **WHEN** 用户在提交后修改生日、身高、体重或性别
- **THEN** 已提交任务继续使用原快照，不读取修改后的资料

### Requirement: 血压 v1 保留旧版 PPG 质量门禁
血压 v1 MUST 对旧规则清洗后的 PPG 计算实际质量分数，使用固定阈值 70。分数严格低于 70 时 MUST 报告 LOW_SCORE 和实际比较值，停止后续 AO 特征与血压预测；分数等于阈值允许继续。算法 MUST NOT 新增 SCG 质量门禁，亦不得将非法评分或意外异常补为默认分数或质量结论。

#### Scenario: 质量门槛两侧
- **WHEN** 实际 PPG 分数分别为 69.999 和 70
- **THEN** 前者低分终止并保留实际比较值，后者可进入后续计算，但不保证最终成功

#### Scenario: 非法评分不代表质量拒绝
- **WHEN** 质量计算得到 NaN、无穷或抛出未预期异常
- **THEN** 按执行或契约错误失败，不伪造质量通过、低分或无可用信号结论

### Requirement: 血压 v1 保留旧版信号与特征解释
血压 v1 MUST 固定旧版 PPG 转换与清洗、基于负 accZ 的 SCG 清洗、AO 重建、峰检测及原始毫秒时标配对。配对 MUST 在 PPG 峰前 50–500 ms 内取最近 AO；PTT MUST 保留旧严格 IQR 筛选、绝对均值与六位小数规则，PR 保留旧时长取整及整数化规则。模型特征 MUST 按 PTT秒、年龄、PR、BMI、身高米、sex 排列，前五项按版本归一化，sex 原样保留，不新增重采样、时钟校准或训练。

#### Scenario: 新旧数值对照
- **WHEN** 新旧计算链使用同一合法文件、资料、配套制品和可核验环境，得到有效有限结果
- **THEN** 清洗波形、质量分数、PTT、PR、六维特征和两个预测在明确记录并解释的浮点容差内一致，分支和整数规则一致

#### Scenario: 两路起点不同
- **WHEN** PPG 与 ACC 使用同一时标但起始时间不同
- **THEN** 算法保留原始时间差配对，不分别归零或自动平移对齐

### Requirement: 血压 v1 保留旧版零值回退并区分非法计算
血压 v1 MUST 保留旧 AO 重建的频率条件回退和无选中模态时的零信号行为；无有效 AO/PPG 配对返回空 PTT 时 MUST 将 PTT 置为 0 后继续旧特征和双模型预测，不新增拒绝。IQR 筛选清空得到的 NaN MUST NOT 被当作无配对补零；非法中间值导致执行失败或非法预测时 MUST 终止，不能伪造成功血压。

#### Scenario: 无有效配对仍预测
- **WHEN** 合法数据经旧规则处理后没有有效 AO/PPG 配对，其他条件允许执行
- **THEN** PTT 按旧规则取 0，继续形成六维特征和双模型预测，不仅因无配对报告质量拒绝

#### Scenario: 无可选 AO 模态
- **WHEN** 旧双条件选择及频率回退均未选中模态
- **THEN** 使用旧零重建信号继续后续路径，不引入新的 SCG 拒绝规则

#### Scenario: 相等 PTT 被严格筛选清空
- **WHEN** 已有配对的 PTT 全部相等，使旧严格 IQR 边界筛选结果为空
- **THEN** 保留该计算的非法值语义，不补零、不放宽边界；后续若报错或产生非有限预测则失败而非保存成功结果

### Requirement: 血压 v1 输出有限且一致的双血压结果
血压 v1 MUST 分别得到一个有限 sbp 和 dbp，完整结果与对应投影值 MUST 一致，指标类型为 number、单位为 mmHg，正式身份为 isExample=false。算法 MUST 保留原预测，不新增限幅、默认值或四舍五入。任一预测缺失、形状不支持或非有限 MUST 报告 RESULT.INVALID_OUTPUT，不能保存单项成功或医学有效性声明。

#### Scenario: 合法预测不被医学范围修正
- **WHEN** 两个模型分别返回有限预测 145.6789 和 92.3456
- **THEN** 完整结果和 sbp/dbp 指标保持对应原值，不限幅或四舍五入，并携带实际 PPG 质量信息

#### Scenario: 单项非法导致整次结果失败
- **WHEN** 任一模型返回空预测、不支持的预测形状、NaN 或无穷，即使另一项预测有限
- **THEN** 本次以 RESULT.INVALID_OUTPUT 失败，不保存部分成功指标或补默认血压

### Requirement: 血压 v1 输出受保护且任务隔离的处理波形
血压 v1 成功执行 MUST 返回 processed_ppg 和 processed_scg CSV，分别包含 timestamp/ppg 与 timestamp/scgX/scgY/scgZ；SCG 的 X/Y 为零、Z 为旧重建波形，时间轴与实际输出长度一致。文件 MUST 写入当前执行目录并以受保护逻辑引用返回。版本资源 MAY 复用，但任务波形、资料、质量、预测和路径 MUST 隔离，失败不得登记成功输出。

#### Scenario: 成功文件可核对
- **WHEN** 正式血压任务成功完成
- **THEN** 所有者可下载两份与本次计算对应的 CSV，包含毫秒时间轴，不暴露服务器路径

#### Scenario: 相邻任务和版本隔离
- **WHEN** 不同输入、资料或版本的任务交错执行
- **THEN** 每个任务使用自身冻结资料、版本模型和输出目录，不继承其他任务的波形、质量或结果

#### Scenario: 拒绝越权和失败结果下载
- **WHEN** 非所有者、不匹配渠道请求输出，或任务未成功登记该输出
- **THEN** 按既有访问规则拒绝，不因新增血压算法开放文件访问
