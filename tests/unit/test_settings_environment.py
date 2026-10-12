import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_REQUIRED_SETTINGS = {
    "DJANGO_SECRET_KEY": "unit-test-secret-key",
    "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1",
    "POSTGRES_DB": "health_service",
    "POSTGRES_USER": "health_service",
    "POSTGRES_PASSWORD": "unit-test-password",
    "POSTGRES_HOST": "database-from-file",
}
_SETTINGS_SCRIPT = """
import json
import config.settings as settings

database = settings.DATABASES["default"]
print(json.dumps({
    "secret_key": settings.SECRET_KEY,
    "debug": settings.DEBUG,
    "allowed_hosts": settings.ALLOWED_HOSTS,
    "database_engine": database["ENGINE"],
    "database_name": database["NAME"],
    "database_host": database["HOST"],
    "database_port": database["PORT"],
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
    return "".join(f"{key}={value}\n" for key, value in values.items())


def _run_settings(
    project_root: Path,
    working_directory: Path,
    process_settings: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DJANGO_", "POSTGRES_"))
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
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


def test_settings_load_required_values_from_project_dotenv(tmp_path: Path):
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


def test_settings_work_with_only_process_environment(tmp_path: Path):
    project_root = _isolated_project(tmp_path)

    settings = _settings_output(_run_settings(project_root, project_root, _REQUIRED_SETTINGS))

    assert settings["secret_key"] == _REQUIRED_SETTINGS["DJANGO_SECRET_KEY"]
    assert settings["database_engine"] == "django.db.backends.postgresql"
    assert settings["database_host"] == "database-from-file"
    assert settings["database_port"] == "5432"


def test_process_environment_overrides_dotenv(tmp_path: Path):
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
    project_root = _isolated_project(tmp_path)
    (project_root / ".env").write_text(_dotenv_content(_REQUIRED_SETTINGS), encoding="utf-8")

    process = _run_settings(project_root, project_root, {"DJANGO_SECRET_KEY": ""})

    assert process.returncode != 0
    assert "Missing required environment variable: DJANGO_SECRET_KEY" in process.stderr


def test_settings_find_project_dotenv_from_another_working_directory(tmp_path: Path):
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
    project_root = _isolated_project(tmp_path)
    shutil.copyfile(_REPOSITORY_ROOT / ".env.template", project_root / ".env.template")

    process = _run_settings(project_root, project_root)

    assert process.returncode != 0
    assert "Missing required environment variable: DJANGO_SECRET_KEY" in process.stderr


def test_template_is_not_loaded_as_runtime_configuration(tmp_path: Path):
    project_root = _isolated_project(tmp_path)
    shutil.copyfile(_REPOSITORY_ROOT / ".env.template", project_root / ".env.template")

    settings = _settings_output(_run_settings(project_root, project_root, _REQUIRED_SETTINGS))

    assert settings["debug"] is False
