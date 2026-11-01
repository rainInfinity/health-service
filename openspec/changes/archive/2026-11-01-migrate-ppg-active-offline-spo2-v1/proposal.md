# Proposal

## Why

当前服务已具备正式离线算法流水线和 HR/BR 实现，但尚不能执行旧项目的血氧计算链。旧血氧依赖本地 pyPPG、配套特征清单和 ONNX 权重，需要在保留明确数值规则的同时迁入受控版本、隔离任务状态，并使用提供的测试制品和 PPG 验证真实链路。

## What Changes

- 新增正式算法 `ppg.active.offline.spo2.v1`，接收单块 PPG CSV，不要求健康资料；采用固定窗口 450、步长 75、`apply_diff=false`，保留旧特征链和任务内归一化规则。
- 将旧项目 `libs/pyPPG/` 迁入 `packages/health-inference/src/health_inference/libs/pyPPG/`，供多个算法共享；固定源码校验基线和运行依赖，记录来源、许可核实及必要适配，不以未经确认的上游版本号替代实际源码身份。
- 将单块 RGB/YUV PPG CSV 的通用读取、通道和时间轴校验提取到 `health_inference/utils/ppg_input.py`，由 HR/BR v1 与 SPO2 v1 共用；样本数、采样率等算法特有边界仍由各自 `load_input.py` 校验。
- 使用实际 PPG 质量分数和阈值 70 执行门禁；空预测及非有限预测拒绝，合法窗口预测取最大值，最终截断至 `[90, 100]`。该范围保留旧行为，不构成医学合理性或准确性声明。
- 正式执行使用 ONNX 和两份 JSON 特征清单，不在运行时加载 pickle；保留原特征排列与稳定特征选择顺序。
- 补齐按固定模型发布版本加载和复用 ONNX 及特征资源的入口；所有任务波形、特征矩阵、评分和输出路径保持调用内隔离，现有 HR/BR 调用方同步迁移。
- 成功结果提供 `spo2`、实际质量、窗口预测及位置，投影单位为 `percent`，通过既有受保护链路下载 `processed_ppg`。
- 仅在测试环境使用 `var/old-assets/` 的血氧三件套及 `sample/` 下 PPG：转换清单，经现有管理 API 上传、创建测试版本并运行消费者。忽略 ACC、frame 文件，不作为生产初始化、不自动配置生产渠道。

## Capabilities

### New Capabilities

无；沿用现有推理算法能力边界。

### Modified Capabilities

- `inference-algorithms`：增加血氧 v1 的输入、固定计算、质量、预测校验、聚合、输出及共享 pyPPG 基线要求。

## Impact

- 计算包：新增血氧算法目录、共享 PPG CSV 输入工具和 pyPPG；调整 `types.py`、注册装配及版本资源执行接口，保持 HR/BR v1 的既有语义。
- 服务包：更新 `infrastructure/inference_adapter.py` 的版本资源构建与释放；复用模型目录、上传、发布、任务、租约和结果事务，无新增业务表、任务状态或血氧专用 HTTP API。
- 依赖：在 `health-inference` 可选依赖及工作区 `uv.lock` 中声明、锁定 ONNX Runtime、DotMap 和实际计算所需依赖；Web 和注册路径保持惰性加载。
- 测试：新增 `tests/unit/inference/spo2/` 真实边界覆盖及 `tests/integration/workflows/` 版本隔离与任务验证；测试资料保持本地，不复制到生产种子或公开文档。
- 文档：更新 `docs/development/models-and-algorithms.md`，新增 `docs/development/algorithms/ppg-active-offline-spo2-v1.md` 和 `docs/development/third-party/pyppg.md`；必要接口示例同步到 `docs/frontend-integration.md`。
- 非目标：不迁移历史业务数据，不训练或重训、不改变模型特征公式，不新增多块融合或采集补救，不处理血压和糖尿病制品，不宣称临床认证。
