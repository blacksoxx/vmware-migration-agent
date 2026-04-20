from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib import error as url_error
from urllib import request as url_request

from loguru import logger


@dataclass
class OPAEvaluationResult:
    passed: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    deny_messages: list[str] = field(default_factory=list)


class OPAClient:
    """Evaluate OPA policies in binary or server mode."""

    def __init__(
        self,
        mode: str = "binary",
        opa_bin: str = "opa",
        opa_server_url: str = "",
        timeout_seconds: int = 120,
    ) -> None:
        normalized_mode = mode.strip().lower() if isinstance(mode, str) else "binary"
        self.mode = normalized_mode if normalized_mode in {"binary", "server"} else "binary"
        self.opa_bin = opa_bin
        self.opa_server_url = opa_server_url.strip()
        self.timeout_seconds = timeout_seconds

    def evaluate(self, input_data: dict[str, object], policies_dir: Path) -> OPAEvaluationResult:
        if self.mode == "server":
            return self._evaluate_server(input_data)
        return self._evaluate_binary(input_data, policies_dir)

    def _evaluate_binary(self, input_data: dict[str, object], policies_dir: Path) -> OPAEvaluationResult:
        result = OPAEvaluationResult()

        if not policies_dir.exists():
            result.errors.append(f"opa_client: policies directory not found: {policies_dir}")
            return result

        with tempfile.TemporaryDirectory(prefix="vmware-migration-agent-opa-") as temp_dir:
            input_path = Path(temp_dir) / "input.json"
            input_path.write_text(json.dumps(input_data, ensure_ascii=True), encoding="utf-8")

            command = [
                self.opa_bin,
                "eval",
                "--format",
                "json",
                "--v0-compatible",
                "--data",
                str(policies_dir),
                "--input",
                str(input_path),
                "data",
            ]

            command_result = _run_command(command, timeout_seconds=self.timeout_seconds)
            if command_result.returncode != 0 and _is_unknown_flag_error(command_result.output):
                fallback_command = [part for part in command if part != "--v0-compatible"]
                command_result = _run_command(fallback_command, timeout_seconds=self.timeout_seconds)

            if command_result.returncode != 0:
                result.errors.append(f"opa eval failed: {command_result.output}")
                return result

            try:
                parsed = json.loads(command_result.stdout)
            except json.JSONDecodeError:
                result.errors.append("opa_client: failed to parse opa eval JSON output")
                return result

            deny_messages = _extract_deny_messages(parsed)
            result.deny_messages.extend(deny_messages)
            result.passed = len(deny_messages) == 0
            if not result.passed:
                result.errors.extend(deny_messages)

            return result

    def _evaluate_server(self, input_data: dict[str, object]) -> OPAEvaluationResult:
        result = OPAEvaluationResult()

        if not self.opa_server_url:
            result.errors.append("opa_client: opa_server_url is required for server mode")
            return result

        endpoint = f"{self.opa_server_url.rstrip('/')}/v1/data"
        payload = json.dumps({"input": input_data}, ensure_ascii=True).encode("utf-8")

        req = url_request.Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with url_request.urlopen(req, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except url_error.URLError as exc:
            result.errors.append(f"opa_client: server request failed: {exc}")
            return result

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            result.errors.append("opa_client: failed to parse OPA server response JSON")
            return result

        deny_messages = _extract_deny_messages(parsed)
        result.deny_messages.extend(deny_messages)
        result.passed = len(deny_messages) == 0
        if not result.passed:
            result.errors.extend(deny_messages)

        return result


def evaluate_policies(
    hcl_output: dict[str, str],
    policies_dir: Path,
    mode: str = "binary",
    opa_bin: str = "opa",
    opa_server_url: str = "",
    timeout_seconds: int = 120,
) -> OPAEvaluationResult:
    """Convenience wrapper to evaluate OPA policies for generated HCL output."""
    client = OPAClient(
        mode=mode,
        opa_bin=opa_bin,
        opa_server_url=opa_server_url,
        timeout_seconds=timeout_seconds,
    )

    input_data = {
        "generated_files": hcl_output,
    }

    result = client.evaluate(input_data=input_data, policies_dir=policies_dir)
    deny_preview = "; ".join(result.deny_messages[:3]) if result.deny_messages else "none"
    logger.info(
        "opa_client: evaluation mode={} passed={} deny_messages={} deny_preview={}",
        client.mode,
        result.passed,
        len(result.deny_messages),
        deny_preview,
    )
    return result


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        parts = [self.stdout.strip(), self.stderr.strip()]
        return "\n".join(part for part in parts if part)


def _run_command(command: list[str], timeout_seconds: int) -> _CommandResult:
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except FileNotFoundError:
        return _CommandResult(
            returncode=127,
            stdout="",
            stderr=f"command not found: {command[0]}",
        )
    except subprocess.TimeoutExpired:
        return _CommandResult(
            returncode=124,
            stdout="",
            stderr=f"command timed out: {' '.join(command)}",
        )

    return _CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _extract_deny_messages(payload: object) -> list[str]:
    return _collect_denies(payload)


def _is_unknown_flag_error(output: str) -> bool:
    normalized = output.lower()
    return (
        "unknown flag" in normalized
        or "flag provided but not defined" in normalized
        or "unknown shorthand flag" in normalized
    )


def _collect_denies(payload: object, path: str = "data") -> list[str]:
    messages: list[str] = []

    if isinstance(payload, dict):
        for key, value in payload.items():
            next_path = f"{path}.{key}"
            if key == "deny" and isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.strip():
                        messages.append(item.strip())
                    elif item is not None:
                        messages.append(f"{next_path}: {item}")
            else:
                messages.extend(_collect_denies(value, next_path))

    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            messages.extend(_collect_denies(value, f"{path}[{index}]"))

    return messages
