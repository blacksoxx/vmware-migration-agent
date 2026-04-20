from __future__ import annotations

import atexit
from collections import deque
import json
import os
import subprocess
import threading
import time
from typing import Any, cast

from loguru import logger

from agent.state import JSONValue, MigrationState


class MCPRetrievalError(Exception):
    """Raised when MCP documentation retrieval fails."""


_PROVIDER_NAMESPACE_MAP: dict[str, str] = {
    "aws": "hashicorp/aws",
    "azure": "hashicorp/azurerm",
    "gcp": "hashicorp/google",
    "openstack": "terraform-provider-openstack/openstack",
}

_RESOURCE_NAME_MAP: dict[str, dict[str, str]] = {
    "compute": {
        "aws": "aws_instance",
        "azure": "azurerm_linux_virtual_machine",
        "gcp": "google_compute_instance",
        "openstack": "openstack_compute_instance_v2",
    },
    "storage": {
        "aws": "aws_ebs_volume",
        "azure": "azurerm_managed_disk",
        "gcp": "google_compute_disk",
        "openstack": "openstack_blockstorage_volume_v3",
    },
    "network": {
        "aws": "aws_vpc",
        "azure": "azurerm_virtual_network",
        "gcp": "google_compute_network",
        "openstack": "openstack_networking_network_v2",
    },
    "placement": {
        "aws": "aws_placement_group",
        "azure": "azurerm_proximity_placement_group",
        "gcp": "google_compute_resource_policy",
        "openstack": "openstack_compute_servergroup_v2",
    },
}

_RESOURCE_TYPES: tuple[str, str, str, str] = ("compute", "storage", "network", "placement")
_SEARCH_PROVIDER_TOOLS: tuple[str, str] = (
    "search_providers",
    "searchProviders",
)
_GET_PROVIDER_DETAILS_TOOLS: tuple[str, str] = (
    "get_provider_details",
    "getProviderDetails",
)
_GET_LATEST_PROVIDER_VERSION_TOOLS: tuple[str, str] = (
    "get_latest_provider_version",
    "getLatestProviderVersion",
)
_GET_PROVIDER_CAPABILITIES_TOOLS: tuple[str, str] = (
    "get_provider_capabilities",
    "getProviderCapabilities",
)


class _MCPStdioClient:
    _sessions: dict[str, "_MCPStdioSession"] = {}
    _sessions_lock = threading.Lock()

    def __init__(
        self,
        command: str,
        args: list[str],
        timeout_seconds: int,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self.command = command
        self.args = args
        self.timeout_seconds = timeout_seconds
        self.env = env or {}
        self.cwd = cwd

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> object:
        session = self._ensure_server_session()
        return session.call_tool(tool_name=tool_name, arguments=arguments)

    def _ensure_server_session(self) -> "_MCPStdioSession":
        session_key = _build_stdio_session_key(
            command=self.command,
            args=self.args,
            env=self.env,
            cwd=self.cwd,
        )

        with self._sessions_lock:
            existing = self._sessions.get(session_key)
            if existing is not None:
                return existing

            created = _MCPStdioSession(
                command=self.command,
                args=self.args,
                timeout_seconds=self.timeout_seconds,
                env=self.env,
                cwd=self.cwd,
            )
            self._sessions[session_key] = created
            return created

    @classmethod
    def close_all_sessions(cls) -> None:
        with cls._sessions_lock:
            sessions = list(cls._sessions.values())
            cls._sessions = {}

        for session in sessions:
            session.close()


class _MCPStdioSession:
    def __init__(
        self,
        command: str,
        args: list[str],
        timeout_seconds: int,
        env: dict[str, str],
        cwd: str | None,
    ) -> None:
        self.command = command
        self.args = args
        self.timeout_seconds = timeout_seconds
        self.env = env
        self.cwd = cwd

        self._process: subprocess.Popen[bytes] | None = None
        self._initialized = False
        self._request_index = 0
        self._lock = threading.RLock()
        self._stderr_tail: deque[str] = deque(maxlen=40)

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> object:
        with self._lock:
            self._ensure_server_session()

            request_id = f"tools-call-{self._next_request_index()}"
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                },
            }

            try:
                self._send(payload)
                response = self._read_response_for_id(request_id)
            except MCPRetrievalError:
                self._close_locked()
                raise
            except Exception as exc:  # pragma: no cover - defensive
                self._close_locked()
                raise MCPRetrievalError(f"mcp_client: stdio call failed: {exc}") from exc

            if not isinstance(response, dict):
                raise MCPRetrievalError("mcp_client: stdio response must be a JSON object")

            if response.get("error") is not None:
                raise MCPRetrievalError(f"mcp_client: stdio tool call error: {response['error']}")

            if "result" in response:
                return response["result"]

            return response

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _ensure_server_session(self) -> None:
        if self._process is not None and self._process.poll() is None and self._initialized:
            return

        self._start_process_locked()
        self._initialize_locked()

    def _start_process_locked(self) -> None:
        self._close_locked()

        merged_env = os.environ.copy()
        merged_env.update(self.env)

        command = [self.command, *self.args]

        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=merged_env,
                cwd=self.cwd or None,
            )
        except FileNotFoundError as exc:
            raise MCPRetrievalError(f"mcp_client: stdio command not found: {self.command}") from exc
        except OSError as exc:
            raise MCPRetrievalError(f"mcp_client: failed to launch stdio command: {exc}") from exc

        if process.stdin is None or process.stdout is None:
            _terminate_process(process)
            raise MCPRetrievalError("mcp_client: stdio process did not provide stdin/stdout pipes")

        self._process = process
        self._initialized = False
        self._start_stderr_drain_thread_locked()

    def _initialize_locked(self) -> None:
        initialize_id = f"initialize-{self._next_request_index()}"
        self._send(
            {
                "jsonrpc": "2.0",
                "id": initialize_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "vmware-migration-agent",
                        "version": "0.1.0",
                    },
                },
            }
        )

        initialize_response = self._read_response_for_id(initialize_id)
        if not isinstance(initialize_response, dict) or initialize_response.get("error") is not None:
            raise MCPRetrievalError(f"mcp_client: stdio initialize failed: {initialize_response}")

        self._send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        )

        self._initialized = True

    def _read_response_for_id(self, request_id: str) -> object:
        process = self._process
        if process is None or process.stdout is None:
            raise MCPRetrievalError("mcp_client: stdio process is not started")

        deadline = time.monotonic() + float(self.timeout_seconds)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPRetrievalError(
                    f"mcp_client: timed out waiting for stdio response id={request_id}"
                )

            response = _read_jsonrpc_message_with_timeout(process.stdout, remaining)
            if not isinstance(response, dict):
                continue

            if response.get("id") != request_id:
                # Ignore notifications and responses for other request IDs.
                continue

            return response

    def _send(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise MCPRetrievalError("mcp_client: stdio process is not started")

        try:
            _write_jsonrpc_message(process.stdin, payload)
        except BrokenPipeError as exc:
            raise MCPRetrievalError("mcp_client: stdio process pipe is closed") from exc

    def _next_request_index(self) -> int:
        self._request_index += 1
        return self._request_index

    def _close_locked(self) -> None:
        if self._process is not None:
            _terminate_process(self._process)

        self._process = None
        self._initialized = False

    def _start_stderr_drain_thread_locked(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return

        stderr_stream = process.stderr

        def _drain_stderr() -> None:
            while True:
                line = stderr_stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="ignore").strip()
                if text:
                    self._stderr_tail.append(text)

        thread = threading.Thread(target=_drain_stderr, daemon=True)
        thread.start()


def _build_stdio_session_key(
    command: str,
    args: list[str],
    env: dict[str, str],
    cwd: str | None,
) -> str:
    key_payload = {
        "command": command,
        "args": args,
        "env": sorted(env.items()),
        "cwd": cwd or "",
    }
    return json.dumps(key_payload, sort_keys=True, ensure_ascii=True)


def mcp_client(state: MigrationState) -> MigrationState:
    """Retrieve Terraform Registry documentation context from MCP (stdio only)."""
    next_state = cast(MigrationState, dict(state))

    config = next_state.get("config", {})
    if not isinstance(config, dict):
        raise MCPRetrievalError("mcp_client: state.config must be a mapping")

    mcp_cfg = _get_mapping(config, "mcp")
    if not _get_bool(mcp_cfg, "enabled", True):
        raise MCPRetrievalError("mcp_client: mcp.enabled must be true")

    cim_doc = next_state.get("sized_cim") or next_state.get("cim")
    if cim_doc is None:
        raise MCPRetrievalError("mcp_client: sized_cim or cim is required before MCP retrieval")

    if _is_empty_workload(cim_doc):
        messages = list(next_state.get("messages", []))
        messages.append("mcp_client: skipped retrieval because CIM workload is empty")
        next_state["mcp_context"] = ""
        next_state["messages"] = messages
        next_state["status"] = "running"

        logger.info("mcp_client: skipped retrieval because CIM workload is empty")
        return next_state

    provider_key = str(cim_doc.target_provider.value).strip().lower()
    provider_namespace = _provider_namespace(provider_key)
    provider_namespace_name, provider_name = _provider_namespace_parts(provider_namespace)
    timeout_seconds = _get_int(mcp_cfg, "tool_timeout_seconds", 30)
    command, args = _terraform_server_command(mcp_cfg)

    client = _MCPStdioClient(
        command=command,
        args=args,
        timeout_seconds=timeout_seconds,
    )

    messages = list(next_state.get("messages", []))
    snippets: list[str] = []
    retrieval_errors: list[str] = []
    for resource_type in _RESOURCE_TYPES:
        try:
            resource_name = _resource_name(provider_key=provider_key, resource_type=resource_type)
            service_slug = _service_slug_from_resource(resource_name)
            doc_id = _resolve_provider_doc_id(
                client=client,
                provider_namespace=provider_namespace_name,
                provider_name=provider_name,
                service_slug=service_slug,
            )
            docs_payload = _call_registry_tool(
                client=client,
                tool_names=_GET_PROVIDER_DETAILS_TOOLS,
                arguments={"provider_doc_id": doc_id},
            )

            doc_snippets = _extract_snippets(docs_payload)
            if not doc_snippets:
                raise MCPRetrievalError(
                    "mcp_client: no snippets returned by Terraform MCP for "
                    f"provider={provider_namespace} resource={resource_name} doc_id={doc_id}"
                )

            snippets.append(
                "\n".join(
                    [
                        "Source: mcp://terraform-mcp-server/registry",
                        f"Provider: {provider_namespace}",
                        f"ResourceType: {resource_type}",
                        f"Resource: {resource_name}",
                        f"ServiceSlug: {service_slug}",
                        f"DocID: {doc_id}",
                        _compact_snippets(doc_snippets),
                    ]
                )
            )
        except MCPRetrievalError as exc:
            retrieval_errors.append(f"{resource_type}:{exc}")

    if not snippets:
        fallback_snippets = _fallback_provider_context(
            client=client,
            provider_namespace=provider_namespace_name,
            provider_name=provider_name,
        )
        snippets.extend(fallback_snippets)

    snippets = _unique_preserve_order(snippets)
    if not snippets:
        detail = "; ".join(retrieval_errors[:3])
        raise MCPRetrievalError(
            "mcp_client: MCP returned no documentation snippets"
            + (f" ({detail})" if detail else "")
        )

    context = _format_context(snippets)

    if retrieval_errors:
        messages.append(
            "mcp_client: detailed resource docs unavailable for provider={} errors={}".format(
                provider_key,
                len(retrieval_errors),
            )
        )

    messages.append(
        "mcp_client: provider={} snippets={} server=terraform-mcp-server".format(
            provider_key,
            len(snippets),
        )
    )

    next_state["mcp_context"] = context
    next_state["messages"] = messages
    next_state["status"] = "running"

    logger.info(
        "mcp_client: retrieved {} snippets for provider {}",
        len(snippets),
        provider_key,
    )

    return next_state


def _is_empty_workload(cim_doc: Any) -> bool:
    try:
        compute_units = len(cim_doc.compute_units)
        clusters = len(cim_doc.clusters)
        distributed_switches = len(cim_doc.network_topology.distributed_switches)
    except Exception:
        return False

    return compute_units == 0 and clusters == 0 and distributed_switches == 0


def _provider_namespace(provider_key: str) -> str:
    namespace = _PROVIDER_NAMESPACE_MAP.get(provider_key)
    if namespace is None:
        raise MCPRetrievalError(f"mcp_client: unsupported target provider '{provider_key}'")
    return namespace


def _resource_name(provider_key: str, resource_type: str) -> str:
    type_mapping = _RESOURCE_NAME_MAP.get(resource_type)
    if type_mapping is None:
        raise MCPRetrievalError(f"mcp_client: unsupported resource type '{resource_type}'")

    resource_name = type_mapping.get(provider_key)
    if resource_name is None:
        raise MCPRetrievalError(
            f"mcp_client: unsupported provider/resource mapping provider={provider_key} "
            f"resource_type={resource_type}"
        )

    return resource_name


def _terraform_server_command(mcp_cfg: dict[str, JSONValue]) -> tuple[str, list[str]]:
    terraform_cfg = _get_mapping(mcp_cfg, "terraform")

    image = str(terraform_cfg.get("image") or "").strip()
    if not image:
        image = "hashicorp/terraform-mcp-server:latest"

    toolsets = str(terraform_cfg.get("toolsets") or "").strip().lower()
    if toolsets and toolsets != "registry":
        raise MCPRetrievalError("mcp_client: mcp.terraform.toolsets must be 'registry'")

    return (
        "docker",
        ["run", "-i", "--rm", image, "stdio", "--toolsets", "registry"],
    )


def _resolve_provider_doc_id(
    client: _MCPStdioClient,
    provider_namespace: str,
    provider_name: str,
    service_slug: str,
) -> str:
    candidate_service_slugs = _unique_preserve_order([service_slug, provider_name])

    for candidate_slug in candidate_service_slugs:
        payload = _call_registry_tool(
            client=client,
            tool_names=_SEARCH_PROVIDER_TOOLS,
            arguments={
                "provider_namespace": provider_namespace,
                "provider_name": provider_name,
                "provider_version": "latest",
                "service_slug": candidate_slug,
                "provider_document_type": "resources",
            },
        )

        doc_id = _extract_doc_id(payload)
        if doc_id is not None:
            return doc_id

    raise MCPRetrievalError(
        "mcp_client: could not resolve doc_id for "
        f"provider={provider_namespace}/{provider_name} service_slug={service_slug}"
    )


def _provider_namespace_parts(provider_namespace: str) -> tuple[str, str]:
    parts = [segment for segment in provider_namespace.split("/") if segment]
    if len(parts) < 2:
        raise MCPRetrievalError(f"mcp_client: invalid provider namespace '{provider_namespace}'")

    return parts[0], parts[-1]


def _service_slug_from_resource(resource_name: str) -> str:
    parts = [segment for segment in resource_name.split("_") if segment]
    if len(parts) <= 1:
        return resource_name
    return "_".join(parts[1:])


def _fallback_provider_context(
    client: _MCPStdioClient,
    provider_namespace: str,
    provider_name: str,
) -> list[str]:
    snippets: list[str] = []

    try:
        latest_payload = _call_registry_tool(
            client=client,
            tool_names=_GET_LATEST_PROVIDER_VERSION_TOOLS,
            arguments={"namespace": provider_namespace, "name": provider_name},
        )
        latest_text = _payload_to_text(latest_payload)
        if latest_text:
            snippets.append(
                "\n".join(
                    [
                        "Source: mcp://terraform-mcp-server/registry",
                        f"Provider: {provider_namespace}/{provider_name}",
                        "ContextType: latest_provider_version",
                        latest_text,
                    ]
                )
            )
    except MCPRetrievalError:
        pass

    try:
        capabilities_payload = _call_registry_tool(
            client=client,
            tool_names=_GET_PROVIDER_CAPABILITIES_TOOLS,
            arguments={
                "namespace": provider_namespace,
                "name": provider_name,
                "version": "latest",
            },
        )
        capabilities_text = _payload_to_text(capabilities_payload)
        if capabilities_text:
            snippets.append(
                "\n".join(
                    [
                        "Source: mcp://terraform-mcp-server/registry",
                        f"Provider: {provider_namespace}/{provider_name}",
                        "ContextType: provider_capabilities",
                        capabilities_text,
                    ]
                )
            )
    except MCPRetrievalError:
        pass

    return snippets


def _payload_to_text(payload: object) -> str:
    extracted = _extract_snippets(payload)
    if extracted:
        return _compact_snippets(extracted)

    try:
        return json.dumps(payload, ensure_ascii=True)
    except TypeError:
        return str(payload)


def _call_registry_tool(
    client: _MCPStdioClient,
    tool_names: tuple[str, ...],
    arguments: dict[str, Any],
) -> object:
    last_tool_not_found_error: MCPRetrievalError | None = None

    for tool_name in tool_names:
        try:
            return client.call_tool(tool_name=tool_name, arguments=arguments)
        except MCPRetrievalError as exc:
            lowered = str(exc).lower()
            if "tool" in lowered and "not found" in lowered:
                last_tool_not_found_error = exc
                continue
            raise

    if last_tool_not_found_error is not None:
        raise last_tool_not_found_error

    raise MCPRetrievalError("mcp_client: no registry tool names provided")


def _extract_doc_id(payload: object) -> str | None:
    if isinstance(payload, str):
        value = payload.strip()
        return value or None

    if isinstance(payload, dict):
        for key in ("provider_doc_id", "doc_id", "docId", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        for key in ("result", "data", "document"):
            nested = payload.get(key)
            doc_id = _extract_doc_id(nested)
            if doc_id is not None:
                return doc_id

        return None

    if isinstance(payload, list):
        for item in payload:
            doc_id = _extract_doc_id(item)
            if doc_id is not None:
                return doc_id
        return None

    return None


def _compact_snippets(snippets: list[str], max_chars: int = 5000) -> str:
    joined = "\n\n".join(snippets)
    if len(joined) > max_chars:
        return joined[:max_chars]
    return joined


def _extract_snippets(payload: object) -> list[str]:
    if isinstance(payload, str) and payload.strip():
        return [payload.strip()]

    snippets: list[str] = []

    if isinstance(payload, dict):
        for key in ("snippets", "documents", "content", "results"):
            value = payload.get(key)
            snippets.extend(_extract_snippets(value))
        return _unique_preserve_order(snippets)

    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, str) and item.strip():
                snippets.append(item.strip())
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or item.get("snippet")
                if isinstance(text, str) and text.strip():
                    snippets.append(text.strip())
                else:
                    snippets.extend(_extract_snippets(item))
        return _unique_preserve_order(snippets)

    return []


def _format_context(snippets: list[str]) -> str:
    sections = [f"[Context {index}]\n{snippet}" for index, snippet in enumerate(snippets, start=1)]
    return "\n\n".join(sections)


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def _get_mapping(source: dict[str, JSONValue], key: str) -> dict[str, JSONValue]:
    value = source.get(key)
    if isinstance(value, dict):
        return cast(dict[str, JSONValue], value)
    return {}


def _get_int(source: dict[str, JSONValue], key: str, default: int) -> int:
    value = source.get(key)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _get_bool(source: dict[str, JSONValue], key: str, default: bool) -> bool:
    value = source.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    return default


def _write_jsonrpc_message(stdin: Any, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    stdin.write(body)
    stdin.write(b"\n")
    stdin.flush()


def _read_jsonrpc_message_with_timeout(stdout: Any, timeout_seconds: int) -> object:
    output: dict[str, object] = {}
    errors: list[BaseException] = []

    def _reader() -> None:
        try:
            output["payload"] = _read_jsonrpc_message(stdout)
        except BaseException as exc:  # pragma: no cover - defensive
            errors.append(exc)

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if thread.is_alive():
        raise MCPRetrievalError("mcp_client: timed out waiting for stdio MCP response")
    if errors:
        raise MCPRetrievalError(f"mcp_client: stdio read failed: {errors[0]}")

    return output.get("payload")


def _read_jsonrpc_message(stdout: Any) -> object:
    line = stdout.readline()
    if not line:
        raise MCPRetrievalError("mcp_client: empty stdio response")

    payload = line.decode("utf-8", errors="ignore").strip()
    if not payload:
        raise MCPRetrievalError("mcp_client: empty stdio response payload")

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise MCPRetrievalError("mcp_client: stdio response body is not valid JSON") from exc


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()


atexit.register(_MCPStdioClient.close_all_sessions)
