# Proposal

## Why

旧项目已有基于 PPG 和 ONNX 的糖尿病风险概率评估，但依赖旧任务对象、共享可变路径和旧文件管理，不能直接接入当前服务。迁移时需保留数值解释，并将旧客户端风险分档纳入服务端后处理，使概率、分类和历史结果具有统一的版本契约。

## What Changes

- 新增正式算法 `ppg.active.offline.diabetes.v1`，接受单块 RGB 或 YUV PPG CSV 和逻辑名 `diabetes` 的 ONNX 制品，不要求健康资料或客户端元数据，不接受可改变计算规则的参数。
- 保留旧版红通道提取、滤波、标准化、三阶梯度和整段 pyPPG 生物标志物统计链，固定 918 维 float32 特征的语义和排列。
- 使用实际 PPG 质量分数及固定阈值 70，在特征提取、预测和成功输出前执行门禁。
- 成功结果同时提供未舍入概率 `prob` 和整数分类 `risk_level`；分类沿用旧客户端 `Number(prob.toFixed(2))`，低于 0.66 为 0（低风险），低于 0.76 为 1（中风险），其余为 2（高风险）。
- 概率投影使用 `diabetes_probability`，单位 `ratio`；分类保留在完整结果中，不新增分类投影需求。成功结果包含实际质量信息和受保护的 `processed_ppg` 文件。
- 建立旧新数值对照与实际任务链路验收。直接使用 `var/old-assets/diabetes-latest-diabetes.onnx` 作为迁移制品，不核验模型制品来源；保留正类映射、输入输出兼容性和特征缺失值行为检查，不将模型加载成功当作迁移完成。

非目标：血糖浓度、医学确诊、模型重训、概率校准、优化风险阈值、修改其他算法或迁移旧业务数据库。沿用旧分档不代表已完成临床风险分层验证。

## Capabilities

### New Capabilities

无；沿用现有算法能力边界。

### Modified Capabilities

- `inference-algorithms`：增加糖尿病风险 v1 的输入、制品、固定计算规则、质量门禁、概率和三分类后处理及输出契约。

## Impact

- 实现阶段新增 `packages/health-inference/src/health_inference/algorithms/ppg_active_offline_diabetes_v1/` 并更新受控注册入口，复用现有流水线、版本资源及共享 PPG 输入解析。
- 复用 ONNX Runtime、NeuroKit2、SciPy 和迁入的 pyPPG；不得静默改变既有血氧等算法的共享依赖和特征解释。
- 复用现有模型发布、任务提交与消费、指标投影和受保护下载，不新增专用 HTTP 接口或数据库结构。
- 补充行为回归与集成验证；使用说明、迁移基线和证据统一记录在 `docs/development/algorithms/ppg-active-offline-diabetes-v1.md`，必要时同步 `docs/frontend-integration.md`。
- 本变更规划制品保存在 OpenSpec 变更目录；不修改仍在进行的血压迁移变更。
