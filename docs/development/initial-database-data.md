# 初始数据库数据维护

本文记录业务数据库首次可用时需要维护的渠道与正式模型目录。数据库结构仍由 Django migrations 管理；本文是初始数据清单和人工维护流程，不提供自动写库命令、SQL 或脚本。

## 算法注册与数据库目录

算法执行器由 `health-inference` 代码显式注册，不存在独立的算法数据库表。当前正式算法包括 `ppg.active.offline.hr_br.v1` 和 `ppg.active.offline.spo2.v1`；本文的初始数据库目录仍只配置 HR/BR 模型，血氧测试制品不会自动进入初始数据或生产配置。数据库中的 `ModelVersion.algorithm_key` 引用完整算法标识；未在当前代码注册的规划中算法不能提前写入模型目录或发布。

渠道、模型、模型版本、指标定义、版本指标关系及渠道选择分别保存在 `health_channel`、`health_model`、`health_model_version`、`health_metric_definition`、`health_model_version_metric` 和 `health_channel_model_selection`。

## 初始渠道

首次建立目录时，维护以下六个渠道：

| 渠道标识 | 类型 | 初始模型选择 |
|---|---|---|
| `android-cn` | 普通渠道 | `ppg.active.offline.hr_br` / `1.0.0` |
| `android-intl` | 普通渠道 | `ppg.active.offline.hr_br` / `1.0.0` |
| `ios-cn` | 普通渠道 | `ppg.active.offline.hr_br` / `1.0.0` |
| `ios-intl` | 普通渠道 | `ppg.active.offline.hr_br` / `1.0.0` |
| `wxapp` | 普通渠道 | `ppg.active.offline.hr_br` / `1.0.0` |
| `manager` | 管理渠道 | 不配置普通用户模型选择 |

上述模型选择是初始配置，不是持续强制的默认值。管理人员后续可以按渠道切换版本；维护时不得因重新录入初始数据而覆盖已有选择。

## 管理账户

`manager` 渠道记录与管理用户是独立数据。每个环境应建立至少一个受信任的管理账户；通过 Django shell 的 `create_user` 创建，并设置 `is_staff=True`、`is_superuser=False`。密码由 Django 哈希存储。用户名和密码按环境单独管理，不在本文或版本库中记录共享凭据。创建步骤见[账号、资料与令牌](accounts.md)。

## 初始模型版本

| 字段 | 初始值 |
|---|---|
| 模型编码 | `ppg.active.offline.hr_br` |
| 模型名称 | `HR/BR v1` |
| 模型说明 | 使用主动采集的 PPG 信号离线测量心率（HR）和呼吸率（BR），返回两项数值、信号质量评分及处理后的 PPG 与呼吸波（RSP）文件；计算结果不构成医学诊断。 |
| 版本号 | `1.0.0` |
| 算法标识 | `ppg.active.offline.hr_br.v1` |
| 参数 | `null`（该算法不接受非空参数） |
| 必需输入 | `ppg` |
| 必需制品 | 无 |
| 必需健康资料 | 无 |
| 示例算法 | 否，`isExample=false` |

模型编码（`health_model.code`）全局唯一；同一模型只保留一条 `Model` 记录，多个 `ModelVersion` 关联该模型并可引用 `.v1`、`.v2` 等不同算法修订。不要为每个算法修订重复创建相同类别的模型记录。

模型版本发布前，还需将以下完整指标集合关联到该版本：

| 指标编码 | 含义 | 值类型 | 单位 |
|---|---|---|---|
| `hr` | 心率 | `number` | `beats_per_minute` |
| `br` | 呼吸率 | `number` | `breaths_per_minute` |

只有正式算法已在代码中注册、参数与算法契约一致、指标代码和值类型与单位匹配后，版本才能发布；之后应分别为五个普通渠道选择该已发布版本。具体接口、请求字段和发布校验见[模型与算法开发](models-and-algorithms.md)中的模型目录操作说明。

## 维护流程

1. 先按[环境配置与服务运行](environment.md)完成数据库迁移；迁移只建立表结构，不会自动创建上述业务目录数据。
2. 确认当前部署的 `health-inference` 已注册本清单所需的 `ppg.active.offline.hr_br.v1`，且算法声明与初始模型的输入、制品、资料和指标契约一致；其他算法版本按各自注册契约核对。
3. 使用受保护的管理端目录接口创建或核对模型、模型版本和指标；设置版本指标后再发布。
4. 为每个普通渠道设置初始版本。`manager` 仅用于管理认证和管理接口，不配置普通模型选择。
5. 首次配置后，按数据库中的现状维护渠道选择。已发布版本不可原地改写；若计算契约改变，应按算法与模型版本规则发布新配置，不要用重新初始化覆盖历史数据。

该文档描述的是初始期望状态，不代表每个环境的数据库当前状态。每次调整初始模型、版本、指标或默认渠道选择时，同步更新本清单，并遵守[模型与算法开发](models-and-algorithms.md)中的发布约束。
