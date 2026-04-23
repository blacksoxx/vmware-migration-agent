from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        parts = [self.stdout.strip(), self.stderr.strip()]
        return "\n".join(part for part in parts if part)


@dataclass
class TFValidationResult:
    terraform_validate_passed: bool = False
    tflint_passed: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.terraform_validate_passed and self.tflint_passed


def validate_hcl(
    hcl_output: dict[str, str],
    terraform_bin: str = "terraform",
    tflint_bin: str = "tflint",
    run_tflint: bool = True,
) -> TFValidationResult:
    """Validate generated HCL with terraform validate and optionally tflint."""
    if not hcl_output:
        return TFValidationResult(
            terraform_validate_passed=False,
            tflint_passed=not run_tflint,
            errors=["tf_runner: hcl_output is empty"],
        )

    result = TFValidationResult(tflint_passed=not run_tflint)

    with tempfile.TemporaryDirectory(prefix="vmware-migration-agent-tf-") as temp_dir:
        workspace = Path(temp_dir)
        _write_hcl_files(workspace, hcl_output)
        terraform_cwd = _resolve_terraform_workdir(workspace, hcl_output)

        init_result = _run_command(
            [terraform_bin, "init", "-backend=false", "-input=false", "-no-color"],
            cwd=terraform_cwd,
        )
        if init_result.returncode != 0:
            result.errors.append(f"terraform init failed: {init_result.output}")
            logger.error(
                "tf_runner: terraform init failed in {}: {}",
                str(terraform_cwd),
                init_result.output[:1000] if init_result.output else "<no output>",
            )
            return result

        validate_result = _run_command(
            [terraform_bin, "validate", "-no-color"],
            cwd=terraform_cwd,
        )
        if validate_result.returncode != 0:
            result.errors.append(f"terraform validate failed: {validate_result.output}")
            logger.error(
                "tf_runner: terraform validate failed in {}: {}",
                str(terraform_cwd),
                validate_result.output[:1000] if validate_result.output else "<no output>",
            )
        else:
            result.terraform_validate_passed = True

        if run_tflint:
            tflint_result = _run_command([tflint_bin, "--no-color"], cwd=terraform_cwd)
            if tflint_result.returncode != 0:
                result.errors.append(f"tflint failed: {tflint_result.output}")
                logger.error(
                    "tf_runner: tflint failed in {}: {}",
                    str(terraform_cwd),
                    tflint_result.output[:1000] if tflint_result.output else "<no output>",
                )
            else:
                result.tflint_passed = True

    if not run_tflint:
        result.tflint_passed = True

    return result


def _write_hcl_files(workspace: Path, hcl_output: dict[str, str]) -> None:
    for raw_path, content in hcl_output.items():
        relative_path = Path(str(raw_path).replace("\\", "/"))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"tf_runner: unsafe output path: {raw_path}")

        destination = workspace / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")


def _resolve_terraform_workdir(workspace: Path, hcl_output: dict[str, str]) -> Path:
    """Prefer the common provider root (e.g. openstack-migration/) when all files share it."""
    roots: set[str] = set()

    for raw_path in hcl_output:
        relative_path = Path(str(raw_path).replace("\\", "/"))
        if relative_path.parts:
            roots.add(relative_path.parts[0])

    if len(roots) != 1:
        return workspace

    root = next(iter(roots))
    candidate = workspace / root
    if candidate.is_dir():
        return candidate

    return workspace


def _run_command(command: list[str], cwd: Path) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            check=False,
            timeout=180,
        )
    except FileNotFoundError:
        return CommandResult(
            returncode=127,
            stdout="",
            stderr=f"command not found: {command[0]}",
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            returncode=124,
            stdout="",
            stderr=f"command timed out: {' '.join(command)}",
        )

    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
