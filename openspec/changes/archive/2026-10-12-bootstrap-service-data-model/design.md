# 设计

## 背景与约束

本变更的动机见 [proposal.md](proposal.md)；运行和持久化要求见 [service-bootstrap 规格](specs/service-bootstrap/spec.md)与 [health-data-model 规格](specs/health-data-model/spec.md)。仓库目前没有 Python 项目、旧迁移或待兼容的 API。现有[后端架构](../../../docs/architecture/backend-architecture.md)规定 Python 3.13、Django 5.2 LTS、ASGI、PostgreSQL 和 Web／推理进程分离；[数据库设计](../../../docs/architecture/database-design.md)定义了 12 张业务表、字段和任务生命周期，但尚未成为可执行迁移。

## 目标与非目标

**目标：** 从空的 PostgreSQL 数据库得到可重复的 Django 5.2 初始模式；使 ASGI 服务在不安装计算依赖时可启动；区分已经由数据库保证的不变量与未来由用例保证的规则。

**非目标：** 不用空 API、模拟 worker 或假的模型输出代表已实现业务；不引入 SQLite 正式路径、旧库导入、Redis Outbox、推理调度、JWT 令牌表或尚未确定的算法输入／输出结构。

## 技术决策

### 1. 单一项目、单一锁文件和明确配置

采用 `src/config` 组织 Django 的 settings、ASGI 与 URL，`src/health_service` 为唯一持久化 Django App，`health_inference` 保持与其平级的普通 Python 包，不从 Web 启动链导入推理运行时。使用一个 `pyproject.toml` 与可提交的 `uv.lock`，固定 Python 3.13 和 Django 5.2 系列；Web 直接依赖 Django Ninja、Ninja Extra、Pydantic v2、`psycopg` 3、Uvicorn，测试工具按开发依赖声明。计算依赖只在有实际算法需要时列入可选安装项，不为未确定的算法预装框架；`django-ninja-jwt` 待鉴权能力实施并验证兼容性后再锁定。现有 `.gitignore` 忽略 `uv.lock`，实施时需解除该忽略以保证干净检出可复现。

使用单一清晰的 settings 入口，从环境读取数据库名称、用户、凭据、主机／端口、`SECRET_KEY` 与允许的主机；缺少关键值时明确报错，`DEBUG` 不默认开启，也不以 SQLite 或内置生产密钥兜底。采用 PostgreSQL 的 Django 官方后端，不增设尚无需求的配置抽象。相较于把 Web、推理拆成两个依赖项目或为本地开发默认 SQLite，这能保持一致的迁移和数据库约束；代价是开发与验证必须准备 PostgreSQL。

### 2. 自定义用户先于其他业务迁移

在首次 `migrate` 前设置 `AUTH_USER_MODEL = "health_service.User"`。用户模型继承 Django `AbstractUser` 并添加手机号、逻辑删除时间和审计字段，复用框架的密码哈希与权限字段；`Meta.db_table = "health_user"`，不更改 Django `AppConfig.label`。显式设置 `DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"`。`health_service` 的首个迁移必须创建用户模型，以满足 Django 对可交换用户模型的迁移依赖；其余业务模型可置于同一初始迁移或后续初始迁移，实际以 Django 生成的无循环依赖图为准。

相比先使用默认 `auth.User` 再替换，此方案避免在已有表和权限关联后迁移身份主键。12 张指的是显式定义的业务模型；Django 内置认证表及自定义用户可能产生的权限关联表不计入其中。已有用户不得物理级联删除历史任务；关联历史记录的外键采用阻止意外删除的策略，账号业务删除使用 `deleted_at` 和 `is_active = FALSE`。此变更不实现注销或登录用例。

### 3. 抽象审计与逐域 ORM 模型

`AuditModel` 只提供 `created_at`、`updated_at`、`created_by`、`updated_by` 四列，`abstract = True`，自身不建表、也不定义主键。创建时两时间取同一时间值；后续普通保存更新修改时间，批量更新须由写入方显式更新修改时间及操作人，不从 HTTP 请求或推理进程隐式抓取身份。操作人使用可空、长度至多 64 的稳定标识快照。相比带用户外键的统一审计表，这避免历史用户状态变化影响审计信息，并让 `health_profile.user_id` 成为共享主键。

在 `health_service.models` 按账号、模型目录、任务分文件，导出 Django 可发现的模型；不为每张表建立空的领域实体或仓储层。十二个显式模型与物理表一一对应：`User`／`health_user`、`Profile`／`health_profile`、`Channel`／`health_channel`、`Model`／`health_model`、`ModelVersion`／`health_model_version`、`ModelArtifact`／`health_model_artifact`、`ChannelModelSelection`／`health_channel_model_selection`、`MetricDefinition`／`health_metric_definition`、`ModelVersionMetric`／`health_model_version_metric`、`Task`／`health_task`、`TaskInputFile`／`health_task_input_file`、`TaskMetric`／`health_task_metric`。字段的长度、空值、单位和数据库类型以[数据库设计的物理模型](../../../docs/architecture/database-design.md#物理模型)为准，实施时逐一映射并检查迁移，不能把该文档当作已有 SQL。

`Profile` 用以用户为主键的 `OneToOneField`，资料字段保留 `NULL` 与未知性别 `0` 的差别；正数身高／体重使用 `DecimalField`。`ModelVersion.params`、`Profile.history_json`、`Task.profile_snapshot`、`Task.result_payload` 与 `Task.failure_detail` 使用 `JSONField`（PostgreSQL `JSONB`），`USE_TZ = True` 使时间对应 `TIMESTAMPTZ`；指标数值采用文档规定的双精度浮点。模型制品和任务原始文件只记录相对存储键、大小与归属，不把二进制文件或绝对路径放入表内。六个渠道 label 暂定，不在初始 DDL 中用检查约束冻结、也不在迁移中擅自灌入未经最终确认的渠道数据。

### 4. 数据库约束与后续用例的责任线

迁移创建非空外键、手机号与渠道／模型／指标编码的唯一约束，以及版本、渠道选择、版本指标、任务指标各自的组合唯一约束；手机号唯一约束不排除逻辑删除行，未绑定统一用 `NULL`。制品存储键、原始文件存储键全局唯一；同版本制品逻辑名唯一；文件大小大于零。同任务同逻辑名的选中文件采用条件唯一约束，未选中的历史行可以有多条。

数据库检查约束覆盖性别取值、正数身高体重、任务状态取值、`SUCCEEDED` 与非空结果双向一致、终态完成时间、`FAILED` 的非空失败代码及非失败状态下失败字段为空，以及指标数值／布尔列恰好一个有值。`MetricDefinition.value_type` 限制为数值或布尔类型；指标单位与具体业务语义由后续模型配置确定。初始任务查询索引优先覆盖 `(user_id, created_at DESC)`、`(channel_id, model_version_id, status)` 与 `(model_version_id, status)`；指标统计索引等有实际查询和执行计划后再补，避免为尚不存在的读路径增加写入成本。

外键和检查约束不能保证 `ChannelModelSelection.current_version_id` 属于该行的 `model_id` 且已发布、`TaskMetric` 只引用成功任务，或模型版本发布后元数据不再改变。这些是[数据库设计](../../../docs/architecture/database-design.md#写入与生命周期约束)明确的事务用例责任，后续实现选择、提交、切换和完成流程时才一并落实并验证；不能以此变更的 ORM `clean()`、信号或单行约束假装已实现并发安全。上传文件落盘与数据库写入也不是一个事务，提交后向 Redis 发布任务的可靠性不在本次建模中承诺。

## 风险与取舍

- **自定义用户迁移顺序错误** → 在空库上先验证 `AUTH_USER_MODEL`、迁移依赖与内置权限表；不对已部署默认用户表执行补丁式切换。
- **锁文件被忽略导致不可复现** → 实施时修改 `.gitignore`，从干净检出验证锁定安装，而不是只检查本机缓存。
- **把库内约束误认为完整业务流程** → 明列跨表、状态转换和并发锁的未交付责任；此变更的验证只覆盖可执行的数据库规则。
- **病史、结果及原始文件含敏感信息** → 数据库存相对键且不公开文件目录；账号逻辑删除后保留这些记录的政策必须在可用业务发布前明确，不能把存留默认等同于合规。
- **预置查询索引增加写入成本** → 只纳入与文档中明确读写路径对应的任务索引；其余按真实负载补充。

## 迁移与验证方案

先确定锁文件与环境变量，再创建 Django 项目和自定义用户的首次迁移，随后加入其余模型、约束及索引。在全新 PostgreSQL 库上运行 `migrate` 与 `check`，重复执行 `migrate` 和 `makemigrations --check --dry-run`，并在无推理依赖的 Web 环境实际启动 ASGI 进程。用数据库写入场景验证共享主键、逻辑删除后的手机号占用、部分唯一、状态／结果一致、正数检查和指标值互斥；这些测试应验证被数据库拒绝的行为，而不是仅断言模型定义存在。

本变更不迁移生产旧数据。尚无已部署模式，失败时可在专用空库上重建并重新迁移；一旦产生真实业务数据，不以删库作为回滚手段，需针对具体迁移审查数据影响并制定反向迁移。

## 可后定问题

各版本必需输入的契约来源、较大结果是否改存受保护文件、JWT 库的异步与黑名单兼容性、六个渠道何时初始化，以及文件与健康数据的保留／清理政策，均不改变本次表结构的核心关系；在相应业务能力实施前分别决策。