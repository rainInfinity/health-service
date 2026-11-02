# PPG+ACC 离线血压 v1

## 前置存储依赖验收

血压算法实施依赖的 `reorganize-file-storage-paths` 已归档于 `openspec/changes/archive/2026-11-01-reorganize-file-storage-paths/`，归档任务清单 1.1–4.4 全部标记完成。归档后的 `inference-runtime` 与 `local-file-storage` 增量规格已同步至 `openspec/specs/`。

实施复用的接口和边界如下：

- 上传格式由算法定义的输入、制品后缀契约校验；新输入按任务创建时间、渠道和身份生成存储键，模型制品按模型 code、版本和逻辑名登记。
- 算法仅向传入的 `.executions/<task_id>/<execution_token.hex>/` 写输出。服务端在成功事务登记不可变实际键后，才以整组目录链接发布；失败、失租及事务回滚不发布。
- 输出下载读取数据库登记的不可变实际键。成功提交后的发布失败不会撤销任务成功，下载或终态消息处理可幂等恢复正式链接，不重新推理。

证据：`docs/development/environment.md` 记录了覆盖 `tests/unit/test_local_storage.py`、`tests/integration/workflows/test_task_results.py`、`test_inference_consumer.py`、API 上传与下载等范围的命令，结果为 115 passed、95 warnings。该记录还包含跨日 HR/BR 消费者实际上传、提交、执行和下载验收。测试代码覆盖数据库权威结果重发布、事务回滚不发布、成功提交后的发布 I/O 失败，以及下载触发恢复；终态消息处理也会按数据库成功记录尝试恢复。

本次在配置的默认任务根目录 `/Users/junsi/project/health-service/var/tasks` 做了临时目录验证：创建相对目录符号链接，在同一父目录以 `os.replace` 替换链接，并读取替换后的目标文件，结果通过；临时目录已自动清理。项目文件系统为本机 `/` 挂载。此结果只证明当前开发环境的任务根目录，不代表其他部署卷；部署使用不同挂载时，仍须在目标卷验证符号链接与同父目录原子替换，失败时不得退化为逐文件复制。

## 候选制品来源

迁移使用的旧版 SBP、DBP 模型及归一化文件位于 `var/old-assets/`：`bp-latest-sbp.joblib`、`bp-latest-dbp.joblib`、`bp-latest-normalize.csv`。旧版 `BpModel` 源码位于 `../nxhe-backend/src/models/services/bp/`，引用这些配套文件名。

## 隔离加载兼容核验

使用 `uv run --no-project --python 3.13` 临时环境，固定 `joblib==1.5.3`、`numpy==2.3.5`、`pandas==2.2.3`、`pygam==0.12.0`、`vmdpy==0.2`、`scikit-learn==1.8.0`、`scipy==1.16.3`。子进程以 `python -I` 从 `/tmp` 启动，环境只含临时 `HOME`/`TMPDIR`、隔离缓存目录和可执行程序路径，不继承业务凭据或项目配置。

SBP、DBP 制品均加载为 `pygam.pygam.LinearGAM`。以形状 `(1, 6)` 的有限输入（前五维为 0，sex=1）调用真实模型，两个模型各返回形状 `(1,)` 的有限预测：SBP `134.66101448131005`、DBP `85.65492879279861`。这是模型输入兼容性探针，不是采集样本结果或医学结论。`n_features_in_` 不存在；六维兼容由该次真实 `predict` 调用验证。

归一化 CSV 以 `miu`、`segma` 为行，`X1`、`age`、`pr`、`bmi`、`height` 为列；这五列均有有限均值和有限非零标准差，与旧代码 `read_csv(index_col=0).to_dict()` 的访问结构一致。

模型将第六维 sex 作为类别特征，允许域为 `[1, 2]`。sex=0 的真实模型探针报“categorical feature 5”超出该域，sex=1 可预测。实现必须保留旧版原始性别值，不映射或补值；未知性别 0 在该制品上的预测异常应按执行失败处理，不能改写成质量拒绝或默认值。

## 固定版本路径与反序列化信任边界

推理资源加载器只接收版本登记的 `sbp`、`dbp` 与 `normalize` 三个精确路径，并拒绝缺失或额外制品；不枚举目录、不解析 `latest`，也不回退默认资源。`var/old-assets/` 保存旧版候选制品，仅用于迁移对照，不作为运行时资源目录。单元测试覆盖固定版本路径隔离和制品清单契约。

本地制品存储键相对于配置的模型制品根目录，格式为 `<model_code>/<version>/<logical_name>-<uuid.hex>.<extension>`。存储实现生成并登记具体键，运行时使用已登记路径，不根据文件名猜测逻辑名或版本。

`joblib.load` 使用 pickle 兼容反序列化，加载不可信制品可能执行其中代码。部署流程应只接收受信制品；扩展名、六维输入探针和预测输出校验只能检查格式及兼容性，不能隔离代码或证明制品安全。上文所列文件仅说明本次迁移使用的旧版配套资源，不构成其他上传制品的信任依据。

## 工作区依赖锁验证

`health-inference` 的 `ppg` 可选依赖显式声明 `joblib>=1.5.3,<2`、`pygam==0.12.0`、`vmdpy==0.2`；joblib 沿用原锁中的 1.6.0，不降级现有 PPG 依赖。`uv lock` 仅新增 `progressbar2==4.6.0`、`pygam==0.12.0`、`python-utils==4.1.4` 和 `vmdpy==0.2`；`uv lock --check` 通过。

在清空业务环境变量的项目运行环境中，通过 `uv run --locked --all-packages --extra ppg` 再次加载真实制品。解析版本为 joblib 1.6.0、pygam 0.12.0、vmdpy 0.2、NumPy 2.3.5、pandas 2.3.3、SciPy 1.16.3；相同六维探针的 SBP/DBP 预测与前述隔离环境一致且有限，归一化参数结构检查通过。
另以虚构凭据和临时存储目录导入 `config.asgi` 并运行 Django `check`，结果为 “no issues”；随后检查 `sys.modules`，NumPy、pandas、SciPy、NeuroKit2、joblib、pygam、vmdpy 均未加载。该 Web 启动路径未反序列化模型或读取算法制品。

## 双传感器输入与预处理契约

PPG 使用 `timestampFrame` 毫秒时间轴和完整 RGB (`avgR/avgG/avgB`) 或 YUV (`avgY/avgU/avgV`) 通道；沿用共享单块解析，`blockIndex` 可省略或为整数 0。RGB 仅取 `-avgR/255`；YUV 使用旧 BT.709 红通道 `-(avgY + 1.5748 × (avgV - 128))/255`。ACC 使用 `timestampEvent` 毫秒时间轴与有限 `accX/accY/accZ`，SCG 信号取 `-accZ`，其余两轴仍校验但不参与预测。

两路采样率分别按各自严格递增时间戳的平均间隔计算并保留两位小数；滤波前不插值、排序、重采样、时钟归零或跨路对齐。PPG 采用二阶 0.75–4 Hz Butterworth 带通，SCG 采用二阶 1–`min(25, floor(fs/2))` Hz 带通；均使用 `filtfilt` 和总体标准差 z-score。输入必须超过滤波填充长度，截止频率须严格位于 Nyquist 内。旧算法只有 PPG 质量门禁；不新增 SCG 质量拒绝。

## PPG 质量门禁

质量评分只使用预处理后的 PPG，调用 `neurokit2.ppg_quality` 时沿用旧版 `int(fs)` 采样率，对返回分数求均值后乘 100，不舍入。固定 `QUALITY_THRESHOLD=70`：实际分数严格低于 70 时以 `LOW_SCORE` 拒绝并保留比较值；等于 70 时继续。门禁位于 AO 重建、峰与特征计算、双模型预测及文件写入之前。空评分、非有限评分、形状或有限性非法的 PPG 波形，以及评分器异常均按执行错误终止，不伪造默认质量结论；算法不新增 SCG 质量拒绝。

## 固定资料快照与特征映射

算法只读取任务提交时冻结的 `profile_snapshot`。`age_years` 使用提交日周岁，`height_cm` 和 `weight_kg` 可为十进制字符串或数值；缺失年龄、身高、体重按旧规则转为 0。身高从厘米换算为米，BMI 使用千克和米计算。提交后修改生日、身高、体重或性别不重算已提交任务的特征。

模型输入顺序固定为 `[PTT秒, 年龄, PR, BMI, 身高米, sex]`，前五项按版本 `miu`、`segma` 归一化，`sex` 原样作为第六项，不做性别编码或缺失补值。常规未知性别为 0；若当前模型制品不接受该值，按执行失败处理，不改写成其他性别或默认预测。

## 结果值与处理文件

`sbp`、`dbp` 保留双模型返回的原始有限预测值，不限幅或舍入；完整结果与同名指标使用完全相同的数值。任一预测缺失、形状不支持或非有限时整次执行以 `RESULT.INVALID_OUTPUT` 失败，不产生部分成功结果。

输出逻辑名 `processed_ppg` 固定对应 `ppg-processed.csv`，列为 `timestamp,ppg`；`processed_scg` 固定对应 `scg-processed.csv`，列为 `timestamp,scgX,scgY,scgZ`，其中 X/Y 为零、Z 为 AO 重建波形。SCG 时间轴按 AO 实际输出长度截断。文件名不附加输入 UUID。

算法只写调用传入的 `.executions/<task_id>/<execution_token.hex>/` 隔离 `output_dir`，并通过逻辑名返回文件；不写正式目录入口或创建发布链接。正式目录发布由服务端在成功事务提交后整组完成。

## 旧版采集样本基线

### 样本与环境

只读运行旧 `BpModel`，输出 CSV 重定向到自动清理的 `/tmp` 临时目录，未写入旧仓库或源样本目录。样本文件及 SHA-256：

| 文件 | SHA-256 |
|---|---|
| `var/old-assets/sample/20261101_134952-5-2-bp_vlatest-ppg.csv` | `db1d1792be071f9d4285a66e2ba47d1241e25d9150f11409d29777db2606ab46` |
| `var/old-assets/sample/20261101_134952-5-2-bp_vlatest-acc.csv` | `4adfcdb1909830670f73933642ed3642c3bb9e42e17682811bc9592adec68852` |

运行环境为 Python 3.13.16、NumPy 2.3.5、pandas 2.2.3、SciPy 1.16.3、NeuroKit2 0.2.12、joblib 1.5.3、pygam 0.12.0、vmdpy 0.2。资料使用旧入口示例中的 age=24、gender=1、height=170 cm、weight=55 kg；该固定资料仅用于数值对照。

PPG 有 929 行、YUV 通道，`timestampFrame` 从 819928815 ms 到 819959744 ms，旧采样率为 30.00 Hz、旧时长取整为 31 秒。ACC 有 6305 行，`timestampEvent` 从 819929811 ms 到 819959835 ms，旧采样率为 209.96 Hz、旧时长取整为 31 秒。两路时间轴直接使用原毫秒时标，起点相差 996 ms，不归零或自动对齐。

### 信号与预测基线

波形哈希采用 `sha256(np.asarray(signal, dtype='<f8').tobytes())`；汇总值和哈希均基于旧实现的真实数组：

| 阶段 | 长度 | SHA-256 | 最小值 | 最大值 | 均值 | 标准差 |
|---|---:|---|---:|---:|---:|---:|
| 清洗 PPG | 929 | `6f928556678d2572f0fe1d86a09cca863082a2ccafce6bf2a32387136701b553` | -2.9039962381520237 | 1.8550709772367433 | -2.0077230154685286e-17 | 0.9999999999999999 |
| 清洗 SCG（负 accZ） | 6305 | `5ae32b16a23022c3a36e2f1ca7eea90a545f305381c1cc0896e1793621fd5a1d` | -4.457843627590017 | 3.545847428336937 | 1.3805152280826689e-17 | 0.9999999999999999 |
| AO 重建 | 6304 | `09f5c92c86a9db849c0e813e5cbfd6204998ac46dd4d43e775178260f1df544c` | -2.3131079702113437 | 2.2176684746519193 | -3.12139100307034e-06 | 0.4979764014241711 |

PPG 质量分为 88.65866718458334，阈值 70，通过。旧配对结果 PTT 为 68.038462 ms、PR 为 79；归一化后的六维模型输入为 `[-2.1483592616088503, -2.181628926004998, 0.1645511816678674, -2.156905138229523, 0.43395908741305994, 1.0]`。SBP 为 125.85039300154746，DBP 为 76.03917873786334。该记录是迁移对照数据，不代表医学准确性或临床结论。

旧处理（包含 VMD）耗时 0.1363 秒，完整读取、预处理和预测耗时 0.1466 秒；在上述开发机和临时依赖环境运行。单次耗时只作消费者超时评估参考，不构成性能保证。

新实现已用相同采集样本直接对照旧 `PPGUtils.clean`/`SCGUtils.clean`。两路采样率均一致（PPG 30.00 Hz、ACC 209.96 Hz）；SCG 长度和哈希逐位相同。旧 Pandas 数值推断与共享解析器的字符串转浮点使 YUV `avgU`/`avgV` 最大输入差分别为 `1.4211e-14`、`2.8422e-14`；PPG 清洗波形最大绝对差为 `3.025912853615864e-13`、平均绝对差为 `8.328337022553216e-14`。新 PPG 哈希为 `28e1754a5128e3fd35e8f022abef20c342268ab81b4e59a920dfc5615947d065`，与旧哈希不同但误差来源已定位，不是滤波系数或采样率变化；清洗 PPG 对照采用 `5e-13` 绝对容差。全链对照结果见下文。

AO 重建在同一真实 ACC 样本的旧版清洗波形上分别运行旧、新实现。两边均产生 4 个模态，模态数组逐位相同；AO 均为 6304 点，逐位匹配上表哈希。

| 指标 | 旧版 | 新版 |
|---|---:|---:|
| 顺序 VMD 循环耗时（ms） | 40.048 | 34.479 |
| 完整 AO 重建耗时（ms） | 70.921 | 37.678 |

以上是同一进程、同一输入的一次测量，仅记录本次环境，不构成性能保证。

### 新旧全链对照与实际消费者 smoke

在隔离测试数据库、Redis Stream 和存储根目录中，使用 `var/old-assets/` 的三个候选制品、上述 PPG/ACC 样本及固定资料（age=24、gender=1、height=170 cm、weight=55 kg），分别运行旧 `BpModel` 和新版完整计算，并通过新版实际消费者提交任务。样本质量分高于 70，未触发拒绝或非法值，因此无需替代输入。

| 项目 | 旧版 | 新版 | 对照 |
|---|---:|---:|---|
| 清洗 PPG 最大绝对差 | — | `3.025912853615864e-13` | 不超过 `5e-13` |
| 清洗 SCG 最大绝对差 | — | `0` | 逐位相同 |
| AO 重建最大绝对差 | — | `0` | 逐位相同 |
| PPG 质量分 | `88.65866718458334` | `88.65866718458382` | 绝对差不超过 `1e-12` |
| PTT | `68.038462 ms` | `68.038462 ms` | 相同 |
| PR | `79` | `79` | 相同 |
| 六维归一化特征 | `[-2.1483592616088503, -2.181628926004998, 0.1645511816678674, -2.156905138229523, 0.43395908741305994, 1.0]` | 与旧版相同 | 绝对容差 `5e-13` |
| SBP | `125.85039300154746` | `125.85039300154746` | 相同；模型预测绝对容差 `1e-10` |
| DBP | `76.03917873786334` | `76.03917873786334` | 相同；模型预测绝对容差 `1e-10` |

验证命令 `uv run pytest -s tests/integration/workflows/test_bp_numeric_smoke_once.py --reuse-db` 结果为 1 passed；实际消费者将任务提交为成功且 Redis pending 为 0。旧版完整单次运行耗时 `86.36 ms`，新版直接流水线（含资源加载）耗时 `1903.33 ms`，消费者调用总耗时 `2461.53 ms`。时延包含不同路径的加载、进程与存储开销，仅代表本次环境，不是性能保证。该命令使用的一次性对照测试文件已在验收后删除；运行中仅出现 NeuroKit2 导入 `scipy.misc` 的弃用警告。

本次全链对照验证了输入时间轴、行数、质量门槛分支、PTT、PR 整数值、特征顺序及双预测；波形哈希或逐点误差仅用于数值复现。

## 全局硬时限与版本发布/回退

### 硬时限

`INFERENCE_EXECUTION_TIMEOUT_SECONDS` 是共享推理消费者的正整数秒数配置，默认 `300`；所有算法共用，不属于模型版本参数。它覆盖父进程交给执行子进程后的固定任务上下文、资源加载、算法计算和输出校验，与数据库租约、心跳及消息等待独立；非法、零、负数、小数或非数字配置会使服务启动失败。配置项已列于 `.env.template`，更新后须重启消费者。

超过时限时父进程先强制终止执行子进程，再使用当前执行令牌提交 `FAILED / INFERENCE.EXECUTION_TIMEOUT`（“推理执行超时”），提交后才确认 Redis 消息。任务不自动重试，不保存成功结果、指标或权威输出；重新执行应创建新任务。进程终止可能留下 `.executions/<task_id>/<execution_token.hex>/` 下的孤儿部分文件；它们不被数据库引用，不会发布或下载，存储层不自动清理。

真实工作流以 1 秒时限运行第二次预测会阻塞 5 秒的模型，消费者实际将任务提交为 `INFERENCE.EXECUTION_TIMEOUT`；断言任务为失败、`attempt_count=1`、无结果载荷、无指标、无正式目录，Redis pending 为 0。共享消费者回归另覆盖执行子进程终止、失败提交后 ACK，以及租约丢失时不提交终态、不 ACK。

### 发布与回退

由授权 manager 通过现有模型目录流程创建 `algorithmKey=ppg_acc.active.offline.bp.v1`、`params=null` 的未发布版本，分别上传 `sbp`、`dbp`、`normalize` 三个逻辑制品，将指标集合设为 `sbp`、`dbp` 后发布。制品由服务端登记并保存到模型版本存储键；不得手工覆盖文件、写入 `latest` 路径或直接操作正式目录链接。

测试和审查通过后，授权 manager 才通过 `PUT /api/v1/manager/channels/{channel}/models/{model_id}/selection` 显式选择目标版本。本次验收未切换任何外部渠道或生产状态。回退时用同一接口选择先前已发布的版本，不覆盖不可变版本制品；已提交或运行中的任务继续使用各自固定版本，切换会使该渠道旧版本的 DRAFT 任务以 `TASK.MODEL_SWITCHED` 失败，不会恢复这些草稿。成功事务后的输出链接若发布失败，按数据库登记的实际文件键请求下载或重放成功终态消息即可幂等恢复，不重新推理；文件缺失时下载安全返回 404。

### 双文件故障与恢复验收

`uv run pytest tests/integration/workflows/test_bp_inference_consumer.py --reuse-db` 结果为 `9 passed`。成功事务提交后注入首次正式目录替换失败，血压任务仍保持成功，双指标、结果载荷、执行次数和两份实际文件字节不变。分别通过下载及重复终态消息恢复，正式目录最终指向同一执行目录并只含 `ppg-processed.csv`、`scg-processed.csv`；终态重放未重新领取或执行算法。

持续注入发布 I/O 故障时，下载仍通过数据库登记的两个实际文件键返回原始字节；删除实际 PPG 文件后，下载安全返回 404 且响应不泄露存储路径。真实消费者的指标唯一约束失败回滚了成功状态与结果载荷，保留无结果、无指标、未确认消息和未发布的隔离文件。另在推理期间替换执行令牌，旧执行晚到的结果提交被拒绝，既有正式双文件链接与字节保持不变。

上述故障均由永久工作流测试注入；未保留一次性故障脚本。


### 回归命令与限制

`uv lock --check` 通过。首次以默认 pytest 导入模式收集多个含同名测试模块的目录时出现 4 个 collection mismatch；改用 `--import-mode=importlib` 后完整回归通过：

```sh
uv run pytest --import-mode=importlib \
  tests/unit/inference \
  tests/unit/test_settings_environment.py \
  tests/integration/workflows/test_bp_inference_consumer.py \
  tests/integration/workflows/test_inference_consumer.py \
  tests/integration/workflows/test_spo2_inference_consumer.py \
  --reuse-db
```

结果为 `322 passed, 68 warnings`；警告包括 NeuroKit2 对 `scipy.misc` 的弃用导入、pyPPG 对 NumPy 数组到标量转换的弃用行为，以及既有空样本边界测试产生的 NumPy/SciPy 运行期警告。真实候选制品和采集样本的临时全链对照另以 `uv run pytest -s tests/integration/workflows/test_bp_numeric_smoke_once.py --reuse-db` 通过（1 passed），临时测试文件已删除。数值与时延仅覆盖本机隔离测试环境和固定旧样本；未验证生产数据、临床准确性或其他部署卷的原子链接替换。上线前仍须按目标卷验证符号链接与同父目录原子替换，且不能把测试结果当成生产发布审批。
