# 初始数据库数据维护

本文记录业务数据库首次可用时需要维护的渠道与正式模型目录。数据库结构仍由 Django migrations 管理；本文是初始数据清单和人工维护流程，不提供自动写库命令、SQL 或脚本。

## 算法注册与数据库目录

算法执行器由 `health-inference` 代码显式注册，不存在独立的算法数据库表。四个独立正式算法为 `ppg.active.offline.hr_br.v1`、`ppg.active.offline.spo2.v1`、`ppg_acc.active.offline.bp.v1` 和 `ppg.active.offline.diabetes.v1`；新增两个固定场景为 `ppg.active.offline.all.v1` 和 `ppg_acc.active.offline.all.v1`。下文保留四个独立模型的初始配置，并另列场景的隔离联调配置流程，不自动替代现有模型或渠道选择。每个版本只有在部署环境提供并核验相应模型制品后才能发布。`var/old-assets/` 中的本机测试或迁移候选制品不作为生产初始数据。数据库中的 `ModelVersion.algorithm_key` 引用完整算法标识；未在当前部署代码注册的算法不能提前写入模型目录或发布。

渠道、模型、模型版本、指标定义、版本指标关系及渠道选择分别保存在 `health_channel`、`health_model`、`health_model_version`、`health_metric_definition`、`health_model_version_metric` 和 `health_channel_model_selection`。

## 初始渠道

首次建立目录时，维护以下六个渠道：

| 渠道标识 | 类型 | 初始模型选择 |
|---|---|---|
| `android-cn` | 普通渠道 | HR/BR、SpO2、BP、糖尿病风险模型分别选择下文的 `1.0.0` |
| `android-intl` | 普通渠道 | HR/BR、SpO2、BP、糖尿病风险模型分别选择下文的 `1.0.0` |
| `ios-cn` | 普通渠道 | HR/BR、SpO2、BP、糖尿病风险模型分别选择下文的 `1.0.0` |
| `ios-intl` | 普通渠道 | HR/BR、SpO2、BP、糖尿病风险模型分别选择下文的 `1.0.0` |
| `wxapp` | 普通渠道 | HR/BR、SpO2、BP、糖尿病风险模型分别选择下文的 `1.0.0` |
| `manager` | 管理渠道 | 不配置普通用户模型选择 |

每个普通渠道分别维护四个模型选择，各自指向对应的已发布初始版本，并非四选一。某算法的真实制品尚未由环境提供并核验时，不得发布空制品版本或配置不存在的选择；补齐制品后再完成该环境的初始配置。上述选择是初始配置，不是持续强制的默认值。管理人员后续可以按渠道切换版本；维护时不得因重新录入初始数据而覆盖已有选择。

两个统一场景不加入上述默认初始选择。只有在获授权的隔离联调环境完成下文配置后，才显式为指定普通渠道选择场景模型；这不会要求删除、取消或切换原四个模型的已有选择。本文更新不执行任何业务数据写入或生产发布。

## 管理账户

`manager` 渠道记录与管理用户是独立数据。每个环境应建立至少一个受信任的管理账户；通过 Django shell 的 `create_user` 创建，并设置 `is_staff=True`、`is_superuser=False`。密码由 Django 哈希存储。用户名和密码按环境单独管理，不在本文或版本库中记录共享凭据。创建步骤见[账号、资料与令牌](accounts.md)。

## 初始模型版本

| 字段 | 初始值 |
|---|---|
| 模型编码 | `ppg.active.offline.hr_br` |
| 模型名称 | `HR/BR v1` |
| 模型说明 | 使用主动采集的 PPG 信号离线测量心率（HR）和呼吸率（BR），返回两项数值、信号质量评分及处理后的 PPG 与呼吸波（RSP）文件；计算结果不构成医学诊断。 |
| 版本号 | `1.0.0` |
| 算法标识 | `ppg.active.offline.hr_br.v1` |
| 参数 | `null`（该算法不接受非空参数） |
| 必需输入 | `ppg`（CSV） |
| 必需制品 | 无 |
| 必需健康资料 | 无 |
| 必需指标 | `hr`、`br` |
| 示例算法 | 否，`isExample=false` |

### 血氧 v1

| 字段 | 初始值 |
|---|---|
| 模型编码 | `ppg.active.offline.spo2` |
| 模型名称 | `SpO2 v1` |
| 模型说明 | 使用主动采集的 PPG 信号离线估算血氧饱和度，并输出处理后的 PPG 文件；计算结果不构成医学诊断。 |
| 版本号 | `1.0.0` |
| 算法标识 | `ppg.active.offline.spo2.v1` |
| 参数 | `null`（该算法不接受非空参数） |
| 必需输入 | `ppg`（CSV） |
| 必需制品 | `spo2`（ONNX）、`feature_names`（JSON）、`stable_features`（JSON） |
| 必需健康资料 | 无 |
| 必需指标 | `spo2` |
| 示例算法 | 否，`isExample=false` |

### 血压 v1

| 字段 | 初始值 |
|---|---|
| 模型编码 | `ppg_acc.active.offline.bp` |
| 模型名称 | `BP v1` |
| 模型说明 | 使用主动采集的 PPG 与 ACC 信号离线测量收缩压（SBP）和舒张压（DBP）；计算结果不构成医学诊断。 |
| 版本号 | `1.0.0` |
| 算法标识 | `ppg_acc.active.offline.bp.v1` |
| 参数 | `null`（该算法不接受非空参数） |
| 必需输入 | `ppg`、`acc`（均为 CSV） |
| 必需制品 | `sbp`、`dbp`（joblib）、`normalize`（CSV） |
| 必需健康资料 | 无；执行读取提交时可用的资料快照。未知性别可能传为 `0`，若制品不支持该值则任务会失败。 |
| 必需指标 | `sbp`、`dbp` |
| 示例算法 | 否，`isExample=false` |

血压的 `joblib` 制品可在加载时执行代码，只能使用部署环境信任的来源；扩展名和兼容性检查不能证明制品安全性。详见[PPG+ACC 离线血压 v1](algorithms/ppg-acc-active-offline-bp-v1.md)。

### 糖尿病风险 v1

| 字段 | 初始值 |
|---|---|
| 模型编码 | `ppg.active.offline.diabetes` |
| 模型名称 | `糖尿病风险 v1` |
| 模型说明 | 使用主动采集的 PPG 信号输出风险概率和风险档位；结果不表示血糖浓度、确诊或医学风险结论。 |
| 版本号 | `1.0.0` |
| 算法标识 | `ppg.active.offline.diabetes.v1` |
| 参数 | `null`（该算法不接受非空参数） |
| 必需输入 | `ppg`（CSV） |
| 必需制品 | `diabetes`（ONNX） |
| 必需健康资料 | 无 |
| 必需指标 | `diabetes_probability` |
| 示例算法 | 否，`isExample=false` |

### 统一场景版本

场景各自对应一个新 `Model` 和一个场景发布版本，不是三个独立版本 ID 的组合参数。名称固定如下；模型编码沿用去掉实现修订后缀的命名约定，前端仍须使用目录实际返回值：

| 字段 | 统一PPG场景 | 统一PPG+ACC场景 |
|---|---|---|
| 模型编码 | `ppg.active.offline.all` | `ppg_acc.active.offline.all` |
| 模型名称 | `统一PPG场景` | `统一PPG+ACC场景` |
| 算法标识 | `ppg.active.offline.all.v1` | `ppg_acc.active.offline.all.v1` |
| 版本号 | 环境实际创建的版本；首次可采用 `1.0.0`，不假定已有 | 同左 |
| 参数 | `null` 或 `{}`，拒绝非空参数 | `null` 或 `{}`，拒绝非空参数 |
| 固定成员 | `hr_br` → `spo2` → `diabetes` | 仅 `bp` |
| 固定成员实现 | `ppg.active.offline.hr_br.v1`、`ppg.active.offline.spo2.v1`、`ppg.active.offline.diabetes.v1` | `ppg_acc.active.offline.bp.v1` |
| 必需输入 | `ppg`（CSV） | `ppg`、`acc`（均为 CSV） |
| 完整必需制品 | `spo2`（ONNX）、`feature_names`、`stable_features`（JSON）、`diabetes`（ONNX） | `sbp`、`dbp`（joblib）、`normalize`（CSV） |
| 完整投影配置建议 | `hr`、`br`、`spo2`、`diabetes_probability` | `sbp`、`dbp` |
| 必需健康资料 | 无 | 无；沿用血压成员的提交时冻结资料及缺失值规则 |
| 示例算法 | 否，`isExample=false` | 否，`isExample=false` |

`all.v1` 的成员集合和实现修订固定在代码中，不自动扩展，也不读取其他模型的渠道当前版本。场景版本直接绑定全部成员制品，不能在参数里传独立 `modelId` 或 `versionId`。独立模型版本切换不改变场景资源；替换场景制品需新建场景版本，改变成员集合或计算解释需新算法修订。

发布仍检查制品集合完整、无额外逻辑名、后缀合法且物理文件存在，不因运行时允许部分成功而放宽。制品内容与信任要求沿用各独立成员，特别是 `joblib` 可在加载时执行代码，不得使用不可信来源。场景只投影成功成员实际产生且版本选定的指标，成员失败不补零；`risk_level` 仅保留在糖尿病完整结果中，不新建指标定义。

四个独立算法和两个场景的 `isExample=false` 均只表示不是示例执行器，不是临床认证、医学准确性保证或诊断许可。合成制品联调只证明流程，不代替真实模型核验或医学验收。

模型编码（`health_model.code`）全局唯一；同一模型只保留一条 `Model` 记录，多个 `ModelVersion` 关联该模型并可引用 `.v1`、`.v2` 等不同算法修订。不要为每个算法修订重复创建相同类别的模型记录。

初始清单按各独立算法的完整指标集合配置；场景复用其成员指标并集，不新建同义指标。接口允许仅选择算法声明中的部分投影项，投影选择不改变完整结果；要按本文配置完整目录，应关联下列对应集合，不同算法的指标不会互相替代：

| 指标编码 | 含义 | 值类型 | 单位 |
|---|---|---|---|
| `hr` | 心率 | `number` | `beats_per_minute` |
| `br` | 呼吸率 | `number` | `breaths_per_minute` |
| `spo2` | 血氧饱和度 | `number` | `percent` |
| `sbp` | 收缩压 | `number` | `mmHg` |
| `dbp` | 舒张压 | `number` | `mmHg` |
| `diabetes_probability` | 糖尿病风险概率（旧版兼容输出） | `number` | `ratio` |

只有正式算法已在当前部署代码中注册、参数与算法契约一致、所需制品由部署环境提供且核验通过、所选指标代码和值类型与单位匹配后，版本才能发布；四个独立模型的初始渠道配置仍按上文维护。两个场景只按下文显式隔离联调流程选择，不自动覆盖旧配置。具体接口、请求字段和发布校验见[模型与算法开发](models-and-algorithms.md)中的模型目录操作说明。

## 维护流程

1. 先按[环境配置与服务运行](environment.md)完成数据库迁移；迁移只建立表结构，不会自动创建上述业务目录数据。
2. 确认当前部署的 `health-inference` 已注册本清单所需的四种正式算法，且每个版本声明的输入、制品、资料和指标契约与对应注册定义一致；所需模型制品必须由该环境提供并完成核验。
3. 使用受保护的管理端目录接口创建或核对四个模型及其版本，上传各版本所需制品，设置完整指标集合后再发布；模型 ID 和版本 ID 使用该环境接口返回的值。
4. 为每个普通渠道分别选择 HR/BR、SpO2、BP 和糖尿病风险模型的已发布 `1.0.0` 版本。`manager` 仅用于管理认证和管理接口，不配置普通模型选择。
5. 首次配置后，按数据库中的现状维护渠道选择。已发布版本不可原地改写；若计算契约改变，应按算法与模型版本规则发布新配置，不要用重新初始化覆盖历史数据。

该文档描述的是初始期望状态，不代表每个环境的数据库当前状态。每次调整初始模型、版本、指标或默认渠道选择时，同步更新本清单，并遵守[模型与算法开发](models-and-algorithms.md)中的发布约束。

## 统一场景的隔离联调发布与选择

以下是人工操作说明，不代表已在测试或生产环境执行，也不提供自动初始化或渠道切换脚本。仅对获授权的隔离联调环境操作；先确认 API、支持两个精确 key 的消费者、数据库、Redis 和存储均就绪。现有四个独立模型及历史任务继续保留，不做数据库迁移、生产发布或原渠道自动切换。

所有路径前缀为该环境的 `/api/v1`。写接口统一使用 `manager` 渠道登录返回的 Access Token；最后的普通目录和任务接口改用目标普通渠道的令牌。`{model_id}`、`{version_id}` 均是占位参数，必须替换为本次创建响应或环境受控记录中的真实整数，不能把文档里的示例当作已存在资源。版本号也使用该环境实际配置值。

### 1. 创建场景模型与空参数版本

分别调用 `POST /api/v1/manager/models`，请求为：

```json
{"code":"ppg.active.offline.all","name":"统一PPG场景"}
```

```json
{"code":"ppg_acc.active.offline.all","name":"统一PPG+ACC场景"}
```

保存各自响应的 `data.modelId`，不要混用。若模型已存在，使用该环境受控记录中的 ID 核对版本；当前没有管理端模型总列表接口，不能虚构查询路径或用普通渠道目录冒充管理端全量记录。冲突不等于本次已创建成功。

对各自的真实模型 ID 调用 `POST /api/v1/manager/models/{model_id}/versions`。首次版本可分别使用以下请求；若已有该版本，先通过 `GET /api/v1/manager/models/{model_id}/versions` 核对，不覆盖已发布配置：

```json
{"version":"1.0.0","algorithmKey":"ppg.active.offline.all.v1","params":null}
```

```json
{"version":"1.0.0","algorithmKey":"ppg_acc.active.offline.all.v1","params":null}
```

保存各自响应的 `data.versionId`、`data.algorithmKey`、`data.params` 和版本号；只有返回的未发布版本才能继续配置。不要把独立算法的发布版本 ID 填入场景参数。

### 2. 上传全部制品并配置指标

对各自真实版本 ID 调用 `POST /api/v1/manager/model-versions/{version_id}/artifacts`，每次 multipart 只上传一个 `logicalName` 和对应 `file`：

| 场景 | 逐个上传的 `logicalName` | 文件要求 |
|---|---|---|
| 统一PPG场景 | `spo2` | 该环境已核验的 `.onnx` 血氧模型 |
| 统一PPG场景 | `feature_names`、`stable_features` | 各自符合血氧成员规则的 `.json` 特征清单 |
| 统一PPG场景 | `diabetes` | 该环境已核验的 `.onnx` 糖尿病风险模型 |
| 统一PPG+ACC场景 | `sbp`、`dbp` | 各自来源受信任且与运行环境兼容的 `.joblib` 模型 |
| 统一PPG+ACC场景 | `normalize` | 符合血压成员规则的 `.csv` 归一化表 |

不能因为已给独立版本上传同名制品就跳过场景版本登记，也不能上传占位文件或额外附件。保存上传响应的 `artifactId`、逻辑名及大小作为操作记录；同版本同逻辑名重复登记会冲突，不适用任务输入的同名重传规则。

按上文指标表核对或通过 `POST /api/v1/manager/metrics` 创建缺少的全局指标，数值类型均为 `number`，单位必须完全一致。已有代码只在确认类型与单位匹配后复用，不能把 409 当作核验成功。然后分别调用各自的 `PUT /api/v1/manager/model-versions/{version_id}/metrics`：

```json
{"metricCodes":["hr","br","spo2","diabetes_probability"]}
```

```json
{"metricCodes":["sbp","dbp"]}
```

PUT 替换完整集合，不是追加。确认响应确为该场景应有指标，且不含 `risk_level`。

### 3. 发布并显式选择隔离联调渠道

确认全部制品已核验后，分别调用 `POST /api/v1/manager/model-versions/{version_id}/publish`，无请求体。以响应的真实版本 ID、算法标识及非空 `publishedAt` 确认发布结果；创建成功不等于发布成功。发布后算法、参数、制品和指标不能原地改写。

发布本身不会让用户目录自动出现新模型。仅在获授权的隔离联调普通渠道，分别调用 `PUT /api/v1/manager/channels/{channel}/models/{model_id}/selection`，请求的 `versionId` 使用对应场景刚发布响应的实际值。此处不写虚构的环境 ID；两个模型的 ID 和版本 ID 必须成对使用。不要操作 `manager` 的普通模型选择，不删除、取消或切换原四个模型的已有渠道配置。对既有模型切换版本会使目标渠道旧版本草稿失效，不能把这种操作当作无影响的初始化。

最后用目标普通渠道账号调用 `GET /api/v1/{channel}/models?offset=0&limit=100`（需要时继续分页），确认两个场景条目的实际 `modelId`、`modelCode`、`modelName`、`versionId`、版本号、`requiredInputs` 和 `isExample=false`。前端创建任务使用这次目录返回的 `modelId`，不是管理响应中的版本 ID，也不硬编码名称推导 ID；普通目录不返回 `algorithmKey`，精确 key 应在前述管理版本响应中核对。

### 4. 按原任务接口联调，不改变历史用法

对场景目录返回的 `modelId` 使用现有创建草稿、上传 `requiredInputs`、提交、详情和下载接口。统一PPG场景上传一份 `ppg`，统一PPG+ACC场景分别上传 `ppg`、`acc`；前端不提交成员列表、算法参数或独立版本 ID。完整结果契约及部分成功、全部失败、血压成员成功/失败和场景自身失败示例见[前端对接指南第 6.5–6.6 节](../frontend-integration.md#65-统一ppg场景ppgactiveofflineallv1)。

联调必须区分任务 `SUCCEEDED` 与测量 `outcome`；全部成员失败仍可为 `SUCCEEDED / ALL_FAILED`，但没有指标或输出。数值从 `result.results.<成员>.result` 读取，文件只按顶层 `result.output_files` 已登记的带成员前缀对象键下载，不访问服务器目录。场景整体超时等自身失败为 `FAILED / result=null`，不保存中间结果。

四个独立算法仍按原模型目录、原任务结果字段和无成员前缀的输出逻辑名调用，历史结果不改写成场景结构。后续如需生产发布、渠道迁移或撤回场景流量，应另走获授权的部署流程；回退前须由支持新 key 的消费者排空已提交场景，保留历史定义与制品，不能用不认识场景 key 的旧消费者接管未终结任务。
