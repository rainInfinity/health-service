"""在重新加载环境的独立进程中验证真实登录、刷新及存量令牌期限。"""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import jwt
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client

_WORKER = """
import json, sys, django
django.setup()
from tests.integration.api.test_token_lifetimes import _api_probe
print(json.dumps(_api_probe(json.load(sys.stdin))))
"""


def _api_probe(data):
    """调用真实认证路由，仅控制时间，不替换签发和校验逻辑。"""
    frozen = datetime.fromtimestamp(data["at"], UTC)

    class Clock(datetime):
        """向 JWT 验证提供确定的当前时刻。"""

        @classmethod
        def now(cls, tz=None):
            """保留 JWT 库所用的带时区当前时间语义。"""
            return frozen.astimezone(tz) if tz else frozen.replace(tzinfo=None)

    client = Client()
    channel = data["channel"]
    with (
        patch("jwt.api_jwt.datetime", Clock),
        patch("ninja_jwt.tokens.aware_utcnow", return_value=frozen),
    ):
        if data["action"] == "login":
            response = client.post(
                f"/api/v1/{channel}/auth/login",
                {
                    "username": data["username"],
                    "password": data["password"],
                },
                content_type="application/json",
            )
        elif data["action"] == "refresh":
            response = client.post(
                f"/api/v1/{channel}/auth/refresh",
                {
                    "refreshToken": data["refresh"],
                },
                content_type="application/json",
            )
        else:
            response = client.get(
                f"/api/v1/{channel}/users/me", HTTP_AUTHORIZATION=f"Bearer {data['access']}"
            )
    return {"status": response.status_code, "body": response.json()}


def _stage(tmp_path, data, *, access_seconds, refresh_seconds):
    """重新启动签发配置并明确连接 pytest 测试库，绝不继承本机业务库。"""
    database = connection.settings_dict
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "DJANGO_SECRET_KEY": settings.SECRET_KEY,
        "JWT_SIGNING_KEY": settings.NINJA_JWT["SIGNING_KEY"],
        "DJANGO_ALLOWED_HOSTS": "testserver",
        "POSTGRES_DB": database["NAME"],
        "POSTGRES_USER": database["USER"],
        "POSTGRES_PASSWORD": database["PASSWORD"],
        "POSTGRES_HOST": database["HOST"],
        "POSTGRES_PORT": str(database["PORT"]),
        "LOG_DIR": str(tmp_path / uuid4().hex),
        "TASK_DATA_ROOT": str(tmp_path / "tasks"),
        "MODEL_ARTIFACT_ROOT": str(tmp_path / "models"),
        "JWT_ACCESS_TOKEN_LIFETIME_SECONDS": str(access_seconds),
        "JWT_REFRESH_TOKEN_LIFETIME_SECONDS": str(refresh_seconds),
        "PYTHONPATH": os.pathsep.join(
            [
                str(Path(__file__).resolve().parents[3]),
                str(Path(__file__).resolve().parents[3] / "src"),
            ]
        ),
    }
    result = subprocess.run(
        [sys.executable, "-c", _WORKER],
        input=json.dumps(data),
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.splitlines()[-1])


def _claims(raw_token):
    """校验签名后读取声明；到期行为由受控时钟下的真实接口验证。"""
    return jwt.decode(
        raw_token,
        settings.NINJA_JWT["SIGNING_KEY"],
        algorithms=["HS256"],
        options={"verify_exp": False, "verify_iat": False},
    )


@pytest.mark.parametrize("channel", ["ios-cn", "manager"])
@pytest.mark.django_db(transaction=True)
def test_login_and_rotation_use_configured_lifetimes_after_restart(tmp_path, channel):
    """真实普通与管理登录和轮换使用环境期限，旧 Refresh 仍被撤销。"""
    password = "Dawn-valley-483!strong"
    user = get_user_model().objects.create_user(
        username=f"ttl-{uuid4().hex}", password=password, is_staff=channel == "manager"
    )
    data = {
        "at": int(datetime.now(UTC).timestamp()),
        "channel": channel,
        "action": "login",
        "username": user.username,
        "password": password,
    }
    logged_in = _stage(tmp_path, data, access_seconds=900, refresh_seconds=7200)
    assert logged_in["status"] == 200
    pair = logged_in["body"]["data"]
    rotated = _stage(
        tmp_path,
        {**data, "action": "refresh", "refresh": pair["refreshToken"]},
        access_seconds=900,
        refresh_seconds=7200,
    )
    assert rotated["status"] == 200
    for tokens in (pair, rotated["body"]["data"]):
        for field, duration in (("accessToken", 900), ("refreshToken", 7200)):
            claims = _claims(tokens[field])
            assert type(claims["exp"]) is int and type(claims["iat"]) is int
            assert claims["exp"] - claims["iat"] == duration
            assert claims["channel"] == channel
    replayed = _stage(
        tmp_path,
        {**data, "action": "refresh", "refresh": pair["refreshToken"]},
        access_seconds=900,
        refresh_seconds=7200,
    )
    assert replayed["status"] == 401


@pytest.mark.django_db(transaction=True)
def test_restart_does_not_shorten_or_revive_existing_tokens(tmp_path):
    """新配置只改变新令牌，旧令牌依原 exp 判定，不需要真实等待。"""
    password = "Dawn-valley-483!strong"
    user = get_user_model().objects.create_user(username=f"ttl-{uuid4().hex}", password=password)
    at = int(datetime.now(UTC).timestamp())
    data = {
        "at": at,
        "channel": "ios-cn",
        "action": "login",
        "username": user.username,
        "password": password,
    }
    old = _stage(tmp_path, data, access_seconds=1800, refresh_seconds=7200)["body"]["data"]
    protected = _stage(
        tmp_path,
        {**data, "at": at + 10, "action": "me", "access": old["accessToken"]},
        access_seconds=1,
        refresh_seconds=2,
    )
    assert protected["status"] == 200
    rotated = _stage(
        tmp_path,
        {**data, "at": at + 10, "action": "refresh", "refresh": old["refreshToken"]},
        access_seconds=1,
        refresh_seconds=2,
    )
    assert rotated["status"] == 200
    assert _claims(rotated["body"]["data"]["accessToken"])["exp"] - (at + 10) == 1
    assert _claims(rotated["body"]["data"]["refreshToken"])["exp"] - (at + 10) == 2
    untouched = _stage(tmp_path, data, access_seconds=1800, refresh_seconds=7200)["body"]["data"]
    expired_access = _stage(
        tmp_path,
        {**data, "at": at + 8000, "action": "me", "access": untouched["accessToken"]},
        access_seconds=36000,
        refresh_seconds=72000,
    )
    expired_refresh = _stage(
        tmp_path,
        {**data, "at": at + 8000, "action": "refresh", "refresh": untouched["refreshToken"]},
        access_seconds=36000,
        refresh_seconds=72000,
    )
    assert expired_access["status"] == 401
    assert expired_refresh["status"] == 401
