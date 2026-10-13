"""验证 API 请求日志的结构、关联与分流。"""

import asyncio
import copy
import json
import logging
import logging.config
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.test import AsyncClient, override_settings


@contextmanager
def _isolated_file_logging(log_dir: Path) -> Iterator[Path]:
    """将文件日志临时写入独立测试目录。"""
    log_dir.mkdir(parents=True)
    original_logging = copy.deepcopy(settings.LOGGING)
    isolated_logging = copy.deepcopy(settings.LOGGING)
    isolated_logging["handlers"]["api_file"]["filename"] = str(log_dir / "server.log")
    isolated_logging["handlers"]["application_file"]["filename"] = str(
        log_dir / "application.log"
    )
    try:
        logging.config.dictConfig(isolated_logging)
        yield log_dir
    finally:
        logging.config.dictConfig(original_logging)


@override_settings(
    ROOT_URLCONF="tests.integration.api.urls",
    ALLOWED_HOSTS=["testserver"],
)
def test_request_completion_is_one_json_record_with_safe_route_metadata(capsys):
    """验证完成记录为单条安全 JSON，并使用路由模板。"""
    output_before = capsys.readouterr().out

    async def send():
        """发送包含客户端请求标识和查询哨兵的请求。"""
        client = AsyncClient()
        return await client.get(
            "/api/v1/success?access_token=QUERY-SECRET-SENTINEL",
            headers={"X-Request-ID": "CLIENT-REQUEST-ID-SENTINEL"},
        )

    response = asyncio.run(send())
    output = capsys.readouterr().out
    records = [json.loads(line) for line in output.splitlines()]

    assert response.status_code == 200
    assert response["X-Request-ID"] != "CLIENT-REQUEST-ID-SENTINEL"
    assert response.json()["requestId"] == response["X-Request-ID"]
    assert len([record for record in records if record["event"] == "request.completed"]) == 1
    record = next(record for record in records if record["event"] == "request.completed")
    assert record["timestamp"].endswith("Z")
    datetime.fromisoformat(record["timestamp"])
    assert record["level"] == "INFO"
    assert record["method"] == "GET"
    assert record["status"] == 200
    assert record["durationMs"] >= 0
    assert record["requestId"] == response["X-Request-ID"]
    assert record["route"].endswith("success")
    assert "QUERY-SECRET-SENTINEL" not in output_before + output
    assert "CLIENT-REQUEST-ID-SENTINEL" not in output


@override_settings(
    ROOT_URLCONF="tests.integration.api.urls",
    ALLOWED_HOSTS=["testserver"],
)
def test_response_header_body_and_completion_log_share_request_id(capsys):
    """验证响应头、正文与完成日志共享请求标识。"""
    async def send_requests():
        """发送覆盖成功和错误状态的请求组。"""
        client = AsyncClient()
        return [
            await client.get("/api/v1/success"),
            await client.get("/api/v1/missing?secret=CORRELATION-QUERY-SENTINEL"),
            await client.get("/api/v1/conflict"),
            await client.post(
                "/api/v1/validate",
                data=json.dumps({"password": "CORRELATION-PASSWORD-SENTINEL"}),
                content_type="application/json",
            ),
        ]

    responses = asyncio.run(send_requests())
    output = capsys.readouterr().out
    records = [json.loads(line) for line in output.splitlines()]
    completed = [record for record in records if record["event"] == "request.completed"]
    by_request_id = {record["requestId"]: record for record in completed}
    expected = {
        200: ("OK", None),
        404: ("NOT_FOUND", "NOT_FOUND"),
        409: ("USERNAME_EXISTS", "USERNAME_EXISTS"),
        422: ("INVALID_INPUT", "INVALID_INPUT"),
    }

    assert len(completed) == len(responses)
    for response in responses:
        body = response.json()
        code, log_code = expected[response.status_code]
        record = by_request_id[response["X-Request-ID"]]
        assert body["requestId"] == response["X-Request-ID"]
        assert body["code"] == code
        assert record["status"] == response.status_code
        assert record.get("errorCode") == log_code
    missing = next(record for record in completed if record["status"] == 404)
    assert missing["route"] == "<unmatched>"
    assert "CORRELATION-QUERY-SENTINEL" not in output
    assert "CORRELATION-PASSWORD-SENTINEL" not in output



@override_settings(
    ROOT_URLCONF="tests.integration.api.urls",
    ALLOWED_HOSTS=["testserver"],
)
def test_concurrent_channel_and_authenticated_user_context_is_isolated(capsys):
    """验证并发请求的渠道和用户上下文互不串扰。"""
    async def send(channel: str, user_id: int) -> tuple[str, object]:
        """发送带独立身份和渠道的受保护请求。"""
        client = AsyncClient()
        response = await client.get(
            f"/api/v1/protected/{channel}?private=QUERY-SECRET-SENTINEL",
            headers={
                "Authorization": f"Bearer test-user-{user_id}",
                "X-Request-ID": f"CLIENT-{user_id}",
            },
        )
        return channel, response

    async def send_concurrently():
        """并发执行不同渠道的受保护请求。"""
        return await asyncio.gather(
            send("android-cn", 101),
            send("ios-intl", 202),
        )

    results = asyncio.run(send_concurrently())
    output = capsys.readouterr().out
    records = [json.loads(line) for line in output.splitlines()]
    completed = [record for record in records if record["event"] == "request.completed"]

    assert len(completed) == 2
    assert len({record["requestId"] for record in completed}) == 2
    expected = {101: "android-cn", 202: "ios-intl"}
    for channel, response in results:
        body = response.json()
        user_id = body["data"]["userId"]
        assert response.status_code == 200
        assert body["requestId"] == response["X-Request-ID"]
        assert body["data"]["channel"] == channel
        assert expected[user_id] == channel

    for record in completed:
        user_id = int(record["userId"])
        assert record["channel"] == expected[user_id]
        assert record["requestId"] in {
            response["X-Request-ID"] for _, response in results
        }
        assert "QUERY-SECRET-SENTINEL" not in output
        assert f"CLIENT-{user_id}" not in output
        assert expected[user_id] not in record["route"]


@override_settings(
    ROOT_URLCONF="tests.integration.api.urls",
    ALLOWED_HOSTS=["testserver"],
)
def test_credentials_and_sensitive_values_stay_out_of_every_log_sink(
    capsys, tmp_path
):
    """验证请求输入和应用异常不会泄露到任一日志输出。"""
    with _isolated_file_logging(tmp_path / "logs") as log_dir:
        capsys.readouterr()

        async def send_requests():
            """发送包含敏感哨兵的隔离请求。"""
            client = AsyncClient()
            unauthenticated = await client.get(
                "/api/v1/protected/android-cn?phone=PHONE-SENTINEL",
                headers={
                    "Authorization": "Bearer ACCESS-TOKEN-SENTINEL",
                    "Cookie": "refresh=REFRESH-TOKEN-SENTINEL",
                    "X-Forwarded-For": "FORWARDED-IP-SENTINEL",
                    "X-Request-ID": "CLIENT-REQUEST-ID-SENTINEL",
                },
            )
            invalid = await client.post(
                "/api/v1/validate",
                data=json.dumps(
                    {
                        "username": "USERNAME-SENTINEL",
                        "password": "PASSWORD-SENTINEL",
                        "phone": "PHONE-BODY-SENTINEL",
                        "history": "HEALTH-HISTORY-SENTINEL",
                    }
                ),
                content_type="application/json",
            )
            unmatched = await client.get(
                "/api/v1/missing/APPLICATION-RAW-PATH-SENTINEL"
                "?secret=APPLICATION-QUERY-SENTINEL",
            )
            return unauthenticated, invalid, unmatched

        responses = asyncio.run(send_requests())
        try:
            raise ValueError("APPLICATION-EXCEPTION-SENTINEL")
        except ValueError:
            logging.getLogger("health_service").exception(
                "APPLICATION-MESSAGE-SENTINEL",
                extra={
                    "event": "APPLICATION-EVENT-SENTINEL",
                    "requestId": "APPLICATION-REQUEST-ID-SENTINEL",
                    "method": "POST",
                    "route": "/private/APPLICATION-RAW-PATH-SENTINEL",
                    "password": "APPLICATION-PASSWORD-SENTINEL",
                    "healthRecord": "APPLICATION-HEALTH-SENTINEL",
                    "forwardedIp": "APPLICATION-FORWARDED-IP-SENTINEL",
                },
            )

        stdout = capsys.readouterr().out
        server_text = (log_dir / "server.log").read_text(encoding="utf-8")
        application_text = (log_dir / "application.log").read_text(encoding="utf-8")
        stdout_records = [json.loads(line) for line in stdout.splitlines()]
        server_records = [json.loads(line) for line in server_text.splitlines()]
        application_records = [
            json.loads(line) for line in application_text.splitlines()
        ]
        all_logs = stdout + server_text + application_text

        assert [response.status_code for response in responses] == [401, 422, 404]
        assert len(application_records) == 1
        assert application_records[0]["event"] == "application.event"
        assert "requestId" not in application_records[0]
        assert "route" not in application_records[0]
        assert application_records == [
            record for record in stdout_records if record["event"] == "application.event"
        ]
        assert all(record["event"] != "application.event" for record in server_records)
        assert [
            record for record in stdout_records if record["event"] != "application.event"
        ] == server_records

        for sentinel in (
            "PHONE-SENTINEL",
            "ACCESS-TOKEN-SENTINEL",
            "REFRESH-TOKEN-SENTINEL",
            "FORWARDED-IP-SENTINEL",
            "CLIENT-REQUEST-ID-SENTINEL",
            "USERNAME-SENTINEL",
            "PASSWORD-SENTINEL",
            "PHONE-BODY-SENTINEL",
            "HEALTH-HISTORY-SENTINEL",
            "APPLICATION-RAW-PATH-SENTINEL",
            "APPLICATION-QUERY-SENTINEL",
            "APPLICATION-MESSAGE-SENTINEL",
            "APPLICATION-EXCEPTION-SENTINEL",
            "APPLICATION-EVENT-SENTINEL",
            "APPLICATION-REQUEST-ID-SENTINEL",
            "APPLICATION-PASSWORD-SENTINEL",
            "APPLICATION-HEALTH-SENTINEL",
            "APPLICATION-FORWARDED-IP-SENTINEL",
        ):
            assert all(
                sentinel not in response.content.decode() for response in responses
            )
            assert sentinel not in all_logs


@override_settings(
    ROOT_URLCONF="tests.integration.api.urls",
    ALLOWED_HOSTS=["testserver"],
)
def test_api_and_application_logs_are_routed_without_duplicates(capsys, tmp_path):
    """验证 API 与通用应用事件分别写入 stdout 和对应文件。"""
    with _isolated_file_logging(tmp_path / "logs") as log_dir:
        capsys.readouterr()

        async def send_success():
            """发送隔离的成功请求。"""
            return await AsyncClient().get("/api/v1/success")

        response = asyncio.run(send_success())
        logging.getLogger("health_service").info(
            "APPLICATION-MESSAGE-SENTINEL",
            extra={
                "event": "application.test",
                "requestId": "VALID-APPLICATION-REQUEST-ID-SENTINEL",
                "route": "/private/VALID-APPLICATION-RAW-PATH-SENTINEL",
                "password": "VALID-APPLICATION-PASSWORD-SENTINEL",
                "healthRecord": "VALID-APPLICATION-HEALTH-SENTINEL",
                "forwardedIp": "VALID-APPLICATION-FORWARDED-IP-SENTINEL",
            },
        )
        stdout = capsys.readouterr().out
        server_text = (log_dir / "server.log").read_text(encoding="utf-8")
        application_text = (log_dir / "application.log").read_text(encoding="utf-8")
        stdout_records = [json.loads(line) for line in stdout.splitlines()]
        server_records = [json.loads(line) for line in server_text.splitlines()]
        application_records = [
            json.loads(line) for line in application_text.splitlines()
        ]
        request_id = response["X-Request-ID"]
        stdout_completed = [
            record
            for record in stdout_records
            if record["event"] == "request.completed"
        ]
        file_completed = [
            record
            for record in server_records
            if record["event"] == "request.completed"
        ]
        stdout_application = [
            record for record in stdout_records if record["event"] == "application.test"
        ]

        assert response.status_code == 200
        assert response.json()["requestId"] == request_id
        assert len(stdout_completed) == len(file_completed) == 1
        assert stdout_completed[0]["requestId"] == request_id
        assert file_completed[0] == stdout_completed[0]
        assert len(stdout_application) == 1
        assert application_records == stdout_application
        assert set(application_records[0]) == {"timestamp", "level", "event"}
        assert all(record["event"] != "application.test" for record in server_records)
        assert all(
            record["event"] != "request.completed" for record in application_records
        )
        for output in (stdout, server_text, application_text):
            for sentinel in (
                "APPLICATION-MESSAGE-SENTINEL",
                "VALID-APPLICATION-REQUEST-ID-SENTINEL",
                "VALID-APPLICATION-RAW-PATH-SENTINEL",
                "VALID-APPLICATION-PASSWORD-SENTINEL",
                "VALID-APPLICATION-HEALTH-SENTINEL",
                "VALID-APPLICATION-FORWARDED-IP-SENTINEL",
            ):
                assert sentinel not in output
