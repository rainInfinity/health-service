# Proposal

## Why

`health_service` 与 `health_inference` 目前共用根目录的项目声明，依靠共同的 `src` 路径导入，无法从项目元数据区分业务依赖与计算依赖。将两者改为同仓的两个 uv workspace 成员，可以明确依赖归属和安装关系，同时保留统一锁定版本及现有协同部署方式。

## What Changes

- 根目录改为仅用于编排的 workspace 根，业务成员为 `packages/health-service` 与 `packages/health-inference`；各自拥有 `pyproject.toml` 和可安装的 `src` 布局。
- 将 `health_service`、Django `config` 包归入 `health-service` 成员，将 `health_inference` 归入 `health-inference` 成员，保留现有 Python 导入名。
- `health-service` 显式声明对 `health-inference` 的 workspace 依赖；业务运行依赖归前者，计算直接依赖归后者，公共开发工具保留在根目录。
- 保留根目录唯一的 `uv.lock`、默认共享 `.venv`，以及 Python 3.13 基线；不引入独立锁文件、Git submodule 或新计算依赖。
- **BREAKING**：移除旧 `src` 布局及其路径注入约定；启动、测试和开发命令改用已安装的 workspace 成员，旧 `--app-dir src` 不再适用。
- 保留根目录 `manage.py`、`.env`、日志和数据目录的使用边界，同步调整设置定位、受影响测试及开发文档；不改变 API、数据库结构、算法含义或消费者处理语义。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `service-bootstrap`：增加双成员 workspace 的依赖归属与按成员安装契约，明确通过可安装成员启动 Web、单独运行计算包，以及目录迁移后仓库级环境配置和相对目录的定位规则。

## Impact

- 项目配置：根目录 `pyproject.toml`、`uv.lock`，以及两个成员的项目和构建配置。
- 源码位置与入口：现有 `src/health_service`、`src/health_inference`、`src/config`、根目录 `manage.py`，其中业务代码只做迁移及必要的路径适配。
- 验证：调整引用旧源码路径的配置隔离、算法独立导入、JWT 和 Uvicorn 子进程测试；保留根目录的测试领域组织。
- 文档：更新 `README.md`、`docs/development.md` 和 `docs/architecture/backend-architecture.md` 的布局、安装与运行说明。
- 部署：Web 与消费者继续使用同版本代码、同一数据库和 Redis，以及共享可达的数据目录；消费者仍为业务成员中的 Django 管理命令。
