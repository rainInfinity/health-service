# Tasks

## 1. 工作区迁移与运行契约

- [x] 1.1 将根 `pyproject.toml` 改为无业务项目声明的 workspace 根，显式列出两个成员并保留公共开发工具和检查配置；为两个成员增加 Python 3.13 项目与 `uv_build` 构建配置，业务成员包含 `health_service` 和 `config`；以能够识别两个可安装成员且根目录无第三个业务项目为完成依据。
- [x] 1.2 将现有源码移入对应成员的 `src`，保留导入名及迁移内容；把现有业务运行依赖和直接使用的 `asgiref` 声明在业务成员中，显式声明本地 `health-inference` workspace 依赖，计算成员不新增第三方运行依赖；以两个成员可被同步安装、源码无旧路径副本且现有第三方锁定版本未升级为完成依据。
- [x] 1.3 保留根 `manage.py` 并移除源码路径注入，调整 `config/settings.py` 从新布局定位仓库根，删除 pytest 的旧源码路径配置；以无源码 `PYTHONPATH` 时管理检查能够完成，根 `.env` 和原有相对日志、数据目录语义不变为完成依据。
- [x] 1.4 调整配置隔离、算法独立导入、JWT 和 Uvicorn 子进程测试使用的源码路径或安装约定，保留已有行为断言；补足成员 `.env` 不覆盖仓库 `.env` 的回归场景，不添加元数据文本或文件搬迁断言；由现有行为测试在新布局中通过、临时配置不读取真实本地凭据验证完成。
- [x] 1.5 更新根目录唯一的 `uv.lock`，执行 `uv sync --locked --all-packages`；在完整修改后集中运行 `uv run --locked --all-packages pytest` 和 `uv run --locked --all-packages ruff check .`，以锁定同步成功、现有行为测试及静态检查通过为完成依据。
- [x] 1.6 在临时环境仅同步业务成员和运行依赖，提供隔离配置、不注入源码目录，实际执行管理检查并启动 Uvicorn，通过 HTTP 获取 API 文档或 OpenAPI 响应；以真实进程启动和 HTTP 响应证明已安装成员可运行，而非仅凭测试或配置文本判断。
- [x] 1.7 在另一个临时环境按同一锁文件仅同步计算成员且关闭开发组，清除源码 `PYTHONPATH` 并从仓库外执行附带 PPG 样本；以 Django 和业务成员均未安装、未导入业务包，且输出 `example: true`、10 个样本、0.9 秒、10 Hz 验证独立安装边界。
- [x] 1.8 在上述运行验证后更新 `README.md`、`docs/development.md` 和 `docs/architecture/backend-architecture.md`，说明成员目录、依赖归属、共享锁与环境，以及使用 `--package` 或 `--all-packages` 的命令；以文档命令与实际验证一致、不再包含失效的 `--app-dir src` 或旧源码路径约定为完成依据。

## 2. 跨成员集成验收

- [x] 2.1 在隔离验证数据库、消息命名空间和文件目录中沿现有流程发布示例版本、上传并提交合成样本，运行根管理入口的消费者 `--once`；以任务成功终态、数据库中的持久化诊断为 10 个样本、0.9 秒、10 Hz、上传输入文件位于指定 `TASK_DATA_ROOT` 为完成依据；示例算法不生成结果文件，结果通过 `Task.result_payload` 和 `TaskMetric` 持久化；不得扫描真实队列。
- [x] 2.2 构建两个成员的分发产物，验证计算包只包含其自身模块、业务包包含 `health_service`、`config` 和全部既有 Django 迁移；以产物中模块可被安装识别且迁移身份保持不变为完成依据，避免仅由 editable 安装推断打包正确。
