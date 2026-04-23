from __future__ import annotations

from pathlib import Path

import validation.tf_runner as tf_runner


def test_resolve_terraform_workdir_prefers_common_root(tmp_path: Path) -> None:
    hcl_output = {
        "openstack-migration/main.tf": "terraform {}\n",
        "openstack-migration/providers.tf": "terraform {}\n",
        "openstack-migration/networking/vpc.tf": 'resource "openstack_networking_network_v2" "net" {}\n',
    }

    resolved = tf_runner._resolve_terraform_workdir(tmp_path, hcl_output)

    assert resolved == tmp_path

    tf_runner._write_hcl_files(tmp_path, hcl_output)
    resolved = tf_runner._resolve_terraform_workdir(tmp_path, hcl_output)

    assert resolved == tmp_path / "openstack-migration"


def test_validate_hcl_runs_commands_in_detected_module_dir(
    monkeypatch,
) -> None:
    command_cwds: list[str] = []

    def _fake_run_command(command: list[str], cwd: Path) -> tf_runner.CommandResult:
        command_cwds.append(str(cwd))
        return tf_runner.CommandResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(tf_runner, "_run_command", _fake_run_command)

    result = tf_runner.validate_hcl(
        hcl_output={
            "openstack-migration/main.tf": "terraform {}\n",
            "openstack-migration/providers.tf": "terraform {}\n",
        },
        run_tflint=False,
    )

    assert result.terraform_validate_passed is True
    assert result.passed is True
    assert len(command_cwds) == 2
    assert all(cwd.endswith("openstack-migration") for cwd in command_cwds)
