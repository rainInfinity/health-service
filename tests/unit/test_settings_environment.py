"""验证 Django 设置从运行环境读取配置。"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_REQUIRED_SETTINGS = {
    "DJANGO_SECRET_KEY": "unit-test-secret-key",
    "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1",
    "POSTGRES_DB": "health_service",
    "POSTGRES_USER": "health_service",
    "POSTGRES_PASSWORD": "unit-test-password",
    "POSTGRES_HOST": "database-from-file",
    "JWT_SIGNING_KEY": "unit-test-independent-jwt-signing-key",
}
_SETTINGS_SCRIPT = """
import json
import config.settings as settings

database = settings.DATABASES["default"]
print(json.dumps({
    "jwt_signing_key": settings.NINJA_JWT["SIGNING_KEY"],
    "access_token_lifetime_seconds": settings.NINJA_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds(),
    "refresh_token_lifetime_seconds": settings.NINJA_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds(),
    "secret_key": settings.SECRET_KEY,
    "debug": settings.DEBUG,
    "allowed_hosts": settings.ALLOWED_HOSTS,
    "database_engine": database["ENGINE"],
    "database_name": database["NAME"],
    "database_host": database["HOST"],
    "database_port": database["PORT"],
    "log_dir": str(settings.LOG_DIR),
    "task_data_root": str(settings.TASK_DATA_ROOT),
    "model_artifact_root": str(settings.MODEL_ARTIFACT_ROOT),
    "max_upload_bytes": settings.MAX_UPLOAD_BYTES,
    "redis_url": settings.REDIS_URL,
    "inference_stream": settings.INFERENCE_STREAM,
    "inference_group": settings.INFERENCE_GROUP,
    "inference_lease_seconds": settings.INFERENCE_LEASE_SECONDS,
    "inference_heartbeat_seconds": settings.INFERENCE_HEARTBEAT_SECONDS,
    "inference_dispatch_interval_seconds": settings.INFERENCE_DISPATCH_INTERVAL_SECONDS,
    "inference_max_attempts": settings.INFERENCE_MAX_ATTEMPTS,
    "inference_poll_milliseconds": settings.INFERENCE_POLL_MILLISECONDS,
}))
"""


def _isolated_project(tmp_path: Path) -> Path:
    """复制配置模块到临时项目，避免触碰仓库中的本地 .env。"""
    project_root = tmp_path / "project"
    config_dir = project_root / "src" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "__init__.py").write_text("", encoding="utf-8")
    shutil.copyfile(_REPOSITORY_ROOT / "src/config/settings.py", config_dir / "settings.py")
    return project_root


def _dotenv_content(values: dict[str, str]) -> str:
    """将环境变量映射序列化为 dotenv 文本。"""
    return "".join(f"{key}={value}\n" for key, value in values.items())


def _run_settings(
    project_root: Path,
    working_directory: Path,
    process_settings: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """在隔离进程中加载临时项目设置。"""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DJANGO_", "POSTGRES_"))
        and key
        not in {
            "LOG_DIR",
            "JWT_SIGNING_KEY",
            "TASK_DATA_ROOT",
            "MODEL_ARTIFACT_ROOT",
            "MAX_UPLOAD_BYTES",
            "REDIS_URL",
            "INFERENCE_STREAM",
            "INFERENCE_GROUP",
            "INFERENCE_LEASE_SECONDS",
            "INFERENCE_HEARTBEAT_SECONDS",
            "INFERENCE_DISPATCH_INTERVAL_SECONDS",
            "INFERENCE_MAX_ATTEMPTS",
            "INFERENCE_POLL_MILLISECONDS",
        }
    }
    for name in ("PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT"):
        environment.pop(name, None)
    environment["PYTHONPATH"] = str(project_root / "src")
    environment.update(process_settings or {})
    return subprocess.run(
        [sys.executable, "-c", _SETTINGS_SCRIPT],
        cwd=working_directory,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )


def _settings_output(process: subprocess.CompletedProcess[str]) -> dict[str, object]:
    """读取成功的隔离设置进程输出。"""
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


def test_settings_load_required_values_from_project_dotenv(tmp_path: Path):
    """验证必需设置可从项目 `.env` 加载。"""
    project_root = _isolated_project(tmp_path)
    (project_root / ".env").write_text(
        _dotenv_content({**_REQUIRED_SETTINGS, "DJANGO_DEBUG": "true", "POSTGRES_PORT": "55432"}),
        encoding="utf-8",
    )

    settings = _settings_output(_run_settings(project_root, project_root))

    assert settings["secret_key"] == _REQUIRED_SETTINGS["DJANGO_SECRET_KEY"]
    assert settings["debug"] is True
    assert settings["allowed_hosts"] == ["localhost", "127.0.0.1"]
    assert settings["database_engine"] == "django.db.backends.postgresql"
    assert settings["database_host"] == "database-from-file"
    assert settings["database_port"] == "55432"

    assert settings["jwt_signing_key"] == _REQUIRED_SETTINGS["JWT_SIGNING_KEY"]
    assert settings["access_token_lifetime_seconds"] == 604800
    assert settings["refresh_token_lifetime_seconds"] == 2592000


def test_settings_work_with_only_process_environment(tmp_path: Path):
    """验证仅使用进程环境也能加载设置。"""
    project_root = _isolated_project(tmp_path)

    settings = _settings_output(_run_settings(project_root, project_root, _REQUIRED_SETTINGS))

    assert settings["secret_key"] == _REQUIRED_SETTINGS["DJANGO_SECRET_KEY"]
    assert settings["database_engine"] == "django.db.backends.postgresql"
    assert settings["database_host"] == "database-from-file"
    assert settings["database_port"] == "5432"


def test_process_environment_overrides_dotenv(tmp_path: Path):
    """验证进程环境优先于项目 `.env`。"""
    project_root = _isolated_project(tmp_path)
    (project_root / ".env").write_text(_dotenv_content(_REQUIRED_SETTINGS), encoding="utf-8")

    settings = _settings_output(
        _run_settings(
            project_root,
            project_root,
            {
                "DJANGO_ALLOWED_HOSTS": "process.example.test",
                "POSTGRES_HOST": "database-from-process",
            },
        )
    )

    assert settings["allowed_hosts"] == ["process.example.test"]
    assert settings["database_host"] == "database-from-process"


def test_empty_required_process_value_is_not_replaced_from_dotenv(tmp_path: Path):
    """验证空进程值不会回退到 `.env`。"""
    project_root = _isolated_project(tmp_path)
    (project_root / ".env").write_text(_dotenv_content(_REQUIRED_SETTINGS), encoding="utf-8")

    process = _run_settings(project_root, project_root, {"DJANGO_SECRET_KEY": ""})

    assert process.returncode != 0
    assert "Missing required environment variable: DJANGO_SECRET_KEY" in process.stderr


def test_missing_jwt_signing_key_fails(tmp_path: Path):
    """验证独立 JWT 签名密钥缺失时 Django 明确拒绝启动。"""
    project_root = _isolated_project(tmp_path)
    required_settings = {
        name: value for name, value in _REQUIRED_SETTINGS.items() if name != "JWT_SIGNING_KEY"
    }

    process = _run_settings(project_root, project_root, required_settings)

    assert process.returncode != 0
    assert "Missing required environment variable: JWT_SIGNING_KEY" in process.stderr


def test_settings_find_project_dotenv_from_another_working_directory(tmp_path: Path):
    """验证项目 `.env` 查找不依赖当前工作目录。"""
    project_root = _isolated_project(tmp_path)
    (project_root / ".env").write_text(_dotenv_content(_REQUIRED_SETTINGS), encoding="utf-8")
    working_directory = tmp_path / "outside-project"
    working_directory.mkdir()
    (working_directory / ".env").write_text(
        _dotenv_content({**_REQUIRED_SETTINGS, "POSTGRES_HOST": "decoy-working-directory"}),
        encoding="utf-8",
    )

    settings = _settings_output(_run_settings(project_root, working_directory))

    assert settings["database_host"] == "database-from-file"


def test_only_template_cannot_supply_required_configuration(tmp_path: Path):
    """验证 `.env.template` 不会充当运行时配置。"""
    project_root = _isolated_project(tmp_path)
    shutil.copyfile(_REPOSITORY_ROOT / ".env.template", project_root / ".env.template")

    process = _run_settings(project_root, project_root)

    assert process.returncode != 0
    assert "Missing required environment variable: DJANGO_SECRET_KEY" in process.stderr


def test_template_is_not_loaded_as_runtime_configuration(tmp_path: Path):
    """验证模板文件不会参与运行时设置加载。"""
    project_root = _isolated_project(tmp_path)
    shutil.copyfile(_REPOSITORY_ROOT / ".env.template", project_root / ".env.template")

    settings = _settings_output(_run_settings(project_root, project_root, _REQUIRED_SETTINGS))

    assert settings["debug"] is False


def test_log_dir_defaults_to_project_root(tmp_path: Path):
    """未设置 LOG_DIR 时使用项目根目录下的默认日志目录。"""
    project_root = _isolated_project(tmp_path)

    settings = _settings_output(_run_settings(project_root, project_root, _REQUIRED_SETTINGS))

    expected = project_root / "logs"
    assert settings["log_dir"] == str(expected)
    assert expected.is_dir()


def test_log_dir_resolves_relative_values_from_project_root(tmp_path: Path):
    """相对 LOG_DIR 从项目根目录解析，即使配置来自 .env。"""
    project_root = _isolated_project(tmp_path)
    (project_root / ".env").write_text(
        _dotenv_content({**_REQUIRED_SETTINGS, "LOG_DIR": "var/logs"}),
        encoding="utf-8",
    )

    settings = _settings_output(_run_settings(project_root, project_root))

    expected = project_root / "var" / "logs"
    assert settings["log_dir"] == str(expected)
    assert expected.is_dir()


def test_log_dir_accepts_absolute_values(tmp_path: Path):
    """绝对 LOG_DIR 保持原路径并创建目录。"""
    project_root = _isolated_project(tmp_path)
    expected = tmp_path / "absolute-logs"

    settings = _settings_output(
        _run_settings(
            project_root,
            project_root,
            {**_REQUIRED_SETTINGS, "LOG_DIR": str(expected)},
        )
    )

    assert settings["log_dir"] == str(expected)
    assert expected.is_dir()


def test_log_dir_rejects_a_file_instead_of_a_directory(tmp_path: Path):
    """日志路径无法作为目录创建时，设置加载明确失败。"""
    project_root = _isolated_project(tmp_path)
    blocked_path = tmp_path / "not-a-directory"
    blocked_path.write_text("occupied", encoding="utf-8")

    process = _run_settings(
        project_root,
        project_root,
        {**_REQUIRED_SETTINGS, "LOG_DIR": str(blocked_path)},
    )

    assert process.returncode != 0
    assert "LOG_DIR must be a writable directory" in process.stderr


def test_inference_settings_defaults_create_project_relative_roots(tmp_path: Path):
    """验证推理配置默认值及项目相对存储目录。"""
    project_root = _isolated_project(tmp_path)

    settings = _settings_output(_run_settings(project_root, project_root, _REQUIRED_SETTINGS))

    assert settings["task_data_root"] == str(project_root / "var" / "tasks")
    assert settings["model_artifact_root"] == str(project_root / "var" / "model-artifacts")
    assert (project_root / "var" / "tasks").is_dir()
    assert (project_root / "var" / "model-artifacts").is_dir()
    assert settings["max_upload_bytes"] == 52_428_800
    assert settings["redis_url"] == "redis://127.0.0.1:6379/0"
    assert settings["inference_stream"] == "health:inference"
    assert settings["inference_group"] == "health-inference"
    assert settings["inference_lease_seconds"] == 60
    assert settings["inference_heartbeat_seconds"] == 10
    assert settings["inference_dispatch_interval_seconds"] == 30
    assert settings["inference_max_attempts"] == 3
    assert settings["inference_poll_milliseconds"] == 1000


def test_inference_process_configuration_overrides_dotenv(tmp_path: Path):
    """验证进程配置优先于同名 `.env` 配置。"""
    project_root = _isolated_project(tmp_path)
    (project_root / ".env").write_text(
        _dotenv_content({**_REQUIRED_SETTINGS, "TASK_DATA_ROOT": "from-file/tasks"}),
        encoding="utf-8",
    )
    process_root = tmp_path / "from-process"

    settings = _settings_output(
        _run_settings(
            project_root,
            project_root,
            {**_REQUIRED_SETTINGS, "TASK_DATA_ROOT": str(process_root)},
        )
    )

    assert settings["task_data_root"] == str(process_root)
    assert process_root.is_dir()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAX_UPLOAD_BYTES", "0"),
        ("INFERENCE_LEASE_SECONDS", "-1"),
        ("INFERENCE_HEARTBEAT_SECONDS", "0"),
        ("INFERENCE_DISPATCH_INTERVAL_SECONDS", "invalid"),
        ("INFERENCE_MAX_ATTEMPTS", "0"),
        ("INFERENCE_POLL_MILLISECONDS", "0"),
    ],
)
def test_nonpositive_or_invalid_inference_integer_settings_fail(
    tmp_path: Path,
    name: str,
    value: str,
):
    """验证非法正整数配置使 Django 启动明确失败。"""
    project_root = _isolated_project(tmp_path)

    process = _run_settings(project_root, project_root, {**_REQUIRED_SETTINGS, name: value})

    assert process.returncode != 0
    assert f"{name} must be a positive integer" in process.stderr


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("REDIS_URL", ""),
        ("INFERENCE_STREAM", " "),
        ("INFERENCE_GROUP", ""),
    ],
)
def test_empty_inference_names_or_url_fail(tmp_path: Path, name: str, value: str):
    """验证队列名称和 Redis URL 不允许为空。"""
    project_root = _isolated_project(tmp_path)

    process = _run_settings(project_root, project_root, {**_REQUIRED_SETTINGS, name: value})

    assert process.returncode != 0
    assert f"{name} must not be empty" in process.stderr


def test_heartbeat_must_be_less_than_one_third_of_lease(tmp_path: Path):
    """验证心跳间隔严格小于租约时长的三分之一。"""
    project_root = _isolated_project(tmp_path)

    process = _run_settings(
        project_root,
        project_root,
        {
            **_REQUIRED_SETTINGS,
            "INFERENCE_LEASE_SECONDS": "60",
            "INFERENCE_HEARTBEAT_SECONDS": "20",
        },
    )

    assert process.returncode != 0
    assert "less than one third" in process.stderr


@pytest.mark.parametrize("name", ["TASK_DATA_ROOT", "MODEL_ARTIFACT_ROOT"])
def test_storage_root_rejects_a_file_instead_of_a_directory(
    tmp_path: Path,
    name: str,
):
    """验证文件存储根必须是可读写目录。"""
    project_root = _isolated_project(tmp_path)
    blocked_path = tmp_path / "not-a-directory"
    blocked_path.write_text("occupied", encoding="utf-8")

    process = _run_settings(
        project_root,
        project_root,
        {**_REQUIRED_SETTINGS, name: str(blocked_path)},
    )

    assert process.returncode != 0
    assert f"{name} must be a writable directory" in process.stderr
