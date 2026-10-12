# Proposal

## Why

当前服务只读取进程环境变量，本地启动前必须逐项 `export`，缺少一份可复制的配置清单；直接依赖启动命令加载环境文件又容易使管理命令与 ASGI 入口行为不一致。需要在不削弱必填配置和生产密钥要求的前提下，让本地配置更容易建立和维护。

## What Changes

- 启动时从项目根目录读取可选的 `.env`；已有进程环境变量优先，缺少必需配置时继续报错，不自动读取 `.env.template`。
- 提交不含真实密钥的 `.env.template` 作为填写模板，保留 `.env` 不入库；只预填安全的本地配置，必填密钥与密码由使用者填写或通过进程环境变量注入。
- 将 `python-dotenv` 加入 Web 运行依赖并更新锁文件，在统一的 Django 配置入口加载环境文件，保持 `manage.py` 与 ASGI 一致。
- 更新本地开发说明并补充配置来源优先级、独立启动入口及缺失必填配置的验证。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `service-bootstrap`：增加 `.env` 配置来源、进程环境变量覆盖顺序和模板不参与运行的启动契约，同时保留 PostgreSQL 与密钥的失败即停止要求。

## Impact

后续实施涉及 `src/config/settings.py`、`pyproject.toml`、`uv.lock`、仓库根目录 `.env.template`、`docs/development.md` 和配置加载测试。现有数据库模型、迁移及业务 API 不在本次范围内；已忽略的 `.env` 继续不入库。
