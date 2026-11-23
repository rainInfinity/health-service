# Spec Delta

## MODIFIED Requirements

### Requirement: 处理后文件仅从成功结果授权引用
系统 MUST 仅为成功任务的完整结果中明确登记的处理后文件提供按逻辑名下载，登记前验证其属于本次执行的受控输出目录；不得接受下载请求指定任意存储键，失败或失去执行权的输出不能成为权威下载内容。场景任务 MUST 只登记成功成员的文件，在 result.output_files 中使用 `<成员名>_<原逻辑名>`；失败成员即使留下物理文件也不得提供引用或下载。场景成员载荷中 MUST NOT 重复保存本地路径或另一套下载引用。

#### Scenario: 下载已登记输出
- **WHEN** 用户请求本人成功任务结果中已经登记的输出逻辑名
- **THEN** 系统返回该结果实际引用的文件，而不是重新执行算法或读取任意请求路径

#### Scenario: 访问未登记或失租输出
- **WHEN** 请求的输出未进入成功结果，或只属于失败／失租执行的目录
- **THEN** 系统拒绝下载，即使物理文件仍存在也不能据此获得访问资格

#### Scenario: 同名成员输出可独立下载
- **WHEN** PPG 场景的 HR/BR、血氧和糖尿病均成功
- **THEN** result.output_files 包含 hr_br_processed_ppg、hr_br_processed_rsp、spo2_processed_ppg、diabetes_processed_ppg，各逻辑名对应各成员本次生成的文件，不相互覆盖

#### Scenario: 失败成员没有下载能力
- **WHEN** diabetes 失败且其他成员成功，客户端请求 diabetes_processed_ppg
- **THEN** 下载按资源不存在处理，场景 result.output_files 不包含该逻辑名，也不回退到其他成员的 PPG

### Requirement: 正式输出保持任务目录与原文件名
新正式结果 MUST 呈现为 `<channel>/<任务创建日期>/outputs/<task_id>/<算法原结果文件名>`；场景结果 MUST 在该任务目录中增加固定成员名子目录，呈现为 `<channel>/<任务创建日期>/outputs/<task_id>/<成员名>/<成员原结果文件名>`。日期与输入采用相同任务创建时间，结果名不附加输入 UUID。正式目录 MUST 只指向最近一次成功提交的完整结果集合，禁止跨根目录和未登记文件下载；失败成员的产物不得混入已发布场景目录。

#### Scenario: HR BR 双文件结果
- **WHEN** 独立 HR/BR 任务成功输出 PPG 与呼吸波形
- **THEN** 同一 outputs/<task_id>/ 下呈现 ppg-processed.csv 和 rsp-processed.csv；跨日完成不改变任务日期目录

#### Scenario: 新成功集合替换旧集合
- **WHEN** 同一任务已有正式发布集合，且出现被运行时合法接受的新成功集合
- **THEN** 正式目录整组切换到新集合，读取不能获得新旧执行混合的文件集合；此规则不新增任务重提或成功任务自动重跑入口

#### Scenario: PPG 场景目录保留各成员原文件
- **WHEN** 三个 PPG 成员均成功并发布正式结果
- **THEN** 同一任务目录中分别存在 hr_br/ppg-processed.csv、hr_br/rsp-processed.csv、spo2/ppg-processed.csv、diabetes/ppg-processed.csv，保留各原算法 CSV 表头、时间轴与数值

#### Scenario: PPG ACC 场景目录
- **WHEN** bp 成员成功并发布正式结果
- **THEN** 同一任务目录中存在 bp/ppg-processed.csv、bp/scg-processed.csv，下载逻辑名分别为 bp_processed_ppg、bp_processed_scg

#### Scenario: 成员先写文件后失败
- **WHEN** 某成员写出临时文件后执行或结果校验失败，其他成员成功且场景即将发布
- **THEN** 失败成员产物不在正式发布目录中；若无法保证该隔离，整个场景不得提交成功或发布目录，不能只删除字典引用后将残留目录一起发布

#### Scenario: 场景全部失败无正式文件
- **WHEN** 场景正常汇总为 ALL_FAILED，成功成员文件集合为空
- **THEN** 不发布正式结果目录，不提供 output_files 引用，成员失败信息仍可从任务详情读取

## ADDED Requirements

### Requirement: 场景子目录不放宽文件安全边界
场景成员文件 MUST 属于同一任务、同一执行令牌的受控目录及该成员子目录，成员名和下载逻辑名 MUST 来自固定场景声明。系统 MUST 拒绝跨成员、跨任务、跨令牌、目录穿越及符号链接逃逸的输出；不得仅因路径位于公共任务根目录内就接受。正式集合发布和恢复 MUST 沿用已有成功事务、账号及来源渠道授权规则。

#### Scenario: 成员返回其他成员文件
- **WHEN** HR/BR 成员将 spo2 子目录文件冒充其 processed_ppg
- **THEN** 输出被拒绝，不能登记为 HR/BR 的成功文件

#### Scenario: 成员输出穿越执行目录
- **WHEN** 成员返回 ../ 路径、其他令牌文件或指向执行目录外的符号链接
- **THEN** 系统拒绝该输出，不保存不可信文件引用，不向普通用户公开本地路径

#### Scenario: 版本与消息重放不重写文件
- **WHEN** 场景结果已提交，随后渠道切换或终态消息重放
- **THEN** 下载仍读取原成功结果登记的不可变文件，只允许恢复其正式发布，不重新执行成员或混用新版本产物
