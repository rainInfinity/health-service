# Proposal

## Why

当前任务输入和模型制品统一保存为随机 `.bin`，磁盘路径无法独立辨认用户、渠道、算法或制品版本。需要让任务记录丢失后的人工归属排查更直接，保留实际格式后缀，并让同一任务的结果以简洁稳定路径呈现。

## What Changes

- **BREAKING**：新任务输入采用 `<channel>/<YYYYMMDD>/<YYYYMMDD_HHmmss>-<task_id>-<user_id>-<algorithm_key>-<logical_name>-<UUID>.<ext>`，时间统一取任务创建时间并转换为 `Asia/Shanghai`；同任务跨日上传仍进入创建日期目录。
- 结果采用同一渠道和创建日期下的 `outputs/<task_id>/<算法原结果文件名>`，不在结果名添加 UUID；稳定目录指向最近一次成功提交的完整结果，失败和失租执行不得覆盖它。
- 新模型制品采用 `<model_code>/<version>/<logical_name>-<UUID>.<ext>`，不增加渠道或日期分组；保留现有发布不可变与同版本逻辑名唯一规则。
- 后缀由算法声明的输入／制品格式白名单确定并校验，不采用客户端路径或完整文件名；当前 PPG 为 CSV，血氧规划中的制品为 ONNX 与 JSON。
- 保留执行令牌隔离工作目录；成功结果使用不可变实际文件引用，稳定目录作为受控成功发布入口，处理进程中断造成的发布滞后。
- 存量文件与已登记相对键保持可读，不批量搬迁、重命名或推断格式；新写入只采用新布局。

## Capabilities

### New Capabilities

无；复用已有文件存储和推理运行能力。

### Modified Capabilities

- `local-file-storage`：定义可辨认输入路径、版本制品路径、格式后缀、稳定结果目录与安全边界。
- `inference-runtime`：明确成功提交与稳定结果发布的顺序、执行隔离和发布中断后的恢复。

## Impact

- 服务包：`infrastructure/storage/local.py`、上传应用用例及持久化上下文、`infrastructure/inference_adapter.py`、`application/execution.py`、结果下载读取路径。
- 计算包：为算法输入和制品补充格式元数据；迁移全部注册方，算法仍只向本次 `output_dir` 写文件，不解析业务路径。
- 与活动血氧变更 `migrate-ppg-active-offline-spo2-v1` 共同涉及算法契约和适配器；以实施时现有版本资源工厂为基线衔接，不改动血氧计算规则或该变更工件。
- 不新增公开 HTTP 路由、依赖或配置项，不改变账号／渠道授权；不新增任务重置或重新提交能力。
- 更新 `docs/development/environment.md`、`models-and-algorithms.md`、`inference-tasks.md` 及受影响下载说明，覆盖真实上传、推理、重传和下载验证。
- 非目标：自动恢复丢失任务、完整计算环境备份、多输入采集批次协议、历史数据迁移、自动清理和生产模型发布。文件名可辅助归属定位，但不保证仅凭原始数据复现依赖制品或资料快照的全部结果。
