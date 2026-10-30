# 前端对接指南

本文面向用户端手机 App 和管理端前端开发，说明当前接口的调用顺序、数据约定、页面状态和联调方法。当前用户端为原生 App，管理端可能运行于浏览器。仅描述已实现能力；标注为「前端建议」的内容是客户端实现建议，不是后端提供的新接口或服务承诺。

完整字段和接口调试使用 OpenAPI；业务顺序和跨接口约束使用本文。后端安装、数据库迁移、管理账号建立和推理消费者启动见[本地开发指南](development.md)。

## 1. 接入说明

### 1.1 地址与文档

| 项目 | 测试环境地址或约定 |
|---|---|
| API Base URL | `https://test-backend.myhealthyclub.com` |
| Swagger UI | [测试环境接口文档](https://test-backend.myhealthyclub.com/api/v1/docs) |
| OpenAPI JSON | `https://test-backend.myhealthyclub.com/api/v1/openapi.json` |

开发联调默认使用上述测试环境。需要调试本机后端时，`BASE_URL` 使用 `http://127.0.0.1:8000`，文档入口为 `http://127.0.0.1:8000/api/v1/docs` 和 `http://127.0.0.1:8000/api/v1/openapi.json`。手机真机上的 `127.0.0.1` 指向手机自身，访问开发机服务需改用手机可达的开发机地址。生产环境使用部署方提供的 HTTPS 地址。

`{channel}`、`{task_id}`、`{model_id}` 等是需要替换的路径参数，不是字面字符串。ID 为服务端返回的整数，不能使用示例 ID 假定资源存在。

导入 Apifox 时使用 OpenAPI JSON 地址。Swagger 当前分组为「账号认证」「用户端用户与资料」「用户端模型目录」「用户端推理任务与文件」「管理端模型管理」。认证接口共用渠道参数，管理端登录时将 `channel` 设置为 `manager`。

### 1.2 渠道与账号边界

| 渠道 | 可用入口 | 说明 |
|---|---|---|
| `android-cn`、`android-intl`、`ios-cn`、`ios-intl`、`wxapp` | 注册、登录、刷新、登出及用户业务接口 | 以下统称普通渠道 |
| `manager` | 登录、刷新、登出及管理业务接口 | 不开放管理账号注册；账号由受信任人员建立 |

账号全局共用：在一个普通渠道注册后，可在其他普通渠道使用同一账号登录。健康资料属于账号，不按渠道分别保存；令牌、模型目录和任务访问则有渠道边界。

令牌绑定签发渠道。相同账号在不同渠道登录，应分别管理各渠道的令牌和页面缓存，不能仅更换请求路径而继续使用旧渠道令牌。任务同时属于账号和创建渠道，同一账号在另一个渠道登录也不能读取原渠道任务。

管理业务要求 `manager` Access Token，并逐请求检查账号当前管理权限。普通令牌不能调用管理业务；管理令牌也不能代替普通渠道令牌读取用户任务。

### 1.3 原生 App 与浏览器管理端联调

原生 App 通过系统网络库直接请求测试环境 API，不受浏览器同源策略和 CORS 限制，不需要为此配置开发代理或 CORS。App 仍需通过 HTTPS 证书校验、Token 鉴权和渠道权限校验；不受 CORS 限制不代表可以绕过访问控制。

浏览器管理端是否涉及跨域，取决于页面与 API 的协议、域名和端口是否一致。同源访问不需要配置 CORS；若从本地开发页面或其他域名直接请求测试后端，则需开发服务器代理、同源反向代理或明确的服务端跨域策略。

当前 Django 应用未配置 CORS 中间件，不能假定部署网关已允许浏览器跨域。浏览器管理端本地开发建议通过开发服务器代理 `/api` 到测试后端，部署时优先采用同源反向代理。接口使用 Bearer Token，不依赖 Django Session 登录；Swagger 与 API 同源，Swagger 能调通不代表其他来源的管理页面也能跨域访问。若使用网关 CORS，需要允许 `Authorization`、所用请求方法和请求头；若前端读取响应头，还需按需暴露 `X-Request-ID`、`Content-Disposition`。这些是部署要求，不表示当前应用已经提供对应 CORS 配置。

若 App 内嵌 WebView，网页 JavaScript 直接发出的请求仍需考虑同源策略和 CORS；通过原生桥接交由系统网络库发起的请求，则按原生 App 场景处理。不能仅凭应用安装在手机上就判断请求不受浏览器限制。

## 2. 公共请求与响应约定

### 2.1 请求格式

JSON 请求使用 `Content-Type: application/json`。受保护接口携带：

```http
Authorization: Bearer ACCESS_TOKEN
```

文件上传使用 `multipart/form-data`，字段是 `logicalName` 和 `file`。浏览器发送 `FormData` 时不要手写 `Content-Type`，由浏览器生成带 boundary 的请求头。

注册、登录、刷新和登出不要求 Access Token；刷新和登出在 JSON 请求体中携带 Refresh Token。最终提交任务、发布版本不需要请求体。

请求字段按本文和 OpenAPI 的公开名称发送。未知字段会被拒绝；不要把整份响应对象原样提交给创建或更新接口，也不要自行附加 `userId`、`versionId`、`is_staff` 等非接口字段。

### 2.2 JSON 包装与请求标识

JSON 业务响应统一为：

```json
{
  "code": "OK",
  "message": "成功",
  "data": {
    "userId": 42,
    "username": "frontend-user"
  },
  "requestId": "0123456789abcdef0123456789abcdef"
}
```

- HTTP 200 或 201 且 `code` 为 `OK` 表示请求成功；无业务载荷时 `data` 为 `null`。
- 失败响应仍包含这四个字段，`data` 固定为 `null`。不要期待错误响应带字段级校验详情。
- `requestId` 由服务端生成，与 `X-Request-ID` 响应头一致，客户端传入值不能覆盖它。
- 中文按 UTF-8 返回；不要依赖 `message` 文案做程序分支，使用 HTTP 状态和 `code`。
- 下文标明为「data 示例」的 JSON 仅展示包装内的 `data`，不是完整 HTTP 响应。

下载成功返回文件字节，不使用上述 JSON 包装。Swagger 页面和 OpenAPI 描述文件也不是业务包装结构。

### 2.3 分页、空值与命名

模型目录、任务列表和管理端版本列表使用 `offset`、`limit`：

| 字段 | 约束 |
|---|---|
| `offset` | 从 0 开始，必须为非负整数，默认 0 |
| `limit` | 1–100，默认 20 |
| `data.items` | 当前页数组，可以为空 |
| `data.total` | 符合条件的总条数，不是当前页长度 |
| `data.offset`、`data.limit` | 本次分页参数 |

任务列表按创建时间、任务 ID 稳定倒序；普通模型目录按模型 ID 稳定排序。偏移分页不是快照，翻页期间新建任务可能移动后续页的位置。

公开业务字段使用 `userId`、`createdAt` 等名称；算法 `result` 和扩展对象保留各自契约，不能递归把所有键转成 camelCase。`null` 表示未填写或当前状态无值，不能统一替换为 `0`、`false` 或空字符串。

### 2.4 日期与数值

业务时间 `createdAt`、`submittedAt`、`startedAt`、`finishedAt`、`uploadedAt`、`publishedAt` 均为北京时间 `Asia/Shanghai`，格式是 `YYYY-MM-DD HH:mm:ss`，无时区后缀和小数秒；未发生的时间为 `null`。`birthDate` 使用 `YYYY-MM-DD`。

前端建议直接按约定展示，或使用明确指定时区的日期库解析；不要直接用 `new Date(value)` 猜测这种时间字符串，也不要自动当作 UTC 再加八小时。需要转换为用户本地时间时，先明确按北京时间解释。任意算法结果中的时间字符串不受这个通用格式约定改写。

身高、体重请求可使用 JSON 数字或合法十进制字符串，响应为十进制字符串或 `null`，例如 `"172.50"`。仅在计算或图表需要时显式转为数值，不要把它们声明成只能接收 JSON number。

### 2.5 错误处理

| HTTP | `code` | 前端处理建议 |
|---|---|---|
| 401 | `INVALID_CREDENTIALS` | 登录失败；提示用户名或密码错误，不触发刷新 |
| 401 | `INVALID_TOKEN` | Access 业务请求可进入一次受控刷新；刷新本身失败则要求重新登录 |
| 403 | `CHANNEL_FORBIDDEN` | 检查令牌渠道或管理权限，不循环刷新 |
| 404 | `NOT_FOUND` | 接口或资源不存在，也可能资源不属于当前账号及渠道；不要据此判断其他人的资源是否存在 |
| 405 | `METHOD_NOT_ALLOWED` | 核对请求方法 |
| 409 | `USERNAME_EXISTS` | 注册用户名冲突 |
| 409 | `CONFLICT` | 任务状态、唯一性或发布后修改等冲突；重新读取可查询的资源状态，不盲目重复写入 |
| 422 | `INVALID_INPUT` | 参数、资料格式、文件或模型配置不符合约束；结合当前操作给出提示 |
| 429 | `RATE_LIMITED` | 降低请求频率；响应提供 `Retry-After` 时按它等待 |
| 500 | `INTERNAL_ERROR` | 显示通用失败，保留 `requestId` 供排查，不展示内部异常 |

错误码是统一映射，不表示所有接口都配置了限流。服务端不回显提交值、SQL、堆栈或内部文件路径。提交问题时提供操作、时间、HTTP 状态、错误码及 `requestId`，不要附密码、令牌或健康资料正文。

网络超时和 HTTP 业务错误不同：没有收到响应，不代表写操作没有生效。创建、上传、提交、刷新等操作不能采用统一的自动重试策略，各自规则见后文。

## 3. 登录与会话管理

### 3.1 接口与载荷

| 方法与路径 | 请求 JSON | 成功状态与 data |
|---|---|---|
| `POST /api/v1/{channel}/auth/register` | `username`、`password` | 201；`userId`、`username`，不返回令牌 |
| `POST /api/v1/{channel}/auth/login` | `username`、`password` | 200；`accessToken`、`refreshToken` |
| `POST /api/v1/{channel}/auth/refresh` | `refreshToken` | 200；新的 `accessToken`、`refreshToken` |
| `POST /api/v1/{channel}/auth/logout` | `refreshToken` | 200；`null` |

注册和登录请求示例：

```json
{
  "username": "frontend-user",
  "password": "Example-passphrase-483!"
}
```

用户名长度为 1–150，并接受服务端的用户名规范化和字符校验。注册密码接受 Django 密码校验，包括最小长度、常见密码、纯数字和与账号信息过于相似等检查；只通过前端非空校验不代表可以注册。未知字段返回 422。`manager` 注册入口不开放，返回 404。
注册来源由请求路径中的 `{channel}` 确定（仅限五个普通渠道）；请求体不提供来源字段，公开注册响应也不返回来源渠道。

登录及刷新 data 示例：

```json
{
  "accessToken": "ACCESS_TOKEN",
  "refreshToken": "REFRESH_TOKEN"
}
```

刷新及登出请求示例：

```json
{
  "refreshToken": "LATEST_REFRESH_TOKEN"
}
```

管理账号不存在、密码错误、停用或不具备管理权限时，管理登录均返回同一认证失败，不提供权限探测接口。

### 3.2 令牌轮换与并发处理

**后端保证：**每次成功刷新原子撤销旧 Refresh 并返回新令牌对；旧 Refresh 不可重用。令牌缺失、过期、已撤销或类型不正确返回 401；有效 Refresh 的渠道与请求路径不匹配返回 403。管理刷新还检查当前管理权限。

Access 默认有效期为 7 天，Refresh 默认为 30 天，部署可分别调整；以签发时配置和令牌 `exp` 为准。响应不单独返回 `expiresIn`，前端不要硬编码默认有效期。解码 JWT 可用于安排刷新时机，但不能代替服务端鉴权。

**前端建议：**

1. 按渠道保存令牌；App 使用平台安全存储，网页结合 XSS 风险制定存储策略，不向地址栏、日志或埋点输出令牌。
2. 对需要 Access 的业务请求遇到 `401 / INVALID_TOKEN`，同一会话最多启动一次刷新；其他请求等待同一个刷新结果。
3. 成功后同时替换 Access 和 Refresh，再重发因鉴权失败而未进入业务处理的原请求一次。登录、刷新本身不进入这个拦截循环。
4. 刷新返回明确的认证失败时清理会话并跳转登录。403 按渠道或权限问题处理；网络错误或 5xx 不等价于凭证必然失效。
5. 多标签页如共享会话，也要协调刷新与令牌替换，避免各自使用同一旧 Refresh。

刷新超时时，新令牌可能已经签发而旧 Refresh 已撤销；接口不保证复用旧 Refresh 能找回新令牌。不要无限重试旧 Refresh，无法恢复会话时重新登录。

### 3.3 登出

登出提交当前最新 Refresh。成功后清除本地两个令牌、会话缓存和受保护页面状态，并停止任务查询。

登出只撤销请求中的 Refresh，不撤销已经签发的 Access，也不影响其他独立登录的 Refresh。账号仍可用且渠道匹配时，旧 Access 可继续工作到过期，因此客户端必须主动丢弃它。管理权限撤销后，仍可独立撤销本人有效的管理 Refresh。

若凭证已无效或网络失败，客户端仍应允许清理本地会话，但不能显示为「已确认服务端撤销」。

## 4. 用户信息与健康资料

### 4.1 查询本人账号

`GET /api/v1/{channel}/users/me` 需要与路径匹配的普通渠道 Access。成功返回 200，data 为 `userId`、`username`，不包含手机号、管理权限或完整健康资料。

### 4.2 读取和部分更新资料

| 方法与路径 | 用途 | 成功结果 |
|---|---|---|
| `GET /api/v1/{channel}/users/me/profile` | 读取本人资料 | 200，完整资料；缺失资料按未填写值返回，不因读取创建记录 |
| `PATCH /api/v1/{channel}/users/me/profile` | 只提交要修改的字段 | 200，更新后的完整资料 |

| 字段 | 请求类型与限制 | 响应及业务含义 |
|---|---|---|
| `name` | string 或 null；最多 50 字符 | 姓名，未填写为 null |
| `gender` | 严格整数 0、1、2；不能显式传 null | 0 未知、1 男、2 女 |
| `birthDate` | `YYYY-MM-DD` 或 null | 出生日期，不直接提交年龄 |
| `heightCm` | 正数或十进制字符串，最多 5 位数字、2 位小数；或 null | cm；十进制字符串或 null |
| `weightKg` | 正数或十进制字符串，最多 6 位数字、2 位小数；或 null | kg；十进制字符串或 null |
| `medicalHistory` | string 或 null | 病史文字 |
| `historyJson` | 下述结构化病史局部对象，或 null | 病史对象；未填写可为 null |

PATCH 请求示例：

```json
{
  "name": "示例用户",
  "gender": 0,
  "birthDate": "1990-05-12",
  "heightCm": "172.50",
  "weightKg": "65.25",
  "historyJson": {
    "isDiabetic": false,
    "isHypertensive": null
  }
}
```

**合并与清空规则：**省略字段表示保持原值；可空字段传 `null` 表示清空。`gender` 清空应发送 0，不是 `null`。不要把表单中未编辑的字段批量转换成 `null`。

`historyJson` 按键合并，当前可写键仅 `isDiabetic`、`isHypertensive`：`true` 表示是，`false` 表示明确否，键值 `null` 删除该键。整个 `historyJson` 为 `null` 时清空对象，空对象不表示清空。未知病史键不能通过 PATCH 新增；响应可能保留既有扩展内容，读取时不要递归改名或假定所有键都可写。

例如上述请求会明确记录「无糖尿病」，删除高血压回答；没有提交的病史文字保持原值。「未知」与「否」不能在界面上合并成同一个布尔值。

## 5. 用户端模型目录

`GET /api/v1/{channel}/models?offset=0&limit=20` 使用普通渠道 Access，只返回该渠道已选择的已发布版本。成功 data 示例：

```json
{
  "items": [
    {
      "modelId": 7,
      "modelCode": "hr-br",
      "modelName": "HR/BR 示例",
      "versionId": 12,
      "version": "1.0.0",
      "requiredInputs": ["ppg"],
      "isExample": true
    }
  ],
  "total": 1,
  "offset": 0,
  "limit": 20
}
```

`modelId` 用于创建任务；`versionId` 和 `version` 用于显示当前版本，不能自行指定给创建任务接口。`requiredInputs` 是必须上传的逻辑名，不是文件扩展名；用它组织上传位置，并原样传给 `logicalName`。

目录不返回文件格式、大小上限、必需资料、算法参数、制品要求或完整结果展示定义。具体文件格式和结果解释必须依据已接入算法的约定；不能仅凭逻辑名自动推导任意模型表单。当前示例约定见第 8 节。

`isExample=true` 必须在选择、执行和结果页面明确标为非临床示例，不将诊断指标展示为健康结论。空目录可能是尚未选择发布版本，不应自动回退到其他渠道模型。

## 6. 用户端推理任务与文件

### 6.1 接口顺序与状态

| 方法与路径 | 用途 | 成功状态与 data |
|---|---|---|
| `POST /api/v1/{channel}/tasks` | 创建固定版本草稿 | 201，任务摘要 |
| `POST /api/v1/{channel}/tasks/{task_id}/files` | 上传一个逻辑输入 | 201，输入文件元数据 |
| `POST /api/v1/{channel}/tasks/{task_id}/submit` | 冻结输入和资料，最终提交 | 200，`SUBMITTED` 摘要 |
| `GET /api/v1/{channel}/tasks` | 查询本人当前渠道任务列表 | 200，任务摘要分页 |
| `GET /api/v1/{channel}/tasks/{task_id}` | 查询单个任务详情 | 200，摘要及结果或失败信息 |
| `GET /api/v1/{channel}/tasks/{task_id}/files/{file_id}/download` | 下载当前或历史输入 | 200，文件字节 |
| `GET /api/v1/{channel}/tasks/{task_id}/outputs/{logical_name}/download` | 下载成功结果的输出文件 | 200，文件字节 |

| `status` | 页面含义 | 允许的用户动作与处理建议 |
|---|---|---|
| `DRAFT` | 草稿，尚未冻结资料 | 上传、同名重传、最终提交 |
| `SUBMITTED` | 已提交，等待执行 | 查看状态；禁止编辑输入或重复提交 |
| `RUNNING` | 执行中 | 查看状态；不展示虚构的完成百分比 |
| `SUCCEEDED` | 成功终态 | 展示详情中的 `result`；有输出文件时提供下载 |
| `FAILED` | 失败终态 | 展示安全失败摘要；需要重做时创建新草稿 |

通常从草稿进入已提交、执行中，再到成功或失败。草稿也可能因模型切换直接失败；恢复或执行次数耗尽等场景也可产生失败。不要用前端假设的固定状态顺序拒绝服务端返回的终态；查询间隔内可能跳过中间状态。

当前没有用户取消、删除任务、重置终态、修改固定版本或手动重试原任务的接口。

### 6.2 创建草稿

创建请求仅接收：

```json
{
  "modelId": 7
}
```

`modelId` 必须为正整数，模型必须已配置给当前渠道。服务端从令牌确定用户和渠道，从渠道选择确定已发布版本；请求中不能指定用户、版本、资料快照或算法参数。

任务摘要 data 示例：

```json
{
  "taskId": 101,
  "modelId": 7,
  "modelCode": "hr-br",
  "modelName": "HR/BR 示例",
  "versionId": 12,
  "version": "1.0.0",
  "status": "DRAFT",
  "isExample": true,
  "createdAt": "2026-10-19 10:00:00",
  "submittedAt": null,
  "startedAt": null,
  "finishedAt": null
}
```

创建时固定版本，但不冻结资料。以创建响应中的固定版本为准，不以先前缓存的目录版本替代。未配置模型返回 404，额外字段返回 422。

创建没有客户端幂等键；超时重试可能创建多个草稿。前端应防止重复点击，发生超时后先提示状态不确定并查询列表，不能承诺重试会复用同一个任务。

### 6.3 上传与同名重传

上传请求示例：

```http
POST /api/v1/ios-cn/tasks/101/files
Authorization: Bearer ACCESS_TOKEN
Content-Type: multipart/form-data; boundary=由客户端生成

logicalName=ppg
file=选中的文件二进制
```

两个表单字段都必填。`logicalName` 必须属于模型要求的输入；只允许操作本人当前渠道的 `DRAFT`。文件必须非空且不超过部署上传上限，默认 `52,428,800` 字节（50 MiB）；接口不提供上限查询，部署还可能有代理请求大小限制。

成功 data 示例：

```json
{
  "fileId": 301,
  "logicalName": "ppg",
  "sizeBytes": 188,
  "uploadedAt": "2026-10-19 10:01:00",
  "isSelected": true
}
```

同逻辑名再次成功上传会选择新记录，旧记录和文件仍保留，不能用旧 `fileId` 代表当前选择。上传超时后重传可能新增一条历史记录，不是覆盖同一条记录。

前端保存上传返回的 `fileId`、`logicalName` 和当前选择状态；历史输入下载依赖 `fileId`。目前没有输入文件列表接口，任务详情也不返回已上传文件集合，因此刷新页面后不能仅靠详情重建上传历史或完整恢复上传表单。不要把本地上传记录当作服务端齐全性校验结果。

已提交或终态任务上传返回 409，非法逻辑名、空文件或超过应用大小限制返回 422。代理层拒绝可能不是应用 JSON，应保留非 JSON 错误处理。

### 6.4 最终提交、资料快照与客户端元数据

最终提交兼容不传请求体的旧客户端。新客户端可提供以下 JSON 请求体：

```json
{
  "clientMetadata": {
    "deviceModel": "Pixel 9",
    "brand": "Google",
    "appVersion": "2.4.1",
    "custom": {
      "appBuild": 42
    }
  }
}
```

省略整个请求体、发送 `{}` 或将 `clientMetadata` 设为 `null`，均表示未提供元数据；`clientMetadata: {}` 是有效的空对象。字段必须是 JSON 对象；字符串、数字、数组等非对象值返回 422，任务仍为草稿。

服务端把该对象作为独立任务快照保存，不并入健康资料；元数据用于任务留档和排查，具体算法可按自身契约选择是否消费。缺省元数据不会阻止推理。客户端自报值不能证明设备真实身份；不要提交密码、令牌等敏感信息。普通任务摘要、详情和日志不包含元数据内容，也没有普通用户查询接口。

服务端检查任务仍是草稿、固定版本仍为渠道当前选择、每个必需输入都已选中且物理文件可读，再冻结当前完整健康资料和提交时计算的年龄。

提交不解析 CSV，也不提前执行算法必需资料校验；资料或出生日期缺失可以进入提交阶段，实际算法若依赖这些资料，会在执行时失败。提交成功表示已接受任务，不表示算法成功，也不保证已立即开始执行。

前端应在提交前等待所有上传响应完成；提交成功后禁用文件编辑和提交按钮。此后修改本人资料只影响后续提交的任务，不会改变已有快照。资料快照不通过任务详情返回。

重复提交或版本、状态冲突返回 409；输入不齐全或不可读返回 422。提交超时时先查询该任务：已提交、运行或终态时不要再提交；仍是草稿时再决定是否重试。

### 6.5 列表、详情与轮询

任务列表支持第 2 节分页参数，只返回摘要，不含完整结果或资料快照。详情在摘要基础上增加：

| 字段 | 类型 | 语义 |
|---|---|---|
| `result` | object 或 null | 仅成功任务返回完整权威结果，不能从列表摘要推导 |
| `failureCode` | string 或 null | 失败任务的安全业务失败码 |
| `failureMessage` | string 或 null | 失败摘要，不含内部诊断 |
| `qualityFailure` | 判别联合对象或 null | 仅合法的质量拒绝失败返回；客户端按 `reason` 识别变体 |

合法质量拒绝仍是 `data.status=FAILED`，详情查询仍返回 HTTP 200 和外层 `code=OK`，`result` 为 `null`。`qualityFailure` 仅在失败状态、错误码 `INFERENCE.QUALITY_REJECTED` 和质量明细均通过校验时有值：

以下 JSON 仅展示详情 `data` 中与质量拒绝相关的字段。低分拒绝包含算法本次实际比较的分数和阈值：

```json
{
  "result": null,
  "failureCode": "INFERENCE.QUALITY_REJECTED",
  "qualityFailure": {
    "reason": "LOW_SCORE",
    "details": {"score": 61.25, "threshold": 70}
  }
}
```

无可用信号不包含评分：

```json
{
  "result": null,
  "failureCode": "INFERENCE.QUALITY_REJECTED",
  "qualityFailure": {
    "reason": "NO_VALID_SIGNAL",
    "details": null
  }
}
```

成功任务、普通失败以及缺少或损坏的历史明细中，`qualityFailure` 为 `null`。不要解析本地化的 `failureMessage` 来判断原因；响应不会包含私有 `failureDetail`、资料快照、原始诊断或文件路径。读取失败任务本身仍是 `HTTP 200 / code=OK`，失败体现在 `data.status=FAILED`。不要把任务失败与 HTTP 请求失败混为一谈。

**前端建议：**仅对 `SUBMITTED`、`RUNNING` 的详情启动轮询，例如每 2 秒查询一次，前一次完成后再安排下一次，避免请求重叠。进入终态、退出页面或登出时停止；页面恢复时重新查询。此间隔是建议，不是后端规定。当前没有进度百分比、预计完成时间、WebSocket 或 SSE 通知。

未运行消费者或依赖故障可能使任务长期等待。查询失败或等待过久不能由前端改判为任务失败，也不能因此自动创建重复任务；保留最后确认状态并提示稍后查询。

### 6.6 任务失败码与恢复方式

任务 `failureCode` 与外层 API `code` 是两套语义。

| `failureCode` | 含义与前端处理建议 |
|---|---|
| `TASK.MODEL_SWITCHED` | 渠道已切换版本，旧草稿失效；刷新目录并创建新草稿，回退版本不会复活旧草稿 |
| `INFERENCE.INVALID_INPUT` | 输入内容不符合算法约定；纠正后创建新任务 |
| `INFERENCE.QUALITY_REJECTED` | 算法拒绝本次输入质量；按 `qualityFailure.reason` 区分原因，重新采集后创建新任务，不重试原任务 |
| `INFERENCE.MISSING_PROFILE` | 快照缺少算法必需资料；补充本人资料后创建并提交新任务 |
| `INFERENCE.INVALID_PROFILE` | 资料快照无效；检查资料并联系后端排查 |
| `TASK.RETRY_EXHAUSTED` | 服务端执行次数耗尽；不能通过重复提交原任务恢复 |
| `TASK.INVALID_VERSION`、`TASK.UNKNOWN_ALGORITHM` | 固定版本或执行器不可用；联系管理端或后端 |
| `INFERENCE.INVALID_ARTIFACT`、`INFERENCE.INVALID_PARAMETERS` | 模型制品或参数不可用；由模型维护方处理 |
| `INFERENCE.OUTPUT_UNAVAILABLE`、`INFERENCE.STORAGE_UNAVAILABLE` | 推理文件存储不可用；联系后端 |
| `RESULT.INVALID_OUTPUT` | 算法输出不符合结果契约；联系后端 |
| `TASK.EXECUTION_ERROR`、`INFERENCE.EXECUTION_ERROR` | 安全的通用执行失败；保留任务 ID，联系后端 |

优先展示服务端的安全 `failureMessage`；未知失败码使用通用提示，不阻断失败页面渲染。不要要求服务端返回堆栈或原始健康数据帮助前端定位。

### 6.7 文件下载

输入下载使用上传返回的 `fileId`，可下载当前或历史输入。输出下载仅适用于 `SUCCEEDED`，从 `result.output_files` 取得逻辑名作为路径参数；该对象可能缺席。

`output_files` 的值是服务端保存的不透明文件标识，不是公开 URL，不应展示、解析、拼接服务器路径或作为请求参数。前端只使用键构造输出下载接口，并对逻辑名做 URL 路径编码。当前示例算法不产生输出文件，不能因为没有下载按钮就判定任务不完整。

下载仍需 `Authorization`，普通 `<a href>` 无法自行添加该请求头。浏览器建议使用带鉴权的 fetch 或 HTTP 客户端请求：先检查 HTTP 状态，失败时尝试读取 JSON 错误，成功后再转 Blob 并触发保存，使用后释放 Object URL。不要将令牌加进下载 URL。

服务端返回 `application/octet-stream` 和附件响应头，不保证文件名包含原扩展名；若需要友好名称，由前端依据算法约定确定。任务不属于账号或来源渠道、未知文件、未登记输出、输出尚未成功或物理文件缺失，均返回安全 404；令牌与路径渠道不匹配返回 403。

## 7. 管理端模型管理

### 7.1 接口与最小请求

本节全部接口要求有效的 `manager` Access Token 和当前管理权限。下表中的路径渠道是管理目标，鉴权令牌始终来自 `manager` 登录，不是目标渠道登录。

| 方法与路径 | 最小请求或参数 | 成功状态与 data |
|---|---|---|
| `POST /api/v1/manager/models` | JSON：`code`、`name`；可选 `description` | 201，模型 |
| `GET /api/v1/manager/models/{model_id}/versions` | 查询：`offset`、`limit` | 200，版本分页 |
| `POST /api/v1/manager/models/{model_id}/versions` | JSON：`version`、`algorithmKey`；可选 `params` | 201，未发布版本 |
| `PATCH /api/v1/manager/model-versions/{version_id}` | JSON：要修改的 `algorithmKey`、`params` | 200，版本 |
| `POST /api/v1/manager/model-versions/{version_id}/artifacts` | multipart：`logicalName`、`file` | 201，制品元数据 |
| `POST /api/v1/manager/metrics` | JSON：`code`、`valueType`；可选 `description`、`unit` | 201，指标定义 |
| `PUT /api/v1/manager/model-versions/{version_id}/metrics` | JSON：`metricCodes` 数组 | 200，完整已选指标定义数组 |
| `POST /api/v1/manager/model-versions/{version_id}/publish` | 无请求体 | 200，含 `publishedAt` 的版本 |
| `PUT /api/v1/manager/channels/{channel}/models/{model_id}/selection` | JSON：`versionId` | 200，选中的已发布版本 |

模型创建 data 使用 `modelId`、`code`、`name`、`description`。版本 data 使用 `versionId`、`modelId`、`modelCode`、`modelName`、`version`、`algorithmKey`、`params`、`publishedAt`；`publishedAt=null` 表示未发布。

制品 data 为 `artifactId`、`logicalName`、`sizeBytes`。指标 data 为 `metricId`、`code`、`description`、`valueType`、`unit`。完整字段限制见 OpenAPI。

模型编码全局唯一；同模型的版本号不能重复；指标代码全局唯一。创建冲突返回 409，资源缺失返回 404，无效配置返回 422。接口未提供客户端幂等键，创建响应丢失后不能假定再次提交会返回原对象。

### 7.2 算法、参数和制品

`algorithmKey` 指向后端代码已注册的执行器，不能用任意字符串上传一段算法代码。当前可用于完整联调的是 `hr_br.example`；正式 `hr_br` 只有模板，没有注册执行器，不能发布为可运行版本。

创建版本请求示例：

```json
{
  "version": "1.0.0",
  "algorithmKey": "hr_br.example",
  "params": null
}
```

版本号最多 64 字符，算法标识最多 255 字符。创建版本成功并不代表已通过发布校验。

PATCH 仅可修改未发布版本的算法标识和参数，不用于修改版本号。字段省略时不变；`params=null` 清空参数，`algorithmKey=null` 无效。不要将整份版本响应回传。

制品按算法要求的逻辑名上传，非空且受部署上传上限约束。同版本同逻辑名重复登记返回冲突，不能将任务输入的「同名重传自动选择」规则套用到模型制品。发布时制品逻辑名集合必须与算法声明完全一致，不能随意增加附件。

当前示例不接受固定参数，不需要任何模型制品；保持 `params=null` 或空对象并跳过制品上传。算法要求的参数和制品需要与后端执行器维护方明确，当前没有算法注册表查询接口供前端动态生成管理表单。

### 7.3 指标配置

`valueType` 只允许 `number`、`boolean`，布尔指标不能设置非空 `unit`。数值指标的代码、类型和单位必须与算法声明一致，单位不能自行翻译成展示文案后再提交。

创建指标请求示例：

```json
{
  "code": "example.ppg.sample_count",
  "description": "PPG 样本数",
  "valueType": "number",
  "unit": "samples"
}
```

版本指标配置请求示例：

```json
{
  "metricCodes": ["example.ppg.sample_count"]
}
```

PUT 是替换完整投影集合，不是追加；空数组表示清空，重复代码、未知代码或不匹配的算法语义返回 422。并非必须选择算法的全部指标，投影集合也不决定详情 `result` 的完整内容。

### 7.4 发布与渠道选择

发布检查算法注册、固定参数、制品名称和物理文件、已选指标语义。成功后 `publishedAt` 非空，算法、参数、制品和指标集合不可再原地修改；前端应禁用相应写操作。重复发布返回原版本及首次发布时间，不会重新发布出一个版本。

**发布不等于渠道可用。** 还需为目标渠道选择此模型的一个已发布版本：

```json
{
  "versionId": 12
}
```

选择的版本必须属于路径中的模型且已发布。管理目标可为已知渠道；面向用户发布时选择对应普通渠道。切换到不同版本会使该渠道旧版本全部草稿变为 `FAILED / TASK.MODEL_SWITCHED`，已提交或运行中的任务继续用固定版本；其他渠道不变。选择相同版本不改变草稿，回退版本也不恢复历史失败草稿。

前端建议在切换前明确提示草稿失效影响，并要求操作人确认。完成后使用目标普通渠道账号重新查询模型目录，不能用管理令牌直接替代这一步。

### 7.5 当前管理页面能力边界

目前没有管理端模型总列表、模型编辑或删除、指标列表、算法列表、制品列表或删除、渠道选择读取接口；版本列表必须先知道 `modelId`，也没有单独的版本详情读取接口。不能从 OpenAPI 中不存在的接口推导出完整后台 CRUD。

管理页面需明确已有 ID 和配置数据的来源，例如本次创建响应或受控的联调配置。不要用普通渠道目录冒充管理端全量模型列表，也不要把浏览器缓存作为发布配置的权威来源。完整后台所需的新增查询能力需另行约定，不属于当前对接范围。

## 8. 完整联调示例

### 8.1 环境准备与示例数据

联调前由后端完成数据库迁移、启动 Web 与独立推理消费者，并确保数据库、Redis 和文件存储可用。只启动 Web 可以提交和查询任务，但不能证明推理会完成。环境操作见[本地开发指南](development/environment.md#独立推理消费者运行与部署)。

使用仓库中的合成文件 [ppg.csv](examples/hr-br/ppg.csv)，不使用真实人员健康数据。示例执行器要求：

- UTF-8 CSV，可有 BOM；包含 `timestampFrame`，并且具备完整 RGB 列 `avgR`、`avgG`、`avgB` 或完整 YUV 列 `avgY`、`avgU`、`avgV`，不能同时具备两套完整通道。
- 时间戳单位毫秒，严格递增；至少两条样本，时间戳及选中通道值必须为有限数值，不能缺失、为 NaN 或 Infinity；表头不能重复。
- 输入逻辑名为 `ppg`；无需健康资料、参数或模型权重。

采集协议和正式算法不是同一契约，不能把此示例的数据约束推定为所有后续模型的通用格式。数据生成说明见[合成 PPG 示例数据](examples/hr-br/README.md)。

### 8.2 管理端准备示例版本

以下顺序在 Swagger 或 Apifox 执行，保存上一步返回的 ID；ID 不写死为本文示例值。

| 步骤 | 操作及请求 | 预期与后续使用 |
|---|---|---|
| 1 | 使用既有管理账号在 `manager` 渠道登录 | 保存管理令牌对，业务请求使用其 Access |
| 2 | 创建模型：`{"code":"hr-br","name":"HR/BR 示例","description":"非临床输入诊断"}` | 201，保存 `modelId`；编码已存在时由环境提供既有 ID，不把冲突响应当作创建结果 |
| 3 | 为该模型创建版本，使用第 7.2 节的请求 | 201，保存 `versionId`，`publishedAt` 为 null；同名版本已存在时换新版本号或核对已有版本 |
| 4 | 使用第 7.3 节请求创建样本数指标 | 201；若全局代码已存在，由后端确认其类型和单位一致，不重复创建 |
| 5 | 给版本设置指标集合为 `example.ppg.sample_count` | 200，返回已选指标数组；此步骤发生在发布前 |
| 6 | 不上传制品，发布该版本 | 200，`publishedAt` 非空 |
| 7 | 将 `ios-cn` 渠道该模型选择为返回的 `versionId` | 200；在共享环境操作会影响旧版本草稿，先确认目标环境与影响 |

指标是查询投影，示例只选择样本数也会保留完整算法结果。不要因为未配置另两个投影指标而在前端丢弃结果里的时长和采样率。

### 8.3 用户端完成一次任务

| 步骤 | 操作 | 预期与页面动作 |
|---|---|---|
| 1 | 在 `ios-cn` 注册普通账号，再用相同账号登录 | 注册 201，不含令牌；登录 200，保存普通渠道令牌对 |
| 2 | 查询本人账号和健康资料 | 均为 200；未填写资料显示未知，示例不要求补全 |
| 3 | 查询 `ios-cn` 模型目录 | 找到刚配置的模型，`requiredInputs=["ppg"]`、`isExample=true` |
| 4 | 用返回的 `modelId` 创建草稿 | 201；保存 `taskId` 和固定版本，状态为 `DRAFT` |
| 5 | 上传合成文件，表单 `logicalName=ppg`、`file=ppg.csv` | 201；保存 `fileId`，不可只上传文件名字符串 |
| 6 | 最终提交，不传请求体 | 200，状态为 `SUBMITTED`；停止允许编辑输入 |
| 7 | 查询详情直到终态 | 预期 `SUCCEEDED`，列表响应不用于读取完整结果 |
| 8 | 展示结果，按 `fileId` 下载原输入 | 结果符合下述诊断值；下载为字节，不解析为业务 JSON |

上传这一步可在仓库根目录用 curl 验证；先将变量替换为本次登录和创建返回的实际值：

```sh
BASE_URL=https://test-backend.myhealthyclub.com
CHANNEL=ios-cn
ACCESS_TOKEN='本次普通渠道登录返回的 data.accessToken'
TASK_ID='本次创建返回的 data.taskId'

curl --include --request POST "$BASE_URL/api/v1/$CHANNEL/tasks/$TASK_ID/files" \
  --header "Authorization: Bearer $ACCESS_TOKEN" \
  --form 'logicalName=ppg' \
  --form 'file=@docs/examples/hr-br/ppg.csv'
```

该合成文件的成功详情中，`data.result` 应为：

```json
{
  "example": true,
  "sample_count": 10,
  "duration_seconds": 0.9,
  "sampling_rate_hz": 10.0
}
```

`sample_count` 是样本数，`duration_seconds` 是首末样本的时间差（秒），`sampling_rate_hz` 是按采样间隔计算的平均采样率（Hz）。结果不包含心率、呼吸率、健康评分或临床判断，也没有 `output_files`。

对于后续接入的真实算法，结果页必须按其固定版本契约实现，不能套用上述四个字段，也不能把任意数值自动解释为医学指标。

## 9. 联调验收与常见问题

### 9.1 验收清单

以下是联调时应执行的清单，不代表本文已在当前环境执行全部业务操作。涉及发布或切换时使用可控的联调环境。

- [ ] 普通渠道注册后能登录；注册失败不进入自动刷新；管理渠道不能注册。
- [ ] Access 业务请求缺失或过期时收到 401；普通渠道错用有效令牌收到 403。
- [ ] 一次刷新后同时替换两个令牌，旧 Refresh 不能再次刷新；并发业务请求不会各自发起刷新。
- [ ] 登出后本地令牌、资料缓存和任务查询均清理；界面不承诺旧 Access 已立即撤销。
- [ ] PATCH 未提交字段保持不变，可空字段能清空；`gender=null` 被拒绝，0 表示未知。
- [ ] 病史 `false` 保留为明确否，键值 null 删除键，整体 null 清空对象。
- [ ] 身高、体重按字符串响应处理；时间不因浏览器或宿主机时区被错误转换。
- [ ] 用户目录只展示本渠道可用版本，示例标志清晰；空目录不回退到其他渠道。
- [ ] 草稿能上传和同名重传，保存不同 `fileId`；旧输入仍可下载。
- [ ] 缺少必需输入时提交失败；合法合成文件能走到成功，并返回第 8.3 节结果。
- [ ] 非法 CSV 可上传和提交，但执行后失败；界面区分 API 成功与任务失败。
- [ ] 重复提交及提交后上传被拒绝；超时不盲目创建或提交重复任务。
- [ ] 修改实时资料不会改变已提交任务；补全资料后通过新任务重新提交。
- [ ] 跨账号和跨来源渠道访问任务或文件返回安全 404；管理令牌不能越权代查。
- [ ] 下载失败显示错误，不把 JSON 错误保存成正常结果文件；令牌不进入下载 URL。
- [ ] 查询进入成功或失败终态后停止；等待过久不自行改判失败。
- [ ] 管理端能发布示例并配置渠道；发布后配置不能修改，重复发布不改变首次发布时间。
- [ ] 切换到不同版本会使旧草稿失败，已提交及运行任务仍使用原版本；回退不复活旧草稿。

### 9.2 常见问题

| 现象 | 检查方向 |
|---|---|
| Swagger 能请求，浏览器管理端报跨域 | 核对管理页面与 API 是否同源；跨域时检查开发代理、同源部署或网关 CORS；原生 App 系统网络请求不受 CORS 限制，不通过新增 Cookie 解决 |
| 同一账号换端后找不到任务 | 核对令牌渠道、URL 渠道、任务来源渠道；账号相同不代表跨渠道可读 |
| 多个请求同时认证失败后被迫登录 | 核对是否重复使用同一 Refresh，是否原子替换令牌对，是否存在旧刷新响应覆盖新会话 |
| 提交成功但一直等待 | 确认消费者及依赖运行，由后端排查；不重复提交来催执行 |
| HTTP 200 但任务失败 | 查看 `data.status`、`failureCode`、`failureMessage`，外层 `code` 仅表示查询成功 |
| 刷新页面后上传列表消失 | 当前没有服务端输入列表接口，详情也没有输入集合；不要伪造服务端恢复结果 |
| 模型已发布但用户看不到 | 检查目标普通渠道是否选择了该版本，并使用目标渠道令牌查询 |
| 成功任务没有输出下载入口 | 检查是否存在 `result.output_files`；当前示例只有诊断 JSON，无输出文件 |
| 时间显示偏移八小时 | 检查是否把无后缀北京时间当成 UTC，或使用浏览器隐式解析 |
| 422 没有具体字段错误 | 当前只返回统一安全错误；前端本地校验并结合当前操作提示，附 `requestId` 联调 |

新增页面若依赖当前未提供的接口，应先约定后端契约；不要以客户端缓存、静态假数据或猜测的 URL 代替真实服务能力。
