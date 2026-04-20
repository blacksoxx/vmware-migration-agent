import click
from pathlib import Path
import json
import os
from loguru import logger

from agent.nodes.mcp_client import _MCPStdioClient

from cim.schema import TargetProvider

SUPPORTED_PROVIDERS = ["anthropic", "openai", "google"]
SUPPORTED_CLOUDS    = ["aws", "azure", "gcp", "openstack"]

PROVIDER_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5",
    "openai":    "gpt-4o",
    "google":    "gemini-2.0-flash",
}

DEFAULT_MCP_CHECK_SERVERS = ("terraform",)

@click.group()
def cli():
    """VMware lift-and-shift migration agent.

    Args:
    Use the terragen command with --input to process a discovery JSON file.
      Use the report command with OUTPUT_DIR to inspect run artifacts.
            Use the mcp-check command to validate MCP server connectivity.
    """
    pass


@cli.command(name="terragen")
@click.option("--input",
              "input_file",
              required=True,
              type=click.Path(exists=True, path_type=Path),
              help="Path to VMware discovery JSON input file.")
# ── Target cloud ──────────────────────────────────────────────────────────────
@click.option("--cloud",    "-c",
              required=True,
              type=click.Choice(SUPPORTED_CLOUDS, case_sensitive=False),
              help="Target cloud provider.")
# ── LLM provider + model ──────────────────────────────────────────────────────
@click.option("--llm-provider", "-p",
              required=True,
              type=click.Choice(SUPPORTED_PROVIDERS, case_sensitive=False),
              help="LLM provider to use for HCL generation.")
@click.option("--llm-model", "-m",
              default=None,
              help="Model name. Defaults to provider default if omitted.")
@click.option("--llm-base-url",
              default=None,
              envvar="LLM_BASE_URL",
              help="Override API base URL (e.g. Azure OpenAI endpoint, self-hosted proxy). "
                   "Also readable from LLM_BASE_URL env var.")
@click.option("--llm-api-key",
              default=None,
              envvar="LLM_API_KEY",
              help="API key. Prefer passing via LLM_API_KEY env var instead of CLI arg.")
# ── Run metadata (injected as required tags) ──────────────────────────────────
@click.option("--environment", "-e",
              required=True,
              help="Target environment name e.g. prod, staging. Applied as tag on all resources.")
@click.option("--owner",
              required=True,
              help="Team or individual owning the migrated resources. Applied as tag.")
# ── Output ────────────────────────────────────────────────────────────────────
@click.option("--output-dir", "-o",
              default="output",
              type=click.Path(path_type=Path),
              show_default=True,
              help="Directory to write generated .tf files and reports.")
@click.option("--config", "config_file",
              default="config.yaml",
              type=click.Path(exists=True, path_type=Path),
              show_default=True,
              help="Path to config.yaml.")
# ── Flags ─────────────────────────────────────────────────────────────────────
@click.option("--dry-run",
              is_flag=True,
              default=False,
              help="Run full pipeline but do not write any files to output-dir.")
@click.option("--skip-validation",
              is_flag=True,
              default=False,
              help="Skip terraform validate, tflint, and OPA. Use for debugging only.")
@click.option("--skip-opa",
              is_flag=True,
              default=False,
              help="Skip only OPA policy evaluation. Terraform validate and tflint still run.")
def terragen(
    input_file, cloud, llm_provider, llm_model, llm_base_url, llm_api_key,
    environment, owner, output_dir, config_file, dry_run, skip_validation, skip_opa
):
    """
    Convert a VMware discovery JSON to Terraform HCL for the target cloud.

        Args:
            --input: Path to VMware discovery JSON file.
            --cloud: Target cloud provider (aws|azure|gcp|openstack).
            --llm-provider: LLM backend (anthropic|openai|google).
            --environment: Environment tag value for all generated resources.
            --owner: Owner tag value for all generated resources.

    \b
    Examples:
      # Anthropic + AWS, API key from env
      export LLM_API_KEY=sk-ant-...
        vma terragen --input discovery.json -c aws -p anthropic -e prod --owner platform-team

      # OpenAI with explicit model, targeting Azure
            vma terragen --input any-file.json -c azure -p openai -m gpt-4o-mini \\
          -e staging --owner devops --llm-api-key $OPENAI_KEY

      # Azure OpenAI (custom base URL + deployment name as model)
            vma terragen --input custom-discovery.json -c aws -p openai \\
          --llm-base-url https://my-instance.openai.azure.com/ \\
          -m my-gpt4o-deployment -e prod --owner sre
    """
    provider_name = llm_provider.lower()
    resolved_model = llm_model or PROVIDER_DEFAULT_MODELS[provider_name]

    from agent.graph import build_graph
    from agent.config_loader import load_config

    cfg = load_config(config_file)

    # Apply CLI overrides into runtime config consumed by nodes.
    cfg["target_cloud"] = cloud.lower()

    llm_cfg = cfg.setdefault("llm", {})
    if isinstance(llm_cfg, dict):
        llm_cfg["provider"] = provider_name
        llm_cfg["model"] = resolved_model
        if llm_base_url:
            llm_cfg["base_url"] = llm_base_url

    output_cfg = cfg.setdefault("output", {})
    if isinstance(output_cfg, dict):
        output_cfg["base_dir"] = str(output_dir)

    if skip_validation:
        validation_cfg = cfg.setdefault("validation", {})
        if isinstance(validation_cfg, dict):
            validation_cfg["run_terraform_validate"] = False
            validation_cfg["run_tflint"] = False
            validation_cfg["run_opa"] = False
    elif skip_opa:
        validation_cfg = cfg.setdefault("validation", {})
        if isinstance(validation_cfg, dict):
            validation_cfg["run_opa"] = False

    if llm_api_key:
        env_var = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "google": "GOOGLE_API_KEY",
        }[provider_name]
        os.environ[env_var] = llm_api_key

    required_tags = cfg.setdefault("required_tags", {})
    if isinstance(required_tags, dict):
        required_tags["Environment"] = environment
        required_tags["Owner"] = owner

    discovery = json.loads(input_file.read_text())
    graph = build_graph()

    initial_state = {
        "discovery_data": discovery,
        "config": cfg,
        "target_provider": TargetProvider(cloud.lower()),
        "quarantine_queue": [],
        "messages": [],
        "retry_count": 0,
        "status": "pending",
        "hcl_output": {},
        "mcp_context": "",
        "validation_result": {},
        "review_notes": "",
    }

    if dry_run:
        click.echo("dry-run requested; file writing behavior depends on node support.")

    try:
        final_state = graph.invoke(initial_state)
    except Exception:
        logger.exception("terragen: pipeline execution failed")
        raise

    click.echo(f"Migration finished with status: {final_state.get('status', 'unknown')}")


@cli.command()
@click.argument("output_dir", type=click.Path(exists=True, path_type=Path))
@click.option("--format", "fmt",
              type=click.Choice(["text", "json"]),
              default="text", show_default=True)
def report(output_dir, fmt):
    """Print a summary of a previous terragen run from OUTPUT_DIR."""
    output_path = Path(output_dir)
    notes_path = output_path / "review_notes.md"
    quarantine_path = output_path / "quarantine_report.json"

    review_notes = notes_path.read_text(encoding="utf-8") if notes_path.exists() else ""

    quarantine_items = []
    if quarantine_path.exists():
        try:
            quarantine_items = json.loads(quarantine_path.read_text(encoding="utf-8"))
            if not isinstance(quarantine_items, list):
                quarantine_items = []
        except json.JSONDecodeError:
            quarantine_items = []

    if fmt == "json":
        payload = {
            "output_dir": str(output_path),
            "review_notes_present": notes_path.exists(),
            "quarantine_report_present": quarantine_path.exists(),
            "quarantine_count": len(quarantine_items),
            "review_notes": review_notes,
        }
        click.echo(json.dumps(payload, indent=2))
        return

    click.echo(f"Output directory: {output_path}")
    click.echo(f"Review notes: {'present' if notes_path.exists() else 'missing'}")
    click.echo(f"Quarantine report: {'present' if quarantine_path.exists() else 'missing'}")
    click.echo(f"Quarantine items: {len(quarantine_items)}")

    if review_notes:
        click.echo("\n--- review_notes.md ---\n")
        click.echo(review_notes)


@cli.command(name="mcp-check")
@click.option("--config", "config_file",
              default="config.yaml",
              type=click.Path(exists=True, path_type=Path),
              show_default=True,
              help="Path to config.yaml.")
@click.option(
    "--servers",
    default="configured",
    show_default=True,
    help=(
        "Comma-separated server names to check (e.g. terraform) or "
        "'configured' to check all configured MCP servers."
    ),
)
@click.option("--top-k",
              default=1,
              type=int,
              show_default=True,
              help="Probe retrieval top_k argument.")
@click.option("--timeout",
              "timeout_seconds",
              default=10,
              type=int,
              show_default=True,
              help="Timeout in seconds for each MCP stdio probe.")
@click.option("--format", "fmt",
              type=click.Choice(["text", "json"]),
              default="text",
              show_default=True)
def mcp_check(config_file, servers, top_k, timeout_seconds, fmt):
    """Check MCP connectivity for the Terraform MCP server."""
    from agent.config_loader import load_config

    cfg = load_config(config_file)
    mcp_cfg = cfg.get("mcp", {}) if isinstance(cfg, dict) else {}
    if not isinstance(mcp_cfg, dict):
        raise click.ClickException("Invalid mcp configuration")

    servers_cfg = _build_mcp_check_servers_cfg(mcp_cfg)

    selected_servers = _resolve_mcp_check_servers(servers=servers, servers_cfg=servers_cfg)
    if not selected_servers:
        raise click.ClickException("No MCP servers selected for connectivity check")

    results: list[dict[str, object]] = []
    for server_name in selected_servers:
        results.append(
            _probe_mcp_server(
                server_name=server_name,
                servers_cfg=servers_cfg,
                top_k=max(1, int(top_k)),
                timeout_seconds=max(1, int(timeout_seconds)),
            )
        )

    failed = [result for result in results if not bool(result.get("ok"))]

    if fmt == "json":
        click.echo(
            json.dumps(
                {
                    "ok": len(failed) == 0,
                    "checked_servers": [item.get("server") for item in results],
                    "results": results,
                },
                indent=2,
                ensure_ascii=True,
            )
        )
    else:
        click.echo("MCP connectivity check")
        for result in results:
            status = "OK" if bool(result.get("ok")) else "FAIL"
            click.echo(
                "- {} [{}] {}".format(
                    result.get("server"),
                    status,
                    result.get("detail", ""),
                )
            )

    _MCPStdioClient.close_all_sessions()

    if failed:
        raise click.exceptions.Exit(1)


def _resolve_mcp_check_servers(servers: str, servers_cfg: dict[str, object]) -> list[str]:
    value = servers.strip().lower() if isinstance(servers, str) else "configured"
    if value in {"", "configured"}:
        resolved = [name for name in DEFAULT_MCP_CHECK_SERVERS if name in servers_cfg]
        for name in servers_cfg.keys():
            if isinstance(name, str) and name not in resolved:
                resolved.append(name)
        return resolved

    selected: list[str] = []
    for item in value.split(","):
        name = item.strip()
        if not name:
            continue
        if name not in selected:
            selected.append(name)
    return selected


def _probe_mcp_server(
    server_name: str,
    servers_cfg: dict[str, object],
    top_k: int,
    timeout_seconds: int,
) -> dict[str, object]:
    del top_k

    raw_server = servers_cfg.get(server_name)
    if not isinstance(raw_server, dict):
        return {
            "server": server_name,
            "ok": False,
            "detail": "missing server configuration",
        }

    command = str(raw_server.get("command", "")).strip()
    if not command:
        return {
            "server": server_name,
            "ok": False,
            "detail": "missing stdio command",
        }

    provider = str(raw_server.get("provider", "hashicorp/aws")).strip() or "hashicorp/aws"
    resource = (
        str(raw_server.get("resource", "")).strip()
        or str(raw_server.get("query", "")).strip()
        or "aws_instance"
    )
    provider_namespace, provider_name = _provider_namespace_parts(provider)
    service_slug = _service_slug_from_resource(resource)

    try:
        client = _MCPStdioClient(
            command=command,
            args=_string_list(raw_server.get("args")),
            timeout_seconds=timeout_seconds,
        )

        result = _call_registry_tool(
            client=client,
            tool_names=("get_latest_provider_version", "getLatestProviderVersion"),
            arguments={"namespace": provider_namespace, "name": provider_name},
        )
    except Exception as exc:
        return {
            "server": server_name,
            "ok": False,
            "detail": f"connection failed: {exc}",
        }

    detail = "reachable" if result is not None else "reachable (empty result)"
    if isinstance(result, dict):
        version = result.get("version") or result.get("latest_version")
        if isinstance(version, str) and version.strip():
            detail = f"reachable (latest provider version: {version.strip()})"

    return {
        "server": server_name,
        "ok": True,
        "detail": detail,
    }


def _build_mcp_check_servers_cfg(mcp_cfg: dict[str, object]) -> dict[str, object]:
    terraform_cfg = mcp_cfg.get("terraform", {})
    if not isinstance(terraform_cfg, dict):
        terraform_cfg = {}

    image = str(terraform_cfg.get("image", "")).strip()
    if not image:
        image = "hashicorp/terraform-mcp-server:latest"

    return {
        "terraform": {
            "command": "docker",
            "args": ["run", "-i", "--rm", image, "stdio", "--toolsets", "registry"],
            "provider": "hashicorp/aws",
            "resource": "aws_instance",
        }
    }


def _call_registry_tool(
    client: _MCPStdioClient,
    tool_names: tuple[str, ...],
    arguments: dict[str, object],
) -> object:
    last_exc: Exception | None = None
    for tool_name in tool_names:
        try:
            return client.call_tool(tool_name=tool_name, arguments=arguments)
        except Exception as exc:
            lowered = str(exc).lower()
            if "tool" in lowered and "not found" in lowered:
                last_exc = exc
                continue
            raise

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("no registry tool names provided")


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


def _provider_namespace_parts(provider: str) -> tuple[str, str]:
    parts = [segment for segment in provider.split("/") if segment]
    if len(parts) < 2:
        return "hashicorp", "aws"
    return parts[0], parts[-1]


def _service_slug_from_resource(resource: str) -> str:
    parts = [segment for segment in resource.split("_") if segment]
    if len(parts) <= 1:
        return resource
    return "_".join(parts[1:])


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def main():
    cli()


if __name__ == "__main__":
    main()