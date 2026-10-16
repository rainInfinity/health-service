# Spec Delta

## ADDED Requirements

### Requirement: JSON 业务回复日期时间统一使用北京时间
系统 MUST 将 JSON 业务回复中具有日期时间类型语义的字段转换到 `Asia/Shanghai`，输出 `YYYY-MM-DD HH:mm:ss`，不带小数秒、`T` 或时区后缀。纯日期 MUST 保留 `YYYY-MM-DD`，空值 MUST 保留 `null`，任意业务字符串 MUST NOT 被识别为时间并改写。展示转换 MUST NOT 改变内部持久化时间或计算精度。

#### Scenario: 不同来源时区表示同一时刻
- **WHEN** 回复字段分别来自 `2026-10-10T11:19:19.987654Z` 和 `2026-10-10T19:19:19.987654+08:00`
- **THEN** 两者均输出 `2026-10-10 19:19:19`，小数秒只在展示时省略，不四舍五入

#### Scenario: 转换后跨越日期边界
- **WHEN** 回复字段的时刻为 `2026-10-10T18:00:00Z`
- **THEN** 对外值为 `2026-10-11 02:00:00`，而不是仅替换 UTC 字符串中的分隔符

#### Scenario: 嵌套列表中的业务时间
- **WHEN** 模型版本、任务分页、任务详情或输入文件元数据包含 `publishedAt`、`createdAt`、`submittedAt`、`startedAt`、`finishedAt` 或 `uploadedAt`
- **THEN** 所有非空日期时间均采用相同的北京时间格式，包括列表中的嵌套对象

#### Scenario: 日期和任意载荷不改写
- **WHEN** 回复包含 `birthDate`、空日期时间及看起来像 ISO 时间的算法结果或扩展资料字符串
- **THEN** 出生日期保持纯日期，空时间保持 `null`，算法结果和扩展资料的字符串内容保持原样

## MODIFIED Requirements

### Requirement: 统一结构的范围和对外契约
系统 MUST 让 JSON API 的声明响应结构与实际回复一致；日期时间响应字段 MUST 声明为采用 `Asia/Shanghai` 的秒级字符串，提供格式约束和示例，MUST NOT 继续声明为 RFC 3339 `date-time`。Django admin、API 文档页面以及非 JSON 的流式或文件响应 MUST NOT 被 JSON 回复包装，文件内容 MUST NOT 因日期时间展示变更而被改写。

#### Scenario: 文档所述结构与实际一致
- **WHEN** 客户端按公布的接口契约调用返回账号数据或校验错误的 JSON API
- **THEN** 两种响应均可按公布的四字段结构解析，不存在文档声明裸数据而实际包裹数据的情况

#### Scenario: 非 JSON 路径不受影响
- **WHEN** 请求 Django admin、API 文档页面或文件下载
- **THEN** 系统不强制将页面或文件内容转换为四字段 JSON 业务回复，不改写下载文件内容

#### Scenario: 日期时间声明匹配实际回复
- **WHEN** 客户端查看包含日期时间响应字段的 OpenAPI 文档
- **THEN** 字段按字符串声明并包含 `YYYY-MM-DD HH:mm:ss` 对应约束、北京时间说明和示例，不再使用 `format: date-time`，纯日期字段仍保留日期声明
