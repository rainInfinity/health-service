# 前端对接指南

本文面向用户端 App 和管理端前端开发，按接入准备、通用约定、模型目录、算法契约、任务调用及联调验收组织。仅描述已实现能力；标注为「前端建议」的内容是客户端建议，不是后端承诺。

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

导入 Apifox 时使用 OpenAPI JSON 地址。Swagger 当前分组为「账号认证」「用户端用户与资料」「用户端模型目录」「用户端推理任务与文件」「用户端健康记录」「管理端模型管理」。认证接口共用渠道参数，管理端登录时将 `channel` 设置为 `manager`。

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

文件上传使用 `multipart/form-data`，字段是 `logicalName` 和 `file`。`logicalName` 必须是当前算法声明的逻辑名；服务端只从 `file` 的文件名提取最后一个后缀，大小写不敏感地校验算法白名单。当前 PPG 输入要求 `.csv`，不支持或缺失后缀返回 `422 INVALID_INPUT`。文件名、路径和 MIME 不代替算法内容校验。浏览器发送 `FormData` 时不要手写 `Content-Type`，由浏览器生成带 boundary 的请求头。

注册、登录、刷新和登出不要求 Access Token；刷新和登出在 JSON 请求体中携带 Refresh Token。最终提交任务兼容无请求体，也支持第 7.4 节的可选 JSON；发布版本不需要请求体。

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

模型目录、任务列表、健康历史和管理端版本列表使用 `offset`、`limit`：

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
      "modelCode": "<当前渠道目录返回值>",
      "modelName": "<当前渠道目录返回值>",
      "versionId": 12,
      "version": "<当前渠道目录返回值>",
      "requiredInputs": ["ppg"],
      "isExample": false
    }
  ],
  "total": 1,
  "offset": 0,
  "limit": 20
}
```

`modelId`、`modelCode`、`modelName`、`versionId` 和 `version` 均使用当前渠道目录接口返回值，不硬编码示例 ID 或版本。`modelId` 用于创建任务；`versionId` 和 `version` 仅用于显示，不能提交给任务创建接口。`requiredInputs` 是必须上传的逻辑名，不是文件扩展名；用它组织上传位置，并原样传给 `logicalName`。

目录不返回文件格式、大小上限、必需资料、算法参数、制品要求或完整结果展示定义。具体文件格式和结果解释见第 6 节；不能仅凭逻辑名自动推导任意模型表单。

通用 `isExample` 字段按算法定义显示；若值为 `true`，须标明非临床用途。本文五个独立算法和两个场景均为 `false`，不代表临床认证、医学准确性或诊断许可。空目录可能是尚未选择发布版本，不自动回退其他渠道。

## 6. 算法对接

各算法按自己的输入、结果与失败契约调用。第 6.1 节为两个独立 HR、BR，第 6.2–6.4 节为血氧、血压、糖尿病，第 6.5–6.6 节为场景。旧融合及旧 PPG 场景 v1 最终退役，不提供别名；以下为新契约，不是已部署或已验证声明。

### 6.1 独立 PPG 主动离线 HR、BR v1

#### 6.1.1 模型信息与调用入口

| 项目 | HR | BR |
|---|---|---|
| 算法标识 | `ppg.active.offline.hr.v1` | `ppg.active.offline.br.v1` |
| 模型编码 | `ppg.active.offline.hr` | `ppg.active.offline.br` |
| 模型名称与版本 | `HR v1`／`1.0.0` | `BR v1`／`1.0.0` |
| 输入逻辑名 | `ppg`，CSV | 同左 |
| 固定参数 | `null` 或空对象，拒绝非空参数 | 同左 |
| 必需制品、健康资料 | 均无 | 均无 |
| `isExample` | `false` | `false` |
| 唯一指标 | `hr`／`beats_per_minute` | `br`／`breaths_per_minute` |

通过 `GET /api/v1/{channel}/models` 获取当前渠道已发布模型，选择响应中的 `modelId` 创建任务。创建、上传、提交、查询和下载顺序见第 7 节；用户端只消费已发布模型，不负责初始化数据库或创建、发布模型。

#### 6.1.2 输入文件

上传字段为 `logicalName=ppg`，客户端文件名必须带 `.csv` 后缀（大小写不敏感）；此逻辑名和后缀是两个独立契约。文件内容必须是 UTF-8 CSV，可带 BOM。时间戳列 `timestampFrame` 必须为有限、严格递增的毫秒数值；通道列只能提供以下两组中的一组，且该组列必须齐全：

| 格式 | 必需列 |
|---|---|
| RGB | `avgR`、`avgG`、`avgB` |
| YUV | `avgY`、`avgU`、`avgV` |

表头不能重复，行列数必须一致，通道值必须有限。`blockIndex` 可省略；若存在，所有行都必须为整数 `0`。不支持多块输入，也不合并、选取、重采样、插值或补齐数据。采样率还须满足算法滤波条件，样本长度须超过滤波器实际填充长度；这些约束由算法执行时最终校验，上传或提交成功不代表文件一定可执行。仓库不提供可直接上传的样例文件。

#### 6.1.3 成功结果与处理文件

HR 的计算成功载荷只有 `hr` 与 `quality: {score, threshold}`，服务端追加 `output_files.processed_ppg`；BR 的计算成功载荷只有 `br` 与相同结构的实际质量，服务端追加 `output_files.processed_rsp`。两者是独立任务，不读取另一指标，不允许成功指标为 `null`，不包含内部 `failures`。

HR 文件为 `ppg-processed.csv`（`timestamp,ppg`），BR 文件为 `rsp-processed.csv`（`timestamp,rsp`），保留输入毫秒时间轴与逐帧信号。引用值不是 URL 或服务器路径，下载使用对象键。两者同源复用共享输入、预处理、PPG 质量门禁及 CSV 写入；阈值均为 `70`，只在严格低于阈值时质量拒绝。

HR 保留有限原估计；BR 必须先检查原始值有限，再限制到 `[8,20]`，不补 `12`、不舍入。NaN/无穷使对应独立任务失败而不造数；独立 HR 不执行 BR 推理，BR 不以 HR 成功为前提。质量与结果不构成医学诊断。

#### 6.1.4 算法失败处理

| `failureCode` | 前端处理 |
|---|---|
| `INFERENCE.INVALID_INPUT` | 展示安全的 `failureMessage`；例如多块输入提示“当前算法版本仅支持单块 PPG”，时间戳无效提示“PPG 时间戳必须为有限且严格递增的毫秒数值”。修正输入后创建新任务。 |
| `INFERENCE.QUALITY_REJECTED` | 按 `qualityFailure.reason` 展示拒绝原因；低分详情使用服务端返回的 `score` 和 `threshold`。提示重新采集并创建新任务，不重试原任务。 |
| `RESULT.INVALID_OUTPUT` | 结果不符合契约，展示安全失败信息并联系后端；不要当作输入错误或质量拒绝。 |

错误详情只使用公开的 `failureMessage` 和经校验的 `qualityFailure`；不要求或展示内部诊断、原始异常或健康数据。通用任务失败处理见第 7.6 节。

### 6.2 PPG 主动离线血氧 v1（`ppg.active.offline.spo2.v1`）

以下为正式血氧 v1 的前端接入契约。完整算法标识固定为 `ppg.active.offline.spo2.v1`；模型 ID、模型编码、名称和发布版本必须从目标普通渠道的模型目录读取，不能在客户端拼接。

#### 6.2.1 模型信息与调用入口

| 项目 | 当前契约 |
|---|---|
| 算法标识 | `ppg.active.offline.spo2.v1` |
| 模型名称与发布版本 | 以目标渠道目录响应为准，不假定初始模型已配置 |
| 输入逻辑名 | `ppg` |
| 固定参数 | `null`，不接受非空参数 |
| 模型制品 | 服务端版本必须绑定 `spo2`、`feature_names`、`stable_features`；普通用户端不上传制品 |
| 健康资料 | 不需要 |
| `isExample` | `false` |
| 投影指标 | `spo2`：`percent` |

通过 `GET /api/v1/{channel}/models` 读取当前已发布模型，再用返回的 `modelId` 创建任务。成功任务摘要的 `isExample=false` 来自代码注册的算法身份，不需要在 `result` 中重复传递。

模型运行细节、JSON 特征清单格式和数值限制见[血氧 v1 算法开发说明](development/algorithms/ppg-active-offline-spo2-v1.md)。

#### 6.2.2 输入文件

上传 `logicalName=ppg`，文件格式和共享 PPG CSV 规则见第 6.1.2 节：时间戳列为有限、严格递增的毫秒 `timestampFrame`，且只能提供完整 RGB 或 YUV 通道组。血氧额外要求至少 450 帧、采样率大于 `8 Hz`；若提供 `blockIndex`，每行必须是整数 `0`。不支持多块输入或通过插值、补帧满足窗口要求。

#### 6.2.3 成功结果与处理文件

成功任务详情的 `data.result` 示例：

```json
{
  "spo2": 100.0,
  "quality": {"score": 88.5, "threshold": 70},
  "window_predictions": [85.0, 103.0],
  "window_starts": [0, 75],
  "output_files": {"processed_ppg": "<opaque file reference>"}
}
```

| 字段 | 含义 |
|---|---|
| `spo2` | 取窗口预测最大值后限幅到 `[90,100]` 的百分数；对应指标值完全相同，单位 `percent` |
| `quality.score`、`quality.threshold` | 本次实际质量评分和固定门槛；客户端展示服务端返回值，不重算 |
| `window_predictions` | 每个窗口的原始有限模型预测，尚未限幅 |
| `window_starts` | 与预测逐项对应的输入样本起点索引 |
| `output_files.processed_ppg` | 本次处理后 PPG 的不透明下载引用，不是 URL 或服务器路径 |

`processed_ppg` 下载内容为 `timestampFrame,ppg` CSV。低于 70 的质量评分会在特征计算和模型调用前拒绝；一个窗口的归一化输入为零、最大值聚合和 `[90,100]` 限幅是固定模型行为，不表示医学准确性或诊断结论。

#### 6.2.4 算法失败处理

| `failureCode` | 前端处理 |
|---|---|
| `INFERENCE.INVALID_INPUT` | 展示安全的 `failureMessage`；修正通道、时间轴、块布局、样本长度或采样率后创建新任务 |
| `INFERENCE.QUALITY_REJECTED` | 按 `qualityFailure.reason` 处理；`LOW_SCORE` 使用服务端给出的实际 `score` 和 `threshold`，提示重新采集并创建新任务 |
| `INFERENCE.INVALID_ARTIFACT`、`INFERENCE.INVALID_PARAMETERS` | 模型制品或固定参数配置错误，联系模型维护方 |
| `RESULT.INVALID_OUTPUT` | 模型预测或结果不符合契约；展示安全失败信息，不显示血氧值，也不伪装成输入错误或质量拒绝 |

质量拒绝和其他失败均为终态；不要重试原任务，也不要把异常、非法评分或无效预测解释成质量通过或医学结论。

### 6.3 PPG+ACC 主动离线血压 v1（`ppg_acc.active.offline.bp.v1`）

#### 6.3.1 模型信息与调用入口

| 项目 | 当前契约 |
|---|---|
| 算法标识 | `ppg_acc.active.offline.bp.v1` |
| 模型名称与发布版本 | 从目标普通渠道模型目录读取，不在客户端拼接 |
| 输入逻辑名 | `ppg`、`acc`，均为 CSV |
| 固定参数 | `null`，不接受非空参数 |
| 模型制品 | 发布版本绑定 `sbp`、`dbp`（joblib）及 `normalize`（CSV）；普通用户端不上传 |
| 必需健康资料 | 无；可用的 `age_years`、`height_cm`、`weight_kg`、`gender` 使用提交时冻结快照 |
| `isExample` | `false` |
| 投影指标 | `sbp`、`dbp`：`number`，单位 `mmHg` |

通过 `GET /api/v1/{channel}/models` 读取渠道当前已发布模型，再用返回的 `modelId` 创建任务。算法使用固定双传感器处理规则和版本制品，不接受客户端设置滤波、质量门槛或模型特征参数。

#### 6.3.2 输入文件与资料快照

PPG 使用 `logicalName=ppg`，通道组、单块布局和 `timestampFrame` 规则沿用第 6.1.2 节。ACC 使用 `logicalName=acc`，CSV 必须含 `timestampEvent,accX,accY,accZ`；时间戳及必需数值须为有限数值，两路时间轴都须严格递增。服务端分别按各自时间戳计算采样率；数据长度和采样率还须满足滤波处理条件。不会排序、插值、重采样、归零时钟或自动对齐两路时间轴。

任务提交时冻结资料快照；缺少年龄、身高或体重时沿用旧算法的零值路径，`gender` 保留原值。提交后更新个人资料不会改变已提交任务。算法不要求额外客户端元数据。

#### 6.3.3 成功结果与处理文件

成功详情的 `data.result` 结构：

```json
{
  "sbp": 120.25,
  "dbp": 80.5,
  "quality": {"score": 88.5, "threshold": 70},
  "output_files": {
    "processed_ppg": "<opaque file reference>",
    "processed_scg": "<opaque file reference>"
  }
}
```

| 字段 | 含义 |
|---|---|
| `sbp`、`dbp` | 双模型返回的有限原值，不限幅或舍入；与同名指标值一致，单位 `mmHg` |
| `quality.score`、`quality.threshold` | 服务端实际 PPG 质量分数及固定门槛 70；客户端不重算 |
| `output_files.processed_ppg` | 对象键 `processed_ppg` 是下载逻辑名；字段值是不透明文件引用，不传给下载接口 |
| `output_files.processed_scg` | 对象键 `processed_scg` 是下载逻辑名；字段值是不透明文件引用，不传给下载接口 |

`processed_ppg` 下载为 `timestamp,ppg` CSV；`processed_scg` 下载为 `timestamp,scgX,scgY,scgZ` CSV，其中 X/Y 为零，Z 为 AO 重建波形。时间戳为毫秒。前端只使用 `output_files` 的逻辑名对象键作为下载路径参数，不发送其字段值；文件引用不是 URL 或服务器路径。血压结果不代表医学准确性或诊断结论。

#### 6.3.4 失败处理与发布滞后

| `failureCode` | 前端处理 |
|---|---|
| `INFERENCE.INVALID_INPUT` | 展示安全 `failureMessage`；修正 PPG/ACC 列、数值、时间轴或采样条件后创建新任务 |
| `INFERENCE.QUALITY_REJECTED` | 按 `qualityFailure.reason` 展示服务端的实际分数和门槛，重新采集后创建新任务 |
| `INFERENCE.INVALID_ARTIFACT`、`INFERENCE.INVALID_PARAMETERS` | 固定版本制品或参数配置不可用，联系模型维护方 |
| `INFERENCE.EXECUTION_TIMEOUT` | 服务端全局硬时限已到；任务失败且无部分结果，不自动重试原任务，恢复后创建新任务；通用行为见第 7.6 节 |
| `INFERENCE.EXECUTION_ERROR`、`RESULT.INVALID_OUTPUT` | 不展示部分血压或输出；保留任务 ID 并联系后端，不把异常解释为质量通过 |

质量拒绝和其他算法失败均为终态；错误详情只使用公开的 `failureCode`、安全 `failureMessage` 和受校验的 `qualityFailure`，不展示原始异常、文件路径或私有诊断。成功事务已登记结果但正式输出目录链接发布滞后时，任务仍是 `SUCCEEDED`；继续使用结果中 `output_files` 的逻辑名对象键调用标准下载接口，不访问正式目录或重跑推理。服务端按数据库登记的不可变存储键读取文件并幂等恢复发布。其他服务级失败码按第 7.6 节处理。

### 6.4 PPG 主动离线糖尿病风险 v1（`ppg.active.offline.diabetes.v1`）

#### 6.4.1 模型信息与调用入口

| 项目 | 当前契约 |
|---|---|
| 算法标识 | `ppg.active.offline.diabetes.v1` |
| 模型名称与发布版本 | 从目标普通渠道模型目录读取，不在客户端拼接 |
| 输入逻辑名 | `ppg`，CSV |
| 固定参数 | `null`，不接受非空参数 |
| 模型制品 | 发布版本绑定 `diabetes`（ONNX）；普通用户端不上传 |
| 必需健康资料 | 无 |
| `isExample` | `false` |
| 投影指标 | `diabetes_probability`：`number`，单位 `ratio`，值域 `[0,1]` |

通过 `GET /api/v1/{channel}/models` 读取渠道当前已发布模型，再用返回的 `modelId` 创建任务。任务仍按普通草稿、上传、提交和状态查询流程执行；提交时固定模型版本，客户端不传算法参数或风险档位。

#### 6.4.2 输入文件

使用 `logicalName=ppg` 上传 UTF-8 CSV，至少包含严格递增的毫秒时间戳 `timestampFrame`，并且恰好包含一组完整通道：`avgR,avgG,avgB` 或 `avgY,avgU,avgV`。存在 `blockIndex` 时只接受单块 `0`。采样率须大于 `8 Hz`，有效样本数须达到采样率对应的 15 秒最短长度；30 Hz 至少 450 帧。服务端不选块、合并、插值、补齐或重采样。

#### 6.4.3 成功结果与处理文件

成功任务详情的 `data.result` 示例：

```json
{
  "prob": 0.5523077249526978,
  "risk_level": 0,
  "quality": {"score": 88.65866718458382, "threshold": 70},
  "output_files": {"processed_ppg": "<opaque file reference>"}
}
```

| 字段 | 含义 |
|---|---|
| `prob` | 模型类别 1 的原始概率，范围 `[0,1]`；不舍入，值与 `diabetes_probability` 指标完全一致，单位 `ratio` |
| `risk_level` | 按 `Number(prob.toFixed(2))` 兼容规则生成的历史整数档位：`0`、`1`、`2`；不使用 ONNX `label` 生成 |
| `quality.score`、`quality.threshold` | 服务端本次实际质量评分及固定门槛 `70`；客户端不重算 |
| `output_files.processed_ppg` | 本次处理后 PPG 的不透明文件引用，不是 URL 或服务器路径 |

分类先将概率按 JavaScript `toFixed(2)` 语义舍入，再按 `<0.66` 得 `0`、`<0.76` 得 `1`、否则得 `2`。这些整数是兼容旧客户端的编码；未经临床验证，不代表血糖浓度、确诊或医学风险分层。`processed_ppg` 下载内容为 `timestampFrame,ppg` CSV，时间戳为输入毫秒值，PPG 与输入帧逐行对应。下载路径使用逻辑名对象键 `processed_ppg`，不得把该键的字段值当作 URL 或下载路径。

#### 6.4.4 算法失败处理

| `failureCode` | 前端处理 |
|---|---|
| `INFERENCE.INVALID_INPUT` | 展示安全 `failureMessage`；检查通道、时间戳、块布局、最短长度和采样条件后创建新任务 |
| `INFERENCE.QUALITY_REJECTED` | 按 `qualityFailure.reason` 展示服务端实际分数与门槛，提示重新采集并创建新任务 |
| `INFERENCE.INVALID_ARTIFACT`、`INFERENCE.INVALID_PARAMETERS` | 固定版本模型制品或参数契约不可用，联系模型维护方 |
| `INFERENCE.EXECUTION_TIMEOUT` | 服务端硬时限已到；任务失败且无成功载荷或输出，不自动重试原任务 |
| `INFERENCE.EXECUTION_ERROR`、`RESULT.INVALID_OUTPUT` | 不展示部分概率、档位或输出；保留任务 ID 并联系后端，不把异常解释为质量通过 |

质量拒绝和其他失败均为终态。不要展示原始异常、文件路径或私有诊断，也不要把概率或档位转换成血糖值。

### 6.5 统一PPG场景（`ppg.active.offline.all.v2`）

#### 6.5.1 固定成员、输入与版本

| 项目 | 当前契约 |
|---|---|
| 模型配置名称 | `统一PPG场景`；前端展示目标渠道目录实际返回的 `modelName` |
| 算法标识 | `ppg.active.offline.all.v2`；模型编码仍为 `ppg.active.offline.all`，新发布版本 `2.0.0` |
| 固定调用顺序 | `hr` → `br` → `spo2` → `diabetes` |
| 固定成员标识 | `ppg.active.offline.hr.v1`、`ppg.active.offline.br.v1`、`ppg.active.offline.spo2.v1`、`ppg.active.offline.diabetes.v1` |
| 必需输入 | 一份 `ppg` CSV，四成员读取同一原始文件，不上传副本或成员处理文件 |
| 固定参数、必需资料 | 参数为 `null` 或空对象，不接受非空参数；无额外必需资料或客户端元数据 |
| 场景版本制品 | `spo2`（ONNX）、`feature_names`、`stable_features`（JSON）、`diabetes`（ONNX）；普通用户端不上传 |
| 投影指标 | 成功成员实际产生的 `hr`、`br`、`spo2`、`diabetes_probability` 中，发布版本已选定的项；失败不补零 |
| `isExample` | `false`，不表示临床认证 |

仍按第 7 节创建一个场景任务，不增加成员列表或子任务。`.all.v2` 固定四成员及实现修订，制品属于场景发布版本，不读取独立模型的渠道当前版本。独立 HR、BR `1.0.0` 与统一 PPG `2.0.0` 的目标为五普通渠道；实际可用版本以目录响应为准。

PPG CSV 规则见第 6.1.2 节。HR、BR 前置同源复用共享模块，但每成员独立执行其质量与推理，BR 失败不影响 HR。其他成员也分别执行原链，不共享质量结论。采集共同显式下限可参考 `fs > 8 Hz` 且 `N >= max(450, ceil(15 * fs))`，不保证质量或推理成功；不插值、补帧或重采样。

#### 6.5.2 两层状态与成员六字段

查询详情成功仍是 `HTTP 200 / code=OK`。按以下顺序解释详情：

| 层级 | 值与含义 |
|---|---|
| 任务 `data.status` | `SUCCEEDED` 只表示全部成员调用和合法汇总完成，**不等于所有测量成功**；`FAILED` 表示场景自身失败 |
| `data.result.outcome` | 按四成员成功数：4 为 `ALL_SUCCEEDED`、1–3 为 `PARTIALLY_SUCCEEDED`、0 为 `ALL_FAILED` |
| `data.result.results.<成员>.status` | 只可能是 `SUCCEEDED` 或 `FAILED`；完整汇总总是包含全部固定成员，没有缺项或处理中状态 |

`result.results` 中每个成员恰好包含以下六个字段：

| 字段 | 成功成员 | 失败成员 |
|---|---|---|
| `algorithmKey` | 对应成员的完整固定算法标识 | 同左 |
| `status` | `SUCCEEDED` | `FAILED` |
| `result` | 对应独立算法的完整计算载荷，不含 `output_files` | `null` |
| `failureCode` | `null` | 安全失败码，沿用第 7.6 节对应分类 |
| `failureMessage` | `null` | 固定安全摘要，不是异常正文 |
| `qualityFailure` | `null` | 仅合法 `INFERENCE.QUALITY_REJECTED` 有值，否则为 `null` |

成员 `qualityFailure` 只有两种公开形式：`{"reason":"LOW_SCORE","details":{"score":61.25,"threshold":70}}` 或 `{"reason":"NO_VALID_SIGNAL","details":null}`。低分时只展示本次实际分数和阈值；无可用信号时不得补造分数。质量拒绝只终止该成员，不代表其他成员通过或失败；没有场景总 `quality`。不解析 `failureMessage` 推断质量原因，也不要求任意 `failureDetail`。

正常汇总时，即使 `outcome=ALL_FAILED`，任务也是 `SUCCEEDED`、`result` 非空，详情顶层 `failureCode`、`failureMessage`、`qualityFailure` 均为 `null`。全部失败没有指标行或输出文件。列表仍只有摘要，不能从列表的 `SUCCEEDED` 判断测量有效，须读取详情。

成功数值必须从成员载荷读取，不能读 `result.hr` 等单算法路径：

| 成员 | 读取路径 | 保留的原结果 |
|---|---|---|
| `hr` | `result.results.hr.result.hr` | 仅 `hr`、`quality` |
| `br` | `result.results.br.result.br` | 仅 `br`、`quality`，范围 `[8,20]` |
| `spo2` | `result.results.spo2.result.spo2` | `spo2`、`quality`、`window_predictions`、`window_starts`，沿用第 6.2 节 |
| `diabetes` | `result.results.diabetes.result.prob`、`.risk_level` | `prob`、`risk_level`、`quality`，沿用第 6.4 节；不重算档位，不转换为血糖 |

先检查对应成员 `status=SUCCEEDED` 再读取数值。HR、BR 分别成功或失败；BR 失败仍展示本次成功 HR，不能把成功 HR 抹去，也不能展示失败 BR 的默认值或上次值。单个独立算法内部不允许 nullable 指标或 `failures`。

#### 6.5.3 PPG 部分成功与全部失败示例

以下及第 6.6 节 JSON 展示详情 `data` 中完整的状态、结果和失败字段；模型身份、固定版本及时间等摘要字段仍按第 7.2 节返回，不在示例中重复。数值仅用于说明结构，不是某环境的实测结果；不据此假定任何模型或任务 ID 存在。

部分成功：HR 与血氧成功，BR 结果无效，糖尿病风险质量拒绝。两个失败成员均不贡献文件或指标，HR 保留：

```json
{
  "status": "SUCCEEDED",
  "result": {
    "outcome": "PARTIALLY_SUCCEEDED",
    "results": {
      "hr": {
        "algorithmKey": "ppg.active.offline.hr.v1",
        "status": "SUCCEEDED",
        "result": {
          "hr": 79.0,
          "quality": {"score": 88.5, "threshold": 70}
        },
        "failureCode": null,
        "failureMessage": null,
        "qualityFailure": null
      },
      "br": {
        "algorithmKey": "ppg.active.offline.br.v1",
        "status": "FAILED",
        "result": null,
        "failureCode": "RESULT.INVALID_OUTPUT",
        "failureMessage": "推理结果格式无效",
        "qualityFailure": null
      },
      "spo2": {
        "algorithmKey": "ppg.active.offline.spo2.v1",
        "status": "SUCCEEDED",
        "result": {
          "spo2": 98.0,
          "quality": {"score": 88.5, "threshold": 70},
          "window_predictions": [96.0, 98.0],
          "window_starts": [0, 75]
        },
        "failureCode": null,
        "failureMessage": null,
        "qualityFailure": null
      },
      "diabetes": {
        "algorithmKey": "ppg.active.offline.diabetes.v1",
        "status": "FAILED",
        "result": null,
        "failureCode": "INFERENCE.QUALITY_REJECTED",
        "failureMessage": "信号质量评分未达到要求，请重新采集",
        "qualityFailure": {
          "reason": "LOW_SCORE",
          "details": {"score": 61.25, "threshold": 70}
        }
      }
    },
    "output_files": {
      "hr_processed_ppg": "<不透明文件引用>",
      "spo2_processed_ppg": "<不透明文件引用>"
    }
  },
  "failureCode": null,
  "failureMessage": null,
  "qualityFailure": null
}
```

全部成员失败仍是完整合法汇总；各成员可以有不同失败原因，`output_files` **缺席**，不是 `null` 或带默认引用的对象：

```json
{
  "status": "SUCCEEDED",
  "result": {
    "outcome": "ALL_FAILED",
    "results": {
      "hr": {
        "algorithmKey": "ppg.active.offline.hr.v1",
        "status": "FAILED",
        "result": null,
        "failureCode": "INFERENCE.QUALITY_REJECTED",
        "failureMessage": "未检测到可用信号，请重新采集",
        "qualityFailure": {"reason": "NO_VALID_SIGNAL", "details": null}
      },
      "br": {
        "algorithmKey": "ppg.active.offline.br.v1",
        "status": "FAILED",
        "result": null,
        "failureCode": "RESULT.INVALID_OUTPUT",
        "failureMessage": "推理结果格式无效",
        "qualityFailure": null
      },
      "spo2": {
        "algorithmKey": "ppg.active.offline.spo2.v1",
        "status": "FAILED",
        "result": null,
        "failureCode": "INFERENCE.INVALID_ARTIFACT",
        "failureMessage": "模型制品不可用",
        "qualityFailure": null
      },
      "diabetes": {
        "algorithmKey": "ppg.active.offline.diabetes.v1",
        "status": "FAILED",
        "result": null,
        "failureCode": "INFERENCE.EXECUTION_ERROR",
        "failureMessage": "推理执行失败",
        "qualityFailure": null
      }
    }
  },
  "failureCode": null,
  "failureMessage": null,
  "qualityFailure": null
}
```

#### 6.5.4 场景输出与场景自身失败

所有文件只登记在场景顶层 `data.result.output_files`，成员的 `result` 内没有 `output_files`。仅成功成员的键出现：

| 场景 | 成员 | 场景顶层下载逻辑名 |
|---|---|---|
| 统一PPG场景 | `hr` | `hr_processed_ppg` |
| 统一PPG场景 | `br` | `br_processed_rsp` |
| 统一PPG场景 | `spo2` | `spo2_processed_ppg` |
| 统一PPG场景 | `diabetes` | `diabetes_processed_ppg` |
| 统一PPG+ACC场景 | `bp` | `bp_processed_ppg`、`bp_processed_scg` |

使用对象**键**调用 `GET /api/v1/{channel}/tasks/{task_id}/outputs/{logical_name}/download`，例如 `hr_processed_ppg`；字段值是不透明引用，不是路径或 URL。各文件保留独立算法 CSV 表头与数值，不能互换。失败成员键缺席，下载按资源不存在处理；全部失败整个 `output_files` 缺席，不发布正式目录。不得猜目录或回退其他成员文件，账号及来源渠道权限不变。

原始输入在服务准备阶段不可读、场景编排/存储错误、整体硬超时或最终汇总契约错误属于任务级失败，而不是 `ALL_FAILED`。整体硬时限覆盖全部成员，无成员自动重试或独立超时；即使部分成员已计算，也不返回中间汇总、部分指标或权威文件。例如整体超时：

```json
{
  "status": "FAILED",
  "result": null,
  "failureCode": "INFERENCE.EXECUTION_TIMEOUT",
  "failureMessage": "推理执行超时",
  "qualityFailure": null
}
```

最终汇总无效时同样是 `FAILED / result=null`，失败码为 `RESULT.INVALID_OUTPUT`、安全摘要为“推理结果格式无效”。详情查询仍为 `HTTP 200 / code=OK`。任务级失败按第 7.6 节处理，不将其伪装为已完成的成员失败；需要重做时创建新任务。

### 6.6 统一PPG+ACC场景（`ppg_acc.active.offline.all.v1`）

模型配置名称固定为 `统一PPG+ACC场景`，算法标识为 `ppg_acc.active.offline.all.v1`。本修订**只有 `bp` 成员**（`ppg_acc.active.offline.bp.v1`），不会同时执行 HR/BR、血氧或糖尿病风险。必需输入为 `ppg`、`acc` 两份 CSV，文件格式、时间轴和提交时资料快照规则沿用第 6.3.2 节；无额外必需资料或元数据，参数为 `null` 或空对象，拒绝非空参数。场景发布版本必须绑定完整的 `sbp`、`dbp`（joblib）及 `normalize`（CSV），不动态引用独立血压模型的当前版本。`isExample=false` 不表示临床认证。

两层状态、六字段、安全质量拒绝、顶层输出和场景自身失败均遵循第 6.5 节。因为只有一个成员，正常汇总只会是 `ALL_SUCCEEDED` 或 `ALL_FAILED`，不会是 `PARTIALLY_SUCCEEDED`。

`bp` 成功示例：

```json
{
  "status": "SUCCEEDED",
  "result": {
    "outcome": "ALL_SUCCEEDED",
    "results": {
      "bp": {
        "algorithmKey": "ppg_acc.active.offline.bp.v1",
        "status": "SUCCEEDED",
        "result": {
          "sbp": 120.25,
          "dbp": 80.5,
          "quality": {"score": 88.5, "threshold": 70}
        },
        "failureCode": null,
        "failureMessage": null,
        "qualityFailure": null
      }
    },
    "output_files": {
      "bp_processed_ppg": "<不透明文件引用>",
      "bp_processed_scg": "<不透明文件引用>"
    }
  },
  "failureCode": null,
  "failureMessage": null,
  "qualityFailure": null
}
```

仅从 `result.results.bp.result.sbp`、`result.results.bp.result.dbp` 读取血压，单位和数值解释沿用第 6.3.3 节；不读取 `result.sbp`。成功且版本选定时投影 `sbp`、`dbp`；下载使用场景顶层 `bp_processed_ppg`、`bp_processed_scg` 键，内容仍为 `timestamp,ppg` 和 `timestamp,scgX,scgY,scgZ` CSV。

`bp` 质量拒绝、但场景正常完成汇总的示例：

```json
{
  "status": "SUCCEEDED",
  "result": {
    "outcome": "ALL_FAILED",
    "results": {
      "bp": {
        "algorithmKey": "ppg_acc.active.offline.bp.v1",
        "status": "FAILED",
        "result": null,
        "failureCode": "INFERENCE.QUALITY_REJECTED",
        "failureMessage": "信号质量评分未达到要求，请重新采集",
        "qualityFailure": {
          "reason": "LOW_SCORE",
          "details": {"score": 61.25, "threshold": 70}
        }
      }
    }
  },
  "failureCode": null,
  "failureMessage": null,
  "qualityFailure": null
}
```

此时不展示任何血压值，不保存指标或提供输出文件；不能把任务 `SUCCEEDED` 当成血压测量成功。若为场景整体超时等自身错误，则使用第 6.5.4 节的任务级失败结构，不返回上述 `bp` 汇总。

## 7. 用户端推理任务与文件

### 7.1 接口顺序与状态

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
| `SUCCEEDED` | 处理完成终态 | 展示详情中的 `result`；场景还须读取 `outcome` 和成员 `status`，不代表全部测量成功；有输出文件时提供下载 |
| `FAILED` | 失败终态 | 展示安全失败摘要；需要重做时创建新草稿 |

通常从草稿进入已提交、执行中，再到成功或失败。草稿也可能因模型切换直接失败；恢复或执行次数耗尽等场景也可产生失败。不要用前端假设的固定状态顺序拒绝服务端返回的终态；查询间隔内可能跳过中间状态。

当前没有用户取消、删除任务、重置终态、修改固定版本或手动重试原任务的接口。

### 7.2 创建草稿

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
  "modelCode": "ppg.active.offline.hr",
  "modelName": "HR v1",
  "versionId": 12,
  "version": "1.0.0",
  "status": "DRAFT",
  "isExample": false,
  "createdAt": "2026-10-19 10:00:00",
  "submittedAt": null,
  "startedAt": null,
  "finishedAt": null
}
```

创建时固定版本，但不冻结资料。以创建响应中的固定版本为准，不以先前缓存的目录版本替代。未配置模型返回 404，额外字段返回 422。

创建没有客户端幂等键；超时重试可能创建多个草稿。前端应防止重复点击，发生超时后先提示状态不确定并查询列表，不能承诺重试会复用同一个任务。

### 7.3 上传与同名重传

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

### 7.4 最终提交、资料快照与采集属性

最终提交兼容不传请求体的旧客户端。新客户端可提供以下 JSON 请求体：

```json
{
  "measurement": {
    "captureMethod": "fingertip",
    "captureMode": "active",
    "durationMs": 30001
  },
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

`measurement` 与私有 `clientMetadata` 独立；可以省略或为 `null`，不提供时保持未知。非空对象必须同时包含三项：`captureMethod` 只允许 `fingertip`、`fingertip_chest`，`captureMode` 只允许 `active`，`durationMs` 是 `1–9223372036854775807` 的严格整数毫秒，拒绝布尔、字符串和小数。空对象、缺字段、额外键或非法值返回 `422 INVALID_INPUT`；不能传入 `capturedAt`、`submittedAt`、账号、渠道、模型或版本覆盖。

三项在同一提交事务中与资料、元数据及首次 `submittedAt` 一起冻结；回滚时全部回滚，重复或并发提交至多一份成功，后续请求返回 409，后台执行不会改写。采集属性是客户端自报记录，不证明设备身份、身体位置或医学有效性；服务端不从算法、输入文件或目标时长补造缺失属性。

服务端检查任务仍是草稿、固定版本仍为渠道当前选择、每个必需输入都已选中且物理文件可读，再冻结当前完整健康资料和提交时计算的年龄。

提交不解析 CSV，也不提前执行算法必需资料校验；资料或出生日期缺失可以进入提交阶段，实际算法若依赖这些资料，会在执行时失败。提交成功表示已接受任务，不表示算法成功，也不保证已立即开始执行。

前端应在提交前等待所有上传响应完成；提交成功后禁用文件编辑和提交按钮。此后修改本人资料只影响后续提交的任务，不会改变已有快照。资料快照不通过任务详情返回。

重复提交或版本、状态冲突返回 409；输入不齐全或不可读返回 422。提交超时时先查询该任务：已提交、运行或终态时不要再提交；仍是草稿时再决定是否重试。

### 7.5 列表、详情与轮询

任务列表支持第 2 节分页参数，只返回摘要，不含完整结果或资料快照。详情在摘要基础上增加：

| 字段 | 类型 | 语义 |
|---|---|---|
| `result` | object 或 null | 仅成功任务返回完整权威结果，不能从列表摘要推导 |
| `failureCode` | string 或 null | 失败任务的安全业务失败码 |
| `failureMessage` | string 或 null | 失败摘要，不含内部诊断 |
| `qualityFailure` | 判别联合对象或 null | 仅合法的质量拒绝失败返回；客户端按 `reason` 识别变体 |
| `measurement` | object 或 null | 已冻结的 `captureMethod/captureMode/durationMs` 三项；草稿与缺属性的旧任务为 null，摘要和提交响应不含此字段 |

独立算法的合法质量拒绝仍是 `data.status=FAILED`，详情查询仍返回 HTTP 200 和外层 `code=OK`，`result` 为 `null`。以下规则针对详情顶层 `qualityFailure`：仅在任务失败状态、错误码 `INFERENCE.QUALITY_REJECTED` 和质量明细均通过校验时有值。场景成员的同名字段独立遵循第 6.5.2 节，不因任务 `SUCCEEDED` 被抹去。

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

成功任务、普通失败以及缺少或损坏的历史明细中，顶层 `qualityFailure` 为 `null`；场景成功汇总中的失败成员仍可有自己的合法 `qualityFailure`。不要解析本地化的 `failureMessage` 来判断原因；响应不会包含私有 `failureDetail`、资料快照、原始诊断或文件路径。读取失败任务本身仍是 `HTTP 200 / code=OK`，失败体现在 `data.status=FAILED`；场景的成员失败则看 `result.outcome` 和成员 `status`。不要把任务/测量失败与 HTTP 请求失败混为一谈。

**前端建议：**仅对 `SUBMITTED`、`RUNNING` 的详情启动轮询，例如每 2 秒查询一次，前一次完成后再安排下一次，避免请求重叠。进入终态、退出页面或登出时停止；页面恢复时重新查询。此间隔是建议，不是后端规定。当前没有进度百分比、预计完成时间、WebSocket 或 SSE 通知。

未运行消费者或依赖故障可能使任务长期等待。查询失败或等待过久不能由前端改判为任务失败，也不能因此自动创建重复任务；保留最后确认状态并提示稍后查询。

### 7.6 任务失败码与恢复方式

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
| `INFERENCE.EXECUTION_TIMEOUT` | 推理超过服务端硬时限；显示安全失败文案“推理执行超时”，不要自动重试当前任务；保留任务 ID 并联系后端，重新发起时创建新任务。 |

输入错误只通过 `failureCode=INFERENCE.INVALID_INPUT` 和固定 `failureMessage` 表达，不新增原因字段，也不返回内部 `failureDetail`；各正式 PPG 算法的文件要求和具体提示见第 6.1.2、6.1.4 节及第 6.2.2、6.2.4 节，血压 v1 见第 6.3.2、6.3.4 节，糖尿病风险 v1 见第 6.4.2、6.4.4 节。

任务级质量拒绝使用 `INFERENCE.QUALITY_REJECTED`，详情顶层只在白名单校验通过时提供 `qualityFailure`。结果契约错误使用 `RESULT.INVALID_OUTPUT` 和固定文案“推理结果格式无效”，不作为输入问题或质量拒绝呈现。以上**任务级**失败均为 `data.status=FAILED` 且 `result=null`；只有质量拒绝可以有顶层 `qualityFailure`。相同安全分类用于场景成员时，失败留在对应成员内，正常汇总的任务仍是 `SUCCEEDED`，详见第 6.5–6.6 节。优先展示安全 `failureMessage`，未知失败码使用通用提示。

不要要求服务端返回堆栈或原始健康数据帮助定位。

### 7.7 文件下载

输入下载使用上传返回的 `fileId`，可下载当前或历史输入。输出下载仅适用于 `SUCCEEDED`，从 `result.output_files` 取得逻辑名作为路径参数；该对象可能缺席。

`output_files` 值是不透明文件标识，不是 URL，不展示或解析为路径。前端只用对象键构造下载接口并编码路径段。五个独立算法输出见第 6.1–6.4 节；场景仅从顶层读取第 6.5.4 节的带成员前缀键。正式链接发布滞后时仍由服务端按登记键读取并幂等恢复，不自行访问目录或重跑推理。

下载仍需 `Authorization`，普通 `<a href>` 无法自行添加该请求头。浏览器建议使用带鉴权的 fetch 或 HTTP 客户端请求：先检查 HTTP 状态，失败时尝试读取 JSON 错误，成功后再转 Blob 并触发保存，使用后释放 Object URL。不要将令牌加进下载 URL。

服务端返回 `application/octet-stream` 和附件响应头，不保证文件名包含原扩展名；若需要友好名称，由前端依据算法约定确定。任务不属于账号或来源渠道、未知文件、未登记输出、输出尚未成功或物理文件缺失，均返回安全 404；令牌与路径渠道不匹配返回 403。

### 7.8 最新健康结果与指标历史

两个接口复用普通渠道 Bearer 认证、JSON 封套与 `requestId`，同时限定当前账号和令牌匹配的来源渠道。未认证返回 401，令牌与路径渠道不匹配返回 403；同账号在其他渠道的记录也不参与计数或最新选择。

| 方法与路径 | 参数 | data |
|---|---|---|
| `GET /api/v1/{channel}/measurements/latest` | 无 | 固定 `bloodPressure/heartRate/respirationRate/oxygenSaturation/diabetesRisk` 五键，各为一条记录或 null |
| `GET /api/v1/{channel}/measurements` | 必填 `metric/from/to`；可选 `offset/limit` | `items/total/offset/limit` |

历史示例：`GET /api/v1/android-cn/measurements?metric=heartRate&from=2026-11-01&to=2026-11-22&offset=0&limit=20`。`from/to` 严格采用北京时间 `YYYY-MM-DD`，包含首尾日，以服务端保存的提交时刻筛选；分页默认 0/20，limit 为 1–100，不额外限制日期区间天数。缺参数、未知指标、非法日期、反向区间和越界分页返回 `422 INVALID_INPUT`。无结果或偏移越界仍返回 200、空 `items` 和整个筛选范围的有效 `total`。

**产品检测时间统一为 `submittedAt`**：服务端最终接受提交的冻结时刻，不是原生正式采集窗口起点，也不是 `finishedAt` 或 `createdAt`。上传或提交延迟会改变归档日期；此口径替代前端建议中按 `capturedAt` 归档的方案，本接口不新增该字段。响应沿用北京时间秒级格式；内部按完整时间精度、任务 ID 稳定倒序，完成顺序不影响最新选择。

统一记录示例（心率）：

```json
{
  "recordId": "101:heartRate",
  "taskId": 101,
  "metric": "heartRate",
  "submittedAt": "2026-11-22 08:30:00",
  "captureMethod": "fingertip",
  "captureMode": "active",
  "durationMs": 30001,
  "readings": [{"code": "hr", "value": 79, "unit": "beats_per_minute"}],
  "quality": {"score": 88.65866718458382, "threshold": 70},
  "riskLevel": null,
  "modelId": 7,
  "versionId": 12,
  "version": "1.0.0",
  "algorithmKey": "ppg.active.offline.hr.v1"
}
```

| metric | readings code / unit | 来源 |
|---|---|---|
| `bloodPressure` | `sbp`、`dbp` / `mmHg`，同一任务成对 | `ppg_acc.active.offline.bp.v1` 或场景 `bp` 成员 |
| `heartRate` | `hr` / `beats_per_minute` | `ppg.active.offline.hr.v1` 或 PPG v2 `hr` 成员 |
| `respirationRate` | `br` / `breaths_per_minute` | `ppg.active.offline.br.v1` 或 PPG v2 `br` 成员，使用自身质量与成功状态 |
| `oxygenSaturation` | `spo2` / `percent` | `ppg.active.offline.spo2.v1` 或场景 `spo2` 成员 |
| `diabetesRisk` | `diabetes_probability` / `ratio`，取原 `prob` | `ppg.active.offline.diabetes.v1` 或场景 `diabetes` 成员 |

`recordId` 为 `<taskId>:<metric>`，同任务的不同指标身份不同。`modelId/versionId/version` 来自任务固定版本；场景时为场景版本，`algorithmKey` 为实际成功成员算法，不是成员独立模型发布版本，也不随渠道当前选择变化。读数原值不舍入、限幅或重新分级；`quality` 仅保留实际合法 `score/threshold`，缺失或非法为 null。风险的 `riskLevel` 保留原 `risk_level` 的 0/1/2 编码，其余指标为 null；概率不是血糖值或临床患病概率解释。

仅正式独立算法和两个已支持场景的有效成功结果参与查询。任务未成功、场景成员失败、`ALL_FAILED`、示例和未知算法不生成记录，血压缺一项不跨任务拼接；每个指标独立回退到此前最新有效结果。已保存 `submittedAt` 的旧任务即使没有可选指标投影，也从权威 `result` 返回有效读数；旧采集属性均为 null，不回填、不重跑、不改原结果或投影集合。完整报告与波形继续通过关联任务及受权文件接口读取；健康记录不公开资料、私有元数据、内部诊断、原始数据或本地路径。

HR、BR 不再同成同败：场景分别读取 `results.hr.result.hr`、`results.br.result.br`，BR 失败仍产生成功 HR 记录，反之亦然。BR 合法范围为 `[8,20]`，查询不造数；旧融合与旧 PPG 场景 v1 不属于现行支持映射。

**分页消费边界：**这是普通 offset 分页，不返回快照、游标、聚合、趋势或评估结果；total 和页面读取当前数据，不保证 SQL 之间或跨请求集合不变。列表可先显示，但必须成功加载整个筛选区间后才能展示最终均值、极值或趋势。任一页失败应显示未完成并允许重试，不能当空页结束。

已知测量完成产生新有效结果、发现 total 变化或重复 `recordId` 时，丢弃该轮所有已取页面，整段重取，不能只刷新第一页或混用旧页。切换账号、渠道、指标和日期后隔离缓存与请求，迟到响应不得写入新区间。去重不是完整性保证，接口无法自动检测全部并发变化。

未经确认可比性时，按采集方式、采集来源、实际成员算法及固定 `versionId` 保守分组；缺失来源保持未知，不自动与明确来源合并。同名指标不代表跨来源或版本可直接求均值。风险档位不求均值，也不因返回概率就默认开放风险趋势。

SDK 和前端仍需在各自仓库接入 DTO、串联提交及查询、完整区间加载和整段刷新；本后端变更不代表这些客户端已完成接入，也不代表联调环境模型、制品和消费者已部署。未新增记录详情、固定快照、历史聚合或医学评估端点。

## 8. 管理端模型管理

### 8.1 接口与最小请求

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

### 8.2 算法、参数和制品

`algorithmKey` 指向代码受控执行器，不能上传任意算法代码。独立 `.hr.v1`、`.br.v1` 各要求 `ppg`、空参数、零制品与零必需资料，只声明自身指标；SPO2、BP、糖尿病的输入、制品和指标规则仍见各算法章节。五者均为 `isExample=false`，不代表医学认证；未注册或退役 key 不可创建或发布版本，不提供无修订号别名。

统一 PPG 保留模型 `ppg.active.offline.all`，新版本 `2.0.0` 绑定 `ppg.active.offline.all.v2`，一份 PPG 和完整 `spo2/feature_names/stable_features/diabetes` 制品；统一 PPG+ACC `.all.v1` 及 BP 不变。两个场景空参数、无额外必需资料，不因部分成功放宽发布完整性。独立 HR、BR 各 `1.0.0` 与 PPG `2.0.0` 的目标为全部五普通渠道，旧融合和 PPG v1 版本最终退役且无别名。配置见[目录维护](development/initial-database-data.md#统一场景的发布与选择)，不是已部署声明。

创建版本请求：

```json
{
  "version": "1.0.0",
  "algorithmKey": "ppg.active.offline.hr.v1",
  "params": null
}
```

版本号最多 64 字符，算法标识最多 255 字符。创建版本成功并不代表已通过发布校验。

PATCH 仅可修改未发布版本的算法标识和参数，不用于修改版本号。字段省略时不变；`params=null` 清空参数，`algorithmKey=null` 无效。不要将整份版本响应回传。

制品按算法要求的逻辑名上传，非空且受部署上传上限约束。同版本同逻辑名重复登记返回冲突，不能将任务输入的「同名重传自动选择」规则套用到模型制品。发布时制品逻辑名集合必须与算法声明完全一致，不能随意增加附件。

独立 HR、BR 无制品、无必需资料，只接受空参数。SPO2 必须绑定三制品，血压绑定 `sbp/dbp`（joblib）和 `normalize`（CSV），糖尿病绑定 `diabetes` ONNX，规则不变。发布按完整契约校验；血压仍使用冻结资料及既有缺失解释。当前无算法注册表查询接口，不动态猜管理表单。

### 8.3 指标配置

`valueType` 只允许 `number`、`boolean`，布尔指标不能设置非空 `unit`。数值指标的代码、类型和单位必须与算法声明一致，单位不能自行翻译成展示文案后再提交。

HR v1 只声明 `hr`，BR v1 只声明 `br`，各自独立配置：

| `code` | `valueType` | `unit` | 含义 |
|---|---|---|---|
| `hr` | `number` | `beats_per_minute` | 心率 |
| `br` | `number` | `breaths_per_minute` | 呼吸率 |

SPO2 v1 声明一个指标：

| `code` | `valueType` | `unit` | 含义 |
|---|---|---|---|
| `spo2` | `number` | `percent` | 血氧百分数 |

血压 v1 声明两个指标：

| `code` | `valueType` | `unit` | 含义 |
|---|---|---|---|
| `sbp` | `number` | `mmHg` | 收缩压 |
| `dbp` | `number` | `mmHg` | 舒张压 |

糖尿病风险 v1 声明一个指标：

| `code` | `valueType` | `unit` | 含义 |
|---|---|---|---|
| `diabetes_probability` | `number` | `ratio` | 糖尿病模型类别 1 的原始概率；不是血糖值或诊断结论 |

创建全局指标时，分别为目标算法创建所需指标；若指标代码已存在，先确认其类型和单位一致，不要把冲突响应当作创建成功。

版本指标配置请求：

```json
{
  "metricCodes": ["hr"]
}
```

以上为 HR；BR 只用 `{"metricCodes":["br"]}`。SPO2、BP、糖尿病分别配置 `["spo2"]`、`["sbp","dbp"]`、`["diabetes_probability"]`。PPG v2 可配置 `["hr","br","spo2","diabetes_probability"]`，PPG+ACC 仍为 `["sbp","dbp"]`。仅允许声明指标，不新增 `risk_level` 或混入无关指标。

PUT 是替换完整投影集合，不是追加；空数组表示清空，重复代码、未知代码或不匹配的算法语义返回 422。并非必须选择算法的全部指标，投影集合也不决定详情 `result` 的完整内容。

### 8.4 发布与渠道选择

发布检查算法注册、固定参数、制品名称和物理文件、已选指标语义。成功后 `publishedAt` 非空，算法、参数、制品和指标集合不可再原地修改；前端应禁用相应写操作。重复发布返回原版本及首次发布时间，不会重新发布出一个版本。

**发布不等于渠道可用。** 还需为目标渠道选择此模型的一个已发布版本：

```json
{
  "versionId": 12
}
```

选择的版本必须属于路径中的模型且已发布。管理目标可为已知渠道；面向用户发布时选择对应普通渠道。切换到不同版本会使该渠道旧版本全部草稿变为 `FAILED / TASK.MODEL_SWITCHED`，已提交或运行中的任务继续用固定版本；其他渠道不变。选择相同版本不改变草稿，回退版本也不恢复历史失败草稿。

前端建议在切换前明确提示草稿失效影响，并要求操作人确认。完成后使用目标普通渠道账号重新查询模型目录，不能用管理令牌直接替代这一步。

### 8.5 当前管理页面能力边界

目前没有管理端模型总列表、模型编辑或删除、指标列表、算法列表、制品列表或删除、渠道选择读取接口；版本列表必须先知道 `modelId`，也没有单独的版本详情读取接口。不能从 OpenAPI 中不存在的接口推导出完整后台 CRUD。

管理页面需明确已有 ID 和配置数据的来源，例如本次创建响应或受控的联调配置。不要用普通渠道目录冒充管理端全量模型列表，也不要把浏览器缓存作为发布配置的权威来源。完整后台所需的新增查询能力需另行约定，不属于当前对接范围。

## 9. 完整联调示例

### 9.1 环境准备

联调前确认 Web API 可访问。要验证任务执行结果，后端还需启动独立推理消费者，并确保数据库、Redis 和文件存储可用；只启动 Web 可以提交和查询任务，但不能证明推理会完成。前端无需迁移数据库或创建初始模型。环境操作见[本地开发指南](development/environment.md#独立推理消费者运行与部署)。

仓库不提供可上传短 PPG 样例，联调方自备第 6.1.2 节格式 CSV；见[HR v1](development/algorithms/ppg-active-offline-hr-v1.md)、[BR v1](development/algorithms/ppg-active-offline-br-v1.md)及各算法说明。二者无必需资料、权重或非空参数，分别验证各自结果及文件，不能用旧融合验收替代。其他算法按第 6 节准备输入和配置。

### 9.2 用户端完成一次任务

| 步骤 | 操作 | 预期与页面动作 |
|---|---|---|
| 1 | 在 `ios-cn` 注册普通账号，再用相同账号登录 | 注册 201，不含令牌；登录 200，保存普通渠道令牌对 |
| 2 | 查询本人账号和健康资料 | 均为 200；正式 v1 不要求补充健康资料 |
| 3 | 查询 `ios-cn` 模型目录 | 使用当前渠道返回的已发布模型及其 `modelId`；要求输入为 `ppg`、`isExample=false` |
| 4 | 用返回的 `modelId` 创建草稿 | 201；保存 `taskId` 和固定版本，状态为 `DRAFT` |
| 5 | 上传符合第 6.1.2 节条件的 CSV，表单 `logicalName=ppg`、`file=<本地文件>` | 201；保存 `fileId`，不可只上传文件名字符串 |
| 6 | 最终提交，不传请求体 | 200，状态为 `SUBMITTED`；停止允许编辑输入 |
| 7 | 查询详情直到终态 | 正常信号预期 `SUCCEEDED`，列表响应不用于读取完整结果 |
| 8 | 展示 `result.hr`、`result.br` 和质量信息；按 `result.output_files` 的逻辑键下载处理文件 | 依第 6.1.3 节呈现，不将结果解释为临床判断 |

上传步骤可在仓库根目录用 curl 验证；先将变量替换为本次登录、创建响应和已校验输入文件的实际值：

```sh
BASE_URL=https://test-backend.myhealthyclub.com
CHANNEL=ios-cn
ACCESS_TOKEN='本次普通渠道登录返回的 data.accessToken'
TASK_ID='本次创建返回的 data.taskId'
PPG_FILE='/本机路径/符合正式算法条件的输入.csv'

curl --include --request POST "$BASE_URL/api/v1/$CHANNEL/tasks/$TASK_ID/files" \
  --header "Authorization: Bearer $ACCESS_TOKEN" \
  --form 'logicalName=ppg' \
  --form "file=@$PPG_FILE"
```

成功详情及处理文件契约见第 6.1.3 节；`output_files` 中的值是服务端不透明引用，前端只按逻辑键构造下载请求，不解析服务器路径。

后续算法的输入与结果页面仍须按其固定版本契约实现，不能把 PPG v1 的字段套用于其他算法，也不能把任意数值自动解释为医学指标。



## 10. 联调验收与常见问题

### 10.1 验收清单

以下是联调时应执行的清单，不代表本文已在当前环境执行全部业务操作。涉及发布或切换时使用可控的联调环境。

- [ ] 普通渠道注册后能登录；注册失败不进入自动刷新；管理渠道不能注册。
- [ ] Access 业务请求缺失或过期时收到 401；普通渠道错用有效令牌收到 403。
- [ ] 一次刷新后同时替换两个令牌，旧 Refresh 不能再次刷新；并发业务请求不会各自发起刷新。
- [ ] 登出后本地令牌、资料缓存和任务查询均清理；界面不承诺旧 Access 已立即撤销。
- [ ] PATCH 未提交字段保持不变，可空字段能清空；`gender=null` 被拒绝，0 表示未知。
- [ ] 病史 `false` 保留为明确否，键值 null 删除键，整体 null 清空对象。
- [ ] 身高、体重按字符串响应处理；时间不因浏览器或宿主机时区被错误转换。
- [ ] 用户目录只展示本渠道可用版本，并按通用 `isExample` 字段标注用途；空目录不回退到其他渠道。
- [ ] 草稿能上传和同名重传，保存不同 `fileId`；旧输入仍可下载。
- [ ] 缺少必需输入提交失败；有效样本分别核对独立 HR 的 `hr/quality/processed_ppg` 与 BR 的 `br/quality/processed_rsp`，BR `[8,20]` 且 NaN 失败不造数；SPO2、BP、糖尿病结果和文件见各算法章节。
- [ ] 非法 CSV 可上传和提交，但执行后失败；界面区分 API 成功与任务失败。
- [ ] 重复提交及提交后上传被拒绝；超时不盲目创建或提交重复任务。
- [ ] 修改实时资料不会改变已提交任务；补全资料后通过新任务重新提交。
- [ ] 跨账号和跨来源渠道访问任务或文件返回安全 404；管理令牌不能越权代查。
- [ ] 下载失败显示错误，不把 JSON 错误保存成正常结果文件；令牌不进入下载 URL。
- [ ] 查询进入成功或失败终态后停止；等待过久不自行改判失败。
- [ ] 管理端能发布示例并配置渠道；发布后配置不能修改，重复发布不改变首次发布时间。
- [ ] 切换到不同版本会使旧草稿失败，已提交及运行任务仍使用原版本；回退不复活旧草稿。
- [ ] 统一PPG场景分别覆盖全部成功、部分成功和全部失败；即使任务 `SUCCEEDED`，也按 `outcome` 和各成员 `status` 展示，并从 `result.results.<成员>.result` 读取原载荷。
- [ ] 统一PPG+ACC场景只包含 `bp`；成功为 `ALL_SUCCEEDED`，成员失败为 `ALL_FAILED`，没有 `PARTIALLY_SUCCEEDED`；场景整体超时则为 `FAILED / result=null`，没有中间汇总。
- [ ] 场景顶层失败字段在正常汇总时为空，成员质量拒绝仍可读取；只下载顶层 `result.output_files` 中已登记的带前缀逻辑名，全部失败时没有此对象。
- [ ] 独立 HR、BR `1.0.0` 及 PPG v2 `2.0.0` 在五普通渠道按实际 ID 配置；先对照验证再清测试推理历史与退役旧版本。PPG v2 四成员完整，BR 失败仍保留成功 HR；健康查询独立产生记录。旧验收记录不作为新版本通过证据，PPG+ACC/BP 配置不变。

### 10.2 常见问题

| 现象 | 检查方向 |
|---|---|
| Swagger 能请求，浏览器管理端报跨域 | 核对管理页面与 API 是否同源；跨域时检查开发代理、同源部署或网关 CORS；原生 App 系统网络请求不受 CORS 限制，不通过新增 Cookie 解决 |
| 同一账号换端后找不到任务 | 核对令牌渠道、URL 渠道、任务来源渠道；账号相同不代表跨渠道可读 |
| 多个请求同时认证失败后被迫登录 | 核对是否重复使用同一 Refresh，是否原子替换令牌对，是否存在旧刷新响应覆盖新会话 |
| 提交成功但一直等待 | 确认消费者及依赖运行，由后端排查；不重复提交来催执行 |
| HTTP 200 但任务或测量失败 | 外层 `code` 仅表示查询成功；任务失败看 `data.status` 和顶层安全失败字段，场景 `SUCCEEDED` 还须看 `result.outcome` 与成员状态 |
| 刷新页面后上传列表消失 | 当前没有服务端输入列表接口，详情也没有输入集合；不要伪造服务端恢复结果 |
| 模型已发布但用户看不到 | 检查目标普通渠道是否选择了该版本，并使用目标渠道令牌查询 |
| 成功任务没有输出下载入口 | 检查是否存在顶层 `result.output_files`；独立算法见第 6.1.3–6.4.3 节，场景见第 6.5.4 节；`ALL_FAILED` 没有输出对象，不能猜测文件或改用其他成员输出 |
| 时间显示偏移八小时 | 检查是否把无后缀北京时间当成 UTC，或使用浏览器隐式解析 |
| 422 没有具体字段错误 | 当前只返回统一安全错误；前端本地校验并结合当前操作提示，附 `requestId` 联调 |

新增页面若依赖当前未提供的接口，应先约定后端契约；不要以客户端缓存、静态假数据或猜测的 URL 代替真实服务能力。
