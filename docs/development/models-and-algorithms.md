# 模型与算法开发

## 算法标识命名

新正式算法的可执行标识采用 `<传感器集合>.<采集模式>.<处理模式>.<输出>.v<实现修订号>`：全部使用小写英文，层级以 `.` 分隔；多传感器集合使用下划线连接，不使用 `+`。实现修订号使用从 `1` 开始的正整数。

| 标识段 | 含义 | 示例 |
|---|---|---|
| 传感器集合 | 算法使用的传感器或信号类型；多个类型按约定顺序连接。 | `ppg`、`rppg`、`ppg_acc` |
| 采集模式 | 测量数据的采集方式。 | `active`、`passive` |
| 处理模式 | 对数据执行推理的方式。 | `offline`、`realtime` |
| 输出 | 算法输出的业务指标类别。 | `hr`、`br`、`spo2`、`bp`、`diabetes`、`all` |
| 实现修订号 | 固定一份可执行实现及其规则，区别于模型发布版本号。 | `v1`、`v2` |

示例：

- `ppg.active.offline.hr.v1`、`ppg.active.offline.br.v1`：主动采集 PPG，分别离线输出心率、呼吸率的第一个独立实现修订。
- `ppg.active.offline.spo2.v1`：主动采集 PPG，离线输出血氧的第一个实现修订。
- `rppg.passive.offline.hr.v1`：被动采集 rPPG，离线输出心率的第一个实现修订。
- `rppg.active.realtime.hr.v1`：主动采集 rPPG，实时输出心率的第一个实现修订。
- `ppg_acc.active.offline.bp.v1`：主动采集 PPG 和加速度信号，离线输出血压相关结果的第一个实现修订。
- `ppg.active.offline.diabetes.v1`：主动采集 PPG，离线输出旧版模型概率及风险档位的第一个实现修订。

`ppg_acc` 表示 PPG 与加速度传感器组合；若算法从原始加速度推导 SCG，`requiredInputs` 应声明实际上传的加速度文件，而不是把它称为 SCG 文件。算法标识描述算法契约，不替代 `requiredInputs`、结果字段或单位声明。`active`／`passive` 表示采集模式；`offline`／`realtime` 表示处理模式。示例只规定命名，不表示相应执行器已经实现或注册。实时标识只有在执行运行时支持实时输入和结果返回时才能用于可执行版本；当前文件上传任务流程是离线处理。

算法标识指向代码中显式注册的稳定执行契约。实现目录使用完整标识的下划线形式，例如 `ppg_active_offline_hr_v1/`；不同修订平级放置，不再嵌套 `v1/`，也不注册无修订号或 `latest` 别名。未在当前代码注册的历史或测试标识不能用于新版本。旧融合 `ppg.active.offline.hr_br.v1` 与旧 PPG 场景 `ppg.active.offline.all.v1` 最终退役，不保留兼容别名；PPG+ACC 场景 `.all.v1` 与血压 `.bp.v1` 不变。

算法实现修订与模型发布版本相互独立：模型发布版本固定引用完整算法标识、参数、制品和投影指标，任务再固定引用该模型版本。仅调整已声明参数值或制品、且解释方式不变时，可创建引用同一实现的新模型版本；预处理、质量判定、推理或后处理行为改变且需保留旧行为时，应增加实现修订，不能覆盖旧标识。输出类别仍须在算法契约中定义具体结果字段、类型和单位。

## 正式离线算法开发规范

正式离线算法遵循统一流水线和策略接口。独立 HR、BR 与 SPO2 v1 分别按自己的结果契约执行；计算成功不代表医学准确性认证，实时算法不强行套用离线流水线。新增 HR、BR 的验证与部署状态须以实际执行记录为准。

### 执行模板与策略

算法定义通过 `create_runtime(VersionResources(...))` 将固定参数与已解析制品路径交给版本资源工厂，创建绑定单个发布版本的 `AlgorithmRuntime`。直接执行使用 `runtime.run(InferenceInput(...))`；入口逐任务校验必需输入、固定参数和制品路径、必需资料，以及完整输出与指标契约。运行时内部由统一离线模板固定执行顺序：读取与校验输入、预处理、质量检测、原始推理、可选的固定版本结果后处理、组织结果与输出文件。具体算法实现阶段方法并装配策略，不自行重写执行顺序，也不引入可任意排列节点的工作流配置。

质量检测和独立结果后处理分别使用策略接口。质量策略只报告本算法实际执行的评价；拒绝后立即停止，不进入推理或成功输出。未执行质检必须与质检通过区分，不能返回默认满分。若范围或有效性规则与最终载荷、文件紧密耦合，可由 `build_output.py` 在构造指标和写文件前处理；无论采用哪种组织方式，完整结果和投影指标都必须使用同一份最终数值。共享模板不设置通用质量阈值、医学范围或补值规则。

### 实现目录与阶段职责

共享执行模板放在 `health_inference/pipeline.py`，策略接口放在 `health_inference/strategies.py`。现有 `health_inference/quality.py` 只保存通用拒绝原因、明细及其校验；具体评价方法归各实现目录所有。

正式实现按职责组织必要模块，不将预处理与推理合并为含义模糊的 `processing.py`：

| 模块 | 职责 |
|---|---|
| `algorithm.py` | 声明算法契约、装配模板，并用短小方法衔接阶段；不堆放长篇输入解析、文件写入或结果校验。 |
| `health_inference/utils/ppg_input.py` | 共享 RGB/YUV PPG CSV 解析、通道和时间轴校验。 |
| `load_input.py` | 校验当前算法额外的输入边界，如固定窗口长度或采样率。 |
| `preprocessing.py` | 通道转换、信号提取、滤波、标准化或特征准备。 |
| `quality.py` | 实现该修订的质量评价策略与拒绝规则。 |
| `inference.py` | 根据预处理数据执行模型推理或确定性数值估计，产生原始预测。 |
| `build_output.py` | 构建最终载荷、指标和处理文件，并校验最终结果。 |
| `postprocessing.py` | 仅在该修订需要独立后处理策略时采用；与输出构造紧密耦合的规则可在 `build_output.py` 中、写文件前处理。 |

数据结构就近定义，使用明确类型传递阶段数据，不恢复不断写入通用字典的旧阶段容器。质量检测和推理复用本次预处理结果，质量检测不原地修改信号。PPG CSV 校验集中在 `utils/ppg_input.py`；独立 HR、BR 同源复用 `utils/ppg_preprocessing.py`、`utils/ppg_quality.py`、`utils/ppg_output.py` 的前置与文件写入，不复制实现，不跨任务或场景成员共享本次评分和波形。其他算法额外边界留在各自实现中，只抽取真实共用职责。

### 版本与任务状态隔离

算法定义、策略和 `AlgorithmRuntime` 只保存对应版本的固定参数、已解析制品路径和可复用执行资源；输入、波形、评分、预测和输出路径属于单次调用，不保存在共享运行时上。不同模型发布版本不能相互覆盖策略配置；不同算法修订不能依赖会随新修订变化的默认实现。公共函数、模板或依赖升级不能无意改变旧修订的解释；需要严格数值复现时，还须保留对应依赖环境，目录名本身不构成复现保证。

服务端适配器以固定模型版本主键缓存 `AlgorithmRuntime`，而非缓存任务输入。消费者停止并排空后调用 `clear_version_runtime_cache()` 释放运行时引用；该操作不删除登记的模型制品。新发布版本使用独立运行时，历史任务仍按其固定版本执行。

### 输入错误、质量拒绝与执行错误

输入不满足算法执行条件时报告 `INFERENCE.INVALID_INPUT` 并提供明确的受控原因。采样、有效时长等不符合执行要求时，不在执行模板中增加重采样、插值或补齐；也不将算法或第三方库的任意异常统一归咎于输入。质量拒绝仅用于算法已经明确得出的质量结论，沿用下文通用契约。

面向用户的错误由服务端按受控原因映射为固定文案，不透传第三方异常正文、原始健康数据或服务器路径。只在算法异常中填写文案不足以保证用户可见，修改错误表达时必须同步更新服务端失败映射。未预期异常仍属于执行错误；质量计算产生无法明确归因的非法值时，不自动补分或推断拒绝原因。

独立算法的输入、质量、执行和结果错误属于任务级失败：任务 `FAILED`、`result=null`，不保存部分指标或权威输出。统一场景则先在成员调用边界处理这些错误，包括成员制品初始化和输出文件错误；失败仅终止该成员，其余成员继续执行。两层均使用计算包 `health_inference.failures` 的同一安全映射，不从异常正文、`safe_message`、堆栈或任意附加属性生成公开信息。

场景成员恰有 `algorithmKey`、`status`、`result`、`failureCode`、`failureMessage`、`qualityFailure` 六个字段。成功成员保留原算法载荷，三个失败字段均为 `null`；失败成员的 `result=null`，只公开受控代码、固定摘要与合法可空质量拒绝。`qualityFailure` 仅为 `{reason, details}`：`LOW_SCORE` 的明细只有实际有限 `score`、`threshold`，`NO_VALID_SIGNAL` 的明细为 `null`。伪造质量码、未知原因或损坏明细降级为安全执行失败，不公开内部 `failureDetail`。最终汇总校验还会再次拒绝伪造文案、额外字段和不一致结局。

### 依赖、输出与验证

`health-inference` 基础依赖包含 pandas 与 NumPy，SciPy、NeuroKit2 等推理库由可选安装项声明并使用工作区单一锁文件。基础安装中的库仍须惰性导入：算法注册和 Web 启动不得加载 CSV 或计算路径专用库。算法不访问 ORM、HTTP 或实时健康资料，只使用执行输入提供的固定数据。算法只向本次唯一 `output_dir` 写文件，并以 `output_files` 的逻辑名到本地路径返回；服务端验证文件属于当前执行目录后，将不可变实际存储键写入成功结果。成功事务提交后，服务端才把 `<渠道代码>/<任务创建日期>/outputs/<task_id>/` 相对目录链接切换到该执行的完整输出集合。算法不得推导或写入该正式路径；用户下载始终按数据库实际键读取，终态消息或下载请求可幂等恢复链接而不重新运行算法。

验证应覆盖真实计算行为、输入拒绝、质量门禁、后处理边界、结果与指标一致性，以及版本和任务隔离。单元测试放在 `tests/unit/`，跨模块任务流程放在 `tests/integration/workflows/`。新增实现应提供实际运行证据；合成样本和旧版数值对照不能替代医学准确性验证。具体算法的后处理范围、补值行为和多块输入策略必须单独确定，不能由通用开发规范代选。

## 模型目录与版本发布

管理目录写接口均要求 `manager` 渠道 Access Token，且每次请求都会核对账号当前 `is_staff=True`。普通用户、普通渠道令牌及已撤销管理权限的账号不能写目录。管理账号建立和登录方式见[账号、资料与令牌](accounts.md)。

管理目录路由为：`POST /manager/models`；`GET`／`POST /manager/models/{model_id}/versions`；`PATCH /manager/model-versions/{version_id}`；`POST /manager/model-versions/{version_id}/artifacts`；`POST /manager/metrics`；`PUT /manager/model-versions/{version_id}/metrics`；`POST /manager/model-versions/{version_id}/publish`；`PUT /manager/channels/{channel}/models/{model_id}/selection`。普通查询为 `GET /{channel}/models`。

独立 `ppg.active.offline.hr.v1` 与 `ppg.active.offline.br.v1` 使用空参数、零制品、零必需健康资料，输入均为 `ppg` CSV。HR 只返回 `hr/quality`、只投影 `hr`／`beats_per_minute`、只输出 `processed_ppg`；BR 只返回 `br/quality`、只投影 `br`／`breaths_per_minute`、只输出 `processed_rsp`。BR 在确认原始值有限后限制到 `[8,20]`，NaN 失败不造数；两者均不允许 nullable 指标或内部 `failures`。规则见[HR v1](algorithms/ppg-active-offline-hr-v1.md)、[BR v1](algorithms/ppg-active-offline-br-v1.md)。

正式 `ppg.active.offline.spo2.v1` 要求 `ppg` 输入、空参数及 `spo2`、`feature_names`、`stable_features` 三个模型制品，投影指标为 `spo2`／`percent`，不要求健康资料。实现规则见[PPG 主动离线血氧 v1](algorithms/ppg-active-offline-spo2-v1.md)。HR、BR 与血氧算法均由代码定义 `isExample=false`，不提供无修订号别名。

正式 `ppg_acc.active.offline.bp.v1` 也已注册，使用 `ppg`、`acc` 两个 CSV 输入，不要求健康资料且只接受空参数。版本必须绑定 `sbp`、`dbp` 两个 joblib 模型和 `normalize` CSV；投影指标为 `sbp`／`dbp`、类型 `number`、单位 `mmHg`。该算法由代码定义 `isExample=false`，不提供无修订号别名。实现规则见[PPG+ACC 离线血压 v1](algorithms/ppg-acc-active-offline-bp-v1.md)。

正式 `ppg.active.offline.diabetes.v1` 也已注册，仅要求 `ppg` CSV，不要求健康资料且只接受空参数；每个版本必须绑定单个 `diabetes` ONNX 制品。投影指标为 `diabetes_probability`，类型 `number`、单位 `ratio`；结果还包含未经舍入的 `prob`、按旧 JavaScript 舍入边界生成的 `risk_level` 和实际质量信息。实现及迁移基线见[PPG 主动离线糖尿病风险 v1](algorithms/ppg-active-offline-diabetes-v1.md)。

### 固定统一离线场景

两个场景沿用原模型发布与任务接口，管理配置的名称和完整算法 key 如下；名称保存在 `Model.name`，不是注册表中的额外字段：

| 模型名称 | 算法 key | 固定成员顺序 | 上传输入 | 本场景版本必需制品 |
|---|---|---|---|---|
| 统一PPG场景 | `ppg.active.offline.all.v2` | `hr` → `br` → `spo2` → `diabetes` | `ppg: csv` | `spo2: onnx`、`feature_names: json`、`stable_features: json`、`diabetes: onnx` |
| 统一PPG+ACC场景 | `ppg_acc.active.offline.all.v1` | `bp` | `ppg: csv`、`acc: csv` | `sbp: joblib`、`dbp: joblib`、`normalize: csv` |

`hr`、`br`、`spo2`、`diabetes`、`bp` 分别固定引用 `ppg.active.offline.hr.v1`、`ppg.active.offline.br.v1`、`ppg.active.offline.spo2.v1`、`ppg.active.offline.diabetes.v1`、`ppg_acc.active.offline.bp.v1` 的完整运行时。HR、BR 的同源前置复用共享模块，但各成员独立执行质量和推理，不共享本次质量结论，也不重算糖尿病风险分类。两个场景不随新增算法或成员新修订自动扩展；PPG+ACC 场景只测血压。

两者均为 `isExample=false`、空参数、无额外必需资料。发布必须绑定完整制品并集、通过原成员的格式规则及物理文件检查；场景指标契约取原成员并集（PPG 为 `hr`、`br`、`spo2`、`diabetes_probability`，PPG+ACC 为 `sbp`、`dbp`），类型和单位不变。仍由版本配置选择投影指标，不新增 `risk_level` 指标。场景的部分成功能力不放宽发布条件，也不代表医学准确性认证。

一个场景任务只固定一个场景发布版本；成员仅使用该版本自己的制品子集，不动态读取独立模型的渠道当前版本。更新制品须发布新场景版本，改变成员或计算解释须新增场景实现修订。成员首次使用时延迟初始化，仅缓存成功准备的版本运行时；初始化失败不在同一任务自动重试，后续任务可以重新准备。每次成员调用使用独立任务输入及输出子目录，不复用上一任务的资料、错误或结果。

每个成员依次执行自己的输入、预处理、质量、推理、后处理和结果校验。首个质量拒绝只终止当前成员，不阻止其他成员，不补零或补分；BR 失败不影响已成功 HR。正常汇总后任务为 `SUCCEEDED`，载荷恰含 `outcome/results`，每成员只有 `SUCCEEDED/FAILED`。PPG 按四成员成功数判定：4 为 `ALL_SUCCEEDED`、1–3 为 `PARTIALLY_SUCCEEDED`、0 为 `ALL_FAILED`。全失败仍保留安全汇总，指标和文件为空，顶层失败字段及 `qualityFailure` 为空；场景存储、隔离清理或汇总错误仍是任务级失败。读数路径分别为 `results.hr.result.hr`、`results.br.result.br`。

所有成员顺序执行，共享既有任务整体硬时限、执行令牌及租约，不增加成员独立超时、并行调度、自动重试或中间检查点。整体超时或失租不能提交已经完成的部分结果。场景不预先采用全部成员的最大输入门槛拒绝整次输入；各成员继续按原规则判断。

物理文件位于本次 `output_dir/<成员名>/`，保留原始文件名和 CSV 表头；成功文件逻辑名统一加成员前缀：PPG 为 `hr_processed_ppg`、`br_processed_rsp`、`spo2_processed_ppg`、`diabetes_processed_ppg`，PPG+ACC 为 `bp_processed_ppg`、`bp_processed_scg`。成员载荷本身不含 `output_files`；服务验证后只在场景顶层追加受保护引用。失败成员本次目录必须安全清理，不能证明隔离时整个场景失败；正式发布树只保留成功成员文件，全部失败不发布正式目录。

### 算法报告质量拒绝

算法只在依据自身固定版本的规则明确确认质量要求不满足时报告拒绝；本契约只传递算法结论，不新增实际质检规则、通用阈值或默认评分。`LOW_SCORE` 必须携带本次实际比较的有限 `score` 和 `threshold`，且 `score < threshold`；不接受布尔值、字符串、NaN 或无穷值，也不先四舍五入。`NO_VALID_SIGNAL` 仅用于算法明确确认没有可用信号的情况，明细为 `None`，不要求或补造评分。未执行质量检查不代表质量通过。

算法通过 `QualityRejectedError` 终止本次计算。以下函数只演示如何表达已由算法自身规则得出的两类拒绝，不定义如何计算分数或判断信号：

```python
from health_inference import (
    LowScoreDetails,
    QualityRejectedError,
    QualityRejection,
    QualityRejectionReason,
)


def reject_low_score(score: int | float, threshold: int | float) -> None:
    """报告算法已经判定的低分结果。"""
    raise QualityRejectedError(
        QualityRejection(
            QualityRejectionReason.LOW_SCORE,
            LowScoreDetails(score=score, threshold=threshold),
        )
    )


def reject_no_valid_signal() -> None:
    """报告算法已经确认没有可用信号。"""
    raise QualityRejectedError(QualityRejection(QualityRejectionReason.NO_VALID_SIGNAL, None))
```

正式 PPG v1 执行其声明的实际质量评估；任何算法都只能在依据自身固定规则明确判定质量不满足时报告质量拒绝，不能把输入错误、未运行质量检查或意外异常推断为拒绝原因。

以下示例使用已经登录取得的 `MANAGER_ACCESS`。创建模型后从响应的 `data.modelId` 取出 `MODEL_ID`：

```sh
export API=http://127.0.0.1:8000/api/v1
export MANAGER_ACCESS='<管理登录响应中的 data.accessToken>'

curl --include --request POST "$API/manager/models" \
  --header "Authorization: Bearer $MANAGER_ACCESS" \
  --header 'Content-Type: application/json' \
  --data '{
    "code": "ppg.active.offline.hr",
    "name": "HR v1",
    "description": "使用主动采集的 PPG 独立测量心率，返回 HR、实际质量和处理后 PPG；结果不构成医学诊断。"
  }'
```

使用返回的模型 ID 创建未发布版本，并从 `data.versionId` 取出 `VERSION_ID`：

```sh
export MODEL_ID='<创建模型返回的 data.modelId>'
curl --include --request POST "$API/manager/models/$MODEL_ID/versions" \
  --header "Authorization: Bearer $MANAGER_ACCESS" \
  --header 'Content-Type: application/json' \
  --data '{"version":"1.0.0","algorithmKey":"ppg.active.offline.hr.v1","params":null}'
```

创建与算法类型、单位完全一致的指标，再用指标代码设置版本完整投影集合：

```sh
export VERSION_ID='<创建版本返回的 data.versionId>'
curl --include --request POST "$API/manager/metrics" \
  --header "Authorization: Bearer $MANAGER_ACCESS" \
  --header 'Content-Type: application/json' \
  --data '{"code":"hr","description":"心率","valueType":"number","unit":"beats_per_minute"}'
```

BR 使用独立模型 `ppg.active.offline.br`、版本 `1.0.0` 和算法 `ppg.active.offline.br.v1`，指标定义为 `br/number/breaths_per_minute`，版本只绑定 `["br"]`。以下 HR 版本只绑定 `["hr"]`；已有指标仅在核对类型与单位后复用。

```sh
curl --include --request PUT "$API/manager/model-versions/$VERSION_ID/metrics" \
  --header "Authorization: Bearer $MANAGER_ACCESS" \
  --header 'Content-Type: application/json' \
  --data '{"metricCodes":["hr"]}'
```

发布会校验算法已注册、固定参数有效、制品逻辑名与算法声明完全一致且物理文件存在，以及所有已选指标的代码、值类型和单位。独立 HR、BR v1 不需要制品；SPO2 v1 必须上传 `spo2`、`feature_names` 和 `stable_features`；血压 v1 必须上传 `sbp`、`dbp` 和 `normalize`；糖尿病 v1 必须上传 `diabetes` ONNX；其他算法按各自契约处理。发布后算法、参数、制品和指标不可原地修改；重复发布返回原发布时间。

算法通过 `input_extensions` 与 `artifact_extensions` 为每个逻辑名声明允许的后缀集合；映射键必须分别与 `required_inputs`、`required_artifacts` 完全一致。后缀使用不带点号的小写扩展名，并在注册时校验。当前正式契约如下：

|算法|输入后缀|制品后缀|
|---|---|---|
|`ppg.active.offline.hr.v1`|`ppg: csv`|无|
|`ppg.active.offline.br.v1`|`ppg: csv`|无|
|`ppg.active.offline.spo2.v1`|`ppg: csv`|`spo2: onnx`、`feature_names: json`、`stable_features: json`|
|`ppg_acc.active.offline.bp.v1`|`ppg: csv`、`acc: csv`|`sbp: joblib`、`dbp: joblib`、`normalize: csv`|
|`ppg.active.offline.diabetes.v1`|`ppg: csv`|`diabetes: onnx`|
|`ppg.active.offline.all.v2`|`ppg: csv`|`spo2: onnx`、`feature_names: json`、`stable_features: json`、`diabetes: onnx`|
|`ppg_acc.active.offline.all.v1`|`ppg: csv`、`acc: csv`|`sbp: joblib`、`dbp: joblib`、`normalize: csv`|

上传端只从客户端文件名提取最后一个后缀并转为小写进行白名单校验；客户端路径、完整文件名和 MIME 不决定存储键。扩展名不证明文件内容有效，CSV 仍由算法解析，ONNX 与 JSON 由版本资源加载器校验；血压版本的 joblib 仅限受信发布，具体信任边界见算法说明。


仅当执行器声明了制品时才上传；`logicalName` 必须与算法契约中的名称完全一致。文件名只用于提取并校验格式后缀，不用于构造存储路径。血氧模型及清单分别使用 `.onnx` 和 `.json`，糖尿病 v1 使用 `logicalName=diabetes` 和 `.onnx`：

```sh
curl --include --request POST "$API/manager/model-versions/$VERSION_ID/artifacts" \
  --header "Authorization: Bearer $MANAGER_ACCESS" \
  --form 'logicalName=spo2' \
  --form 'file=@./spo2.onnx'
```

```sh
curl --include --request POST "$API/manager/model-versions/$VERSION_ID/artifacts" \
  --header "Authorization: Bearer $MANAGER_ACCESS" \
  --form 'logicalName=diabetes' \
  --form 'file=@./diabetes.onnx'
```

```sh
curl --include --request POST "$API/manager/model-versions/$VERSION_ID/publish" \
  --header "Authorization: Bearer $MANAGER_ACCESS"
curl --include --request PUT "$API/manager/channels/ios-cn/models/$MODEL_ID/selection" \
  --header "Authorization: Bearer $MANAGER_ACCESS" \
  --header 'Content-Type: application/json' \
  --data "{\"versionId\":$VERSION_ID}"
```

每个已知渠道独立选择本模型的一个已发布版本。同版本重选不改变草稿；切换版本会在同一事务中将该渠道旧版本全部 `DRAFT` 任务标为 `FAILED`／`TASK.MODEL_SWITCHED`，已提交或运行中的任务继续使用固定版本。回退不会恢复已失败草稿。

本次目录目标为新增 `ppg.active.offline.hr`、`ppg.active.offline.br` 各 `1.0.0`，保留 `ppg.active.offline.all` 模型并新增 `2.0.0` 绑定 `.all.v2`，三者均选择到五普通渠道 `android-cn/android-intl/ios-cn/ios-intl/wxapp`；旧融合模型及旧 PPG 场景 v1 发布版本最终退役。退役前应先完成新实现对照验证、按授权清理测试环境全部推理历史及依赖，再处理旧版本与渠道引用，不能用兼容别名兜底。此处为操作要求，不是已执行记录；详见[目录清单](initial-database-data.md)。

普通渠道使用与路径匹配的 Access 查询自己已选的目录：

```sh
curl --include "$API/ios-cn/models?offset=0&limit=20" \
  --header "Authorization: Bearer <ios-cn 普通用户 Access>"
```

结果仅包含本渠道当前已发布版本、算法要求的输入逻辑名和 `isExample`，按模型 ID 稳定排序；`offset` 不得为负数，`limit` 范围为 1–100、默认 20。普通响应不包含算法参数、制品存储键或服务器路径。完整路由和字段见 `/api/v1/docs`。

Swagger UI 按「账号认证」「管理端模型管理」「用户端模型目录」「用户端用户与资料」「用户端推理任务与文件」分组。登录、刷新和登出共用 `/{channel}` 路由；在 Apifox 中将 `channel` 设为 `manager` 或目标普通渠道，以匹配令牌的渠道。

