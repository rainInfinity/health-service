# Design

## Context

`src/config/settings.py` 目前直接从 `os.environ` 读取 `DJANGO_SECRET_KEY`、`DJANGO_ALLOWED_HOSTS` 和 PostgreSQL 参数，保留 `DJANGO_DEBUG=false` 与 `POSTGRES_PORT=5432` 两个代码默认值；`manage.py` 与 `src/config/asgi.py` 最终都加载同一份 Django 配置。`docs/development.md` 当前要求启动前在 shell 里逐项 `export`，`.gitignore` 已忽略 `.env`。具体新增行为见本变更的 `service-bootstrap` 规格增量。

## Goals / Non-Goals

**Goals:**

- 在 Django 配置入口提供同一套、与工作目录无关的环境文件读取，保留进程环境变量的最高优先级。
- 保留现有的必填配置、PostgreSQL 后端和代码中已有的安全默认值；提供不含可用密钥的本地填写模板。

**Non-Goals:**

- 不引入 `.env.production`、环境模式切换、集中式配置服务或配置热重载。
- 不改变业务模型约束、数据库迁移或业务 API。

## Decisions

### 在 Django 配置入口加载单一环境文件

将 `python-dotenv` 声明为 Web 运行依赖并更新 `uv.lock`。在 `src/config/settings.py` 读取任何配置变量之前，根据该文件位置确定仓库根目录，对根目录的 `.env` 调用 `load_dotenv(..., override=False)`；文件不存在则不改变环境。继续使用已有的 `_required_env`、布尔解析和 PostgreSQL 配置，不建立第二套配置对象。这样经由 Django 配置启动的管理命令和 ASGI 应用共用相同规则，也不依赖调用方是否使用 `uv run --env-file`；仅使用后者会使直接执行的入口漏读文件。

### 配置优先级与失败行为

优先级为已设置的进程环境变量 > `.env` > `settings.py` 中明确允许的默认值。已有进程变量即便为空也不回退到 `.env`：空的必填项由 `_required_env` 拒绝，避免错误的部署注入被磁盘文件掩盖。`.env` 缺失不单独报错；缺失必填密钥、允许的主机或数据库参数仍按既有规则失败，不回退 SQLite 或通用密钥。选择默认 `override=False` 而非文件覆盖环境，是为了支持容器或部署平台注入凭据。

### 模板只用于生成私有配置

在仓库根目录提交 `.env.template`，列出当前服务需要的配置项；非敏感值可预填与 `docs/development.md` 中本地 PostgreSQL 示例相符的值，`DJANGO_SECRET_KEY` 与 `POSTGRES_PASSWORD` 留空并提示使用者生成或填写。实际 `.env` 继续由现有 `.gitignore` 排除。程序不加载 `.env.template`，避免示例值误成为生产配置；模板的预填值只有在复制到 `.env` 后才参与配置解析。

## Risks / Trade-offs

- 遗留的 shell 变量会覆盖本地 `.env`，导致观察到的值与文件不同 → 文档明确优先级，并用冲突值的独立进程场景验证。
- `.env` 是本地私有文件，不会自动同步到部署环境 → 部署仍可直接注入进程变量；验证不依赖模板和本地文件。
- 模板若填入可用的通用密钥或数据库密码，复制后可能误用于生产 → 密钥和密码在模板中留空，继续依赖启动时必填检查。

## Migration Plan

1. 增加运行依赖并更新锁文件，使干净检出的环境仍可使用 `uv sync --locked`。
2. 加载器、模板及文档一同交付；现有只依赖进程环境变量的部署无需迁移，开发者按文档复制模板并填入本地凭据即可。
3. 发布前验证仅文件、仅进程环境变量、两者冲突、缺少必填值以及不同工作目录下的配置加载，并实际执行 Django 配置检查与 ASGI 启动。回退时保持原有进程变量供应，移除文件加载依赖即可恢复原有启动方式。
