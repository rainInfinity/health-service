"""验证真实 Uvicorn 请求链的日志分流和敏感信息边界。"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tests.log_records import parse_console_records


def _free_loopback_port() -> int:
    """分配用于隔离 Uvicorn 子进程的本地端口。"""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _wait_for_listener(process: subprocess.Popen[str], port: int) -> None:
    """等待 Uvicorn 开始监听，或在启动失败时报告子进程输出。"""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output, _ = process.communicate()
            raise AssertionError(f"Uvicorn 在启动时退出：\n{output}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError("等待 Uvicorn 监听超时")


def _get_json(port: int, path: str, headers: dict[str, str] | None = None):
    """发送本地 HTTP 请求并解析 API JSON 回复。"""
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        headers=headers or {},
    )
    try:
        with urlopen(request, timeout=5) as response:
            status = response.status
            response_headers = response.headers
            body = response.read()
    except HTTPError as error:
        status = error.code
        response_headers = error.headers
        body = error.read()
    return status, response_headers, json.loads(body.decode("utf-8"))


def test_real_uvicorn_requests_write_separate_safe_logs(tmp_path: Path):
    """通过真实 Uvicorn 验证成功、404、500 与通用事件日志。"""
    project_root = Path(__file__).resolve().parents[3]
    log_dir = tmp_path / "logs"
    port = _free_loopback_port()
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.update(
        {
            "DJANGO_DEBUG": "false",
            "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost",
            "LOG_DIR": str(log_dir),
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.integration.api.asgi:application",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            "1",
            "--no-access-log",
            "--log-level",
            "info",
        ],
        cwd=project_root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_listener(process, port)
        success = _get_json(
            port,
            "/api/v1/success",
            {
                "X-Request-ID": "CLIENT-REQUEST-ID-SENTINEL",
                "X-Forwarded-For": "UNTRUSTED-FORWARDED-IP-SENTINEL",
            },
        )
        unmatched = _get_json(
            port,
            "/api/v1/missing/RAW-PATH-SENTINEL?secret=RAW-QUERY-SENTINEL",
        )
        failure = _get_json(port, "/api/v1/failure?secret=FAILURE-QUERY-SENTINEL")
        application_event = _get_json(port, "/api/v1/application-event")
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            stdout, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, _ = process.communicate()

    responses = (success, unmatched, failure, application_event)
    request_ids = [response_headers["X-Request-ID"] for _, response_headers, _ in responses]
    assert [status for status, _, _ in responses] == [200, 404, 500, 200]
    assert len(set(request_ids)) == len(request_ids)
    for _, response_headers, body in responses:
        assert body["requestId"] == response_headers["X-Request-ID"]

    assert success[2]["data"] == {"value": "ready"}
    assert success[1]["X-Request-ID"] != "CLIENT-REQUEST-ID-SENTINEL"
    assert unmatched[2]["code"] == "NOT_FOUND"
    assert failure[2]["code"] == "INTERNAL_ERROR"
    assert application_event[2]["data"] == {"value": "application-event"}

    server_text = (log_dir / "server.log").read_text(encoding="utf-8")
    application_text = (log_dir / "application.log").read_text(encoding="utf-8")
    server_records = [json.loads(line) for line in server_text.splitlines()]
    application_records = [json.loads(line) for line in application_text.splitlines()]
    stdout_records = parse_console_records(stdout)
    api_stdout_records = [
        record for record in stdout_records if record["event"] != "application.test"
    ]
    application_stdout_records = [
        record for record in stdout_records if record["event"] == "application.test"
    ]
    completed = [record for record in server_records if record["event"] == "request.completed"]
    exceptions = [record for record in server_records if record["event"] == "request.exception"]

    assert len(api_stdout_records) == len(server_records)
    for console_record, file_record in zip(api_stdout_records, server_records, strict=True):
        assert console_record.keys() == file_record.keys()
        for field, value in file_record.items():
            if field == "locations":
                assert json.loads(console_record[field]) == value
            else:
                assert console_record[field] == str(value)
    assert len(application_stdout_records) == len(application_records)
    for console_record, file_record in zip(
        application_stdout_records, application_records, strict=True
    ):
        assert console_record == {field: str(value) for field, value in file_record.items()}
    assert len(completed) == 4
    assert {record["requestId"] for record in completed} == set(request_ids)
    assert next(record for record in completed if record["status"] == 404)["route"] == "<unmatched>"
    assert len(exceptions) == 1
    assert exceptions[0]["requestId"] == failure[1]["X-Request-ID"]
    assert len(application_records) == 1
    assert set(application_records[0]) == {"timestamp", "level", "event"}
    assert application_records[0]["level"] == "INFO"
    assert application_records[0]["event"] == "application.test"
    assert "\x1b[" not in stdout
    assert '"GET /api/v1/' not in stdout

    all_logs = stdout + server_text + application_text
    for sentinel in (
        "CLIENT-REQUEST-ID-SENTINEL",
        "UNTRUSTED-FORWARDED-IP-SENTINEL",
        "RAW-PATH-SENTINEL",
        "RAW-QUERY-SENTINEL",
        "FAILURE-QUERY-SENTINEL",
        "EXCEPTION-SENTINEL",
        "病史",
        "手机号",
        "SQL-SENTINEL",
        "/private/config/path",
        "TEST-APPLICATION-MESSAGE-SENTINEL",
    ):
        assert sentinel not in all_logs
        assert all(sentinel not in json.dumps(body, ensure_ascii=False) for _, _, body in responses)
