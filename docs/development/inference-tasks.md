# 推理任务流程

## 推理任务草稿、上传、提交与查询

先使用当前普通渠道登录取得 Access，并查询渠道已配置模型及其 `requiredInputs`。创建请求只提交 `modelId`；账号和来源渠道均从令牌确定，客户端不能指定 `userId` 或任意 `versionId`：

```sh
CHANNEL=ios-cn
API=http://127.0.0.1:8000/api/v1
ACCESS_TOKEN='<该渠道登录响应中的 data.accessToken>'
MODEL_ID='<GET /api/v1/ios-cn/models 返回的 data.items[].modelId>'

curl --include --request POST "$API/$CHANNEL/tasks" \
  --header "Authorization: Bearer $ACCESS_TOKEN" \
  --header 'Content-Type: application/json' \
  --data "{\"modelId\":$MODEL_ID}"
```

创建成功返回 HTTP 201 和 `taskId`、固定模型版本、`status: DRAFT` 及按算法定义派生的 `isExample`；正式 PPG v1 返回 `false`。未配置给当前渠道的模型不能创建草稿；请求额外包含 `userId`、`versionId` 或权限字段会被拒绝。

只可向本人当前渠道的 `DRAFT` 上传算法声明的逻辑输入名。请求使用 multipart 的 `logicalName` 和 `file`；文件完整写入后才登记为当前选择：

```sh
TASK_ID='<创建响应中的 data.taskId>'
PPG_FILE='/本机路径/符合所选 PPG 算法契约的文件.csv'
curl --include --request POST "$API/$CHANNEL/tasks/$TASK_ID/files" \
  --header "Authorization: Bearer $ACCESS_TOKEN" \
  --form 'logicalName=ppg' \
  --form "file=@$PPG_FILE"
```

逻辑名必须来自模型目录返回的算法输入契约；当前正式 HR/BR 与 SPO2 v1 均使用 `ppg`。上传文件应满足对应算法文档中的共享 CSV 格式和专属采样率、长度等条件：[HR/BR v1](algorithms/ppg-active-offline-hr-br-v1.md)、[SPO2 v1](algorithms/ppg-active-offline-spo2-v1.md)。仓库不附带可直接上传的健康数据样例。同名重传只将新记录设为选中，旧记录和文件保留；`SUBMITTED`、`RUNNING` 或终态任务不能上传。HTTP 响应不包含服务器文件路径或存储键。

新输入存储路径按任务创建时间生成，而不是按上传时间：日期目录和文件名前缀使用同一次 `Asia/Shanghai` 转换，格式为 `yyyyMMdd` 和 `yyyyMMdd_HHmmss`。文件名包含 `task_id`、`user_id`、固定算法 key、逻辑名和 UUID；客户端原始文件名不写入路径，只从其最后一个扩展名提取并按算法契约校验。跨日上传仍归入任务创建日期，同名重传使用新 UUID，旧文件继续保留。路径编码、模型制品布局和存量读取规则见[运行环境中的本地文件目录约定](environment.md#新文件目录与命名)。路径字段用于运维辨认归属，不替代数据库权限检查，也不能单独恢复任务的模型参数或资料快照。

最终提交兼容不带请求体的旧请求：

```sh
curl --include --request POST "$API/$CHANNEL/tasks/$TASK_ID/submit" \
  --header "Authorization: Bearer $ACCESS_TOKEN"
```

也可提交可选采集属性，与私有 `clientMetadata` 并列：

```sh
curl --include --request POST "$API/$CHANNEL/tasks/$TASK_ID/submit" \
  --header "Authorization: Bearer $ACCESS_TOKEN" \
  --header 'Content-Type: application/json' \
  --data '{"measurement":{"captureMethod":"fingertip_chest","captureMode":"active","durationMs":30000},"clientMetadata":{"deviceModel":"Example"}}'
```

省略 `measurement`、令其为 `null`，或者整个请求体为 `{}`、仅含 `clientMetadata` 均兼容旧提交；无属性时保持未知。非空 `measurement` 必须完整且仅含三项：`captureMethod` 为 `fingertip` 或 `fingertip_chest`，`captureMode` 仅为 `active`，`durationMs` 是 `1` 至 `9223372036854775807` 的严格整数毫秒，拒绝布尔、字符串、小数及越界值。空采集对象、缺字段和额外字段（包括检测时间、账号、渠道、模型与版本）返回 422。自报属性不证明传感器位置或医学有效性。

三项属性与资料快照、私有元数据、首次 `submittedAt` 及状态在原提交事务内原子冻结，错误时一起回滚；重复或并发提交仅一份可接受，其余返回 409。消息补发、租约恢复和结果写入不改属性及提交时间。`submittedAt` 是唯一产品检测时间，用于健康记录归档，而非原生采集窗口起点；上传和提交延迟会改变归档日期，不接受 `capturedAt`。

本人当前渠道任务详情新增 `measurement`，仅返回上述三项原值；旧任务、草稿及未提供属性的任务返回 `null`。示例：`"measurement":{"captureMethod":"fingertip_chest","captureMode":"active","durationMs":30000}`。详情不返回 `clientMetadata` 或完整资料快照；任务列表及提交响应继续使用原摘要，不增加采集字段。

提交返回 HTTP 200 和 `SUBMITTED` 摘要。服务按“渠道 → 当前版本选择 → 任务 → 用户 → 资料”加锁，检查固定版本仍是当前选择、算法要求的每个输入都已选中且实际文件可读，再冻结当前完整资料和提交日期对应的 `age_years`。缺少资料或出生日期时不创建资料记录，年龄保持 `null`；未知病史键继续缺席，不填默认值。提交阶段只验证逻辑文件齐全和可读，不解析 CSV 或提前执行算法资料校验；算法必需资料在消费者运行时依固定版本契约检查。

任务列表采用创建时间、任务 ID 稳定倒序；`offset` 必须非负，`limit` 为 1–100、默认 20。列表只返回状态、固定版本、`isExample` 和业务时间，不加载完整结果或资料快照：

```sh
curl --include "$API/$CHANNEL/tasks?offset=0&limit=20" \
  --header "Authorization: Bearer $ACCESS_TOKEN"
curl --include "$API/$CHANNEL/tasks/$TASK_ID" \
  --header "Authorization: Bearer $ACCESS_TOKEN"
```

详情对 `SUCCEEDED` 返回数据库保存的完整权威 `result`，不会重新运行算法或从指标行重组结果；对 `FAILED` 只返回安全 `failureCode`、`failureMessage` 和完成时间，不返回 `failure_detail`、资料快照、原始输入或本地绝对路径。只有状态为 `FAILED`、错误码为 `INFERENCE.QUALITY_REJECTED` 且历史明细通过校验时，`qualityFailure` 才包含类型化白名单信息：

```json
{
  "qualityFailure": {
    "reason": "LOW_SCORE",
    "details": {"score": 61.25, "threshold": 70}
  }
}
```

无可用信号不提供评分或阈值：

```json
{
  "qualityFailure": {
    "reason": "NO_VALID_SIGNAL",
    "details": null
  }
}
```

成功任务、普通失败、缺少明细或损坏的历史明细中，`qualityFailure` 为 `null`。客户端应按稳定的 `reason` 值分支，不解析本地化的 `failureMessage`。查询、上传、提交及下载都同时按账号及任务来源渠道授权；跨账号或跨来源渠道的任务返回 404。重复提交及不允许的状态变更返回冲突，任务不能由用户重置或改写固定版本。

### 失败类别与安全文案

失败任务仍由详情返回安全 `failureCode` 和 `failureMessage`；普通失败的 `qualityFailure` 为 `null`，内部 `failure_detail` 不通过接口返回。输入原因不会新增响应字段，服务端只把受控原因转换为固定文案：

| 类别 | `failureCode` | `failureMessage` 与详情 |
|---|---|---|
| 输入错误 | `INFERENCE.INVALID_INPUT` | 按受控原因显示固定文案，例如多块输入为“当前算法版本仅支持单块 PPG”，非递增时间戳为“PPG 时间戳必须为有限且严格递增的毫秒数值”，样本不足为“PPG 数据不足以执行本算法所需处理”；`qualityFailure` 为 `null`。 |
| 质量拒绝 | `INFERENCE.QUALITY_REJECTED` | 显示质量拒绝摘要；合法拒绝另通过 `qualityFailure.reason` 区分，`LOW_SCORE` 保留实际评分与阈值。 |
| 结果无效 | `RESULT.INVALID_OUTPUT` | 显示“推理结果格式无效”；不伪装成输入错误或质量拒绝，`qualityFailure` 为 `null`。 |
| 硬执行超时 | `INFERENCE.EXECUTION_TIMEOUT` | 显示固定安全文案“推理执行超时”；任务为失败终态，服务端不自动重试，只有失败状态提交后才确认消费者消息。 |

旧版通用输入异常和无法识别的原因继续使用“推理输入无效”。客户端按稳定失败码及合法 `qualityFailure.reason` 分支，不解析本地化文案，也不要求服务器返回异常正文或路径。


### 下载当前或历史输入及成功输出

上传响应中的 `fileId` 可用于下载本人任务的当前或历史输入；同名重传不会删除旧文件。成功任务 `result.output_files` 的逻辑名可用于下载处理后文件。两类下载都要求 Access Token 的渠道与路径一致，并同时匹配任务所属账号和来源渠道；管理令牌不能绕过此归属检查。输出仅在任务为 `SUCCEEDED` 且该逻辑名已进入权威结果时可下载。请求不接受存储键或本地路径：

```sh
FILE_ID='<上传响应中的 fileId>'
curl --fail --show-error --output ./input-history.csv \
  "$API/$CHANNEL/tasks/$TASK_ID/files/$FILE_ID/download" \
  --header "Authorization: Bearer $ACCESS_TOKEN"

OUTPUT_NAME='<成功结果 result.output_files 中的逻辑名>'
curl --fail --show-error --output ./result-output.bin \
  "$API/$CHANNEL/tasks/$TASK_ID/outputs/$OUTPUT_NAME/download" \
  --header "Authorization: Bearer $ACCESS_TOKEN"
```

成功下载返回 `FileResponse` 原始文件字节，不使用 JSON 业务包装；令牌渠道与请求路径不一致返回 403，任务账号或来源渠道不匹配、未知文件或逻辑名、缺失物理文件及失败／失租输出返回安全 404，不暴露存储路径。正式目录 `<渠道代码>/<任务创建日期>/outputs/<task_id>/` 是服务端维护的相对目录链接，不是下载授权依据；输出下载会尝试幂等恢复该链接，但仍直接打开数据库结果中的实际 `storage_key`。恢复链接失败但实际文件仍存在时下载继续成功，文件本身缺失时返回安全 404；恢复不重跑算法，旧结果继续按原登记键读取。

