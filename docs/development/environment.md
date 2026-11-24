# 环境配置与服务运行

## 前置条件

- Python 3.13，由 `uv` 管理解释器与依赖。
- Docker，以及 PostgreSQL 16 或兼容的 PostgreSQL 服务。
- 根目录唯一的 `uv.lock`；完整开发和测试环境使用 `uv sync --locked --all-packages`，不要单独升级依赖而不更新锁文件。

### Python 工作区

根目录 `pyproject.toml` 只编排两个成员，不是第三个业务项目；所有成员共用根目录的 `uv.lock` 和默认 `.venv`。`packages/health-service` 包含 Django `health_service` 与 `config`，声明业务运行依赖并显式依赖本地 `health-inference`；`health-inference` 基础依赖包含 pandas 与 NumPy，SciPy 和 NeuroKit2 仅由其 `ppg` 可选项提供。

```sh
# 完整开发与测试环境
uv sync --locked --all-packages --all-extras
# 仅业务成员及其运行依赖
uv sync --locked --package health-service --no-dev
# 仅计算成员及其运行依赖，包含 pandas/NumPy，不安装 SciPy/NeuroKit2
uv sync --locked --package health-inference --no-dev
# PPG/HR/BR 推理计算环境，额外安装 SciPy/NeuroKit2
uv sync --locked --package health-inference --extra ppg --no-dev
```

`uv run --package ...` 选择运行成员，不切换当前目录；以下管理命令和 Web/消费者入口均从仓库根目录运行。

## 创建并启动本机 PostgreSQL 测试数据库

本机应用和测试共用此 PostgreSQL 容器，但使用不同数据库：`.env` 中的 `POSTGRES_DB=health_service` 用于本机应用；pytest-django 默认使用独立的 `test_health_service`。首次创建具名数据卷和容器：

```sh
docker volume create health-service-test-postgres-data

docker run --detach \
  --name health-service-test-postgres \
  --restart unless-stopped \
  --publish 127.0.0.1:55432:5432 \
  --mount type=volume,source=health-service-test-postgres-data,target=/var/lib/postgresql/data \
  --env POSTGRES_DB=health_service \
  --env POSTGRES_USER=health_service \
  --env POSTGRES_PASSWORD=local-development-only \
  postgres:16.4-alpine
```

确认数据库已就绪：

```sh
docker exec health-service-test-postgres pg_isready --username=health_service --dbname=health_service
```

输出 `accepting connections` 后即可使用。之后启动和停止容器使用：

```sh
docker start health-service-test-postgres
docker stop health-service-test-postgres
```

数据库文件保存在 `health-service-test-postgres-data` 具名卷中；停止容器不会删除数据。不要使用 `docker volume rm health-service-test-postgres-data`，该命令会永久删除本机数据库。示例密码仅用于本机开发，不要用于生产环境；PostgreSQL 初始化后，修改 `.env` 密码不会自动修改数据库用户密码。

## 配置环境变量

### 使用根目录 `.env`

推荐先复制模板，再填写本地配置：

```sh
cp .env.template .env
uv run --locked --all-packages python -c 'import secrets; print(secrets.token_urlsafe(50))'
uv run --locked --all-packages python -c 'import secrets; print(secrets.token_urlsafe(50))'
```

将第一条命令输出填写到 `.env` 的 `DJANGO_SECRET_KEY`，第二条填写到独立的 `JWT_SIGNING_KEY`；不要复用密钥。将 `POSTGRES_PASSWORD` 设为本机数据库密码；使用上面的容器示例时填 `local-development-only`。该密码仅用于本机开发，不要用于生产环境。

Django 从项目根目录读取可选的 `.env`，不受启动时工作目录影响。进程环境变量优先于 `.env`；已设置为空的必填进程变量仍会报错，不会回退到文件值。`.env` 已加入 Git 忽略规则，不要提交真实密钥或数据库密码。

`DJANGO_SECRET_KEY`、`JWT_SIGNING_KEY`、`DJANGO_ALLOWED_HOSTS`、`POSTGRES_DB`、`POSTGRES_USER`、`POSTGRES_PASSWORD` 和 `POSTGRES_HOST` 必须显式提供。模板中的 `DJANGO_DEBUG` 默认设为 `true`，未设置时默认为关闭；`POSTGRES_PORT` 未设置时默认为 `5432`。

### Token 有效期配置

| 变量 | 默认值 | 约束与用途 |
|---|---|---|
| `JWT_ACCESS_TOKEN_LIFETIME_SECONDS` | `604800` | Access 有效期，正整数秒；默认 7 天。 |
| `JWT_REFRESH_TOKEN_LIFETIME_SECONDS` | `2592000` | Refresh 有效期，正整数秒；默认 30 天。 |

两项可独立省略，支持进程环境和 `.env`，进程环境优先；显式空值、零、负数、小数或非数字会使启动失败，不回退到文件或默认值。修改后需重启签发服务，普通渠道和 manager 的登录及 Refresh 轮换均使用新配置。已有令牌仍依各自的 `exp` 失效：缩短配置不会提前撤销，延长配置也不会延长或复活旧令牌。JWT 的 `exp`、`iat` 保持标准数值格式，不转换为北京时间字符串；不要在日志中输出令牌。

### 推理服务与本地目录配置

以下变量可由进程环境或项目根目录 `.env` 提供；进程环境优先。相对目录以项目根目录为基准，Web 与消费者必须配置为同一批共享可达的目录。服务启动时会创建目录并检查读写权限。

| 变量 | 默认值 | 约束与用途 |
|---|---|---|
| `TASK_DATA_ROOT` | `var/tasks` | 原始任务输入和处理后文件根目录。 |
| `MODEL_ARTIFACT_ROOT` | `var/model-artifacts` | 模型制品根目录。 |
| `MAX_UPLOAD_BYTES` | `52428800` | 上传大小上限，必须为正整数，单位字节。 |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis 连接；若 URL 含凭证，不得记录到日志。 |
| `INFERENCE_STREAM` | `health:inference` | Redis Stream 名称，不能为空。 |
| `INFERENCE_GROUP` | `health-inference` | 消费组名称，不能为空。 |
| `INFERENCE_LEASE_SECONDS` | `60` | 执行租约时长，正整数秒。 |
| `INFERENCE_HEARTBEAT_SECONDS` | `10` | 心跳间隔，必须小于租约时长的三分之一。 |
| `INFERENCE_EXECUTION_TIMEOUT_SECONDS` | `300` | 所有算法共享的硬执行上限，正整数秒；独立于租约和心跳。 |
| `INFERENCE_DISPATCH_INTERVAL_SECONDS` | `30` | 数据库补发间隔，正整数秒。 |
| `INFERENCE_MAX_ATTEMPTS` | `3` | 有效执行领取次数上限，正整数。 |
| `INFERENCE_POLL_MILLISECONDS` | `1000` | 消费轮询等待上限，正整数毫秒。 |

所有时长和数量配置必须为正整数；无效值、空队列名称或不可读写的存储目录会使 Django 启动失败。

`TASK_DATA_ROOT` 包含原始健康输入和推理输出，不能配置为公开静态目录；`MODEL_ARTIFACT_ROOT` 保存模型制品。Web 与消费者必须配置为访问同一物理卷上的目录。上传写盘与数据库登记不是原子操作，失败可能留下孤儿文件。成功推理先提交数据库结果与指标，再发布正式目录链接；数据库和文件系统不构成原子事务，链接发布可能短暂滞后。发布失败不回滚成功状态，也不重跑算法；后续成功终态消息或授权输出下载会幂等恢复链接。下载始终打开数据库登记的不可变实际 `storage_key`，不通过正式链接解析结果；链接恢复失败但实际文件存在时仍可下载，实际文件缺失返回安全 404。存储不会自动清理旧输入、输出、孤儿文件或执行目录；路径本身不授予读取权限。

#### 新文件目录与命名

新上传的输入、模型制品和推理输出分别使用以下相对路径；根目录配置仍由 `TASK_DATA_ROOT` 与 `MODEL_ARTIFACT_ROOT` 控制：

```text
var/
├── tasks/
│   ├── <渠道代码>/
│   │   └── <YYYYMMDD>/
│   │       ├── <YYYYMMDD_HHmmss>-<task_id>-<user_id>-<algorithm_key>-<logical_name>-<UUID>.<ext>
│   │       └── outputs/
│   │           └── <task_id>/                 # 指向成功执行目录的受控相对目录链接
│   └── .executions/
│       └── <task_id>/<execution_token>/
│           └── <算法原结果文件名>
└── model-artifacts/
    └── <model_code>/<version>/
        ├── <logical_name>-<UUID>.<ext>
        └── ...
```

任务输入渠道目录使用固定渠道代码。日期与文件时间前缀取任务创建时间并转换到 `Asia/Shanghai`，跨日上传仍用创建日期。文件名携带任务 ID、用户 ID、完整算法 key、逻辑名及 32 位小写十六进制 UUID，不使用客户端原名。例如 `ios-cn/20261101/20261101_003045-42-7-ppg.active.offline.hr.v1-ppg-<UUID>.csv`。

模型制品路径按稳定 `model_code`、版本号和逻辑名分层；例如 `spo2` ONNX 制品使用 `<model_code>/<version>/spo2-<UUID>.onnx`，`feature_names` 与 `stable_features` 清单使用 `.json`，UUID 同样为 32 位小写十六进制。输入和制品的扩展名从上传文件名提取、转为小写并按算法契约校验；逻辑名与扩展名是独立字段，扩展名不代替内容校验。

算法 key、逻辑名、模型编码和版本号等业务路径段按 UTF-8 字节进行可逆百分号编码：字母、数字、点和下划线保留，其余字节（包括连字符）编码为大写 `%HH`；固定渠道代码保留原值。整段 `.`、`..` 必须编码或拒绝。保存前按文件系统字节上限检查目录段和完整文件名，超限时安全拒绝，不截断、不替换字符。

正式结果入口是 `<渠道代码>/<任务创建日期>/outputs/<task_id>/`，由服务端管理的相对目录符号链接指向同一任务根目录内已成功执行的 `.executions/<task_id>/<execution_token>/`。目录整组切换，保留算法原文件名，例如 `ppg-processed.csv`；算法只写本次 `output_dir`，不得写入或推导正式目录。执行临时文件不进入正式结果目录。

固定场景沿用整组链接发布。PPG v2 成功成员目录分别为 `hr/ppg-processed.csv`、`br/rsp-processed.csv`、`spo2/ppg-processed.csv`、`diabetes/ppg-processed.csv`，下载逻辑名为 `hr_processed_ppg`、`br_processed_rsp`、`spo2_processed_ppg`、`diabetes_processed_ppg`。PPG+ACC 仍为 `bp/ppg-processed.csv`、`bp/scg-processed.csv` 及 `bp_processed_ppg/bp_processed_scg`。保留成员子目录与原表头、时间轴；失败目录安全清理后才整组发布，全失败无正式目录。

统一 PPG 使用 `ppg.active.offline.all.v2`、模型 `ppg.active.offline.all` 新版本 `2.0.0`，依次调用 `hr→br→spo2→diabetes`；PPG+ACC `ppg_acc.active.offline.all.v1` 不变。新独立 HR、BR 模型分别为 `ppg.active.offline.hr/br`，各 `1.0.0`，三个新发布版本均投放五普通渠道。先验证新算法与旧融合对照，再按授权清测试环境全部推理历史、退役旧融合及旧 PPG 场景 v1，不保留别名。管理步骤和完整制品见[模型开发](models-and-algorithms.md)及[目录清单](initial-database-data.md)。无新增环境变量或数据库迁移；本文不是已执行记录，不自动授权生产操作。

执行时只有这两个固定场景允许安全解析尚未实际加载的候选制品路径，最终文件缺失、类型错误或模型损坏在成员初始化边界处理。根目录、登记键和现有符号链接仍严格校验；发布时的完整集合、允许后缀及实际文件存在性检查不放宽，普通算法与下载也继续严格解析。场景整体与各成员的失败解释不同：正常汇总包括 `ALL_FAILED` 都保存为 Task `SUCCEEDED`，成员错误位于 `result.results`，场景自身错误才为顶层 `FAILED`。整个场景仍受一次任务硬时限约束，未完成的中间成员结果不保存。

新路径只用于新写入。数据库已登记的旧 `storage_key` 继续按根目录边界读取，包括旧输入和制品的 `uploads/<UUID>.bin`、旧结果的 `outputs/<task_id>/<execution_token>/<文件名>`；不解析旧名来授权，不搬迁或重命名存量文件。文件系统与数据库登记失败可能留下孤儿文件，本地存储不扫描或自动清理旧文件、孤儿文件及执行目录。

派发扫描不新增环境变量：每轮最多查询 100 个任务，按主键稳定排序，处理未发布或超过 `INFERENCE_DISPATCH_INTERVAL_SECONDS` 的 `SUBMITTED`，也会重新派发数据库时间判定已过期的 `RUNNING`。Redis 发布失败不更新 `last_dispatched_at`，不消耗执行次数；消息成功但数据库补记失败时允许安全重复派发。

执行领取与续约使用 PostgreSQL 数据库时间。每次有效领取生成新令牌并增加一次 `attempt_count`，只在首次领取时写入 `started_at`；过期后重领保留首次开始时间。达到 `INFERENCE_MAX_ATTEMPTS` 且最后租约过期时，任务以安全失败码 `TASK.RETRY_EXHAUSTED` 终结。暂时 Redis／数据库故障不会直接记为算法失败；消息可重复投递，不承诺恰好一次执行或 Redis 高可用。

### 仅使用进程环境变量

没有 `.env` 时，也可以在启动 Django 的同一 shell 中提供全部必填配置：

```sh
export DJANGO_SECRET_KEY="$(uv run --locked --all-packages python -c 'import secrets; print(secrets.token_urlsafe(50))')"
export JWT_SIGNING_KEY="$(uv run --locked --all-packages python -c 'import secrets; print(secrets.token_urlsafe(50))')"
export DJANGO_DEBUG=true
export DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1"
export POSTGRES_DB=health_service
export POSTGRES_USER=health_service
export POSTGRES_PASSWORD=local-development-only
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=55432
```

## 运行本机测试

先按上文启动 PostgreSQL 容器，并确保 Django 使用的数据库连接配置（根目录 `.env` 或当前进程环境）与容器一致。所有本机 pytest 命令都使用可复用的测试库：

```sh
uv run --locked --all-packages pytest --reuse-db
```

测试库名默认为 `test_health_service`。`--reuse-db` 会跨测试运行保留该库及其结构；迁移或测试库结构变化后，使用以下命令重建。它会清空并重建测试库，不会重建应用数据库 `health_service`：

```sh
uv run --locked --all-packages pytest --reuse-db --create-db
```

日常运行不要省略 `--reuse-db`，否则 pytest-django 会按默认行为重新创建并清理测试库。

### 文件路径与结果发布验收记录

以下是旧融合 HR/BR 与当时存储实现的历史验收，不是独立 HR/BR v1 或 PPG 场景 v2 的新验证记录；其中命令与测试路径仅保留为历史上下文，不应直接当作现行回归清单。

该命令使用 `--reuse-db` 保留 pytest 测试库；存储集成场景使用临时目录，消费者测试使用独立命名的 Redis Stream：

```sh
uv run --locked --all-packages pytest --reuse-db \
  tests/unit/test_inference_algorithms.py \
  tests/unit/test_local_storage.py \
  tests/unit/test_inference_logging.py \
  tests/integration/api/test_task_inputs.py \
  tests/integration/api/test_catalog.py \
  tests/integration/api/test_task_file_downloads.py \
  tests/integration/catalog/test_publication.py \
  tests/integration/workflows/test_inference_adapter.py \
  tests/integration/workflows/test_task_results.py \
  tests/integration/workflows/test_inference_consumer.py \
  tests/integration/workflows/test_spo2_inference_consumer.py \
  -q
```

最近一次结果为 115 passed、95 warnings，耗时 55.21 秒。警告来自 SciPy/NeuroKit 与 pyPPG/NumPy 的弃用和数值运行时提示。

HR/BR 工作流将任务创建时间设为执行日前一日，经普通 `ios-cn` 账号的 HTTP 路由上传两份 PPG、提交并运行 Redis Stream 消费者；输入和结果路径仍按任务创建日生成。历史输入仍可下载，正式目录链接到数据库登记的 `.executions` 执行目录，两个波形下载字节与实际结果文件相同。越权账号和跨渠道下载均被拒绝。

SpO2 自动化工作流回归使用测试生成的 ONNX；另以本机旧制品完成真实上传验收：通过 Django HTTP 客户端调用 Manager API，在 `health_service` 新建 `ppg.active.offline.spo2` 的 DRAFT 版本 `1.0.0`。三次制品上传均返回 HTTP 201；版本未发布、未切换渠道，既有 HR/BR 版本未改动。

本次从 `var/old-assets/` 上传真实 `spo2-latest-spo2.onnx`；363 项 `feature_names.npy` 使用 `allow_pickle=False` 读取并转换为 JSON。22 项 `stable_features.pkl` 仅用 opcode 白名单解析为字符串列表后转换为 JSON，未执行 pickle。资源工厂通过数据库登记的存储键成功创建 ONNX Runtime 会话；22 个稳定特征都存在于完整清单，且匹配 ONNX 输入宽度。

制品保存于 `var/model-artifacts/`，相对键符合以下结构：

```text
ppg.active.offline.spo2/1.0.0/spo2-<UUID>.onnx
ppg.active.offline.spo2/1.0.0/feature_names-<UUID>.json
ppg.active.offline.spo2/1.0.0/stable_features-<UUID>.json
```

### 统一场景真实制品验收记录

以下为 2026-11-10 的旧 PPG 场景 v1（三成员）及 PPG+ACC v1 历史验收。PPG v2 已拆为四成员，以下数值、通过项数、版本与文件结构不能证明新版本已验证或已部署，BR 失败同拖 HR 的旧观察也不是新契约。

2026-11-10 使用独立 PostgreSQL 测试库、每轮唯一 Redis Stream/消费组及临时任务/制品目录，经 Manager HTTP 创建、上传、发布和选择场景，再由普通 `android-cn` HTTP 创建、上传和提交任务，运行实际 `run_inference --once` 消费者。未修改现有业务数据库、模型制品或渠道选择。

PPG 全成功输入来自 `tests/integration/ppg_inputs.py::synthetic_ppg_csv(heart_rate_hz=1.8)`：1800 帧、30 Hz、无额外噪声；部分成功使用同一输入，但在发布后、首次加载前删除该场景版本的 diabetes 制品。另保留 `var/old-assets/sample/20261101_134952-5-2-bp_vlatest-ppg.csv` 的真实部分成功路径；血压使用该 PPG 及同目录的配套 ACC。全失败用例上传只有一帧的 PPG，ACC 仍使用配套样本。

资源来自 `var/old-assets/`：`spo2-latest-spo2.onnx`、`diabetes-latest-diabetes.onnx`、`bp-latest-sbp.joblib`、`bp-latest-dbp.joblib`、`bp-latest-normalize.csv`。血氧的 `spo2-latest-feature_names.npy` 使用 `allow_pickle=False` 转为 JSON；`spo2-latest-stable_features.pkl` 只解析纯字符串列表的 opcode 白名单，不执行 pickle，再转为 JSON。joblib 使用本机已有受信任制品，不推广为可加载不可信文件。

| 资源 | SHA-256 |
|---|---|
| sample PPG | `db1d1792be071f9d4285a66e2ba47d1241e25d9150f11409d29777db2606ab46` |
| sample ACC | `4adfcdb1909830670f73933642ed3642c3bb9e42e17682811bc9592adec68852` |
| spo2 ONNX | `bd94bb362655355e5dbe79ed963076a27f1524d047d68fd5c4d1ae9702dbbf6e` |
| feature_names NPY | `29373a98be4fe4c662728b21fbd37987ceb3997adc9052934a6745adad1a7e76` |
| stable_features PKL | `32a7f019fbb038240964bdd49b1b537055a3939490b7fcaac73c38fb615de726` |
| diabetes ONNX | `08f7c9bfa8096b1ff1cc1bccb56a9ac07ae8d463df65ca2085e8e77563db49a7` |
| sbp joblib | `b0484c9f67a32a0fba8e935bc26e4906aba8353dbd13528c1f9c859e2314c32f` |
| dbp joblib | `f01c425ff041c59de48ff131adf093781da06bb870ad0851451db0628b5fca93` |
| normalize CSV | `07fb86796b28415996c14413bfcbc0af99700dc8c3d96e9b270a373ed98e10d8` |

| 输入与场景 | 实际 outcome | 实际指标 |
|---|---|---|
| 60 秒合成 PPG，完整真实制品 | `ALL_SUCCEEDED` | hr=`108.16986587002928`，br=`12.039611195405097`，spo2=`90.0`，diabetes_probability=`0.1936863660812378` |
| 同上，diabetes 制品缺失 | `PARTIALLY_SUCCEEDED` | hr、br、spo2 与上一行相同，无 diabetes_probability |
| sample PPG，完整真实制品 | `PARTIALLY_SUCCEEDED` | spo2=`94.64368438720703`，diabetes_probability=`0.5523077249526978`；HR/BR 按原结果契约拒绝，无 hr/br |
| 一帧 PPG | `ALL_FAILED` | 无指标 |
| sample PPG＋ACC，完整真实制品 | `ALL_SUCCEEDED` | sbp=`125.85039300154746`，dbp=`76.03917873786334` |
| 一帧 PPG＋sample ACC | `ALL_FAILED` | 无指标 |

这六类汇总均保存为 Task `SUCCEEDED`；逐一核对 HTTP 详情、数据库指标、正式目录实际集合和下载字节。各成功成员的完整载荷及文件字节与同输入、同冻结资料、同版本制品的独立计算完全一致；全失败没有正式目录或输出引用。血压资料为提交时冻结的 24 岁、gender=1、170 cm、55 kg。

另在 HR/BR 已生成实际文件、下一成员尚未完成时触发 20 秒整体硬时限，观察到 `FAILED / INFERENCE.EXECUTION_TIMEOUT`；真实完整汇总被损坏后为 `FAILED / RESULT.INVALID_OUTPUT`；真实计算后令牌被替换则拒绝保存、保留 `RUNNING` 和待恢复消息。以上均无权威结果、指标或正式目录。已成功的部分汇总重放只恢复原目录链接，执行次数仍为 1。普通详情、所有已登记文件下载、`/api/v1/docs` 和 `/api/v1/openapi.json` 均实际返回 200。

这些结果只证明编排、数值复用与安全边界，不证明生理或医学准确性。合成输入不是临床样本，`isExample=false` 不改变此限制。

本次定向回归分批通过：场景计算/服务/文件用例 79 项，其余相关算法、存储及工作流用例 344 项，共享安全失败和质量用例 47 项；不是全仓库测试结果。合并收集使用 `--import-mode=importlib`，避免既有不同算法目录中同名测试模块冲突。运行中仍有 SciPy/NumPy/pyPPG 的弃用与数值警告，未为组合场景屏蔽或改变原数值计算。

一次性真实制品验收脚本、独立测试数据库及该验收的 Redis 流已清理；永久行为回归保留在 `tests/unit/inference/scenes/` 与 `tests/integration/workflows/test_scene_*.py`。

## 安装、迁移与启动

升级既有数据库前，应先停止旧推理消费者，并只读检查是否存在旧 `RUNNING` 任务；在维护窗口运行迁移。迁移会为这些任务生成执行令牌并设置已过期租约，保留 `started_at`、输入快照及历史结果，随后由新消费者恢复。不要让新旧消费者在迁移期间并行运行。

```sh
uv python install 3.13
uv sync --locked --all-packages
uv run --locked --package health-service python manage.py check
uv run --locked --package health-service python manage.py migrate
LOG_DIR=logs/web uv run --locked --package health-service uvicorn config.asgi:application --host 127.0.0.1 --port 8000 --no-access-log --workers 1
```

当前提供五个普通渠道的账号注册、密码登录与本人账号／健康资料接口，参见[账号、资料与令牌](accounts.md)；模型目录、算法及版本发布见[模型与算法开发](models-and-algorithms.md)。

## 独立推理消费者运行与部署

Web 与消费者必须使用同一版本代码、数据库、Redis 配置及同一物理卷上的 `TASK_DATA_ROOT`、`MODEL_ARTIFACT_ROOT`，但使用各自独立的 `LOG_DIR`，避免多个进程写入同一轮转文件。当前正式目录发布依赖 POSIX 相对目录符号链接及同一父目录内 `os.replace` 的原子替换；部署前必须在实际卷验证两项能力，不支持时停止部署，不退化为逐文件复制。各进程使用的操作系统账号都须有相应目录权限。在项目根目录另开进程运行常驻消费者；每个消费者实例使用不同名称，省略 `--consumer` 时命令会生成主机名、进程号和随机后缀：

```sh
LOG_DIR=logs/inference uv run --locked --package health-service python manage.py run_inference --consumer local-inference-01
```

可用单轮模式手动验证或执行一次有界恢复扫描：

```sh
LOG_DIR=logs/inference uv run --locked --package health-service python manage.py run_inference --consumer manual-recovery --once
```

每轮先执行最多 100 条的数据库补发扫描，再用 `XAUTOCLAIM` 重领未确认消息，剩余名额用于领取新消息；`--once` 不等待后续消息，空队列时也会退出。常驻消费者无消息时最多等待 `INFERENCE_POLL_MILLISECONDS`。成功或失败终态持久化后才确认消息；成功任务的终态消息在确认前会尝试恢复正式结果链接，失败终态不发布输出。畸形、缺失或已终态任务消息会安全确认。SIGINT／SIGTERM 停止继续处理后续消息，当前执行会先按租约和令牌规则完成；未确认消息在租约到期后可由其他消费者重领。不要用强制终止代替正常停止；进程被强制结束时依赖数据库租约到期与 Redis 重领恢复。

每次算法执行由共享串行子进程承载，`INFERENCE_EXECUTION_TIMEOUT_SECONDS` 是所有算法共用的硬上限，与租约和心跳独立。超时会强制终止子进程并按当前执行令牌提交安全失败码 `INFERENCE.EXECUTION_TIMEOUT`（“推理执行超时”）；只有失败终态提交后才确认消息，任务不会自动重试。正常任务复用子进程运行时缓存，超时后后续任务使用新进程。

算法只在明确质量拒绝时以 `INFERENCE.QUALITY_REJECTED` 进入 `FAILED` 终态；消费者在失败信息提交后才确认消息。`LOW_SCORE` 私有明细保存本次实际分数和阈值，`NO_VALID_SIGNAL` 不保存评分。已拒绝任务不会由补发扫描或消息重放重新执行；重新采集应创建携带新输入的新任务。既有恢复仍仅覆盖可派发的 `SUBMITTED` 和租约已过期的 `RUNNING`，不改变失败终态。事件日志只记录实际错误码，不记录质量原因、评分、阈值或异常正文。这条反馈通道不代表已新增质检规则，也不验证医学准确性。

消费者遇到数据库或 Redis 故障会停止并报告固定错误，不把基础设施故障记成算法失败。修复依赖后可重新启动；数据库补发扫描会再次驱动仍可执行任务。Redis Stream 不会自动裁剪，可能持续增长；不要手工清空或裁剪仍有待恢复消息的 Stream，部署时应监控 Redis 容量并制定与消息恢复语义兼容的保留策略。

**升级顺序：**先暂停新任务提交并备份数据库及两个文件根目录；停止旧消费者，检查遗留 `RUNNING` 任务；在维护窗口运行迁移；确认共享卷支持相对目录链接和同父目录原子替换；以共享配置启动新 Web 与消费者并确认可用后再开放提交。新旧消费者不得并行运行。**回滚顺序：**先暂停新提交，向消费者发送 SIGTERM 并等待当前安全执行结束，再停止 Web；保留数据库迁移字段、Redis Stream、`.executions`、正式目录链接及所有被数据库键引用的文件，不执行破坏性反向迁移或清理。回退版本必须先验证其通用存储键读取器可读新路径；不能假定旧版执行目录校验理解 `.executions`。若无法证明兼容，保留当前版本并前滚修复，不移动或删除存量文件。

日志不得包含原始输入、输出内容、完整结果、健康资料快照、文件路径、执行令牌、Access／Refresh Token 或含凭证的 Redis URL。消费者故障定位使用固定错误摘要；不要为排查故障而放宽日志白名单。

消费者结构化事件同时写入 stdout 与当前进程的 `application.log`。启动立即记录 `inference.worker.starting`；消费组初始化及首轮数据库扫描成功后记录一次 `inference.worker.ready`。空闲轮询与正常续约不反复打印 INFO；单轮结束或 SIGINT／SIGTERM 正常清理完成后记录 `inference.worker.stopped`。初始化失败记录 `inference.worker.error` 并错误退出，不误报就绪或正常停止。

| 事件 | 含义 |
|---|---|
| `inference.message.received` | 收到消息；尚不表示拿到执行权。 |
| `inference.task.started` | 已实际取得执行权，开始计算。 |
| `inference.task.succeeded` | 成功结果与终态已提交，尚不保证 Redis ACK 成功。 |
| `inference.task.failed` | 失败终态已提交，包含实际安全 `errorCode`。 |
| `inference.task.lease_lost` | 续约失权、维护异常或终态写入被拒绝；不报告当前执行成功。 |
| `inference.dispatch.failed` | 发布或派发时间补记失败，仍保留既有恢复资格。 |
| `inference.output.publish_failed` | 成功结果保持不变，但正式目录链接发布失败；实际文件仍可按数据库键下载，后续请求可恢复链接。 |
| `inference.worker.error` | 数据库或 Redis 基础设施故障，不等同于算法失败。 |

任务事件可用 `consumer`、`taskId`、`messageId` 关联，`durationMs` 为单调时钟耗时，不因时间展示到秒而丢失毫秒精度。重复终态消息或有效租约占用不会制造新的开始事件；成功终态重放会基于数据库权威结果幂等恢复正式链接后确认，不重新计算。确认失败发生在成功事件之后时，以数据库成功状态为准；不能把基础设施错误当作业务失败。受限字段不含原始异常正文、堆栈、SQL、输入输出或执行令牌。


### 独立 HR/BR 拆分验收记录（2026-11-24）

主流程已完成三组新独立算法与旧融合实现的真实数值、质量及波形文件逐字节对照。原任务 14 的输入得到 HR `77.90509214733797`，BR 单独失败而前置通过，说明该输入下 HR 可以独立保留；这不是医学准确性认证，也不是最终回归通过声明。

经本次授权，主库已清理 15 个推理任务、20 条输入记录、38 条指标投影，以及任务存储根目录全部文件和推理 Stream；仅移除旧融合及旧 PPG v1 模型版本、其四个制品副本，其他 20 条渠道选择保留。旧融合、旧 PPG all.v1 源码及旧融合算法文档已移除，不保留别名；历史验收内容和 OpenSpec 归档材料不改写成新结果。

独立 HR、BR 已分别发布 `1.0.0`，模型及版本 ID 分别为 `7/7`、`8/8`；统一 PPG 模型 ID `5` 新增 `2.0.0`，版本 ID `9` 绑定 `ppg.active.offline.all.v2`。三项均已选择到 `android-cn`、`android-intl`、`ios-cn`、`ios-intl`、`wxapp`，逐渠道认证查询确认目录含七个模型；其余二十条渠道选择保持不变。

使用原任务 14 输入，经真实 HTTP 创建、上传、提交和常驻消费者执行：独立 HR 成功且为 `77.90509214733797`，独立 BR 失败；统一 PPG v2 为 `PARTIALLY_SUCCEEDED`，`hr`、`spo2`、`diabetes` 成功而 `br` 单独失败。数据库仅投影实际成功指标；心率最新和历史查询正常，呼吸率无伪造记录；独立及场景的 PPG 下载字节一致，失败 BR 下载及跨渠道下载均返回 404。三条验收任务、输入、输出和已确认消息随后按本次 ID 清理，未清理并发用户数据；验收结束时主库推理任务数为零，临时原始输入副本已释放。

独立 HR、BR 及公共输入、预处理和质量门禁的定向回归共 `89 passed`；其余迁移后的注册、场景、目录、消费者、健康查询和下载回归共 `508 passed`，两组共 `597 passed`，均使用 `--reuse-db --import-mode=importlib`。更新后的十四份主规格全部通过校验。运行中仍有 SciPy/NumPy/pyPPG 的弃用及数值精度警告，未屏蔽或据此改变计算。这些结果只验证实现一致性、失败隔离和接口流程，不构成医学准确性认证。

