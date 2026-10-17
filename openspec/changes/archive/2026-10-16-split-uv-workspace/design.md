# Design

## Context

动机和范围见 `proposal.md`。本变更跨越两个 Python 包、Django 项目设置、进程入口和子进程验证，满足需要独立设计产物的条件。

当前根项目使用 `package = false`，`manage.py` 注入仓库 `src`，pytest 设置 `pythonpath = ["src"]`，Uvicorn 命令使用 `--app-dir src`。`config/settings.py` 根据源码层级定位根目录，环境配置测试也会复制这一布局。

`health_inference` 当前只依赖标准库；业务包通过算法注册表读取输入和发布规则，消费者还要调用其执行器。因此不能将计算包从 Web 的安装依赖中直接移除，也不能把依赖 Django ORM 和 Redis 的消费者迁入纯计算成员。源码还直接导入 `asgiref`，但根项目尚未直接声明它。

## Goals / Non-Goals

**Goals:**

- 用项目元数据表达单向依赖，不再通过仓库源码路径隐式获得另一个成员。
- 让干净环境中的业务安装、计算包单独安装和根目录整体开发安装都可按同一锁文件复现。
- 保持仓库根目录的环境配置、日志和数据定位，以及现有导入名、管理命令与业务行为。

**Non-Goals:**

- 不提供成员各自的常驻虚拟环境、独立锁文件、不同 Python 版本或相互冲突的依赖版本。
- 不拆仓、不引入 Git submodule，不创建第三个公共契约包。
- 不新增算法、计算依赖、空的 runtime extra、插件发现机制或消费者入口。
- 不改变 API、模型与迁移内容、任务协议、日志语义或测试领域划分。
- 不新增脱离仓库的 Web wheel 部署方式；本次运行契约仍以 workspace 检出为基础。

## Decisions

### 1. 根目录只编排，显式列出两个成员

根目录 `pyproject.toml` 不再包含业务 `[project]`，以 `[tool.uv.workspace]` 显式列出 `packages/health-service` 与 `packages/health-inference`。根目录保留开发依赖组、pytest 和 Ruff 配置，使用唯一的 `uv.lock` 和默认 `.venv`。

两个成员均保持 `requires-python = ">=3.13,<3.14"`。不保留根目录同名业务项目，避免与 `health-service` 成员重名或形成第三个业务安装单元。

选择显式成员而不是通配符，防止新增普通目录被意外纳入工作区。未采用两个独立项目，因为用户已选择统一版本解析与默认共享环境。

### 2. 保留导入名，以可安装包承接目录迁移

| 成员目录 | 分发名称 | 包含的 Python 包 |
| --- | --- | --- |
| `packages/health-service` | `health-service` | `src/health_service`、`src/config` |
| `packages/health-inference` | `health-inference` | `src/health_inference` |

使用 `uv_build` 作为两个成员的构建后端，声明有上下界的兼容版本范围。业务成员显式配置 `module-name = ["health_service", "config"]`，计算成员采用与分发名对应的默认模块名。Django 迁移随业务包一起安装，保持 App label 和迁移身份不变。

开发环境依赖 uv 的 editable 安装。完整移走旧 `src` 下的实现，不保留转发模块、重复副本或兼容路径。

未将 `config` 改名或并入新的 Python 命名空间，因为那会扩大导入和 Django 设置契约的变更范围。多模块构建能力见 [uv 构建后端文档](https://docs.astral.sh/uv/concepts/build-backend/#namespace-packages)。

### 3. 依赖归属显式，计算包基础安装保持轻量

`health-service` 的运行依赖包含现有业务依赖及 `health-inference`，后者在业务成员的 `[tool.uv.sources]` 中标为 `workspace = true`。现有依赖范围不变；补充业务代码直接使用的 `asgiref` 声明，范围应兼容当前锁定的 `3.12.1`，不因此升级已锁定第三方包。

Django、Ninja 及认证扩展、PostgreSQL 驱动、Pydantic、dotenv、Redis 和 Uvicorn 均归业务成员。数据库驱动和 ASGI 服务器即使不是直接 Python import，也属于该成员实际运行所需依赖。

`health-inference` 当前不声明第三方运行依赖。pytest、pytest-django、Ruff 保留在根目录开发组；验证纯计算安装时必须关闭该开发组，否则 pytest-django 会间接引入 Django，掩盖依赖边界。

Web 仍可导入计算包的轻量定义和注册信息。未来真正引入重型算法依赖时，必须继续满足现有 Web 不要求安装或加载可选计算运行时的契约；此次不预建 extra，也不提前重构执行器加载方式。

workspace 不会自动禁止成员导入其他成员碰巧安装的第三方库，因此依赖声明和仅安装计算成员的验证都需要保留。限制见 [uv workspace 文档](https://docs.astral.sh/uv/concepts/projects/workspaces/#when-not-to-use-workspaces)。

### 4. 保留根入口，运行依赖已安装成员

根目录 `manage.py` 保持为 Django 管理入口，移除对旧 `src` 的 `sys.path` 注入。`config.settings` 和 `config.asgi:application` 的名称不变。消费者继续位于业务包的 `management/commands/run_inference.py`。

统一文档中的主要运行约定，以下为实施后的目标命令：

- 整体开发安装：`uv sync --locked --all-packages`。
- 管理检查与迁移：`uv run --locked --package health-service python manage.py check`，以及相同前缀的 `migrate`。
- Web：`uv run --locked --package health-service uvicorn config.asgi:application --host 127.0.0.1 --port 8000 --no-access-log`；开发和部署的 reload、workers、日志目录规则沿用原说明。
- 消费者：`uv run --locked --package health-service python manage.py run_inference`，保留现有消费者名称和 `--once` 参数。
- 仓库验证：`uv run --locked --all-packages pytest` 与同前缀的 `ruff check .`。

删除文档和真实启动流程中不再适用的 `--app-dir src`、pytest `pythonpath` 和生产代码路径注入，不以新的全局 `PYTHONPATH` 代替项目安装。`--package` 选择运行项目，不作为改变当前工作目录的手段；上述根入口命令从仓库根目录执行。

### 5. 环境与数据仍以仓库根目录为准

调整迁移后的 `config/settings.py`，由已确定的 workspace 源码布局定位仓库根目录，而不是把业务成员目录当作根目录。不依赖启动工作目录、`.git` 存在与否或新增环境变量。

根 `.env`、`.env.template`、默认 `logs`、`var/tasks`、`var/model-artifacts` 保持位置不变，环境变量优先级、必填校验和相对路径解释不变。不移动或重建用户现有数据，不增加配置项，因此无需新增模板变量。

临时环境配置测试复制新的成员布局，并继续使用临时根 `.env`，防止读取真实本地凭据。为选中临时配置副本而设置的测试专用导入路径可以保留；它不等于恢复业务启动对旧仓库 `src` 的依赖。

### 6. 验证实际安装与运行，而非只检查元数据

调整现有环境配置、算法独立导入、JWT 子进程和 Uvicorn 子进程测试，移除对旧源码布局的假设。保留其原有配置优先级、安全边界、令牌和 HTTP 日志行为断言，不编写只检查 TOML 文本或文件搬迁的新测试。

实施完成后集中执行现有受影响测试及全套验证，并完成以下运行证据：

1. 用临时 `UV_PROJECT_ENVIRONMENT` 从同一锁文件同步业务成员且不安装开发依赖，实际启动 ASGI 并请求 API 文档或 OpenAPI 路径，验证无需旧 `src` 或路径注入。
2. 在另一个临时环境中仅同步计算成员且使用 `--no-dev`；清除 `PYTHONPATH`，从仓库外工作目录通过安装后的包运行附带 PPG 样本，结果为 10 个样本、0.9 秒和 10 Hz，同时确认 Django 未安装、业务包未导入。
3. 在隔离的验证数据库、消息命名空间和数据目录中运行现有示例提交流程及消费者 `--once`，核对任务成功和诊断结果，不对真实队列执行恢复扫描。
4. 构建两个成员的分发产物，核对普通包及 Django 迁移均被打包，避免 editable 安装隐藏遗漏。

临时环境仅用于证明成员依赖闭包，不改变默认共享虚拟环境的工作方式。验证后删除本次临时脚本和构建产物。

## Risks / Trade-offs

- [共享环境掩盖遗漏声明] → 源码直接依赖归属核对，加上关闭开发组的独立计算成员安装验证；不声称 workspace 提供 import 沙箱。
- [目录层级改变导致误读 `.env` 或生成新的数据目录] → 保持仓库根定位，沿用并调整已有配置隔离测试，覆盖外部工作目录和相对目录配置。
- [业务 wheel 漏掉 `config` 或迁移] → 使用显式多模块构建配置，构建产物验证不只依赖 editable 安装。
- [只运行根项目时没有安装成员] → 文档明确使用 `--all-packages` 或 `--package health-service`，不继续使用未指定成员的旧安装约定。
- [算法和业务将来需要冲突版本] → workspace 仍要求联合兼容；届时另行决策，不将此次拆分描述为独立解析或独立部署。

## Migration Plan

1. 在同一次实现中迁移源码、声明成员及依赖、调整入口和根目录定位，再更新单一锁文件；不保留新旧双路径。
2. 调整受影响测试并完成安装、实际 HTTP、示例消费和构建验证，通过后更新 `README.md` 及 `docs` 下的开发和架构说明。
3. 部署时停止旧 Web 和消费者，在同一代码版本上按新命令同步依赖并启动；数据库和任务数据不做迁移或清理。继续遵守现有任务租约恢复及同版本部署约束。
4. 回滚代码、项目声明、锁文件和启动命令为同一旧版本，再同步旧依赖；保留数据库、Redis 状态、`.env` 和全部数据目录，不执行破坏性反向迁移。
