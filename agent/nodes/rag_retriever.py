from __future__ import annotations

from pathlib import Path
from typing import Iterable, cast

from loguru import logger

from agent.state import JSONValue, MigrationState


def rag_retriever(state: MigrationState) -> MigrationState:
    """Build deterministic retrieval context for HCL generation from local corpora."""
    next_state = cast(MigrationState, dict(state))

    config = next_state.get("config", {})
    if not isinstance(config, dict):
        config = {}

    rag_config = _get_mapping(config, "rag")
    enabled = _get_bool(rag_config, "enabled", True)

    if not enabled:
        next_state["rag_context"] = ""
        messages = list(next_state.get("messages", []))
        messages.append("rag_retriever: disabled")
        next_state["messages"] = messages
        next_state["status"] = "running"
        logger.info("rag_retriever: RAG disabled in config")
        return next_state

    sized_cim = next_state.get("sized_cim")
    cim = next_state.get("cim")
    cim_doc = sized_cim if sized_cim is not None else cim
    if cim_doc is None:
        raise ValueError("rag_retriever: sized_cim or cim is required before retrieval")

    provider_key = _provider_key(next_state)
    top_k = _get_int(rag_config, "top_k", 8)

    provider_corpus_path = Path(
        _get_str(rag_config, "provider_corpus_path", "rag/corpora/provider_docs")
    ).resolve()
    internal_corpus_path = Path(
        _get_str(rag_config, "internal_corpus_path", "rag/corpora/internal_standards")
    ).resolve()

    query_terms = _build_query_terms(provider_key, cim_doc.compute_units)

    snippets = _retrieve_from_filesystem(
        provider_key=provider_key,
        top_k=top_k,
        query_terms=query_terms,
        provider_corpus_path=provider_corpus_path,
        internal_corpus_path=internal_corpus_path,
    )

    vector_db = _get_str(rag_config, "vector_db", "chroma").lower()
    chroma_persist_dir = Path(
        _get_str(rag_config, "chroma_persist_dir", ".rag_cache/chroma")
    ).resolve()
    if vector_db == "chroma" and len(snippets) < top_k:
        remaining = top_k - len(snippets)
        snippets.extend(
            _retrieve_from_chroma(
                provider_key=provider_key,
                query_terms=query_terms,
                top_k=remaining,
                chroma_persist_dir=chroma_persist_dir,
            )
        )

    unique_snippets = _unique_preserve_order(snippets)[:top_k]
    rag_context = _format_rag_context(unique_snippets)

    messages = list(next_state.get("messages", []))
    messages.append(
        "rag_retriever: context_snippets={} provider={}".format(
            len(unique_snippets),
            provider_key,
        )
    )

    next_state["rag_context"] = rag_context
    next_state["messages"] = messages
    next_state["status"] = "running"

    logger.info(
        "rag_retriever: built context with {} snippets for provider {}",
        len(unique_snippets),
        provider_key,
    )

    return next_state


def _provider_key(state: MigrationState) -> str:
    target_provider = state.get("target_provider")
    if target_provider is not None:
        return str(target_provider).strip().lower()

    cim_doc = state.get("sized_cim") or state.get("cim")
    if cim_doc is not None:
        return str(cim_doc.target_provider.value).strip().lower()

    raise ValueError("rag_retriever: cannot determine target provider")


def _build_query_terms(provider_key: str, compute_units: object) -> list[str]:
    terms = [
        provider_key,
        "terraform",
        "network",
        "subnet",
        "security group",
        "tagging",
        "Environment",
        "Owner",
        "MigratedFrom",
        "encryption",
        "no_open_ingress",
        "no_public_s3",
        "no_default_vpc",
    ]

    if isinstance(compute_units, list):
        cpu_values = sorted({int(cu.vcpus) for cu in compute_units})
        ram_values = sorted({int(cu.ram_mb) for cu in compute_units})
        terms.extend([f"vcpus {value}" for value in cpu_values[:3]])
        terms.extend([f"ram_mb {value}" for value in ram_values[:3]])

    return terms


def _retrieve_from_filesystem(
    provider_key: str,
    top_k: int,
    query_terms: list[str],
    provider_corpus_path: Path,
    internal_corpus_path: Path,
) -> list[str]:
    candidates: list[tuple[int, str]] = []

    for base_path in (provider_corpus_path, internal_corpus_path):
        if not base_path.exists():
            continue

        for file_path in _iter_text_files(base_path):
            content = _read_text(file_path)
            if not content:
                continue

            score = _score_content(content, file_path, provider_key, query_terms)
            if score <= 0:
                continue

            snippet = _summarize_content(file_path, content)
            candidates.append((score, snippet))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [snippet for _, snippet in candidates[:top_k]]


def _retrieve_from_chroma(
    provider_key: str,
    query_terms: list[str],
    top_k: int,
    chroma_persist_dir: Path,
) -> list[str]:
    if top_k <= 0 or not chroma_persist_dir.exists():
        return []

    try:
        import chromadb
    except Exception:
        return []

    try:
        client = chromadb.PersistentClient(path=str(chroma_persist_dir))
        collection = client.get_collection(name="provider_docs")
    except Exception:
        return []

    query = " ".join([provider_key, *query_terms[:6]])
    try:
        result = collection.query(query_texts=[query], n_results=top_k)
    except Exception:
        return []

    documents = result.get("documents", [])
    if not documents or not isinstance(documents, list):
        return []

    first_row = documents[0] if documents else []
    if not isinstance(first_row, list):
        return []

    snippets: list[str] = []
    for doc in first_row:
        if isinstance(doc, str) and doc.strip():
            snippets.append(doc.strip()[:1200])

    return snippets


def _format_rag_context(snippets: list[str]) -> str:
    if not snippets:
        return ""

    sections = [f"[Context {index}]\n{snippet}" for index, snippet in enumerate(snippets, start=1)]
    return "\n\n".join(sections)


def _score_content(content: str, file_path: Path, provider_key: str, query_terms: list[str]) -> int:
    text = content.lower()
    path_text = str(file_path).lower()

    score = 0
    if provider_key in path_text:
        score += 4
    if provider_key in text:
        score += 4

    for term in query_terms:
        term_norm = term.strip().lower()
        if not term_norm:
            continue
        if term_norm in text:
            score += 1

    return score


def _summarize_content(file_path: Path, content: str) -> str:
    stripped = content.strip()
    if len(stripped) > 1400:
        stripped = stripped[:1400]
    return f"Source: {file_path}\n{stripped}"


def _iter_text_files(root: Path) -> Iterable[Path]:
    allowed_extensions = {".md", ".txt", ".rst", ".rego", ".tf", ".json", ".yaml", ".yml"}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in allowed_extensions:
            yield path


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


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


def _get_str(source: dict[str, JSONValue], key: str, default: str) -> str:
    value = source.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


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
