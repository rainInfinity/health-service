# 初始数据库数据维护

本文记录业务数据库首次可用时需要维护的渠道与正式模型目录。数据库结构仍由 Django migrations 管理；本文是初始数据清单和人工维护流程，不提供自动写库命令、SQL 或脚本。

## 算法注册与数据库目录

算法执行器由 `health-inference` 显式注册，不存在独立算法数据库表。现行独立算法为 `ppg.active.offline.hr.v1`、`ppg.active.offline.br.v1`、`ppg.active.offline.spo2.v1`、`ppg_acc.active.offline.bp.v1`、`ppg.active.offline.diabetes.v1`；固定场景为 `ppg.active.offline.all.v2` 和 `ppg_acc.active.offline.all.v1`。数据库版本引用完整算法标识，当前部署未注册的 key 不能发布。每个版本需核验真实制品；`var/old-assets/` 本机候选制品不构成生产初始数据。本文是目标配置与操作流程，不是已部署、已发布或验证通过记录。

渠道、模型、模型版本、指标定义、版本指标关系及渠道选择分别保存在 `health_channel`、`health_model`、`health_model_version`、`health_metric_definition`、`health_model_version_metric` 和 `health_channel_model_selection`。

## 初始渠道

首次建立目录时，维护以下六个渠道：

| 渠道标识 | 类型 | 初始模型选择 |
|---|---|---|
| `android-cn` | 普通渠道 | 独立 HR、BR、SpO2、BP、糖尿病风险各 `1.0.0`；统一 PPG `2.0.0` |
| `android-intl` | 普通渠道 | 同上 |
| `ios-cn` | 普通渠道 | 同上 |
| `ios-intl` | 普通渠道 | 同上 |
| `wxapp` | 普通渠道 | 同上 |
| `manager` | 管理渠道 | 不配置普通用户模型选择 |

每个普通渠道独立维护对应模型的选择，不是多选一；本次独立 HR、BR 各 `1.0.0` 与统一 PPG `2.0.0` 均投放五普通渠道。SpO2、BP、糖尿病及 PPG+ACC 场景的现有版本和选择保持不变。制品未核验不得发布空制品版本或不存在的选择，`manager` 不配置用户模型选择。

切换前先新增独立 HR、BR 并对照旧融合实现；对照完成后按授权清理测试环境全部推理历史及其输入、指标、输出等依赖，再退役旧融合模型及引用 `ppg.active.offline.all.v1` 的旧发布版本。保留 `ppg.active.offline.all` 模型，新增 `2.0.0` 绑定 `.all.v2`。旧融合 key、旧 PPG 场景 v1 不保留兼容别名；不可将这些测试环境清理步骤擅自用于生产。

## 管理账户

`manager` 渠道记录与管理用户是独立数据。每个环境应建立至少一个受信任的管理账户；通过 Django shell 的 `create_user` 创建，并设置 `is_staff=True`、`is_superuser=False`。密码由 Django 哈希存储。用户名和密码按环境单独管理，不在本文或版本库中记录共享凭据。创建步骤见[账号、资料与令牌](accounts.md)。

## 初始模型版本

### 独立心率与呼吸率 v1

| 字段 | HR | BR |
|---|---|---|
| 模型编码 | `ppg.active.offline.hr` | `ppg.active.offline.br` |
| 模型名称 | `HR v1` | `BR v1` |
| 模型说明 | 独立测量心率、实际质量及处理后 PPG，不构成医学诊断 | 独立测量呼吸率、实际质量及处理后呼吸波，不构成医学诊断 |
| 版本号 | `1.0.0` | `1.0.0` |
| 算法标识 | `ppg.active.offline.hr.v1` | `ppg.active.offline.br.v1` |
| 参数 | `null` 或 `{}`，拒绝非空参数 | 同左 |
| 必需输入 | `ppg`（CSV） | `ppg`（CSV） |
| 必需制品、健康资料 | 均无 | 均无 |
| 指标 | 仅 `hr` | 仅 `br` |
| 输出文件逻辑名 | 仅 `processed_ppg` | 仅 `processed_rsp` |
| 示例算法 | 否，`isExample=false` | 否，`isExample=false` |

二者同源复用共享 PPG 输入、预处理与质量规则，独立推理；HR 仅 `hr/quality`，BR 仅 `br/quality`。BR 先拒绝 NaN/无穷，再限制到 `[8,20]`，不补 `12`；独立成功载荷不含 nullable 指标或内部 `failures`。

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

场景各自固定一个 `ModelVersion`，不是独立版本 ID 的组合参数。统一 PPG 复用既有 `ppg.active.offline.all` 模型并发布 `2.0.0`，不重复建模型；PPG+ACC 保持原配置。名称与编码如下，前端仍须使用目录实际返回值：

| 字段 | 统一PPG场景 | 统一PPG+ACC场景 |
|---|---|---|
| 模型编码 | `ppg.active.offline.all` | `ppg_acc.active.offline.all` |
| 模型名称 | `统一PPG场景` | `统一PPG+ACC场景` |
| 算法标识 | `ppg.active.offline.all.v2` | `ppg_acc.active.offline.all.v1` |
| 版本号 | `2.0.0` | 保持环境已有版本，首次可用 `1.0.0` |
| 参数 | `null` 或 `{}`，拒绝非空参数 | 同左 |
| 固定成员 | `hr` → `br` → `spo2` → `diabetes` | 仅 `bp` |
| 固定成员实现 | `ppg.active.offline.hr.v1`、`ppg.active.offline.br.v1`、`ppg.active.offline.spo2.v1`、`ppg.active.offline.diabetes.v1` | `ppg_acc.active.offline.bp.v1` |
| 必需输入 | `ppg`（CSV） | `ppg`、`acc`（均为 CSV） |
| 完整必需制品 | `spo2`（ONNX）、`feature_names`、`stable_features`（JSON）、`diabetes`（ONNX） | `sbp`、`dbp`（joblib）、`normalize`（CSV） |
| 完整投影配置建议 | `hr`、`br`、`spo2`、`diabetes_probability` | `sbp`、`dbp` |
| 必需健康资料 | 无 | 无；沿用血压成员的提交时冻结资料及缺失值规则 |
| 示例算法 | 否，`isExample=false` | 否，`isExample=false` |

两个场景的成员集合和实现修订固定在代码中，不自动扩展，也不读取其他模型的渠道当前版本。场景版本直接绑定全部成员制品，不能在参数里传独立 `modelId/versionId`。独立版本切换不改变场景资源；替换制品需新建场景版本，改变成员集合或解释需新算法修订。PPG v2 按四成员成功数决定 outcome：4/1–3/0 分别为 `ALL_SUCCEEDED/PARTIALLY_SUCCEEDED/ALL_FAILED`；BR 失败不影响 HR，文件分别为 `hr_processed_ppg/br_processed_rsp`。

发布仍检查制品集合完整、无额外逻辑名、后缀合法且物理文件存在，不因运行时允许部分成功而放宽。制品内容与信任要求沿用各独立成员，特别是 `joblib` 可在加载时执行代码，不得使用不可信来源。场景只投影成功成员实际产生且版本选定的指标，成员失败不补零；`risk_level` 仅保留在糖尿病完整结果中，不新建指标定义。

五个独立算法和两个场景的 `isExample=false` 只表示不是示例执行器，不是临床认证、医学准确性保证或诊断许可。合成制品联调只证明流程，不代替真实模型核验或医学验收。

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

只有正式算法已在当前部署中注册、参数符合契约、制品核验通过、所选指标代码、类型和单位匹配后才能发布。独立 HR、BR 与 PPG v2 的五普通渠道目标见上文；具体接口见[模型与算法开发](models-and-algorithms.md)。

## 维护流程

1. 先按[环境配置与服务运行](environment.md)完成数据库迁移；迁移只建立表结构，不会自动创建上述业务目录数据。
2. 确认当前部署支持五种正式独立算法及两个精确场景 key，所需制品由环境提供并核验。
3. 创建独立 HR、BR 模型各 `1.0.0`，分别绑定 `hr`、`br` 后发布；其他独立模型沿用既有配置，不重复初始化。
4. 保留统一 PPG 模型，创建 `2.0.0` 绑定 `.all.v2`，上传完整制品并绑定四指标后发布；把 HR、BR 和 PPG v2 各自选择到全部五普通渠道。
5. 先对照验证再按授权清测试推理历史、退役旧融合及旧 PPG v1 版本与渠道引用。不得原地改已发布配置，不自动覆盖无关模型或 PPG+ACC 选择。保留实际操作记录，本文不代表操作已执行。

该文档描述的是初始期望状态，不代表每个环境的数据库当前状态。每次调整初始模型、版本、指标或默认渠道选择时，同步更新本清单，并遵守[模型与算法开发](models-and-algorithms.md)中的发布约束。

## 统一场景的发布与选择

以下为人工操作说明，不代表已在测试或生产环境执行。操作须先确认 API、支持精确 key 的消费者、数据库、Redis 和共享存储就绪。此次测试环境 HR/BR 拆分授权包含历史清理与旧版本退役；生产操作需独立授权。PPG+ACC 场景仅保留既有流程，不因 PPG 升级重建或切换。

所有路径前缀为该环境的 `/api/v1`。写接口统一使用 `manager` 渠道登录返回的 Access Token；最后的普通目录和任务接口改用目标普通渠道的令牌。`{model_id}`、`{version_id}` 均是占位参数，必须替换为本次创建响应或环境受控记录中的真实整数，不能把文档里的示例当作已存在资源。版本号也使用该环境实际配置值。

### 1. 创建场景模型与空参数版本

仅在模型确实不存在的全新环境调用 `POST /api/v1/manager/models`，可用以下请求；此次升级必须复用既有统一 PPG 模型，不重新创建 PPG+ACC 模型：

```json
{"code":"ppg.active.offline.all","name":"统一PPG场景"}
```

```json
{"code":"ppg_acc.active.offline.all","name":"统一PPG+ACC场景"}
```

保存各自响应的 `data.modelId`，不要混用。若模型已存在，使用该环境受控记录中的 ID 核对版本；当前没有管理端模型总列表接口，不能虚构查询路径或用普通渠道目录冒充管理端全量记录。冲突不等于本次已创建成功。

对真实模型 ID 调用 `POST /api/v1/manager/models/{model_id}/versions`。统一 PPG 在既有模型下新增 `2.0.0`；若版本已存在，先用版本列表核对，不能覆盖已发布配置：

```json
{"version":"2.0.0","algorithmKey":"ppg.active.offline.all.v2","params":null}
```

PPG+ACC 已有版本原样保留；上文 `ppg_acc.active.offline.all.v1` 的 `1.0.0` 仅是全新环境首次配置约定，不要求此次升级再次创建、发布或切换。

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

### 3. 发布并选择普通渠道

确认全部制品已核验后，分别调用 `POST /api/v1/manager/model-versions/{version_id}/publish`，无请求体。以响应的真实版本 ID、算法标识及非空 `publishedAt` 确认发布结果；创建成功不等于发布成功。发布后算法、参数、制品和指标不能原地改写。

发布本身不会自动增加用户目录。对五普通渠道 `android-cn/android-intl/ios-cn/ios-intl/wxapp`，分别调用 `PUT /api/v1/manager/channels/{channel}/models/{model_id}/selection`，使用对应独立 HR、BR 的 `1.0.0` 和统一 PPG `2.0.0` 实际版本 ID。三组模型与版本必须成对使用，不选择 `manager`；无关模型与 PPG+ACC 原选择不变。旧融合选择和旧 PPG v1 发布版本按前述验证、历史清理顺序退役。切换既有模型版本会使目标渠道旧草稿失效，不是无影响初始化。

最后以各普通渠道账号读取 `GET /api/v1/{channel}/models?offset=0&limit=100` 并按需分页，确认独立 HR、BR 和 PPG v2 的真实模型/版本 ID、版本号、requiredInputs 与 `isExample=false`，同时确认其他模型选择未改。前端使用目录 modelId，不用版本 ID。普通目录不返回 algorithmKey，精确 key 由管理版本响应核对；不得把目标清单写成已发布证据。

### 4. 按原任务接口联调

对目录返回的 `modelId` 使用原草稿、上传、提交、详情和下载接口；PPG v2 只上传一份 `ppg`，四成员依次读取同一原始文件，前端不提交成员列表、算法参数或独立版本 ID。PPG+ACC 仍上传 `ppg/acc`。结果示例见[前端指南第 6.5–6.6 节](../frontend-integration.md#65-统一ppg场景ppgactiveofflineallv2)。

联调必须区分任务 `SUCCEEDED` 与测量 `outcome`；全部成员失败仍可为 `SUCCEEDED / ALL_FAILED`，但没有指标或输出。数值从 `result.results.<成员>.result` 读取，文件只按顶层 `result.output_files` 已登记的带成员前缀对象键下载，不访问服务器目录。场景整体超时等自身失败为 `FAILED / result=null`，不保存中间结果。

五个独立算法仍使用非场景结果及不带成员前缀的输出逻辑名；HR/BR 现在是两个独立模型、任务与成功条件。场景 HR、BR 路径分别为 `result.results.hr.result.hr`、`result.results.br.result.br`，独立失败或 BR 成员失败不抹去其他成功结果。旧实现验收记录不能作为新版本验证证据，需另存真实执行结果；旧 key 退役后不通过兼容别名接管未终结任务。
