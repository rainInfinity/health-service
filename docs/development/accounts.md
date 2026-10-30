# 账号、资料与令牌

## 账号注册、密码登录与本人账号查询
普通渠道为 `android-cn`、`ios-cn`、`android-intl`、`ios-intl` 和 `wxapp`；示例中的 `{channel}` 使用其中一个值。`manager` 只允许已有管理账号登录及访问管理接口，不开放注册。账号是全局共用的，但签发的令牌绑定登录渠道。

注册时提交 `username` 和 `password`：

```sh
curl --include --request POST \
  http://127.0.0.1:8000/api/v1/android-cn/auth/register \
  --header 'Content-Type: application/json' \
  --data '{"username":"local-user","password":"Example-passphrase-483!"}'
```

注册成功返回 HTTP 201。登录使用同一请求字段，成功时返回 HTTP 200：

```sh
curl --include --request POST \
  http://127.0.0.1:8000/api/v1/android-cn/auth/login \
  --header 'Content-Type: application/json' \
  --data '{"username":"local-user","password":"Example-passphrase-483!"}'
```

登录响应中的公开字段使用 `accessToken` 和 `refreshToken` 别名：

```json
{
  "code": "OK",
  "message": "成功",
  "data": {
    "accessToken": "<access-token>",
    "refreshToken": "<refresh-token>"
  },
  "requestId": "<server-request-id>"
}
```

使用 Access Token 查询当前渠道的本人账号；只返回 `userId` 和 `username`，不返回手机号或资料：

```sh
ACCESS_TOKEN='<从登录响应的 data.accessToken 复制>'
curl --include \
  http://127.0.0.1:8000/api/v1/android-cn/users/me \
  --header "Authorization: Bearer ${ACCESS_TOKEN}"
```

成功响应仍使用统一字段，账号 ID 的公开别名为 `userId`：

```json
{
  "code": "OK",
  "message": "成功",
  "data": {"userId": 42, "username": "local-user"},
  "requestId": "<server-request-id>"
}
```

Access Token 仅适用于签发它的渠道，过期、缺失或无效令牌均不能读取账号。错误响应结构和统一错误码见[运行与 API 运维](operations.md)。

## 管理账号建立与登录

管理账号由受信任的运维人员在 Django shell 中创建，不通过公开注册接口建立。仅授予 `is_staff=True`；除非确有 Django 管理站点需求，不要同时授予 `is_superuser`：

```sh
uv run --locked --package health-service python manage.py shell
```

```python
from getpass import getpass
from django.contrib.auth import get_user_model

username = input("管理账号用户名: ")
password = getpass("管理账号密码: ")
get_user_model().objects.create_user(
    username=username,
    password=password,
    is_staff=True,
    is_superuser=False,
)
```

使用管理渠道登录，失败时不存在、停用、密码错误或当前无管理权限均返回相同的认证错误：

```sh
read -r -s -p '管理账号密码: ' MANAGER_PASSWORD; printf '\\n'
curl --include --request POST \\
  http://127.0.0.1:8000/api/v1/manager/auth/login \\
  --header 'Content-Type: application/json' \\
  --data "{\\"username\\":\\"manager-user\\",\\"password\\":\\"${MANAGER_PASSWORD}\\"}"
unset MANAGER_PASSWORD
```

管理接口的每次请求都会重查当前 `is_staff` 权限和管理渠道；撤销权限后，旧管理 Access 立即不能访问管理业务，旧 Refresh 不能轮换。账号仍可用时，用户仍可提交自己的管理 Refresh 登出。普通注册不能提交权限字段，`/api/v1/manager/auth/register` 不存在。


## 本人健康资料读取与部分更新

资料接口只从当前渠道的 Access 确定账号，不接受请求指定 `userId`。`GET /api/v1/<channel>/users/me/profile` 不创建缺失的 Profile；未填写值返回 `null`，性别以 `0` 表示未知：

```json
{
  "code": "OK",
  "message": "成功",
  "data": {
    "name": null,
    "gender": 0,
    "birthDate": null,
    "heightCm": null,
    "weightKg": null,
    "medicalHistory": null,
    "historyJson": null
  },
  "requestId": "<server-request-id>"
}
```

使用 `PATCH` 只提交要修改的字段；未提交字段保持原值，可空字段显式设为 `null` 时清空。`gender` 不可为 `null`，`0` 表示未知；身高和体重非空时必须为正数：

```sh
ACCESS_TOKEN='<当前渠道的 Access Token>'
curl --include --request PATCH \
  http://127.0.0.1:8000/api/v1/android-cn/users/me/profile \
  --header 'Content-Type: application/json' \
  --header "Authorization: Bearer ${ACCESS_TOKEN}" \
  --data '{"name":"示例用户","gender":0,"heightCm":null,"historyJson":{"isHypertensive":false}}'
```

`historyJson` 对象按键合并：键值 `false` 会保留为明确否，键值 `null` 删除该键，整个字段为 `null` 则清空病史对象。未知入参（包括 `userId`）会被拒绝；PATCH 成功返回 HTTP 200 和更新后的完整资料。

请求和响应中的已定义病史键继续使用 `isDiabetic`、`isHypertensive`；资料内部数据及 PostgreSQL `history_json` 使用 `is_diabetic`、`is_hypertensive`。API 只转换这两个已定义键，不改写其他扩展内容。此命名转换不改变表结构，也不需要数据迁移。

## Refresh 轮换与登出

普通和管理渠道的刷新、登出只在请求体中接收当前渠道的 `refreshToken`，不要求有效 Access；不要把 Refresh 放入 `Authorization` 头。管理 Refresh 轮换时还会重新检查账号当前管理权限；撤权后仍允许独立登出。成功刷新返回 HTTP 200，`data` 使用与登录相同的 `accessToken`、`refreshToken` 字段：

```sh
REFRESH_TOKEN='<从最近一次成功响应的 data.refreshToken 复制>'
curl --include --request POST \
  http://127.0.0.1:8000/api/v1/android-cn/auth/refresh \
  --header 'Content-Type: application/json' \
  --data "{\"refreshToken\":\"${REFRESH_TOKEN}\"}"
```

每次成功刷新都会原子撤销旧 Refresh 并签发新令牌对。客户端应安全保存新对，并在一次成功处理后同时替换本地 Access 和 Refresh；旧 Refresh 不可重用。刷新旧令牌、已登出令牌或渠道不匹配的令牌均不会产生新令牌。

登出只撤销请求中的那枚 Refresh。客户端应提交当前最新的 Refresh，并在收到成功响应后立即清除本地保存的令牌：

```sh
REFRESH_TOKEN='<当前渠道最新的 data.refreshToken>'
curl --include --request POST \
  http://127.0.0.1:8000/api/v1/android-cn/auth/logout \
  --header 'Content-Type: application/json' \
  --data "{\"refreshToken\":\"${REFRESH_TOKEN}\"}"
```

登出成功返回 HTTP 200，`data` 为 `null`；响应仍包含服务端生成的 `requestId`：

```json
{
  "code": "OK",
  "message": "成功",
  "data": null,
  "requestId": "<server-request-id>"
}
```

登出不会撤销已签发的 Access；在签发时配置决定的有效期内（默认 7 天），只要账号仍可用且渠道匹配，该 Access 仍能访问本人接口。客户端登出后应立即丢弃它；服务端不承诺提前使其失效。其他渠道的独立 Refresh 不受本次登出影响。

