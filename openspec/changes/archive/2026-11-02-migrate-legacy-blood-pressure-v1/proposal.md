# Proposal

## Why

当前项目已支持正式 HR/BR 和血氧离线算法，但尚不能执行旧项目的血压模型。旧血压实现、双模型制品、归一化参数及采集样本已可见，需要将完整计算链接入现有任务体系，同时保留旧算法的数值解释与退化行为。

## What Changes

- 新增正式算法 `ppg_acc.active.offline.bp.v1`，使用 `ppg`、`acc` CSV，输出收缩压 `sbp`、舒张压 `dbp`、实际 PPG 质量信息及处理后的 PPG、SCG 文件。
- 固定迁移旧版颜色转换、滤波、标准化、SCG/AO 重建、峰值配对、PTT、六维特征与双 LinearGAM 预测规则，不新增参数、限幅或医学范围修正。
- 保留旧版资料数值缺失补零、无有效 PTT 配对补零后继续预测等行为；不把 IQR 筛选产生的 NaN 当成无配对或补成零。成功结果仍须满足现有有限数值契约。
- 使用版本固定的 `sbp`、`dbp`、`normalize` 制品及任务提交资料快照，复用离线流水线、质量拒绝、指标投影、版本缓存和受保护下载。
- 补充计算一致性、真实任务链路和异常边界验证，并更新算法开发与接入文档。
- 为独立推理消费者增加独立于租约心跳的全局硬执行时限，默认 300 秒并允许正整数环境配置；超时强制终止执行进程并按安全失败终态处理，不改变 VMD 循环停止条件。

不迁移旧 URL、数据库结构或按输入路径推导输出路径的做法；不改变现有 HR/BR、血氧算法数值解释、存储布局或客户端采集方式；通用消费者硬时限统一约束所有算法执行；不训练新模型，不将运行成功表述为医学准确性认证。

## Capabilities

### New Capabilities

无。沿用现有算法能力边界。

### Modified Capabilities

- `inference-algorithms`：增加血压 v1 的输入、版本制品、资料映射、旧计算规则、质量门禁、退化路径与结果契约。
- `inference-runtime`：增加所有消费者算法执行的全局硬时限、强制终止及安全失败语义。

## Impact

- 计算包：新增 `packages/health-inference/src/health_inference/algorithms/ppg_acc_active_offline_bp_v1/`，更新显式注册和推理可选依赖、工作区锁文件；不改变已有算法解释。
- 服务：复用现有目录发布、资料快照、消费者和文件下载接口；在独立消费者中增加全局硬时限、可强制终止的串行执行进程、`INFERENCE_EXECUTION_TIMEOUT_SECONDS` 配置和 `INFERENCE.EXECUTION_TIMEOUT` 安全失败映射；不新增血压专用 HTTP 路由或数据库表。
- 依赖：以旧声明的 `pygam==0.12.0`、`vmdpy==0.2` 为迁移验证基线，核对其与当前 Python 3.13、NumPy、SciPy 环境的兼容性；计算依赖保持惰性导入。
- 制品：现有本地 `bp-latest-sbp.joblib`、`bp-latest-dbp.joblib`、`bp-latest-normalize.csv` 作为候选迁移来源；存在性已确认，加载兼容性与配套关系尚未验证。joblib 是可执行对象格式，限经核验的受信制品，不能宣称管理上传或扩展名校验消除了反序列化风险。
- 验证和文档：沿用 `tests/unit/inference/`、`tests/integration/workflows/`；实现阶段在 `docs/development/algorithms/` 增加血压说明，并同步模型开发和前端接入说明。
