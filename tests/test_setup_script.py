"""Run the setup script in a temporary project with a fake psql command."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture
def setup_script(tmp_path):
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("PowerShell is required for the Windows setup script tests")

    scripts = tmp_path / "infra" / "db"
    scripts.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "infra" / "db" / "setup.ps1"
    shutil.copyfile(source, scripts / "setup.ps1")

    fake_psql = tmp_path / "fake-psql.ps1"
    fake_psql.write_text("""
@{ arguments = @($args); password = $env:PGPASSWORD } |
    ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $env:TEST_CAPTURE_PATH
$global:LASTEXITCODE = [int]$env:TEST_PSQL_EXIT_CODE
""", encoding="utf-8")
    harness = tmp_path / "run-setup.ps1"
    harness.write_text("""
function Get-Command {
    param($Name, $ErrorAction)
    return [pscustomobject]@{ Source = $env:TEST_FAKE_PSQL }
}
try {
    & $env:TEST_SETUP_PATH -Action $env:TEST_SETUP_ACTION
} finally {
    $env:PGPASSWORD | Set-Content -LiteralPath $env:TEST_RESTORED_PASSWORD
}
""", encoding="utf-8")

    def run(action="Check", url=None, dotenv=None, exit_code=0):
        env = os.environ.copy()
        env.pop("DATABASE_URL", None)
        env.update({
            "PGPASSWORD": "previous-synthetic-password",
            "TEST_SETUP_PATH": str(scripts / "setup.ps1"),
            "TEST_SETUP_ACTION": action,
            "TEST_FAKE_PSQL": str(fake_psql),
            "TEST_CAPTURE_PATH": str(tmp_path / "capture.json"),
            "TEST_RESTORED_PASSWORD": str(tmp_path / "restored.txt"),
            "TEST_PSQL_EXIT_CODE": str(exit_code),
        })
        if url is not None:
            env["DATABASE_URL"] = url
        if dotenv is not None:
            (tmp_path / ".env").write_text(dotenv, encoding="utf-8")
        return subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
            env=env, capture_output=True, text=True, timeout=20,
        )

    return run, tmp_path


@pytest.mark.parametrize("quote", ["", '"', "'"])
def test_setup_reads_dotenv_and_keeps_password_out_of_arguments(setup_script, quote):
    run, project = setup_script
    url = "postgresql://user:synthetic%40password@localhost:5432/tsdb?sslmode=require"
    result = run(dotenv=f"# Synthetic config\nDATABASE_URL={quote}{url}{quote}\n")

    assert result.returncode == 0, result.stderr
    capture = json.loads((project / "capture.json").read_text(encoding="utf-8-sig"))
    assert capture["password"] == "synthetic@password"
    assert "synthetic" not in " ".join(capture["arguments"])
    assert "synthetic" not in result.stdout + result.stderr
    assert any("timescaledb_information.hypertables" in arg for arg in capture["arguments"])
    assert (project / "restored.txt").read_text(encoding="utf-8-sig").strip() == "previous-synthetic-password"


def test_setup_prefers_environment_and_selects_migration_file(setup_script):
    run, project = setup_script
    result = run(
        action="Apply", url="postgres://user:synthetic@environment-host/tsdb",
        dotenv="DATABASE_URL=postgres://user:other@file-host/tsdb",
    )

    assert result.returncode == 0, result.stderr
    capture = json.loads((project / "capture.json").read_text(encoding="utf-8-sig"))
    assert "--dbname=postgres://user@environment-host/tsdb" in capture["arguments"]
    assert any(arg.endswith("001_initial.sql") for arg in capture["arguments"])
    assert not any(arg.startswith("--command=") for arg in capture["arguments"])


def test_setup_reports_psql_failure_and_restores_password(setup_script):
    run, project = setup_script
    result = run(url="postgres://user:synthetic@localhost/tsdb", exit_code=1)

    assert result.returncode != 0
    assert "Database command failed" in result.stderr
    assert "synthetic" not in result.stdout + result.stderr
    assert (project / "restored.txt").read_text(encoding="utf-8-sig").strip() == "previous-synthetic-password"


@pytest.mark.parametrize("url", [None, "sqlite:///local.db", "postgres://user:synthetic@@@"])
def test_setup_rejects_missing_or_invalid_configuration(setup_script, url):
    run, project = setup_script
    result = run(url=url)

    assert result.returncode != 0
    assert not (project / "capture.json").exists()
    assert "synthetic" not in result.stdout + result.stderr
