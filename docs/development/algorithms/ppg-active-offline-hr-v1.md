# PPG 主动离线心率 v1

算法标识为 `ppg.active.offline.hr.v1`，实现目录为 `health_inference/algorithms/ppg_active_offline_hr_v1/`。模型编码为 `ppg.active.offline.hr`，初始发布版本为 `1.0.0`。这是独立心率算法，不调用呼吸率推理，不返回 BR 或内部部分失败结构；`isExample=false` 不代表医学准确性认证。

## 输入与版本配置

输入逻辑名为 `ppg`，后缀为 `.csv`，内容为 UTF-8 CSV，可带 BOM。时间戳列 `timestampFrame` 必须是有限、严格递增的毫秒数；通道列必须恰好包含一组完整的 `avgR,avgG,avgB` 或 `avgY,avgU,avgV`，各值必须有限。表头不得重复，行列数必须一致。可选 `blockIndex` 存在时每行必须为整数 `0`；不选块、合并、插值、补齐或重采样。其他相机元数据列可忽略。

至少两行用于计算时间差；平均时间差换算的采样率按原规则保留两位小数。采样率必须满足滤波器 Nyquist 条件，样本长度必须超过实际滤波填充长度，标准化方差不得为零。固定参数只能为 `null` 或空对象，不需要模型制品、必需健康资料或客户端元数据。

## 同源共享前置与独立推理

HR 与 [BR v1](ppg-active-offline-br-v1.md) 复用 `utils/ppg_input.py` 的 `load_ppg`、`utils/ppg_preprocessing.py` 的 `PreprocessedPPG/preprocess_ppg`、`utils/ppg_quality.py` 的 `PPGQualityResult/PPGQualityStrategy` 以及 `utils/ppg_output.py` 的 `write_processed_csv`，不复制同源前置规则。共享代码不表示缓存或跨成员共享本次评分、波形和任务状态。

RGB 提取为 `-avgR / 255`；YUV 按 BT.709 提取为 `-(avgY + 1.5748 × (avgV - 128)) / 255`。PPG 经二阶 Butterworth `0.75–4 Hz` 带通与总体标准差 Z-score。质量使用 NeuroKit2 `ppg_quality` 对该 PPG 评价，采样率为 `int(fs)`，分数为评价数组均值乘 `100`。固定阈值为 `70`，严格低于阈值以 `LOW_SCORE` 拒绝，等于阈值可继续；非法评分按执行错误失败，不补分或伪造拒绝原因。

质量通过后只执行 NeuroKit2 `ppg_findpeaks`、`ppg_rate`，采样率使用 `int(fs)`，取速率数组算术均值。有限 HR 保留原值，不新增限幅或舍入；NaN、正负无穷以 `RESULT.INVALID_OUTPUT` 失败，不生成默认数值。

## 成功结果与文件

计算载荷恰好为 `hr` 和 `quality: {score, threshold}`；投影仅为 `hr`，类型 `number`，单位 `beats_per_minute`，数值与载荷相同。服务端只在成功结果追加 `output_files.processed_ppg` 的不透明引用。文件名为 `ppg-processed.csv`，表头 `timestamp,ppg`，保留输入毫秒时间轴并逐帧对应处理后的 PPG，使用受保护下载接口读取。

输入、质量、推理或输出失败均使独立任务 `FAILED / result=null`，无部分指标和权威输出；成功载荷不允许 `hr=null`、`br` 或内部 `failures`。统一 PPG v2 中 HR 为独立 `hr` 成员，读取路径是 `result.results.hr.result.hr`，下载逻辑名是 `hr_processed_ppg`；BR 失败不使 HR 失败。

## 发布与验证边界

应将独立模型 `ppg.active.offline.hr` 的 `1.0.0` 发布版本选择到 `android-cn`、`android-intl`、`ios-cn`、`ios-intl`、`wxapp`，不配置 `manager` 用户模型选择。具体配置见[目录清单](../initial-database-data.md)。旧融合算法与旧 PPG 场景 v1 最终退役，不提供兼容别名。

本文描述目标契约，不是测试通过、部署完成或渠道已切换的证明。旧融合算法的验收数字不能作为本实现的验证记录；合成信号与旧链对照也不能代替医学准确性验证。
