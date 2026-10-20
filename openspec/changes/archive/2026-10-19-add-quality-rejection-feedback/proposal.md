# Proposal

## Why

当前推理失败只向客户端提供通用安全摘要，无法说明质量拒绝的具体原因；即使算法得到了质量分数和阈值，这些信息也不会保存并公开。后续算法可能依据有效信号、采集条件或其他规则拒绝计算，因此需要以稳定的原因码为核心，而不是强制所有算法返回分数。

## What Changes

- 增加独立于 Django 的质量拒绝契约及专用异常，以受控 `reason` 和对应的类型化 `details` 表达拒绝；首批支持 `LOW_SCORE` 和不要求评分的 `NO_VALID_SIGNAL`。
- 使用稳定错误码 `INFERENCE.QUALITY_REJECTED`，由服务层生成原因对应的固定安全提示，不公开算法异常正文。
- 在现有失败事务中，将合法原因和白名单明细保存到 `failure_detail.quality`；继续使用 `FAILED`，不产生成功结果、指标或权威输出。
- 在本人任务详情中新增可空的 `qualityFailure`，按原因公开允许的明细；不开放任意 `failureDetail`，不让列表加载失败明细。
- 保留执行令牌、租约、消息确认、重复消息、账号及渠道隔离规则；质量拒绝保存为终态后不自动重跑同一输入。
- 补充契约、持久化、消费者与 HTTP 隐私回归验证，并更新开发说明。
- 本次只建立反馈能力，不迁移旧算法，不新增实际采集判定规则，不给 `hr_br.example` 添加虚构评分，不新增配置或通用质检规则引擎。

## Capabilities

### New Capabilities

无。质量拒绝属于现有算法、执行及任务查询能力。

### Modified Capabilities

- `inference-algorithms`：增加原因驱动的质量拒绝契约，约束明细类型、评分有效性和算法异常的边界。
- `inference-runtime`：增加受控质量拒绝的安全映射与原子保存，保持执行所有权和终态后确认规则。
- `inference-tasks`：允许普通用户读取白名单质量拒绝信息，继续禁止公开内部诊断及敏感数据。

## Impact

- 计算包：新增 `packages/health-inference/src/health_inference/quality.py`，修改异常及公开导出；不新增第三方运行依赖。
- 服务包：修改执行失败映射、任务持久化、内部任务数据、详情用例及响应 Schema；现有消费者捕获分支可复用。
- API：任务详情新增 `qualityFailure`；现有字段、HTTP 成功查询语义、列表及下载接口不变。客户端可依据 `reason` 交互，不识别新原因时仍可展示安全 `failureMessage`。
- 数据库：复用已有 `failure_detail`，不增加表、列、状态或迁移；历史任务不补分数、不重解释异常。
- 测试与文档：新增质量契约单元测试，扩展任务结果、消费者及任务 API 集成测试，更新 `docs/development.md`。
- 规格冲突处理：现有 `inference-tasks` 只允许安全失败摘要，本变更通过增量规格明确增加类型化质量信息这一受控例外，而非直接公开内部诊断。错误码沿用数据库设计已有的 `INFERENCE.QUALITY_REJECTED`，不引入另一套 `QUALITY_FAILED`。
