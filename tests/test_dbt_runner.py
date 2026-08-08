from pathlib import Path

import pytest

from stitch_lineage.io.dbt_runner import StitchDbtRunnerError, run_docs_generate


def _install_fake_dbt(tmp_path, monkeypatch, body):
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    exe = bin_dir / "dbt"
    exe.write_text(f"#!/bin/sh\n{body}\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))


@pytest.fixture()
def project(tmp_path):
    path = tmp_path / "project"
    path.mkdir()
    return path


def test_success_runs_dbt_docs_generate_in_project_dir(tmp_path, monkeypatch, project):
    args_file = tmp_path / "args.txt"
    cwd_file = tmp_path / "cwd.txt"
    _install_fake_dbt(
        tmp_path, monkeypatch, f'echo "$@" > "{args_file}"\npwd > "{cwd_file}"\nexit 0'
    )
    run_docs_generate(project)
    assert args_file.read_text().strip() == "docs generate"
    assert Path(cwd_file.read_text().strip()).resolve() == project.resolve()


def test_extra_args_appended_to_command(tmp_path, monkeypatch, project):
    args_file = tmp_path / "args.txt"
    _install_fake_dbt(tmp_path, monkeypatch, f'echo "$@" > "{args_file}"\nexit 0')
    run_docs_generate(project, ["--target", "prod"])
    assert args_file.read_text().strip() == "docs generate --target prod"


def test_output_streams_through(tmp_path, monkeypatch, project, capfd):
    _install_fake_dbt(tmp_path, monkeypatch, 'echo "progress line"\necho "warn line" >&2\nexit 0')
    run_docs_generate(project)
    captured = capfd.readouterr()
    assert "progress line" in captured.out
    assert "warn line" in captured.err


def test_nonzero_exit_raises_with_stderr_tail(tmp_path, monkeypatch, project):
    _install_fake_dbt(tmp_path, monkeypatch, 'echo "Database Error: boom" >&2\nexit 2')
    with pytest.raises(StitchDbtRunnerError, match="exited with code 2") as excinfo:
        run_docs_generate(project)
    assert "Database Error: boom" in str(excinfo.value)


def test_dbt_not_on_path_names_the_fix(tmp_path, monkeypatch, project):
    empty_bin = tmp_path / "emptybin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    with pytest.raises(StitchDbtRunnerError, match="not found on PATH"):
        run_docs_generate(project)
