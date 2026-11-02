# PPG 主动离线糖尿病风险 v1

算法标识：`ppg.active.offline.diabetes.v1`。实现目录：`packages/health-inference/src/health_inference/algorithms/ppg_active_offline_diabetes_v1/`。算法为正式实现（`isExample=false`），只接受一个 `ppg` CSV，不要求健康资料或非空参数；每个发布版本绑定且只绑定一个 `diabetes` ONNX 制品。该契约不核验模型制品来源或权属。

## 输入与执行条件

输入为 UTF-8 CSV（可带 BOM），时间戳列为有限、严格递增的毫秒数 `timestampFrame`，并且恰好提供一组完整通道：RGB 的 `avgR,avgG,avgB`，或 YUV 的 `avgY,avgU,avgV`。共享 PPG 解析器拒绝缺列、重复列、非有限通道、时间戳无效和多块数据；不选块、不合并、不插值、不重采样或补齐。

采样率必须严格大于 `8 Hz`，满足 `4 Hz` 高截止频率的 Nyquist 条件；样本数必须至少达到 `ceil(fs × 15)`，满足 pyPPG 的 15 秒最短时长。30 Hz 输入至少 450 帧；不足或滤波不能执行时报告受控输入错误，不进入质量、特征或模型阶段。

## 固定计算链

RGB 波形取 `-avgR / 255`。YUV 沿用旧 BT.709 矩阵转换后取负红通道并除以 `255`。波形使用二阶 Butterworth `0.75–4 Hz` 带通、`filtfilt` 和总体标准差 Z-score；采样率沿共享解析器以两位小数规则计算。零方差或非法预处理结果不生成后续质量输入。

质量阶段调用 NeuroKit2 `ppg_quality`，采样率传 `int(fs)`，质量分数为返回值均值乘以 `100`，不舍入。分数严格小于 `70` 时以 `INFERENCE.QUALITY_REJECTED`／`LOW_SCORE` 拒绝，明细保留实际分数和门槛；恰好 `70` 继续执行。空或非有限评分作为执行异常处理，不补分、不继续预测。

特征阶段按旧 pyPPG 规则在整段清洗 PPG 上计算三个梯度、生物标志物和统计量。为避免 `pyPPG.datahandling` 顶层无条件导入 `mne`，实现直接构造与旧 `load_signal` 等价的 `DotMap` 信号对象，再调用同一套 pyPPG `PPG`、fiducial、biomarker 和统计代码。修正点固定为 `on,dn,dp,v,w,f`。统计组顺序为 `derivs_ratios`、`ppg_derivs`、`ppg_sig`、`sig_ratios`；各组列数分别为 `270,135,315,198`，总计 `918`。组内先按统计行 `mean,median,std,percentile_25,percentile_75,iqr,skew,kurtosis,mad`，再按特征列展开；最终矩阵为 `(1,918)` 的 `float32`。列名 SHA-256 为 `a76fea6646c8a69a869a5f1e1d7324b0bc8862358e23e95d8b850715fcbf8185`。

不填充、不删列、不替换任何统计值。旧统计会忽略单个特征列中的 NaN 样本；剩余数据太少时偏度／峰度等可能成为 NaN。整列全缺失会导致旧统计异常，而不是生成完整统计行。正负无穷不由旧统计的 NaN 过滤逻辑移除；是否能继续由实际 ONNX 行为决定。模型输出仍必须是有限、范围合法、和为 1 的二分类概率，并与标签一致。

## ONNX 制品和概率映射

资源加载器要求一个输入 `input: tensor(float)[None,918]`，并按节点名识别两个输出：`label: tensor(int64)[None]` 和 `probabilities: tensor(float)[None,2]`。实测模型的类别顺序为 `[0,1]`；目标风险概率取 `probabilities` 第 1 列（类别 1），不依赖 `get_outputs()` 的列表位置，不用 `label` 直接生成三分类。运行时还校验 `label == argmax(probabilities)`；不配套类别映射以 `INFERENCE.INVALID_ARTIFACT` 失败。对非有限、越界、概率和错误或其他输出形状错误的结果，以 `RESULT.INVALID_OUTPUT` 失败。

服务端保留未经舍入的 `prob`，并复制到 `diabetes_probability` 指标（类型 `number`、单位 `ratio`）。风险档位仅按旧 JavaScript `Number(prob.toFixed(2))` 后比较：舍入值 `<0.66` 为 `0`，`<0.76` 为 `1`，其余为 `2`。Python 用二进制浮点数精确值执行 `ROUND_HALF_UP`，保留旧客户端 `toFixed(2)` 的边界行为；`label` 不参与档位划分。档位编码没有临床分层验证，不表示血糖浓度、确诊或医学风险结论。

成功载荷示例（真实旧链对照样本的本次新实现结果）：

```json
{
  "prob": 0.5523077249526978,
  "risk_level": 0,
  "quality": {"score": 88.65866718458382, "threshold": 70},
  "output_files": {"processed_ppg": "<不透明文件引用>"}
}
```

`processed_ppg` 对应当前执行目录中的 `ppg-processed.csv`，CSV 表头为 `timestampFrame,ppg`，每行时间戳与清洗波形逐帧对应。只有所有质量、模型和结果校验成功后才写入该文件；失败不登记指标或正式输出。下载继续使用逻辑名 `processed_ppg` 和通用任务所有权、渠道检查。

## 迁移基线与数值证据

### 样本与环境边界

基线使用 `var/old-assets/sample/20261101_134952-5-2-bp_vlatest-ppg.csv`：929 帧、约 30 Hz、YUV 通道。该文件来自本地旧血压采集样本目录，不是糖尿病专用或临床验证样本；这里只用于确认计算迁移兼容性，不据此推断疾病预测准确性。原文件未修改，旧链输出写入临时目录并在进程结束时删除。

旧版完整对照使用项目当前 `uv` 环境（Python `3.13.14`），包版本为：NumPy `2.3.5`、pandas `2.3.3`、SciPy `1.16.3`、NeuroKit2 `0.2.12`、ONNX Runtime `1.23.2`、DotMap `1.3.30`、scikit-learn `1.9.1`、ONNX `1.23.2`。旧项目 `.venv` 缺 NumPy，不能作为运行环境；旧 `datahandling.py` 顶层无条件导入未安装的 `mne`，基线脚本仅为完成 CSV 路径模块导入临时注入空 `mne` 模块。CSV 路径不调用 MNE API。故本次证明的是旧源码在上述锁定计算依赖下的数值链对照，不声称复现了旧项目虚拟环境或 MNE/EDF 行为。

为固定数值输入的 SHA-256（不作为来源证明）：

| 文件 | SHA-256 |
|---|---|
| 指定 ONNX 制品 `diabetes-latest-diabetes.onnx` | `08f7c9bfa8096b1ff1cc1bccb56a9ac07ae8d463df65ca2085e8e77563db49a7` |
| 血压采集 PPG CSV | `db1d1792be071f9d4285a66e2ba47d1241e25d9150f11409d29777db2606ab46` |
| 旧 `diabetes/main.py` | `eb1c4154c648aae9603eeb6562c13b90fb72566365650175115823e857b293f4` |
| 旧 `diabetes/utils/ppg.py` | `a1534ddf1298cad8cbbe77aebd3222b7fbd9cf7593c6cf87efbe3088f1cb6d51` |
| 旧 pyPPG `ppg_bm/statistics.py` | `bb74ae91550cc5c8eb8bef407704055021f22f748be254293ff60802ed7413a0` |
| 旧 pyPPG `biomarkers.py` | `b24fe324bd54724c90992d89a8a1b87387ccdfff325bb0bbc9f2e3db2d25b2cd` |
| 旧 pyPPG `fiducials.py` | `0442c4893e94a404c8d2b190bf800a964639817ca91b62c01a815101605fc488` |

迁入后的 `statistics.py` 与旧版哈希相同；`biomarkers.py`、`fiducials.py` 的差异仅为包导入路径改为 `health_inference.libs.pyPPG`，其余逻辑逐行一致。

### 新旧完整链对照

实际旧版命令以 `uv run --locked --all-packages --extra ppg python -c ...` 在临时目录运行旧源码，再由同一进程比较迁移实现；输出目录与输入副本均位于临时目录。旧 `DiabetesModel` 完整运行成功，新算法加载同一 CSV 和 ONNX 并完成质量、特征和推理。测得：

| 阶段 | 旧版 | 新版 | 最大绝对误差／结论 |
|---|---:|---:|---|
| 输入采样率 | `30.0 Hz` | `30.0 Hz` | 完全一致 |
| 质量分数 | `88.65866718458334` | `88.65866718458382` | 误差 `4.83e-13` |
| 清洗 PPG | 929 点 | 929 点 | 最大绝对误差 `3.03e-13` |
| 特征矩阵 | `(1,918)` | `(1,918)` | 918 列逐元素完全相同，NaN/Inf 均为 0 |
| 原始类别 1 概率 | `0.5523077249526978` | `0.5523077249526978` | 完全一致 |
| ONNX 标签 | `1` | `1` | 完全一致 |

特征列名顺序与摘要也逐项一致，列名 SHA-256 为 `a76fea6646c8a69a869a5f1e1d7324b0bc8862358e23e95d8b850715fcbf8185`。本次结果不以最终档位相同替代中间阶段比较。

pyPPG 边界探针确认 450 个 30 Hz 样本（15 秒）成功；449 个样本旧版直接触发“Signal must be at least fifteen seconds”，迁移实现将其映射为 `INFERENCE.INVALID_INPUT`／`INSUFFICIENT_DATA`。统计缺失值探针确认 `[1, NaN, 3]` 只用 `[1,3]` 求统计；全 NaN 列在旧版和迁入统计代码中均以 `ValueError` 失败，没有补默认值。

同一真实 ONNX 对受控 918 维特殊值输入的探针也均返回了有限合法概率：单个 NaN 和单个 `Inf` 输入返回约 `[0.43901908,0.56098092]`，全 NaN 输入返回约 `[0.64930236,0.35069764]`。这是该固定制品的实测行为，不泛化为任意模型保证；迁移实现原样传递 pyPPG 特征、不补值，继续校验最终模型输出契约。

### 真实消费者端到端验收

以同一真实 ONNX 和上述实际 PPG 文件进行了一次隔离消费者验收：将模型制品上传并发布为糖尿病算法版本，使用普通用户经正式任务上传接口提交 CSV，运行 Redis Streams 消费者，查询任务和指标并下载处理后文件。任务成功，`prob=0.5523077249526978`、`risk_level=0`、质量分数约 `88.658667`、处理 CSV 为 929 行；指标与概率一致，所有者下载内容与登记文件字节一致，其他用户下载返回 `404`，Redis pending 数为 `0`。验收使用独立测试数据库、Redis Stream 和临时任务/制品存储；测试文件在运行后删除。该一次验收不构成临床准确性验证。

- 一次性真实制品端到端验收当时执行：`uv run --locked --all-packages --extra ppg pytest tests/integration/workflows/_test_diabetes_actual_asset_smoke.py -q`，结果 `1 passed in 5.20s`。临时测试文件运行后删除，不作为永久测试依赖。
- 常驻消费者回归：`uv run --locked --all-packages --extra ppg pytest --reuse-db tests/integration/workflows/test_diabetes_inference_consumer.py -q`，结果 `1 passed`。
- 糖尿病单元及注册回归：`uv run --locked --all-packages --extra ppg pytest tests/unit/inference/diabetes tests/unit/test_inference_algorithms.py -q`，结果 `66 passed, 38 warnings`。
- 共享回归：`uv run --locked --all-packages --extra ppg pytest --reuse-db --import-mode=importlib tests/unit tests/integration/catalog tests/integration/workflows tests/integration/api/test_task_file_downloads.py -q`，结果 `498 passed, 68 warnings in 125.10s`。使用 `importlib` 导入模式以避免不同算法目录下同名测试模块的默认导入冲突；警告来自 pyPPG 数值边界探针及 NeuroKit/NumPy/SciPy 弃用提示。
- 上述回归后 Ruff 检查为 `All checks passed!`，格式检查为 `25 files already formatted`。
