"""Tests for launch.sh + start_server.py: secrets come from Infisical, never from an env file."""

import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent


def _write_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def sandbox(tmp_path):
    """A copy of the launcher with a stub `uv` that records the env it was given."""
    app = tmp_path / "app"
    app.mkdir()
    for name in ("launch.sh", "start_server.py"):
        shutil.copy(REPO / name, app / name)
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    record = tmp_path / "uv-env.txt"
    _write_exe(bin_dir / "uv", f'#!/bin/bash\nenv > "{record}"\necho "ARGS=$*" >> "{record}"\n')
    py_args = tmp_path / "python3-args.txt"
    _write_exe(bin_dir / "python3", f'#!/bin/bash\necho "$*" > "{py_args}"\nexec "{sys.executable}" "$@"\n')
    env = {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin"}
    return {"app": app, "home": home, "record": record, "py_args": py_args, "env": env}


def _fake_wrapper(sandbox, injected: str) -> None:
    """Stand-in for infisical-run.sh: exports `injected` (shell syntax), then runs its args."""
    _write_exe(
        sandbox["home"] / ".claude/scripts/infisical-run.sh",
        f'#!/bin/bash\n{injected}\nexec "$@"\n',
    )


def _launch(sandbox, extra_env=None):
    env = dict(sandbox["env"], **(extra_env or {}))
    return subprocess.run(
        ["bash", str(sandbox["app"] / "launch.sh")],
        env=env, capture_output=True, text=True, timeout=10,
    )


def _stage2(sandbox, extra_env):
    env = dict(sandbox["env"], **extra_env)
    return subprocess.run(
        ["python3", "-I", "-S", str(sandbox["app"] / "start_server.py")],
        env=env, capture_output=True, text=True, timeout=10,
    )


def _recorded(sandbox) -> dict[str, str]:
    lines = sandbox["record"].read_text().splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


def test_key_comes_from_infisical_wrapper_not_envrc(sandbox):
    (sandbox["app"] / ".envrc").write_text("export TELEGRAM_BOT_TOKEN=from-envrc TELEGRAM_CHAT_ID=from-envrc\n")
    _fake_wrapper(sandbox, "export TELEGRAM_BOT_TOKEN_CLAUDIA=from-infisical TELEGRAM_CHAT_ID=42")
    result = _launch(sandbox)
    assert result.returncode == 0, result.stderr
    seen = _recorded(sandbox)
    assert seen["TELEGRAM_BOT_TOKEN"] == "from-infisical"
    assert seen["TELEGRAM_CHAT_ID"] == "42"
    assert "claude-telegram-bridge" in seen["ARGS"]


def test_stage2_python_skips_site_startup_code(sandbox):
    # -S keeps site-packages .pth files from running while every secret is in the env.
    _fake_wrapper(sandbox, "export TELEGRAM_BOT_TOKEN_CLAUDIA=from-infisical TELEGRAM_CHAT_ID=42")
    result = _launch(sandbox)
    assert result.returncode == 0, result.stderr
    assert sandbox["py_args"].read_text().split()[:2] == ["-I", "-S"]


def test_parent_key_does_not_stand_in_for_infisical(sandbox):
    _fake_wrapper(sandbox, ":")  # Infisical injects nothing
    result = _launch(sandbox, extra_env={
        "TELEGRAM_BOT_TOKEN_CLAUDIA": "stale", "TELEGRAM_BOT_TOKEN": "stale", "TELEGRAM_CHAT_ID": "stale",
    })
    assert result.returncode != 0
    assert "TELEGRAM_BOT_TOKEN_CLAUDIA" in result.stderr
    assert not sandbox["record"].exists()


def test_only_the_bridge_secrets_reach_the_server(sandbox):
    result = _stage2(sandbox, {
        "TELEGRAM_BOT_TOKEN_CLAUDIA": "k",
        "TELEGRAM_CHAT_ID": "42",
        "TELEGRAM_BOT_TOKEN_IRIS": "leak",  # another bot's token
        "GITHUB_TOKEN": "leak",
        "ATLASSIAN-SW-API": "leak",  # not a shell identifier: invisible to bash's compgen
        "a.b": "leak",
        "UV_PUBLISH_TOKEN": "leak",
        "INFISICAL_TOKEN": "leak",
    })
    assert result.returncode == 0, result.stderr
    seen = _recorded(sandbox)
    assert seen["TELEGRAM_BOT_TOKEN"] == "k"
    assert "leak" not in sandbox["record"].read_text()


def test_missing_wrapper_fails_loudly(sandbox):
    (sandbox["app"] / ".envrc").write_text("export TELEGRAM_BOT_TOKEN=from-envrc TELEGRAM_CHAT_ID=from-envrc\n")
    result = _launch(sandbox)
    assert result.returncode != 0
    assert "infisical-run.sh" in result.stderr
    assert not sandbox["record"].exists()


def test_missing_key_fails_loudly(sandbox):
    result = _stage2(sandbox, {})
    assert result.returncode != 0
    assert "TELEGRAM_BOT_TOKEN_CLAUDIA" in result.stderr
    assert not sandbox["record"].exists()


def test_key_never_appears_in_argv(sandbox):
    result = _stage2(sandbox, {"TELEGRAM_BOT_TOKEN_CLAUDIA": "secret-k", "TELEGRAM_CHAT_ID": "42"})
    assert result.returncode == 0, result.stderr
    assert "secret-k" not in _recorded(sandbox)["ARGS"]


def test_missing_chat_id_alone_fails_loudly(sandbox):
    result = _stage2(sandbox, {"TELEGRAM_BOT_TOKEN_CLAUDIA": "k"})
    assert result.returncode != 0
    assert "TELEGRAM_CHAT_ID" in result.stderr
    assert not sandbox["record"].exists()


def test_unmapped_token_name_is_not_used(sandbox):
    # A plain TELEGRAM_BOT_TOKEN (e.g. inherited) must not stand in for the Infisical secret.
    result = _stage2(sandbox, {"TELEGRAM_BOT_TOKEN": "stale", "TELEGRAM_CHAT_ID": "42"})
    assert result.returncode != 0
    assert "TELEGRAM_BOT_TOKEN_CLAUDIA" in result.stderr
    assert not sandbox["record"].exists()
