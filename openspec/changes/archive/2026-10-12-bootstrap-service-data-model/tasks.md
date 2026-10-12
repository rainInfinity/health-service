# 任务

## 1. 依赖、配置与服务启动

- [x] 1.1 建立 Python 3.13 的 `pyproject.toml`、Web／开发依赖与单一 `uv.lock`，从 `.gitignore` 移除对锁文件的忽略；在干净环境仅安装 Web 依赖执行 `uv sync --locked`，确认无需解析新版本且不安装推理运行时依赖。
- [x] 1.2 建立 Django 5.2 的 `config` 与 `health_service` 基础配置，设置 PostgreSQL、`AUTH_USER_MODEL`、时区、应用密钥、`DEBUG` 和允许的主机；运行 `manage.py check`，并用缺少密钥或数据库配置的进程验证会明确失败、不会回退 SQLite。
- [x] 1.3 接通 ASGI／Uvicorn 与 Django Ninja Extra 的入口，使 `health_inference` 保持独立于 Web 启动链的普通 Python 包；在未安装推理依赖的环境执行 Django 配置检查并加载 ASGI 应用，确认没有导入算法运行时或创建虚假的业务接口。
- [x] 1.4 在 `docs` 下编写开发启动说明，列明 PostgreSQL 环境变量、锁定安装、迁移和 ASGI 启动命令；按文档逐条执行其初始化命令，确认从干净环境可以复现。

## 2. 自定义用户、健康资料与审计

- [x] 2.1 实现不定义主键的抽象 `AuditModel`，在 `health_service` 的首次迁移建立继承 `AbstractUser` 的 `health_user`；通过 PostgreSQL 行为测试验证 Django 能取得自定义用户、密码被哈希、多个空手机号可共存、逻辑删除后原手机号仍阻止重复注册，并验证创建时间及可空操作人快照。
- [x] 2.2 实现共享用户主键的 `health_profile`、可空健康资料与性别／正数测量约束；通过 PostgreSQL 写入测试验证同用户不能有第二份资料、未知值不被写成零或 `false`，无效性别和非正数身高体重被拒绝。

## 3. 渠道、模型版本及指标目录

- [x] 3.1 建立 `health_channel`、`health_model`、`health_model_version`、`health_channel_model_selection` 的字段、外键及唯一约束；通过 PostgreSQL 写入测试验证重复 label／code／模型版本／渠道模型配置被拒绝，不同渠道能各自保存当前版本，不预置尚未最终确认的渠道记录。
- [x] 3.2 建立 `health_model_artifact`、`health_metric_definition`、`health_model_version_metric` 的字段、关系及约束；通过 PostgreSQL 写入测试验证同版本制品名、全局存储键、指标 code 和版本指标关联的唯一性，制品大小为正，数值／布尔指标定义可被不同版本引用。

## 4. 任务、原始文件与结果投影

- [x] 4.1 建立 `health_task` 的归属外键、快照、业务时间、状态／结果／失败字段、检查约束及设计中的初始任务索引；通过 PostgreSQL 写入测试验证空快照草稿可保存、非法引用被拒绝，成功必须有结果与完成时间，失败必须有完成时间与非空失败代码，其他状态不能携带冲突字段。
- [x] 4.2 建立 `health_task_input_file` 的文件元数据、正数大小、唯一存储键及仅对选中行生效的 `(task_id, logical_name)` 唯一约束；通过 PostgreSQL 写入测试验证同名旧文件可保留但不能同时选中两条，非正数大小与重复存储键被拒绝。
- [x] 4.3 建立 `health_task_metric` 的任务／指标归属、联合唯一及数值／布尔值互斥约束；通过 PostgreSQL 写入测试验证 `false` 与缺值不同、两列同时有值或同时为空及重复投影被拒绝，不为未出现的指标造零值记录。

## 5. 跨模块集成验证

- [x] 5.1 在全新 PostgreSQL 数据库实际执行全部迁移，确认自定义用户、十二张显式业务表和 Django 权限关系创建成功；重复运行 `migrate`、`manage.py check` 与 `makemigrations --check --dry-run`，确认无迁移漂移及重建表行为。
- [x] 5.2 在仅安装 Web 依赖并完成迁移的环境实际启动 Uvicorn，发送 HTTP 请求观察 ASGI 服务响应，同时确认未加载推理运行时；不将空路由响应或数据库表的存在误报为登录、上传或推理流程已可用。
