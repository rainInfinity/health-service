import asyncio
import json

from django.test import AsyncClient, override_settings

from tests.log_records import parse_console_records


def _schema_properties(schema, components):
    if "$ref" in schema:
        component_name = schema["$ref"].rsplit("/", maxsplit=1)[-1]
        return _schema_properties(components[component_name], components)
    properties = dict(schema.get("properties", {}))
    for branch_name in ("allOf", "anyOf", "oneOf"):
        for branch in schema.get(branch_name, []):
            properties.update(_schema_properties(branch, components))
    return properties


@override_settings(
    ROOT_URLCONF="tests.integration.api.urls",
    ALLOWED_HOSTS=["testserver"],
)
def test_success_statuses_and_empty_payload_use_the_declared_envelope():
    async def send_requests():
        client = AsyncClient()
        return (
            await client.get("/api/v1/success"),
            await client.post("/api/v1/created"),
            await client.delete("/api/v1/empty"),
        )

    success, created, empty = asyncio.run(send_requests())

    assert success.status_code == 200
    assert success.json() == {
        "code": "OK",
        "message": "成功",
        "data": {"value": "ready"},
        "requestId": success["X-Request-ID"],
    }
    assert created.status_code == 201
    assert created.json()["data"] == {"value": "created"}
    assert created.json()["requestId"] == created["X-Request-ID"]
    assert empty.status_code == 200
    assert empty.json()["data"] is None
    assert empty.json()["requestId"] == empty["X-Request-ID"]
    assert all("request_id" not in response.json() for response in (success, created, empty))


@override_settings(
    ROOT_URLCONF="tests.integration.api.urls",
    ALLOWED_HOSTS=["testserver"],
)
def test_known_errors_and_route_fallbacks_keep_safe_statuses_and_envelopes():
    async def send_requests():
        client = AsyncClient()
        return [
            (
                await client.get("/api/v1/invalid-credentials"),
                401,
                "INVALID_CREDENTIALS",
            ),
            (await client.get("/api/v1/protected/android-cn"), 401, "INVALID_TOKEN"),
            (await client.get("/api/v1/permission"), 403, "CHANNEL_FORBIDDEN"),
            (await client.get("/api/v1/conflict"), 409, "USERNAME_EXISTS"),
            (await client.get("/api/v1/generic-conflict"), 409, "CONFLICT"),
            (await client.get("/api/v1/rate-limited"), 429, "RATE_LIMITED"),
            (
                await client.post(
                    "/api/v1/validate",
                    data=json.dumps({"password": "PASSWORD-SENTINEL"}),
                    content_type="application/json",
                ),
                422,
                "INVALID_INPUT",
            ),
            (
                await client.get("/api/v1/missing?secret=QUERY-SENTINEL"),
                404,
                "NOT_FOUND",
            ),
            (await client.post("/api/v1/success"), 405, "METHOD_NOT_ALLOWED"),
        ]

    responses = asyncio.run(send_requests())

    for response, status, code in responses:
        body = response.json()
        assert response.status_code == status
        assert body["code"] == code
        assert body["data"] is None
        assert body["requestId"] == response["X-Request-ID"]
        assert set(body) == {"code", "message", "data", "requestId"}
        assert "PASSWORD-SENTINEL" not in response.content.decode()
    rate_limited = next(response for response, status, _ in responses if status == 429)
    assert rate_limited["Retry-After"] == "2"


@override_settings(
    ROOT_URLCONF="tests.integration.api.urls",
    ALLOWED_HOSTS=["testserver"],
)
def test_unknown_exception_is_safe_in_both_debug_modes(capsys):
    async def send_failure():
        client = AsyncClient()
        return await client.get(
            "/api/v1/failure?token=QUERY-TOKEN-SENTINEL",
            headers={
                "Authorization": "Bearer ACCESS-TOKEN-SENTINEL",
                "Cookie": "refresh=REFRESH-TOKEN-SENTINEL",
                "X-Forwarded-For": "FORWARDED-IP-SENTINEL",
            },
        )

    for debug in (False, True):
        with override_settings(DEBUG=debug):
            response = asyncio.run(send_failure())
            output = capsys.readouterr().out

        assert response.status_code == 500
        assert response.json()["code"] == "INTERNAL_ERROR"
        assert response.json()["requestId"] == response["X-Request-ID"]
        assert "EXCEPTION-SENTINEL" not in response.content.decode()
        assert "RuntimeError" not in response.content.decode()
        assert "SQL-SENTINEL" not in response.content.decode()
        assert "/private/config/path" not in response.content.decode()
        for sentinel in (
            "QUERY-TOKEN-SENTINEL",
            "ACCESS-TOKEN-SENTINEL",
            "REFRESH-TOKEN-SENTINEL",
            "FORWARDED-IP-SENTINEL",
            "EXCEPTION-SENTINEL",
            "SQL-SENTINEL",
            "/private/config/path",
        ):
            assert sentinel not in output

        records = parse_console_records(output)
        exception_records = [record for record in records if record["event"] == "request.exception"]
        completion_records = [
            record for record in records if record["event"] == "request.completed"
        ]
        assert len(exception_records) == 1
        assert len(completion_records) == 1
        exception_record = exception_records[0]
        completion_record = completion_records[0]
        assert exception_record["exceptionType"] == "RuntimeError"
        assert exception_record["requestId"] == response["X-Request-ID"]
        assert completion_record["requestId"] == response["X-Request-ID"]
        assert completion_record["status"] == "500"
        assert completion_record["errorCode"] == "INTERNAL_ERROR"
        locations = json.loads(exception_record["locations"])
        assert locations
        assert all(set(location) == {"module", "function", "line"} for location in locations)


@override_settings(
    ROOT_URLCONF="tests.integration.api.urls",
    ALLOWED_HOSTS=["testserver"],
)
def test_openapi_matches_http_envelopes_and_non_json_paths_are_not_wrapped():
    async def send_requests():
        client = AsyncClient()
        schema_response = await client.get("/api/v1/openapi.json")
        docs = await client.get("/api/v1/docs")
        admin_page = await client.get("/admin/test-page")
        download = await client.get("/download")
        stream = download.streaming_content
        if hasattr(stream, "__aiter__"):
            content = b"".join([chunk async for chunk in stream])
        else:
            content = b"".join(stream)
        actual_responses = [
            (
                "/api/v1/success",
                "get",
                await client.get("/api/v1/success"),
            ),
            (
                "/api/v1/protected/{channel}",
                "get",
                await client.get("/api/v1/protected/android-cn"),
            ),
            (
                "/api/v1/permission",
                "get",
                await client.get("/api/v1/permission"),
            ),
            (
                "/api/v1/created",
                "post",
                await client.post("/api/v1/created"),
            ),
            (
                "/api/v1/success",
                "get",
                await client.get("/api/v1/missing"),
            ),
            (
                "/api/v1/success",
                "get",
                await client.post("/api/v1/success"),
            ),
            (
                "/api/v1/conflict",
                "get",
                await client.get("/api/v1/conflict"),
            ),
            (
                "/api/v1/rate-limited",
                "get",
                await client.get("/api/v1/rate-limited"),
            ),
            (
                "/api/v1/validate",
                "post",
                await client.post(
                    "/api/v1/validate",
                    data=json.dumps({}),
                    content_type="application/json",
                ),
            ),
            (
                "/api/v1/failure",
                "get",
                await client.get("/api/v1/failure"),
            ),
        ]
        return schema_response, docs, admin_page, download, content, actual_responses

    schema_response, docs, admin_page, download, content, actual_responses = asyncio.run(
        send_requests()
    )
    schema = schema_response.json()
    components = schema.get("components", {}).get("schemas", {})

    for path, method, response in actual_responses:
        operation = schema["paths"][path][method]
        declaration = operation["responses"][str(response.status_code)]
        response_schema = declaration["content"]["application/json"]["schema"]
        properties = _schema_properties(response_schema, components)
        body = response.json()

        assert set(body) == set(properties)
        assert set(body) == {"code", "message", "data", "requestId"}
        assert properties["code"]["type"] == "string"
        assert properties["requestId"]["type"] == "string"
        assert body["requestId"] == response["X-Request-ID"]
        if body["data"] is not None:
            data_properties = _schema_properties(properties["data"], components)
            assert set(body["data"]) == set(data_properties)
        else:
            assert body["data"] is None

    assert docs.status_code == 200
    assert docs["Content-Type"].startswith("text/html")
    assert b"requestId" not in docs.content
    assert admin_page.status_code == 200
    assert admin_page.content == b"<html>admin</html>"
    assert "X-Request-ID" not in admin_page
    assert download.status_code == 200
    assert content == b"download"
    assert "X-Request-ID" not in download
