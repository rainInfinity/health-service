# PPG 主动离线呼吸率 v1

算法标识为 `ppg.active.offline.br.v1`，实现目录为 `health_inference/algorithms/ppg_active_offline_br_v1/`。模型编码为 `ppg.active.offline.br`，初始发布版本为 `1.0.0`。这是独立呼吸率算法，不调用心率推理，不返回 HR 或内部部分失败结构；`isExample=false` 不代表医学准确性认证。

## 输入与共享前置

输入逻辑名为 `ppg`，后缀为 `.csv`，内容为 UTF-8 CSV，可带 BOM。时间戳 `timestampFrame` 必须为有限、严格递增的毫秒数，通道恰好包含完整 `avgR,avgG,avgB` 或 `avgY,avgU,avgV` 其中一组，通道值均有限。表头不得重复，行列数必须一致；可选 `blockIndex` 仅允许整数 `0`，不选块、融合、插值、补齐或重采样。至少两行计算采样率；采样率与输入长度须满足实际滤波条件，标准化方差不得为零。

固定参数只能为 `null` 或空对象，不要求制品、必需健康资料或客户端元数据。BR 与 [HR v1](ppg-active-offline-hr-v1.md) 同源复用 `utils/ppg_input.py` 的 `load_ppg`、`utils/ppg_preprocessing.py` 的 `PreprocessedPPG/preprocess_ppg`、`utils/ppg_quality.py` 的 `PPGQualityResult/PPGQualityStrategy` 和 `utils/ppg_output.py` 的 `write_processed_csv`，不复制前置实现，也不跨任务或成员共享本次数据与质量结论。

RGB 提取为 `-avgR / 255`；YUV 按 BT.709 提取为 `-(avgY + 1.5748 × (avgV - 128)) / 255`。平均时间差换算采样率并按原规则保留两位小数；二阶 Butterworth 分别提取 `0.75–4 Hz` PPG 与 `0.1–0.5 Hz` 呼吸波，采用总体标准差 Z-score，不设额外最短采集秒数。

质量门禁依据 PPG 而非呼吸波：NeuroKit2 `ppg_quality` 使用 `int(fs)`，分数为评价数组均值乘 `100`，固定阈值 `70`。严格低于 `70` 以实际分数报告 `LOW_SCORE`，等于阈值允许继续；NaN 或无法解释的评分按执行错误失败，不补零、满分或伪造 `NO_VALID_SIGNAL`。

## 独立推理与范围

质量通过后只对呼吸波执行 NeuroKit2 `rsp_rate`，采样率为 `int(fs)`，取速率数组算术均值。必须先确认原始 BR 有限：NaN 或正负无穷以 `RESULT.INVALID_OUTPUT` 失败，不能通过限幅隐藏。有限 BR 按 `max(8, min(br, 20))` 限制到闭区间 `[8,20]` 次/分钟，区间内保留原值；不补为 `12`，不舍入，不运行 HR 估计作为成功条件。

## 成功结果与文件

计算载荷恰好为 `br` 和 `quality: {score, threshold}`；投影仅为 `br`，类型 `number`，单位 `breaths_per_minute`，载荷与投影使用同一最终数值。服务端只在成功结果追加 `output_files.processed_rsp` 的不透明引用。文件名为 `rsp-processed.csv`，表头 `timestamp,rsp`，保留输入毫秒时间轴并逐帧对应呼吸波，通过受保护下载链路读取。

任何阶段失败均使独立任务 `FAILED / result=null`，无部分指标或权威文件；成功载荷不允许 `br=null`、`hr` 或内部 `failures`。统一 PPG v2 中 BR 为独立 `br` 成员，读取路径为 `result.results.br.result.br`，下载逻辑名为 `br_processed_rsp`。BR 失败时 HR 仍可成功，健康查询不再要求两项同时有效。

## 发布与验证边界

应将模型 `ppg.active.offline.br` 的 `1.0.0` 发布版本选择到全部五个普通渠道：`android-cn`、`android-intl`、`ios-cn`、`ios-intl`、`wxapp`。与同模型独立发布版本不同，统一 PPG 模型仍为 `ppg.active.offline.all`，新发布版本 `2.0.0` 绑定 `ppg.active.offline.all.v2`；具体见[目录清单](../initial-database-data.md)。

本文描述目标契约，不表示已完成验证、部署或渠道选择。旧融合算法文档中的验收记录仅属旧实现，不能挪用为 BR v1 证据；实际迁移需独立核对输入、质量、BR 范围、文件与失败隔离，医学准确性仍需另外验证。
