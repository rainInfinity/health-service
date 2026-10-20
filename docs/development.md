# 本地开发

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

当前提供五个普通渠道的账号注册、密码登录与本人账号／健康资料接口；模型目录与版本发布、管理渠道校验及普通渠道可用模型查询见后续章节。

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

## 账号注册、密码登录与本人账号查询
普通渠道为 `android-cn`、`ios-cn`、`android-intl`、`ios-intl` 和 `wxapp`；示例中的 `{channel}` 使用其中一个值。`manager` 只允许已有管理账号登录及访问管理接口，不开放注册。账号是全局共用的，但签发的令牌绑定登录渠道。

注册时提交 `username` 和 `password`：

```sh
curl --include --request POST \
  http://127.0.0.1:8000/api/v1/android-cn/auth/register \
  --header 'Content-Type: application/json' \
  --data '{"username":"local-user","password":"Example-passphrase-483!"}'
```

注册成功返回 HTTP 201。登录使用同一请求字段，成功时返回 HTTP 200：

```sh
curl --include --request POST \
  http://127.0.0.1:8000/api/v1/android-cn/auth/login \
  --header 'Content-Type: application/json' \
  --data '{"username":"local-user","password":"Example-passphrase-483!"}'
```

登录响应中的公开字段使用 `accessToken` 和 `refreshToken` 别名：

```json
{
  "code": "OK",
  "message": "成功",
  "data": {
    "accessToken": "<access-token>",
    "refreshToken": "<refresh-token>"
  },
  "requestId": "<server-request-id>"
}
```

使用 Access Token 查询当前渠道的本人账号；只返回 `userId` 和 `username`，不返回手机号或资料：

```sh
ACCESS_TOKEN='<从登录响应的 data.accessToken 复制>'
curl --include \
  http://127.0.0.1:8000/api/v1/android-cn/users/me \
  --header "Authorization: Bearer ${ACCESS_TOKEN}"
```

成功响应仍使用统一字段，账号 ID 的公开别名为 `userId`：

```json
{
  "code": "OK",
  "message": "成功",
  "data": {"userId": 42, "username": "local-user"},
  "requestId": "<server-request-id>"
}
```

Access Token 仅适用于签发它的渠道，过期、缺失或无效令牌均不能读取账号。错误响应的结构与统一错误码见下文。

## 管理账号建立与登录

管理账号由受信任的运维人员在 Django shell 中创建，不通过公开注册接口建立。仅授予 `is_staff=True`；除非确有 Django 管理站点需求，不要同时授予 `is_superuser`：

```sh
uv run --locked --package health-service python manage.py shell
```

```python
from getpass import getpass
from django.contrib.auth import get_user_model

username = input("管理账号用户名: ")
password = getpass("管理账号密码: ")
get_user_model().objects.create_user(
    username=username,
    password=password,
    is_staff=True,
    is_superuser=False,
)
```

使用管理渠道登录，失败时不存在、停用、密码错误或当前无管理权限均返回相同的认证错误：

```sh
read -r -s -p '管理账号密码: ' MANAGER_PASSWORD; printf '\\n'
curl --include --request POST \\
  http://127.0.0.1:8000/api/v1/manager/auth/login \\
  --header 'Content-Type: application/json' \\
  --data "{\\"username\\":\\"manager-user\\",\\"password\\":\\"${MANAGER_PASSWORD}\\"}"
unset MANAGER_PASSWORD
```

管理接口的每次请求都会重查当前 `is_staff` 权限和管理渠道；撤销权限后，旧管理 Access 立即不能访问管理业务，旧 Refresh 不能轮换。账号仍可用时，用户仍可提交自己的管理 Refresh 登出。普通注册不能提交权限字段，`/api/v1/manager/auth/register` 不存在。


## 模型目录与版本发布

管理目录写接口均要求 `manager` 渠道 Access Token，且每次请求都会核对账号当前 `is_staff=True`。普通用户、普通渠道令牌及已撤销管理权限的账号不能写目录。管理账号建立和登录方式见上一节。

管理目录路由为：`POST /manager/models`；`GET`／`POST /manager/models/{model_id}/versions`；`PATCH /manager/model-versions/{version_id}`；`POST /manager/model-versions/{version_id}/artifacts`；`POST /manager/metrics`；`PUT /manager/model-versions/{version_id}/metrics`；`POST /manager/model-versions/{version_id}/publish`；`PUT /manager/channels/{channel}/models/{model_id}/selection`。普通查询为 `GET /{channel}/models`。

`hr_br.example` 是只读取 PPG CSV 并返回样本数、时长和采样率诊断的非临床示例；不接受固定参数，也不需要模型权重制品。正式 `hr_br` 目前只有输入／结果接口模板，没有注册的执行器，不能发布。

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
    raise QualityRejectedError(
        QualityRejection(QualityRejectionReason.NO_VALID_SIGNAL, None)
    )
```

文件格式或输入契约错误仍应报告 `INFERENCE.INVALID_INPUT`；意外异常不得按异常类型或文案推断质量原因。当前 `hr_br.example` 只做输入诊断，不新增质量检查，也不会自动产生上述拒绝。

以下示例使用已经登录取得的 `MANAGER_ACCESS`。创建模型后从响应的 `data.modelId` 取出 `MODEL_ID`：

```sh
export API=http://127.0.0.1:8000/api/v1
export MANAGER_ACCESS='<管理登录响应中的 data.accessToken>'

curl --include --request POST "$API/manager/models" \
  --header "Authorization: Bearer $MANAGER_ACCESS" \
  --header 'Content-Type: application/json' \
  --data '{"code":"hr-br","name":"HR/BR 示例","description":"非临床输入诊断"}'
```

使用返回的模型 ID 创建未发布版本，并从 `data.versionId` 取出 `VERSION_ID`：

```sh
export MODEL_ID='<创建模型返回的 data.modelId>'
curl --include --request POST "$API/manager/models/$MODEL_ID/versions" \
  --header "Authorization: Bearer $MANAGER_ACCESS" \
  --header 'Content-Type: application/json' \
  --data '{"version":"1.0.0","algorithmKey":"hr_br.example","params":null}'
```

创建与算法类型、单位完全一致的指标，再用指标代码设置版本完整投影集合：

```sh
export VERSION_ID='<创建版本返回的 data.versionId>'
curl --include --request POST "$API/manager/metrics" \
  --header "Authorization: Bearer $MANAGER_ACCESS" \
  --header 'Content-Type: application/json' \
  --data '{"code":"example.ppg.sample_count","description":"PPG 样本数","valueType":"number","unit":"samples"}'
curl --include --request PUT "$API/manager/model-versions/$VERSION_ID/metrics" \
  --header "Authorization: Bearer $MANAGER_ACCESS" \
  --header 'Content-Type: application/json' \
  --data '{"metricCodes":["example.ppg.sample_count"]}'
```

发布会校验算法已注册、固定参数有效、制品逻辑名与算法声明完全一致且文件存在，以及所有已选指标的代码、值类型和单位。示例算法允许零制品；正式版本若声明制品，通过 multipart `logicalName` 与 `file` 上传。发布成功后算法、参数、制品和指标集合不可原地修改；重复发布返回原发布时间。

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

## 推理任务草稿、上传、提交与查询

先使用当前普通渠道登录取得 Access，并查询渠道已配置模型及其 `requiredInputs`。创建请求只提交 `modelId`；账号和来源渠道均从令牌确定，客户端不能指定 `userId` 或任意 `versionId`：

```sh
CHANNEL=ios-cn
API=http://127.0.0.1:8000/api/v1
ACCESS_TOKEN='<该渠道登录响应中的 data.accessToken>'
MODEL_ID='<GET /api/v1/ios-cn/models 返回的 data.items[].modelId>'

curl --include --request POST "$API/$CHANNEL/tasks" \
  --header "Authorization: Bearer $ACCESS_TOKEN" \
  --header 'Content-Type: application/json' \
  --data "{\"modelId\":$MODEL_ID}"
```

创建成功返回 HTTP 201 和 `taskId`、固定模型版本、`status: DRAFT` 及 `isExample`。未配置给当前渠道的模型不能创建草稿；请求额外包含 `userId`、`versionId` 或权限字段会被拒绝。

只可向本人当前渠道的 `DRAFT` 上传算法声明的逻辑输入名。请求使用 multipart 的 `logicalName` 和 `file`；文件完整写入后才登记为当前选择：

```sh
TASK_ID='<创建响应中的 data.taskId>'
curl --include --request POST "$API/$CHANNEL/tasks/$TASK_ID/files" \
  --header "Authorization: Bearer $ACCESS_TOKEN" \
  --form 'logicalName=ppg' \
  --form 'file=@docs/examples/hr-br/ppg.csv'
```

逻辑名必须来自模型目录返回的算法输入契约，例如示例算法使用 `ppg`。同名重传只将新记录设为选中，旧记录和文件保留；`SUBMITTED`、`RUNNING` 或终态任务不能上传。HTTP 响应不包含服务器文件路径或存储键。

最终提交不接收请求体：

```sh
curl --include --request POST "$API/$CHANNEL/tasks/$TASK_ID/submit" \
  --header "Authorization: Bearer $ACCESS_TOKEN"
```

提交返回 HTTP 200 和 `SUBMITTED` 摘要。服务按“渠道 → 当前版本选择 → 任务 → 用户 → 资料”加锁，检查固定版本仍是当前选择、算法要求的每个输入都已选中且实际文件可读，再冻结当前完整资料和提交日期对应的 `age_years`。缺少资料或出生日期时不创建资料记录，年龄保持 `null`；未知病史键继续缺席，不填默认值。提交阶段只验证逻辑文件齐全和可读，不解析 CSV 或提前执行算法资料校验；算法必需资料在消费者运行时依固定版本契约检查。

任务列表采用创建时间、任务 ID 稳定倒序；`offset` 必须非负，`limit` 为 1–100、默认 20。列表只返回状态、固定版本、示例标志和业务时间，不加载完整结果或资料快照：

```sh
curl --include "$API/$CHANNEL/tasks?offset=0&limit=20" \
  --header "Authorization: Bearer $ACCESS_TOKEN"
curl --include "$API/$CHANNEL/tasks/$TASK_ID" \
  --header "Authorization: Bearer $ACCESS_TOKEN"
```

详情对 `SUCCEEDED` 返回数据库保存的完整权威 `result`，不会重新运行算法或从指标行重组结果；对 `FAILED` 只返回安全 `failureCode`、`failureMessage` 和完成时间，不返回 `failure_detail`、资料快照、原始输入或本地绝对路径。只有状态为 `FAILED`、错误码为 `INFERENCE.QUALITY_REJECTED` 且历史明细通过校验时，`qualityFailure` 才包含类型化白名单信息：

```json
{
  "qualityFailure": {
    "reason": "LOW_SCORE",
    "details": {"score": 61.25, "threshold": 70}
  }
}
```

无可用信号不提供评分或阈值：

```json
{
  "qualityFailure": {
    "reason": "NO_VALID_SIGNAL",
    "details": null
  }
}
```

成功任务、普通失败、缺少明细或损坏的历史明细中，`qualityFailure` 为 `null`。客户端应按稳定的 `reason` 值分支，不解析本地化的 `failureMessage`。查询、上传、提交及下载都同时按账号及任务来源渠道授权；跨账号或跨来源渠道的任务返回 404。重复提交及不允许的状态变更返回冲突，任务不能由用户重置或改写固定版本。

### 下载当前或历史输入及成功输出

上传响应中的 `fileId` 可用于下载本人任务的当前或历史输入；同名重传不会删除旧文件。成功任务 `result.output_files` 的逻辑名可用于下载处理后文件。两类下载都要求 Access Token 的渠道与路径一致，并同时匹配任务所属账号和来源渠道；管理令牌不能绕过此归属检查。输出仅在任务为 `SUCCEEDED` 且该逻辑名已进入权威结果时可下载。请求不接受存储键或本地路径：

```sh
FILE_ID='<上传响应中的 fileId>'
curl --fail --show-error --output ./input-history.csv \
  "$API/$CHANNEL/tasks/$TASK_ID/files/$FILE_ID/download" \
  --header "Authorization: Bearer $ACCESS_TOKEN"

OUTPUT_NAME='<成功结果 result.output_files 中的逻辑名>'
curl --fail --show-error --output ./result-output.bin \
  "$API/$CHANNEL/tasks/$TASK_ID/outputs/$OUTPUT_NAME/download" \
  --header "Authorization: Bearer $ACCESS_TOKEN"
```

成功下载返回 `FileResponse` 原始文件字节，不使用 JSON 业务包装；令牌渠道与请求路径不一致返回 403，任务账号或来源渠道不匹配、未知文件或逻辑名、缺失物理文件及失败／失租输出返回安全 404，不暴露存储路径。没有已登记 `output_files` 的成功结果不提供输出下载。

## 本人健康资料读取与部分更新

资料接口只从当前渠道的 Access 确定账号，不接受请求指定 `userId`。`GET /api/v1/<channel>/users/me/profile` 不创建缺失的 Profile；未填写值返回 `null`，性别以 `0` 表示未知：

```json
{
  "code": "OK",
  "message": "成功",
  "data": {
    "name": null,
    "gender": 0,
    "birthDate": null,
    "heightCm": null,
    "weightKg": null,
    "medicalHistory": null,
    "historyJson": null
  },
  "requestId": "<server-request-id>"
}
```

使用 `PATCH` 只提交要修改的字段；未提交字段保持原值，可空字段显式设为 `null` 时清空。`gender` 不可为 `null`，`0` 表示未知；身高和体重非空时必须为正数：

```sh
ACCESS_TOKEN='<当前渠道的 Access Token>'
curl --include --request PATCH \
  http://127.0.0.1:8000/api/v1/android-cn/users/me/profile \
  --header 'Content-Type: application/json' \
  --header "Authorization: Bearer ${ACCESS_TOKEN}" \
  --data '{"name":"示例用户","gender":0,"heightCm":null,"historyJson":{"isHypertensive":false}}'
```

`historyJson` 对象按键合并：键值 `false` 会保留为明确否，键值 `null` 删除该键，整个字段为 `null` 则清空病史对象。未知入参（包括 `userId`）会被拒绝；PATCH 成功返回 HTTP 200 和更新后的完整资料。

请求和响应中的已定义病史键继续使用 `isDiabetic`、`isHypertensive`；资料内部数据及 PostgreSQL `history_json` 使用 `is_diabetic`、`is_hypertensive`。API 只转换这两个已定义键，不改写其他扩展内容。此命名转换不改变表结构，也不需要数据迁移。

## Refresh 轮换与登出

普通和管理渠道的刷新、登出只在请求体中接收当前渠道的 `refreshToken`，不要求有效 Access；不要把 Refresh 放入 `Authorization` 头。管理 Refresh 轮换时还会重新检查账号当前管理权限；撤权后仍允许独立登出。成功刷新返回 HTTP 200，`data` 使用与登录相同的 `accessToken`、`refreshToken` 字段：

```sh
REFRESH_TOKEN='<从最近一次成功响应的 data.refreshToken 复制>'
curl --include --request POST \
  http://127.0.0.1:8000/api/v1/android-cn/auth/refresh \
  --header 'Content-Type: application/json' \
  --data "{\"refreshToken\":\"${REFRESH_TOKEN}\"}"
```

每次成功刷新都会原子撤销旧 Refresh 并签发新令牌对。客户端应安全保存新对，并在一次成功处理后同时替换本地 Access 和 Refresh；旧 Refresh 不可重用。刷新旧令牌、已登出令牌或渠道不匹配的令牌均不会产生新令牌。

登出只撤销请求中的那枚 Refresh。客户端应提交当前最新的 Refresh，并在收到成功响应后立即清除本地保存的令牌：

```sh
REFRESH_TOKEN='<当前渠道最新的 data.refreshToken>'
curl --include --request POST \
  http://127.0.0.1:8000/api/v1/android-cn/auth/logout \
  --header 'Content-Type: application/json' \
  --data "{\"refreshToken\":\"${REFRESH_TOKEN}\"}"
```

登出成功返回 HTTP 200，`data` 为 `null`；响应仍包含服务端生成的 `requestId`：

```json
{
  "code": "OK",
  "message": "成功",
  "data": null,
  "requestId": "<server-request-id>"
}
```

登出不会撤销已签发的 Access；在签发时配置决定的有效期内（默认 7 天），只要账号仍可用且渠道匹配，该 Access 仍能访问本人接口。客户端登出后应立即丢弃它；服务端不承诺提前使其失效。其他渠道的独立 Refresh 不受本次登出影响。

## JWT 密钥和过期令牌维护

Access Token 默认有效期为 7 天，Refresh Token 默认有效期为 30 天，可通过上文两个 `JWT_*_TOKEN_LIFETIME_SECONDS` 环境变量独立调整，以签发时配置和令牌自身 `exp` 为准。`JWT_SIGNING_KEY` 与 Django 的 `DJANGO_SECRET_KEY` 分开生成并保存；更换 JWT 密钥会使此前签发的令牌无法通过签名校验，这与仅调整有效期不同。

启用账号 API 后，旧 Session 不会转换成 JWT。客户端部署新版本后必须重新登录，并安全保存登录响应中的 Access 与 Refresh；后续按接口契约轮换 Refresh。

Ninja JWT 的黑名单表会记录已签发的 Refresh。部署环境应每日运行以下管理命令，删除已经过期的令牌记录：

```sh
uv run --locked --package health-service python manage.py flushexpiredtokens
```

定时任务须使用应用配置中的 `JWT_SIGNING_KEY` 和数据库连接环境变量；不要在 API 请求中执行清理命令。

## 查看与采集请求日志

API 请求完成和受限的异常定位事件以“无键日期时间、无键级别、其余 `key=value`”的单行格式写入 Web 进程 stdout，并以单行 JSON 写入 `server.log`；`health_service` 通用应用事件同样写入 stdout，并以单行 JSON 写入 `application.log`。API 记录只输出请求上下文白名单；普通应用记录只输出稳定事件名，inference 仅额外保留其专属安全关联字段，均不继承其他请求的标识、用户或渠道。通用事件名必须使用固定的小写点分隔标识，无效名称统一输出为 `application.event`。任意日志消息和异常正文均不序列化。

日志内容统一使用 `Asia/Shanghai` 的 `YYYY-MM-DD HH:mm:ss` 时间，不带小数秒和时区后缀，不依赖宿主机时区。完成记录还包含级别、事件、请求标识、HTTP 方法、路由模板、状态和耗时；只有可信的普通渠道、已鉴权用户 ID 或稳定错误码可用时才包含相应字段。stdout 连接交互终端时会高亮级别、事件和 HTTP 状态；重定向或被采集时不输出 ANSI 颜色码。以下为一个 404 的控制台示例：

```text
2026-10-12 20:00:00 INFO status=404 event=request.completed requestId=0123456789abcdef0123456789abcdef method=GET route=<unmatched> durationMs=1.25 errorCode=NOT_FOUND
```

JSON 文件保留 `timestamp`、`level` 键，时间值与同一事件的 stdout 相同。采集端需先读取固定 19 字符的日期时间，再读取级别与后续键值字段，不能再把整行直接作为 `key=value` 解析。旧 ISO 时间、`timestamp=` 和 `level=` 前缀不再输出；部署时同步切换客户端和采集解析规则，历史日志不重写。Uvicorn 自身启动信息和第三方输出不属于此结构化应用日志格式。

未预期异常另有一条 `request.exception` 事件，包含同一 `requestId`、异常类型及 `module`／`function`／`line` 定位；不包含异常文本、源码行或局部变量。不要为复现故障而把凭证或健康资料写入日志。

`LOG_DIR` 可选，默认是项目根目录下的 `logs/`；相对路径以项目根目录为基准，绝对路径按原值使用。启动时会创建目录；运行进程的操作系统账号必须对该目录有读取、写入和搜索权限，否则启动失败。API 日志写入 `server.log`，通用应用日志写入 `application.log`。

两个文件分别按宿主机本地午夜轮转，每个保留最近 30 个归档，另有一个当前文件。本地午夜由操作系统时区决定，不使用 Django 的 `TIME_ZONE`，因此归档日期可能与内容中的北京时间日期不同。标准库轮转处理器不协调多进程写入；每个 Web worker、inference 消费者或单轮恢复进程必须使用独立日志目录，或改用外部采集方案。上述命令按 `logs/web`、`logs/inference` 分离 Web 与消费者；不要同时运行两个共享 `logs/inference` 的消费进程。

Uvicorn 访问日志已关闭，Django 请求错误和数据库调试日志也不会输出原始路径、查询参数或 SQL。日志不得记录请求／响应正文、密码、令牌、Cookie、手机号、健康资料或未经验证的转发 IP。

## JSON API 回复与错误码

`/api/v1/` 下的 JSON 业务路由使用 `code`、`message`、`data`、`requestId` 四个字段；成功码为字符串 `OK`，无业务载荷时 `data` 为 `null`。HTTP 200／201 表示成功，响应正文的 `requestId` 必须与 `X-Request-ID` 响应头一致。客户端提供的请求标识不会覆盖服务端值。

成功与错误 JSON 响应均使用 UTF-8 编码，直接输出 `message`、`data` 中的中文等非 ASCII 字符，不转义为 `\uXXXX`。API 的 JSON renderer 与公共错误响应统一设置 `ensure_ascii=False`；引号、反斜杠和控制字符仍按 JSON 规则转义，解析后的字段值保持不变。

业务日期时间字段（`createdAt`、`submittedAt`、`startedAt`、`finishedAt`、`uploadedAt`、`publishedAt`）统一转换为北京时间 `Asia/Shanghai`，输出 `YYYY-MM-DD HH:mm:ss`；例如 `2026-10-10T18:00:00Z` 返回 `2026-10-11 02:00:00`。小数秒仅在展示时省略，不四舍五入，数据库时间、排序和租约精度不变；空时间仍为 `null`，`birthDate` 仍为 `YYYY-MM-DD`。任意结果或扩展资料中的字符串及下载文件内容不改写。OpenAPI 声明对应字符串格式、示例和时区，不再将响应日期时间声明为 RFC 3339 `date-time`。客户端必须按北京时间解释，不应依赖本地时区推断。

以下 JSON 示例为 HTTP 200；创建成功使用 HTTP 201，回复结构相同。

```json
{
  "code": "OK",
  "message": "成功",
  "data": {"value": "业务结果"},
  "requestId": "0123456789abcdef0123456789abcdef"
}
```

业务路由应声明包含实际载荷的回复 Schema，并使用公共 `success_response` 构造器；Schema 的别名应以 `requestId` 等公开字段名生成 OpenAPI。错误响应的 `data` 固定为 `null`，状态与业务码如下：

| HTTP 状态 | 业务码 | 对外文案 |
|---:|---|---|
| 401 | `INVALID_CREDENTIALS` | 用户名或密码错误 |
| 401 | `INVALID_TOKEN` | 认证凭证无效 |
| 403 | `CHANNEL_FORBIDDEN` | 无权访问此资源 |
| 404 | `NOT_FOUND` | 接口不存在 |
| 405 | `METHOD_NOT_ALLOWED` | 请求方法不支持 |
| 409 | `USERNAME_EXISTS` | 用户名已存在 |
| 409 | `CONFLICT` | 请求冲突 |
| 422 | `INVALID_INPUT` | 请求参数无效 |
| 429 | `RATE_LIMITED` | 请求过于频繁 |
| 500 | `INTERNAL_ERROR` | 服务器内部错误 |

错误文案来自固定映射，不回显提交值、令牌、任意异常文本、堆栈或 SQL；即使 `DEBUG=True`，API 客户端也只收到通用 500。Django 管理页、API 文档页及非 JSON 流／文件响应不使用此 JSON 包装。