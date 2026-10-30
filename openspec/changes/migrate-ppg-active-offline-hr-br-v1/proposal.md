# Proposal

## Why

当前服务只有 PPG 输入诊断示例和未实现的 HR/BR 接口模板，不能执行真实的心率、呼吸率计算。需要迁移旧项目的实际计算链，同时统一正式离线算法的执行顺序、质量门禁、后处理和版本隔离，避免各算法自行编排或覆盖历史实现。

## What Changes

- 建立离线算法模板：统一读取与校验输入、预处理、质量检测、原始推理、后处理、结果与文件输出；质量检测和后处理采用策略接口，预处理与推理独立实现。
- 注册正式实现 `ppg.active.offline.hr_br.v1`，目录为 `ppg_active_offline_hr_br_v1/`；实现修订平级并存，不使用无修订号或 `latest` 别名。模型发布版本仍固定引用完整 key、参数、制品和指标。
- 迁移旧版负红通道提取、Butterworth 滤波、NeuroKit 质量评分与 HR/BR 估计；v1 仅接受单块 PPG，不要求模型权重或健康资料。
- 保留有限 BR 的上限 `18`；HR/BR 非有限时报告 `RESULT.INVALID_OUTPUT`，取消旧版 BR 为 NaN 时补 `12` 的行为；HR 不新增限幅。
- 为输入失败增加受控原因及服务端固定文案映射，保留 `INFERENCE.INVALID_INPUT` 错误分类；不透传第三方异常或原始数据。
- 正式执行器接替未实现的 `hr_br` 模板并移除失效模板及相关测试；`hr_br.example` 的 key、身份和诊断行为保持不变，不为其补造流水线阶段。
- 在计算成员声明可选科学计算依赖并更新唯一锁文件；Web 的算法注册不加载可选依赖。
- 开发规范已先更新至 `docs/development/models-and-algorithms.md`。实施时同步补充具体算法说明、运行命令和验证证据，并更新现有架构、前端对接文档中的正式算法边界。

## Capabilities

### New Capabilities

无。复用已有计算与运行时能力边界，不另建近似能力。

### Modified Capabilities

- `inference-algorithms`：增加带实现修订的正式算法身份、统一离线执行模板、策略与阶段隔离；以可执行 HR/BR v1 接替未实现模板，规定单块输入、计算方法、质量门禁和结果后处理。
- `inference-runtime`：输入错误按受控原因生成用户可见的安全文案；保留现有任务状态、执行所有权、质量拒绝和结果原子保存机制。

## Impact

- 计算代码：`packages/health-inference/src/health_inference/` 的模板、策略接口、输入异常表达、显式注册及新增正式算法目录；删除正式执行器接替的旧模板，不改示例契约。
- 业务代码：`health_service.application.execution` 的输入失败白名单映射，以及必要的失败边界调用方；不增加 HTTP 路由、数据库字段或任务状态。
- 依赖：Python 3.13 下的 NumPy、SciPy、NeuroKit2 及其传递依赖，归入 `health-inference` 的可选安装项，共用根 `uv.lock`；不引入 ONNX Runtime、Celery 或额外锁文件。
- 测试与文档：阶段终止、真实数值、后处理边界、输入错误可见性、版本及任务隔离、正式发布与执行、处理后文件下载；算法文档统一放在 `docs/`。
- 不包括实时算法、其他传感器算法、v2 实现、多块选取或融合、重采样或插值、采集端设计、医学准确性认证。有限 BR 被截断属于明确的业务后处理，不应解释为对高呼吸率测量准确性的证明。
