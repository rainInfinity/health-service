# 模型与算法开发

## 算法标识命名

新正式算法的可执行标识采用 `<传感器集合>.<采集模式>.<处理模式>.<输出>.v<实现修订号>`：全部使用小写英文，层级以 `.` 分隔；多传感器集合使用下划线连接，不使用 `+`。实现修订号使用从 `1` 开始的正整数。

| 标识段 | 含义 | 示例 |
|---|---|---|
| 传感器集合 | 算法使用的传感器或信号类型；多个类型按约定顺序连接。 | `ppg`、`rppg`、`ppg_acc` |
| 采集模式 | 测量数据的采集方式。 | `active`、`passive` |
| 处理模式 | 对数据执行推理的方式。 | `offline`、`realtime` |
| 输出 | 算法输出的业务指标类别。 | `hr`、`hr_br`、`bp` |
| 实现修订号 | 固定一份可执行实现及其规则，区别于模型发布版本号。 | `v1`、`v2` |

示例：

- `ppg.active.offline.hr_br.v1`：主动采集 PPG，离线输出心率和呼吸率的第一个实现修订。
- `rppg.passive.offline.hr.v1`：被动采集 rPPG，离线输出心率的第一个实现修订。
- `rppg.active.realtime.hr.v1`：主动采集 rPPG，实时输出心率的第一个实现修订。
- `ppg_acc.active.offline.bp.v1`：主动采集 PPG 和加速度信号，离线输出血压相关结果的第一个实现修订。

`ppg_acc` 表示 PPG 与加速度传感器组合；若算法从原始加速度推导 SCG，`requiredInputs` 应声明实际上传的加速度文件，而不是把它称为 SCG 文件。算法标识描述算法契约，不替代 `requiredInputs`、结果字段或单位声明。`active`／`passive` 表示采集模式；`offline`／`realtime` 表示处理模式。示例只规定命名，不表示相应执行器已经实现或注册。实时标识只有在执行运行时支持实时输入和结果返回时才能用于可执行版本；当前文件上传任务流程是离线处理。

算法标识指向代码中显式注册的稳定执行契约。实现目录使用完整标识的下划线形式，例如 `ppg_active_offline_hr_br_v1/`；不同修订平级放置，不再嵌套 `v1/`，也不注册无修订号或 `latest` 别名。未在当前代码注册的历史或测试标识不能用于新版本。

算法实现修订与模型发布版本相互独立：模型发布版本固定引用完整算法标识、参数、制品和投影指标，任务再固定引用该模型版本。仅调整已声明参数值或制品、且解释方式不变时，可创建引用同一实现的新模型版本；预处理、质量判定、推理或后处理行为改变且需保留旧行为时，应增加实现修订，不能覆盖旧标识。输出类别仍须在算法契约中定义具体结果字段、类型和单位。

## 正式离线算法开发规范

统一离线流水线、策略接口和正式 HR/BR v1 执行器均已实现；`ppg.active.offline.hr_br.v1` 会根据单块 PPG 输入计算 HR/BR 并执行实际质量门禁，但计算结果不代表医学准确性认证。实时算法不强行套用离线流水线。

### 执行模板与策略

保留 `AlgorithmDefinition.run()` 作为契约校验入口，负责必需文件、制品、资料、固定参数及输出契约校验。其内部由统一离线模板固定执行顺序：读取与校验输入、预处理、质量检测、原始推理、可选的固定版本结果后处理、组织结果与输出文件。具体算法实现阶段方法并装配策略，不自行重写执行顺序，也不引入可任意排列节点的工作流配置。

质量检测和独立结果后处理分别使用策略接口。质量策略只报告本算法实际执行的评价；拒绝后立即停止，不进入推理或成功输出。未执行质检必须与质检通过区分，不能返回默认满分。若范围或有效性规则与最终载荷、文件紧密耦合，可由 `build_output.py` 在构造指标和写文件前处理；无论采用哪种组织方式，完整结果和投影指标都必须使用同一份最终数值。共享模板不设置通用质量阈值、医学范围或补值规则。

### 实现目录与阶段职责

共享执行模板放在 `health_inference/pipeline.py`，策略接口放在 `health_inference/strategies.py`。现有 `health_inference/quality.py` 只保存通用拒绝原因、明细及其校验；具体评价方法归各实现目录所有。

正式实现按职责组织必要模块，不将预处理与推理合并为含义模糊的 `processing.py`：

| 模块 | 职责 |
|---|---|
| `algorithm.py` | 声明算法契约、装配模板，并用短小方法衔接阶段；不堆放长篇输入解析、文件写入或结果校验。 |
| `load_input.py` | 读取文件，校验字段、数值与时间轴，形成已校验输入。 |
| `preprocessing.py` | 通道转换、信号提取、滤波、标准化或特征准备。 |
| `quality.py` | 实现该修订的质量评价策略与拒绝规则。 |
| `inference.py` | 根据预处理数据执行模型推理或确定性数值估计，产生原始预测。 |
| `build_output.py` | 构建最终载荷、指标和处理文件，并校验最终结果。 |
| `postprocessing.py` | 仅在该修订需要独立后处理策略时采用；与输出构造紧密耦合的规则可在 `build_output.py` 中、写文件前处理。 |

数据结构就近定义，使用明确类型传递阶段数据，不恢复不断写入通用字典的旧阶段容器。质量检测和推理复用同一份预处理结果；质量检测不原地修改后续推理使用的信号。PPG v1 使用 `load_input.py` 与 `build_output.py`，不创建算法专属 `postprocessing.py`；通用模板的可选策略接口仍保留。公共计算逻辑仅在存在真实复用时按职责提取，不预先搬入笼统的 `utils` 层。

### 版本与任务状态隔离

算法实例及策略只保存对应版本的固定配置和可复用资源，输入、波形、评分、预测和输出路径属于单次调用，不保存在共享实例上。不同模型发布版本不能相互覆盖策略配置；不同算法修订不能依赖会随新修订变化的默认实现。公共函数、模板或依赖升级不能无意改变旧修订的解释；需要严格数值复现时，还须保留对应依赖环境，目录名本身不构成复现保证。

### 输入错误、质量拒绝与执行错误

输入不满足算法执行条件时报告 `INFERENCE.INVALID_INPUT` 并提供明确的受控原因。采样、有效时长等不符合执行要求时，不在执行模板中增加重采样、插值或补齐；也不将算法或第三方库的任意异常统一归咎于输入。质量拒绝仅用于算法已经明确得出的质量结论，沿用下文通用契约。

面向用户的错误由服务端按受控原因映射为固定文案，不透传第三方异常正文、原始健康数据或服务器路径。只在算法异常中填写文案不足以保证用户可见，修改错误表达时必须同步更新服务端失败映射。未预期异常仍属于执行错误；质量计算产生无法明确归因的非法值时，不自动补分或推断拒绝原因。

### 依赖、输出与验证

`health-inference` 基础依赖包含 pandas 与 NumPy，SciPy、NeuroKit2 等推理库由可选安装项声明并使用工作区单一锁文件。基础安装中的库仍须惰性导入：算法注册和 Web 启动不得加载 CSV 或计算路径专用库。算法不访问 ORM、HTTP 或实时健康资料，只使用执行输入提供的固定数据。处理后文件写入本次执行的 `output_dir`，通过逻辑引用返回，不依据上传路径替换规则推导输出位置。

验证应覆盖真实计算行为、输入拒绝、质量门禁、后处理边界、结果与指标一致性，以及版本和任务隔离。单元测试放在 `tests/unit/`，跨模块任务流程放在 `tests/integration/workflows/`。新增实现应提供实际运行证据；合成样本和旧版数值对照不能替代医学准确性验证。具体算法的后处理范围、补值行为和多块输入策略必须单独确定，不能由通用开发规范代选。

## 模型目录与版本发布

管理目录写接口均要求 `manager` 渠道 Access Token，且每次请求都会核对账号当前 `is_staff=True`。普通用户、普通渠道令牌及已撤销管理权限的账号不能写目录。管理账号建立和登录方式见[账号、资料与令牌](accounts.md)。

管理目录路由为：`POST /manager/models`；`GET`／`POST /manager/models/{model_id}/versions`；`PATCH /manager/model-versions/{version_id}`；`POST /manager/model-versions/{version_id}/artifacts`；`POST /manager/metrics`；`PUT /manager/model-versions/{version_id}/metrics`；`POST /manager/model-versions/{version_id}/publish`；`PUT /manager/channels/{channel}/models/{model_id}/selection`。普通查询为 `GET /{channel}/models`。

正式 `ppg.active.offline.hr_br.v1` 已注册，可用空参数、零制品和零健康资料发布；输入逻辑名为 `ppg`，投影指标使用 `hr`／`br` 及其固定单位。实现规则见[PPG 主动离线 HR/BR v1](algorithms/ppg-active-offline-hr-br-v1.md)，其 `isExample` 为 `false`，不提供无修订号别名。

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
    "code": "ppg.active.offline.hr_br",
    "name": "HR/BR v1",
    "description": "使用主动采集的 PPG 信号离线测量心率（HR）和呼吸率（BR），返回两项数值、信号质量评分及处理后的 PPG 与呼吸波（RSP）文件；计算结果不构成医学诊断。"
  }'
```

使用返回的模型 ID 创建未发布版本，并从 `data.versionId` 取出 `VERSION_ID`：

```sh
export MODEL_ID='<创建模型返回的 data.modelId>'
curl --include --request POST "$API/manager/models/$MODEL_ID/versions" \
  --header "Authorization: Bearer $MANAGER_ACCESS" \
  --header 'Content-Type: application/json' \
  --data '{"version":"1.0.0","algorithmKey":"ppg.active.offline.hr_br.v1","params":null}'
```

创建与算法类型、单位完全一致的指标，再用指标代码设置版本完整投影集合：

```sh
export VERSION_ID='<创建版本返回的 data.versionId>'
curl --include --request POST "$API/manager/metrics" \
  --header "Authorization: Bearer $MANAGER_ACCESS" \
  --header 'Content-Type: application/json' \
  --data '{"code":"hr","description":"心率","valueType":"number","unit":"beats_per_minute"}'
curl --include --request POST "$API/manager/metrics" \
  --header "Authorization: Bearer $MANAGER_ACCESS" \
  --header 'Content-Type: application/json' \
  --data '{"code":"br","description":"呼吸率","valueType":"number","unit":"breaths_per_minute"}'
curl --include --request PUT "$API/manager/model-versions/$VERSION_ID/metrics" \
  --header "Authorization: Bearer $MANAGER_ACCESS" \
  --header 'Content-Type: application/json' \
  --data '{"metricCodes":["hr","br"]}'
```

发布会校验算法已注册、固定参数有效、制品逻辑名与算法声明完全一致且文件存在，以及所有已选指标的代码、值类型和单位。正式 PPG v1 不需要模型制品；其他算法是否需要制品以其注册契约为准。发布成功后算法、参数、制品和指标集合不可原地修改；重复发布返回原发布时间。

仅当执行器声明了制品时才上传；`logicalName` 必须与算法契约中的名称完全一致，文件名不会用于构造存储路径：

```sh
curl --include --request POST "$API/manager/model-versions/$VERSION_ID/artifacts" \
  --header "Authorization: Bearer $MANAGER_ACCESS" \
  --form 'logicalName=weights' \
  --form 'file=@./weights.bin'
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

普通渠道使用与路径匹配的 Access 查询自己已选的目录：

```sh
curl --include "$API/ios-cn/models?offset=0&limit=20" \
  --header "Authorization: Bearer <ios-cn 普通用户 Access>"
```

结果仅包含本渠道当前已发布版本、算法要求的输入逻辑名和 `isExample`，按模型 ID 稳定排序；`offset` 不得为负数，`limit` 范围为 1–100、默认 20。普通响应不包含算法参数、制品存储键或服务器路径。完整路由和字段见 `/api/v1/docs`。

Swagger UI 按「账号认证」「管理端模型管理」「用户端模型目录」「用户端用户与资料」「用户端推理任务与文件」分组。登录、刷新和登出共用 `/{channel}` 路由；在 Apifox 中将 `channel` 设为 `manager` 或目标普通渠道，以匹配令牌的渠道。

