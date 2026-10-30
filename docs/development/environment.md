# 环境配置与服务运行

## 前置条件

- Python 3.13，由 `uv` 管理解释器与依赖。
- Docker，以及 PostgreSQL 16 或兼容的 PostgreSQL 服务。
- 根目录唯一的 `uv.lock`；完整开发和测试环境使用 `uv sync --locked --all-packages`，不要单独升级依赖而不更新锁文件。

### Python 工作区

根目录 `pyproject.toml` 只编排两个成员，不是第三个业务项目；所有成员共用根目录的 `uv.lock` 和默认 `.venv`。`packages/health-service` 包含 Django `health_service` 与 `config`，声明业务运行依赖并显式依赖本地 `health-inference`；`packages/health-inference` 是纯 Python 计算包，目前不声明第三方运行依赖。

```sh
# 完整开发与测试环境
uv sync --locked --all-packages
# 仅业务成员及其运行依赖
uv sync --locked --package health-service --no-dev
# 仅计算成员及其运行依赖
uv sync --locked --package health-inference --no-dev
```

`uv run --package ...` 选择运行成员，不切换当前目录；以下管理命令和 Web/消费者入口均从仓库根目录运行。

## 启动 PostgreSQL

可使用已有 PostgreSQL 实例，也可用仓库外部的临时容器：

```sh
docker run --rm --detach \
  --name health-service-postgres \
  --publish 127.0.0.1:55432:5432 \
  --env POSTGRES_DB=health_service \
  --env POSTGRES_USER=health_service \
  --env POSTGRES_PASSWORD=local-development-only \
  postgres:16.4-alpine
```

容器停止后会自动删除。不要在生产环境使用示例密码。

## 配置环境变量

### 使用根目录 `.env`

推荐先复制模板，再填写本地配置：

```sh
cp .env.template .env
uv run --locked --all-packages python -c 'import secrets; print(secrets.token_urlsafe(50))'
uv run --locked --all-packages python -c 'import secrets; print(secrets.token_urlsafe(50))'
```

将第一条命令输出填写到 `.env` 的 `DJANGO_SECRET_KEY`，第二条填写到独立的 `JWT_SIGNING_KEY`；不要复用密钥。再为 `POSTGRES_PASSWORD` 填入本地数据库密码。上面的 PostgreSQL 容器示例使用 `local-development-only`，仅用于本地开发，不要用于生产环境。

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
| `INFERENCE_DISPATCH_INTERVAL_SECONDS` | `30` | 数据库补发间隔，正整数秒。 |
| `INFERENCE_MAX_ATTEMPTS` | `3` | 有效执行领取次数上限，正整数。 |
| `INFERENCE_POLL_MILLISECONDS` | `1000` | 消费轮询等待上限，正整数毫秒。 |

所有时长和数量配置必须为正整数；无效值、空队列名称或不可读写的存储目录会使 Django 启动失败。

`TASK_DATA_ROOT` 包含原始健康输入和推理输出，不能配置为公开静态目录；`MODEL_ARTIFACT_ROOT` 保存模型制品。运行 Web 与消费者的操作系统账号都必须对相同目录具备搜索、读取和写入权限。上传写盘与数据库登记不是原子操作；中断或登记回滚可能留下孤儿文件，本地存储不会自动清理旧输入、输出或孤儿文件。只有通过任务记录或成功结果登记的相对键才可由业务读取；输入和输出下载仍须检查账号、来源渠道及成功结果授权。

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

Web 与消费者必须使用同一版本代码、数据库、Redis 配置及共享可达的 `TASK_DATA_ROOT`、`MODEL_ARTIFACT_ROOT`，但使用各自独立的 `LOG_DIR`，避免多个进程写入同一轮转文件。在项目根目录另开进程运行常驻消费者；每个消费者实例使用不同名称，省略 `--consumer` 时命令会生成主机名、进程号和随机后缀：

```sh
LOG_DIR=logs/inference uv run --locked --package health-service python manage.py run_inference --consumer local-inference-01
```

可用单轮模式手动验证或执行一次有界恢复扫描：

```sh
LOG_DIR=logs/inference uv run --locked --package health-service python manage.py run_inference --consumer manual-recovery --once
```

每轮先执行最多 100 条的数据库补发扫描，再用 `XAUTOCLAIM` 重领未确认消息，剩余名额用于领取新消息；`--once` 不等待后续消息，空队列时也会退出。常驻消费者无消息时最多等待 `INFERENCE_POLL_MILLISECONDS`。成功或失败终态持久化后才确认消息；畸形、缺失或已终态任务消息会安全确认。SIGINT／SIGTERM 停止继续处理后续消息，当前执行会先按租约和令牌规则完成；未确认消息在租约到期后可由其他消费者重领。不要用强制终止代替正常停止；进程被强制结束时依赖数据库租约到期与 Redis 重领恢复。

算法只在明确质量拒绝时以 `INFERENCE.QUALITY_REJECTED` 进入 `FAILED` 终态；消费者在失败信息提交后才确认消息。`LOW_SCORE` 私有明细保存本次实际分数和阈值，`NO_VALID_SIGNAL` 不保存评分。已拒绝任务不会由补发扫描或消息重放重新执行；重新采集应创建携带新输入的新任务。既有恢复仍仅覆盖可派发的 `SUBMITTED` 和租约已过期的 `RUNNING`，不改变失败终态。事件日志只记录实际错误码，不记录质量原因、评分、阈值或异常正文。这条反馈通道不代表已新增质检规则，也不验证医学准确性。

消费者遇到数据库或 Redis 故障会停止并报告固定错误，不把基础设施故障记成算法失败。修复依赖后可重新启动；数据库补发扫描会再次驱动仍可执行任务。Redis Stream 不会自动裁剪，可能持续增长；不要手工清空或裁剪仍有待恢复消息的 Stream，部署时应监控 Redis 容量并制定与消息恢复语义兼容的保留策略。

**升级顺序：**先暂停新任务提交并备份数据库及两个文件根目录；停止旧消费者，检查遗留 `RUNNING` 任务；在维护窗口运行迁移；以共享配置启动新 Web 与消费者并确认可用后再开放提交。新旧消费者不得并行运行。**回滚顺序：**先暂停新提交，向消费者发送 SIGTERM 并等待当前安全执行结束，再停止 Web；保留数据库迁移字段、Redis Stream 和文件，不执行破坏性反向迁移或清理。旧版本不具备租约及 Stream 协议时不要启动旧消费者；未完成任务应留待兼容版本恢复。

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
| `inference.worker.error` | 数据库或 Redis 基础设施故障，不等同于算法失败。 |

任务事件可用 `consumer`、`taskId`、`messageId` 关联，`durationMs` 为单调时钟耗时，不因时间展示到秒而丢失毫秒精度。重复终态消息或有效租约占用不会制造新的开始事件。确认失败发生在成功事件之后时，以数据库成功状态为准，后续重放只需确认；不能把基础设施错误当作业务失败。受限字段不含原始异常正文、堆栈、SQL、输入输出或执行令牌。

