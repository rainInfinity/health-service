# Proposal

## Why

`hr_br.example` 只是非临床输入诊断执行器，却作为可发布算法进入生产注册表，并被文档和通用流程测试广泛依赖。正式 PPG v1 已可执行，应移除这个独立的示例算法及其专用样例，避免它继续被发布、运行或误认为产品能力。

## What Changes

- **BREAKING**：取消 `hr_br.example` 注册并删除其实现；该标识不再可创建、发布或执行。
- 删除仅服务于该算法的合成 CSV、README、诊断指标和诊断专用测试。
- 更新活跃用户文档，使用正式 `ppg.active.offline.hr_br.v1` 说明发布与任务流程；不再提供诊断算法或其十行输入样例。
- 将目录、任务和运行时测试中对该算法的通用夹具依赖迁移到测试专用定义；保留正式 PPG v1 的实际计算覆盖。
- 保留通用 `isExample` 契约，正式算法继续返回 `false`。不改写归档 OpenSpec 历史。
- 数据库中的既有版本记录不删除、不迁移；引用已移除标识的版本将无法解析或执行。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `inference-algorithms`：移除示例执行器、专用诊断行为与合成示例数据契约；已移除的 `hr_br.example` 不再是可执行算法。
- `model-catalog`：不再允许发布该示例版本；保留通用的示例身份字段和未知算法拒绝规则。
- `inference-runtime`：单轮消费契约不再依赖或验证“示例任务”，改为处理任意满足已注册算法契约的任务。

## Impact

- 计算包：`health_inference.algorithms` 注册表及 `ppg_diagnostic_example.py`。
- 服务端：算法目录、发布与任务流程仍按通用契约工作；不增加数据库迁移或删除历史版本。
- 测试：更新目前把 `hr_br.example` 当作目录／任务夹具的测试，并删除诊断行为测试；保留正式算法及通用流程验证。
- 文档：模型算法开发指南、任务指南、架构说明、前端联调指南和正式 PPG v1 说明；移除 `docs/examples/hr-br/` 专用样例。
