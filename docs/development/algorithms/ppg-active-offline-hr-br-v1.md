# PPG 主动离线 HR/BR v1

算法标识：`ppg.active.offline.hr_br.v1`。实现目录：`packages/health-inference/src/health_inference/algorithms/ppg_active_offline_hr_br_v1/`。共享输入解析位于 `health_inference/utils/ppg_input.py`，预处理和输出构建分别位于 `preprocessing.py`、`build_output.py`；类型名中的 PPG 缩写统一大写。

## 输入格式

输入逻辑名为 `ppg`，内容为 UTF-8 CSV，可带 BOM。必需列为有限、严格递增的毫秒时间戳 `timestampFrame`，以及且仅一组完整通道：

| 通道格式 | 必需列 | PPG 提取方式 |
|---|---|---|
| RGB | `avgR`、`avgG`、`avgB` | `-avgR / 255` |
| YUV | `avgY`、`avgU`、`avgV` | `-(avgY + 1.5748 × (avgV - 128)) / 255`，按 BT.709 提取红通道 |

表头不得重复，行列数必须一致；缺少时间戳、通道组不完整、同时提供两组完整通道、非法或非有限通道值、非有限或非递增时间戳均拒绝。其他相机元数据列可以保留并忽略。`blockIndex` 可省略；存在时每行必须是整数 `0`。非零、非整数块编号以及不支持的多块布局拒绝处理，不选块、不合并、不插值。

至少需要两条记录计算时间差；时间戳差的均值用于采样率，沿用旧链将 Hz 值四舍五入到两位小数的规则。输入不要求健康资料、算法参数或模型权重。


## 预处理

输入信号分别经过二阶 Butterworth 带通滤波和总体标准差 Z-score：

- HR/PPG：`0.75–4 Hz`；
- 呼吸波：`0.1–0.5 Hz`。

采样率必须满足滤波器 Nyquist 前提；`filtfilt` 输入长度必须超过实际滤波填充长度，标准化方差不得为零。除此之外不设最短采集秒数，不重采样、不插值、不裁剪通道值。两组滤波使用同一份提取信号，分别形成 HR 与呼吸波。

## 质量门禁

质量评价调用 NeuroKit2 `ppg_quality`，输入为预处理后的 PPG 波形，采样率参数沿用旧链的 `int(fs)`。实际分数为评价数组均值乘以 `100`，保留原精度；固定阈值为 `70`。分数严格小于 `70` 时抛出 `LOW_SCORE` 并保留本次 `score` 与 `threshold`；等于阈值允许进入后续阶段。非有限或无法解释的评分按执行错误终止，不补零分、满分或 `NO_VALID_SIGNAL`。

## 原始推理与结果约束

心率估计沿用 NeuroKit2 `ppg_findpeaks` 与 `ppg_rate`，呼吸率估计沿用 `rsp_rate`；两者均使用速率数组的算术均值。传给 NeuroKit2 的采样率为 `int(fs)`，与旧链一致。HR 使用 0.75–4 Hz PPG 波形，BR 使用 0.1–0.5 Hz 呼吸波形。

`build_output.py` 在构造载荷、指标和 CSV 文件前检查原始 HR 与 BR 均为有限数值；任一为 NaN 或正负无穷均以 `RESULT.INVALID_OUTPUT` 失败。通过检查后仅执行 `br = min(br, 18)`：不补值 `12`，不增加 BR 下限，不四舍五入，也不对有限 HR 作额外限幅。PPG v1 不创建 `postprocessing.py`。

## 成功结果与处理文件

成功载荷固定包含 `hr`、`br` 和实际质量结论 `quality: {score, threshold}`。指标代码为 `hr`、`br`，单位分别为 `beats_per_minute`、`breaths_per_minute`；payload 与 metrics 使用相同的结果约束后数值。任何本地文件路径都不写入载荷。

处理文件写在本次执行专属目录，逻辑名与文件名如下：

| 逻辑名 | 文件名 | CSV 表头 |
|---|---|---|
| `processed_ppg` | `ppg-processed.csv` | `timestamp,ppg` |
| `processed_rsp` | `rsp-processed.csv` | `timestamp,rsp` |

每行时间戳保留输入 `timestampFrame` 的毫秒值，并与同一输入帧的处理后信号对应。下载引用由服务端受保护文件链路提供。

## 安装与独立验证

`health-inference` 基础依赖包含 pandas `2.3.3` 与 NumPy `2.3.5`，共享输入读取和输出 CSV 写入使用 pandas；SciPy `1.16.3` 与 NeuroKit2 `0.2.12` 仍由 `ppg` extra 安装。Web 基础环境会包含 pandas/NumPy，但算法注册保持惰性导入。读取时关闭缺失值推断和空行跳过，保留既有 CSV 拒绝语义。本次未执行性能基准，不据此宣称加速：

```sh
uv sync --locked --package health-inference --extra ppg --no-dev
```

以下纯算法测试在隔离目录生成确定性合成输入，直接调用注册定义，并逐行核对两个输出文件，不需要 Django、数据库或真实健康数据：

```sh
uv run --locked --python 3.13 --all-packages --extra ppg pytest --reuse-db \
  tests/unit/inference/hr_br/test_output.py::test_success_payload_metrics_and_processed_csvs_match_input
```

### Web、端到端与项目检查

仅 Web 安装烟测使用不带 `ppg` extra 的隔离环境：

```sh
UV_PROJECT_ENVIRONMENT=/tmp/health-service-web-only-ppg-v1-env uv sync --locked --package health-service --no-dev
UV_PROJECT_ENVIRONMENT=/tmp/health-service-web-only-ppg-v1-env uv run --locked --package health-service python manage.py check
```

`manage.py check` 输出 `System check identified no issues (0 silenced).`。在终端 A 启动 Web 服务：

```sh
UV_PROJECT_ENVIRONMENT=/tmp/health-service-web-only-ppg-v1-env uv run --locked --package health-service uvicorn config.asgi:application --host 127.0.0.1 --port 8765 --no-access-log --workers 1
```

在终端 B 请求 OpenAPI：

```sh
curl http://127.0.0.1:8765/api/v1/openapi.json
```

隔离 Web 环境安装 pandas `2.3.3` 与 NumPy `2.3.5`，不安装 SciPy 或 NeuroKit2。`manage.py check` 通过，算法注册时 pandas、NumPy、SciPy 和 NeuroKit2 均未导入；Web HTTP 烟测结果见下方增补验证记录。

正式算法独立测试与 HTTP 任务端到端测试均通过：

```sh
UV_PROJECT_ENVIRONMENT=/tmp/health-service-test-v1-env uv run --locked --python 3.13 --all-packages --extra ppg pytest --reuse-db \
  tests/unit/inference/hr_br/test_output.py::test_success_payload_metrics_and_processed_csvs_match_input

UV_PROJECT_ENVIRONMENT=/tmp/health-service-test-v1-env uv run --locked --python 3.13 --all-packages --extra ppg pytest --reuse-db \
  tests/integration/workflows/test_inference_consumer.py::test_official_ppg_v1_http_task_executes_and_downloads_outputs
```

结果分别为 `1 passed`、`1 passed`。项目全套测试命令与结果：

```sh
UV_PROJECT_ENVIRONMENT=/tmp/health-service-test-v1-env uv run --locked --python 3.13 --all-packages --extra ppg pytest --reuse-db
```

共 370 项，`363 passed, 7 failed, 1 warning`。6 项账户/任务测试因测试库缺少 `health_user.registration_channel_id` 失败；另有 `tests/integration/api/test_uvicorn_logging.py::test_real_uvicorn_requests_write_separate_safe_logs` 因空 HTTP 响应触发 `JSONDecodeError`。PPG 相关测试通过；1 条警告来自 NeuroKit2 导入已弃用的 `scipy.misc`。

全仓 Ruff 检查实际运行如下：

```sh
UV_PROJECT_ENVIRONMENT=/tmp/health-service-test-v1-env uv run --locked --python 3.13 --all-packages --extra ppg ruff check .
UV_PROJECT_ENVIRONMENT=/tmp/health-service-test-v1-env uv run --locked --python 3.13 --all-packages --extra ppg ruff format --check .
```

此前一轮定向变更文件的 lint 与格式检查通过。全仓 lint 尚有 4 条 `RUF012`，位于 `health_service/migrations/0009_user_registration_channel.py` 和 `0010_task_client_metadata.py`；全仓格式检查报告 45 个文件需格式化、230 个已符合，未格式化无关文件。该轮临时 Web/测试环境及隔离 PostgreSQL 容器已清理；无一次性验证脚本或真实健康数据留存。

### 本次模块与 pandas 调整的增补验证

- PPG 实现和相关测试的 Ruff 检查及格式检查通过（16 个文件符合格式）。
- `pytest tests/unit/inference/hr_br tests/unit/test_execution_failure_mapping.py tests/unit/test_inference_algorithms.py -q`：`86 passed, 1 warning`；警告来自 NeuroKit2 导入已弃用的 `scipy.misc`。
- 正式算法实际路径烟测：1800 行合成 RGB 输入得到 HR `72.0`、BR `12.03265584092234`，质量分数 `99.99994628531353`；`ppg-processed.csv` 与 `rsp-processed.csv` 均为 1800 行，使用 pandas 读取核对了列名和行数。
- 隔离 Web 环境安装 pandas `2.3.3`、NumPy `2.3.5`，未安装 SciPy/NeuroKit2；`manage.py check` 无问题，注册正式算法后四个计算模块均未导入。Uvicorn 启动后 `/api/v1/openapi.json` 返回 HTTP `200`、`application/json`（80,410 字节），标题为“健康服务 API”，包含 21 个路径。本轮隔离环境、临时日志和临时输入均已清理。


## 实现状态

正式 v1 已完成计算、质量门禁、结果约束、文件输出和注册，算法标识为 `ppg.active.offline.hr_br.v1`；不注册 `hr_br` 或无修订号别名。合成信号对照只验证迁移行为，不代表医学准确性认证。
