"""管理受全局硬时限约束的串行推理子进程。"""

from __future__ import annotations

import json
import multiprocessing
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from importlib import import_module
from multiprocessing.connection import Connection
from pathlib import PurePosixPath
from time import monotonic
from uuid import UUID

from django.conf import settings
from django.db import DatabaseError, connections
from health_inference.exceptions import (
    InferenceError,
    InputError,
    InputErrorReason,
    QualityRejectedError,
)
from health_inference.quality import quality_rejection_from_mapping
from redis.exceptions import RedisError

from health_service.schemas.internal.execution import PreparedInferenceResult

_EXECUTION_POLL_SECONDS = 0.05
_STARTUP_TIMEOUT_SECONDS = 10.0
_SAFE_ERROR_MESSAGE = "推理执行失败"
_TIMEOUT_CODE = "INFERENCE.EXECUTION_TIMEOUT"
_TIMEOUT_MESSAGE = "推理执行超时"


@dataclass(frozen=True, slots=True)
class _ExecutorReference:
    """描述可在独立解释器中导入的执行函数及其固定参数。"""

    module_name: str  # 执行函数所在的可导入模块。
    qualified_name: str  # 模块内的执行函数限定名称。
    args: tuple[object, ...]  # 预先绑定的可序列化位置参数。
    kwargs: dict[str, object]  # 预先绑定的可序列化关键字参数。


class InferenceExecutionProcess:
    """在可强制终止的单一子进程中串行运行算法。"""

    def __init__(
        self,
        executor: Callable[..., PreparedInferenceResult | None],
        *,
        timeout_seconds: int,
    ) -> None:
        """保存执行函数和正整数硬时限。"""
        if type(timeout_seconds) is not int:
            raise TypeError("推理执行时限必须是正整数")
        if timeout_seconds <= 0:
            raise ValueError("推理执行时限必须是正整数")
        self._executor = executor
        self._timeout_seconds = timeout_seconds
        self._process: multiprocessing.Process | None = None
        self._connection: Connection | None = None

    def start(self) -> None:
        """启动子进程并确认其已进入请求循环。"""
        if self._process is not None and self._process.is_alive():
            return
        self._discard_process()

        executor_reference = _executor_reference(self._executor)
        runtime_settings = _runtime_settings_snapshot()
        context = multiprocessing.get_context("spawn")
        parent_connection, child_connection = context.Pipe(duplex=True)
        process = context.Process(
            target=_execution_process_main,
            args=(
                child_connection,
                executor_reference,
                settings.SETTINGS_MODULE,
                runtime_settings,
            ),
            name="health-inference-executor",
            daemon=True,
        )
        try:
            process.start()
        except BaseException:
            parent_connection.close()
            child_connection.close()
            raise
        child_connection.close()
        self._process = process
        self._connection = parent_connection

        if not parent_connection.poll(_STARTUP_TIMEOUT_SECONDS):
            self._discard_process()
            raise RuntimeError("推理执行子进程启动失败")
        try:
            message = _read_message(parent_connection)
        except (EOFError, OSError, ValueError, json.JSONDecodeError):
            self._discard_process()
            raise RuntimeError("推理执行子进程启动失败") from None
        if message.get("kind") != "ready":
            self._discard_process()
            raise RuntimeError("推理执行子进程启动失败")

    def execute(
        self,
        *,
        task_id: int,
        execution_token: UUID,
    ) -> PreparedInferenceResult | None:
        """执行单个任务；超过时限先杀死子进程再报告受控超时。"""
        self.start()
        connection = self._connection
        if connection is None:
            raise RuntimeError("推理执行子进程不可用")

        deadline = monotonic() + self._timeout_seconds
        try:
            _send_message(
                connection,
                {
                    "kind": "execute",
                    "task_id": task_id,
                    "execution_token": str(execution_token),
                },
            )
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    self._discard_process()
                    raise InferenceError(_TIMEOUT_CODE, _TIMEOUT_MESSAGE)
                if connection.poll(min(remaining, _EXECUTION_POLL_SECONDS)):
                    if monotonic() >= deadline:
                        self._discard_process()
                        raise InferenceError(_TIMEOUT_CODE, _TIMEOUT_MESSAGE)
                    response = _read_message(connection)
                    if monotonic() >= deadline:
                        self._discard_process()
                        raise InferenceError(_TIMEOUT_CODE, _TIMEOUT_MESSAGE)
                    return _decode_response(response)
                if self._process is None or not self._process.is_alive():
                    self._discard_process()
                    raise RuntimeError("推理执行子进程意外退出")
        except (EOFError, OSError, ValueError, json.JSONDecodeError):
            self._discard_process()
            raise RuntimeError("推理执行子进程通信失败") from None

    def close(self) -> None:
        """正常关闭子进程，并在退出无响应时强制回收。"""
        process = self._process
        connection = self._connection
        if process is None:
            return
        if process.is_alive() and connection is not None:
            try:
                _send_message(connection, {"kind": "stop"})
                process.join(timeout=1.0)
            except (BrokenPipeError, OSError, ValueError):
                pass
        self._discard_process()

    def _discard_process(self) -> None:
        """终止并回收当前子进程及管道。"""
        process = self._process
        connection = self._connection
        self._process = None
        self._connection = None
        if connection is not None:
            connection.close()
        if process is not None:
            if process.is_alive():
                process.kill()
                process.join()
            else:
                process.join()
            process.close()


def _execution_process_main(
    connection: Connection,
    executor_reference: _ExecutorReference,
    settings_module: str,
    runtime_settings: Mapping[str, object],
) -> None:
    """在独立解释器中加载 Django 设置并串行处理执行请求。"""
    settings_context = None
    settings_context_enabled = False
    try:
        if settings_module:
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)
        if not os.environ.get("DJANGO_SETTINGS_MODULE"):
            raise RuntimeError("Django 设置模块不可用")
        for name in ("LOG_DIR", "TASK_DATA_ROOT", "MODEL_ARTIFACT_ROOT"):
            os.environ[name] = os.fspath(runtime_settings[name])

        import django
        from django.apps import apps
        from django.conf import settings as django_settings
        from django.test.utils import override_settings

        _ = django_settings.INSTALLED_APPS
        settings_context = override_settings(**runtime_settings)
        settings_context.enable()
        settings_context_enabled = True
        if not apps.ready:
            django.setup()
        connections.close_all()
        executor = _load_executor(executor_reference)
        _send_message(connection, {"kind": "ready"})
        while True:
            try:
                request = _read_message(connection)
            except (EOFError, OSError, ValueError):
                break
            if request.get("kind") == "stop":
                break
            response = _execute_request(executor, request)
            try:
                _send_message(connection, response)
            except (BrokenPipeError, OSError, ValueError):
                break
    finally:
        try:
            from health_service.infrastructure.inference_adapter import clear_version_runtime_cache

            clear_version_runtime_cache()
            connections.close_all()
        except Exception:  # noqa: BLE001, S110  # 子进程退出时不透传清理异常。
            pass
        try:
            if settings_context_enabled:
                settings_context.disable()
        finally:
            connection.close()


def _runtime_settings_snapshot() -> dict[str, object]:
    """复制子进程必须与当前消费者一致的有效环境配置。"""
    runtime_settings = {
        name: getattr(settings, name)
        for name in ("LOG_DIR", "TASK_DATA_ROOT", "MODEL_ARTIFACT_ROOT")
    }
    runtime_settings["DATABASES"] = {
        alias: connections[alias].settings_dict
        for alias in settings.DATABASES
    }
    return runtime_settings


def _executor_reference(executor: Callable[..., object]) -> _ExecutorReference:
    """将顶层函数或 partial 转换为不提前导入执行模块的引用。"""
    if isinstance(executor, partial):
        reference = _executor_reference(executor.func)
        return _ExecutorReference(
            module_name=reference.module_name,
            qualified_name=reference.qualified_name,
            args=(*reference.args, *executor.args),
            kwargs={**reference.kwargs, **(executor.keywords or {})},
        )
    module_name = getattr(executor, "__module__", None)
    qualified_name = getattr(executor, "__qualname__", None)
    if (
        not callable(executor)
        or not isinstance(module_name, str)
        or not isinstance(qualified_name, str)
        or "<locals>" in qualified_name
    ):
        raise TypeError("推理执行函数必须是可导入的模块级函数")
    return _ExecutorReference(module_name, qualified_name, (), {})


def _load_executor(reference: _ExecutorReference) -> Callable[..., object]:
    """在 Django 初始化后导入并还原任务执行函数。"""
    executor: object = import_module(reference.module_name)
    for name in reference.qualified_name.split("."):
        executor = getattr(executor, name)
    if not callable(executor):
        raise TypeError("推理执行函数不可调用")
    if reference.args or reference.kwargs:
        return partial(executor, *reference.args, **reference.kwargs)
    return executor


def _execute_request(
    executor: Callable[..., object],
    request: Mapping[str, object],
) -> dict[str, object]:
    """执行经校验的请求并生成不含原始异常内容的响应。"""
    task_id = request.get("task_id")
    token_value = request.get("execution_token")
    if type(task_id) is not int or task_id <= 0 or not isinstance(token_value, str):
        return {"kind": "unexpected"}
    try:
        execution_token = UUID(token_value)
    except ValueError:
        return {"kind": "unexpected"}

    response: dict[str, object]
    try:
        result = executor(task_id=task_id, execution_token=execution_token)
        response = _result_response(result)
    except DatabaseError:
        response = {"kind": "database_error"}
    except RedisError:
        response = {"kind": "redis_error"}
    except InputError as error:
        response = {"kind": "input_error", "reason": error.reason.value}
    except QualityRejectedError as error:
        rejection = error.rejection
        details = rejection.details
        response = {
            "kind": "quality_error",
            "rejection": {
                "reason": rejection.reason.value,
                "details": (
                    None
                    if details is None
                    else {"score": details.score, "threshold": details.threshold}
                ),
            },
        }
    except InferenceError as error:
        response = {"kind": "inference_error", "code": error.code}
    except Exception:  # noqa: BLE001  # 不把第三方异常或堆栈带回父进程。
        response = {"kind": "unexpected"}

    try:
        connections.close_all()
    except DatabaseError:
        return {"kind": "database_error"}
    except Exception:  # noqa: BLE001  # 无法安全确认连接清理时不提交执行结果。
        return {"kind": "unexpected"}
    return response


def _result_response(result: object) -> dict[str, object]:
    """仅序列化受控推理结果及相对输出键。"""
    if result is None:
        return {"kind": "no_result"}
    if not isinstance(result, PreparedInferenceResult):
        return {"kind": "unexpected"}
    try:
        payload = dict(result.payload)
        metrics = dict(result.metrics)
        output_files = dict(result.output_files)
        if any(
            not isinstance(name, str)
            or not isinstance(storage_key, str)
            or PurePosixPath(storage_key).is_absolute()
            for name, storage_key in output_files.items()
        ):
            return {"kind": "unexpected"}
        response = {
            "kind": "result",
            "payload": payload,
            "metrics": metrics,
            "output_files": output_files,
        }
        json.dumps(response, allow_nan=False)
    except (TypeError, ValueError):
        return {"kind": "unexpected"}
    return response


def _decode_response(
    response: Mapping[str, object],
) -> PreparedInferenceResult | None:
    """在父进程重建受控结果或固定失败类型。"""
    kind = response.get("kind")
    if kind == "result":
        payload = response.get("payload")
        metrics = response.get("metrics")
        output_files = response.get("output_files")
        if not all(isinstance(value, Mapping) for value in (payload, metrics, output_files)):
            raise RuntimeError("推理执行结果格式无效")
        return PreparedInferenceResult(
            payload=payload,
            metrics=metrics,
            output_files=output_files,
        )
    if kind == "no_result":
        return None
    if kind == "input_error":
        try:
            reason = InputErrorReason(response.get("reason"))
        except (TypeError, ValueError):
            raise RuntimeError("推理输入错误格式无效") from None
        raise InputError(reason)
    if kind == "quality_error":
        rejection = quality_rejection_from_mapping(response.get("rejection"))
        if rejection is None:
            raise RuntimeError("推理质量错误格式无效")
        raise QualityRejectedError(rejection)
    if kind == "inference_error":
        code = response.get("code")
        if not isinstance(code, str):
            raise RuntimeError("推理执行错误格式无效")
        raise InferenceError(code, _SAFE_ERROR_MESSAGE)
    if kind == "database_error":
        raise DatabaseError("推理执行数据库操作失败")
    if kind == "redis_error":
        raise RedisError("推理执行消息服务操作失败")
    raise RuntimeError("推理执行子进程失败")


def _read_message(connection: Connection) -> dict[str, object]:
    """读取并校验 JSON 对象消息。"""
    value = json.loads(connection.recv_bytes().decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError("进程消息必须是对象")
    return value


def _send_message(connection: Connection, message: Mapping[str, object]) -> None:
    """以 JSON 字节发送跨进程消息。"""
    connection.send_bytes(json.dumps(message, allow_nan=False).encode("utf-8"))
