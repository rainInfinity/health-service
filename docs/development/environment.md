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

任务输入的渠道目录使用固定渠道代码，不在文件名中重复渠道。`<YYYYMMDD>` 与文件名前缀 `<YYYYMMDD_HHmmss>` 均从任务创建时间转换到 `Asia/Shanghai` 后生成，格式分别为 `yyyyMMdd` 与 `yyyyMMdd_HHmmss`；上传跨日时仍使用任务创建日期。输入文件名依次携带任务 ID、用户 ID、算法 key、逻辑名和 32 位小写十六进制 UUID，客户端原始文件名不进入存储路径。示例：`ios-cn/20261101/20261101_003045-42-7-ppg.active.offline.hr_br.v1-ppg-<UUID>.csv`。

模型制品路径按稳定 `model_code`、版本号和逻辑名分层；例如 `spo2` ONNX 制品使用 `<model_code>/<version>/spo2-<UUID>.onnx`，`feature_names` 与 `stable_features` 清单使用 `.json`，UUID 同样为 32 位小写十六进制。输入和制品的扩展名从上传文件名提取、转为小写并按算法契约校验；逻辑名与扩展名是独立字段，扩展名不代替内容校验。

算法 key、逻辑名、模型编码和版本号等业务路径段按 UTF-8 字节进行可逆百分号编码：字母、数字、点和下划线保留，其余字节（包括连字符）编码为大写 `%HH`；固定渠道代码保留原值。整段 `.`、`..` 必须编码或拒绝。保存前按文件系统字节上限检查目录段和完整文件名，超限时安全拒绝，不截断、不替换字符。

正式结果入口是 `<渠道代码>/<任务创建日期>/outputs/<task_id>/`，由服务端管理的相对目录符号链接指向同一任务根目录内已成功执行的 `.executions/<task_id>/<execution_token>/`。目录整组切换，保留算法原文件名，例如 `ppg-processed.csv`；算法只写本次 `output_dir`，不得写入或推导正式目录。执行临时文件不进入正式结果目录。

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

