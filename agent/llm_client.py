from __future__ import annotations

import importlib
import json
import os
import threading
import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, Mapping

from anthropic import Anthropic
from dotenv import load_dotenv
from loguru import logger
from openai import OpenAI


class LLMProvider(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"


@dataclass(frozen=True)
class LLMSettings:
    provider: LLMProvider
    model: str
    temperature_hcl: float
    temperature_report: float
    max_tokens: int
    timeout_seconds: int
    api_key: str
    base_url: str | None


class LLMClient:
    """Provider-agnostic LLM client used by graph nodes."""

    _cache_lock: ClassVar[threading.Lock] = threading.Lock()
    _instance_cache: ClassVar[dict[str, "LLMClient"]] = {}

    def __new__(
        cls,
        config: Mapping[str, object],
        provider_override: str | None = None,
        model_override: str | None = None,
        base_url_override: str | None = None,
        api_key_override: str | None = None,
    ) -> "LLMClient":
        # Ensure env-derived settings are populated before key computation.
        load_dotenv()

        cache_key = _build_client_cache_key(
            config=config,
            provider_override=provider_override,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
        )

        with cls._cache_lock:
            cached = cls._instance_cache.get(cache_key)
            if cached is not None:
                return cached

            instance = super().__new__(cls)
            instance._cache_key = cache_key
            instance._initialized = False
            cls._instance_cache[cache_key] = instance
            return instance

    def __init__(
        self,
        config: Mapping[str, object],
        provider_override: str | None = None,
        model_override: str | None = None,
        base_url_override: str | None = None,
        api_key_override: str | None = None,
    ) -> None:
        with self.__class__._cache_lock:
            if getattr(self, "_initialized", False):
                return

            try:
                load_dotenv()
                self.settings = self._build_settings(
                    config=config,
                    provider_override=provider_override,
                    model_override=model_override,
                    base_url_override=base_url_override,
                    api_key_override=api_key_override,
                )

                self._anthropic_client: Anthropic | None = None
                self._openai_client: OpenAI | None = None
                self._google_client: Any | None = None
                self._google_legacy_model: Any | None = None

                self._initialized = True
            except Exception:
                cache_key = getattr(self, "_cache_key", "")
                if cache_key and self.__class__._instance_cache.get(cache_key) is self:
                    del self.__class__._instance_cache[cache_key]
                raise

        logger.info(
            "Initialized LLMClient with provider={} model={}",
            self.settings.provider.value,
            self.settings.model,
        )

    @classmethod
    def clear_cache(cls) -> None:
        with cls._cache_lock:
            cls._instance_cache.clear()

    def generate_hcl(self, prompt: str, system_prompt: str | None = None) -> str:
        return self._generate(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=self.settings.temperature_hcl,
        )

    def generate_review_notes(self, prompt: str, system_prompt: str | None = None) -> str:
        return self._generate(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=self.settings.temperature_report,
        )

    def _generate(self, prompt: str, system_prompt: str | None, temperature: float) -> str:
        provider = self.settings.provider
        if provider == LLMProvider.ANTHROPIC:
            return self._generate_anthropic(prompt, system_prompt, temperature)
        if provider == LLMProvider.OPENAI:
            return self._generate_openai(prompt, system_prompt, temperature)
        if provider == LLMProvider.GOOGLE:
            return self._generate_google(prompt, system_prompt, temperature)

        raise ValueError(f"Unsupported LLM provider: {provider}")

    def _generate_anthropic(
        self,
        prompt: str,
        system_prompt: str | None,
        temperature: float,
    ) -> str:
        client = self._get_anthropic_client()
        system_text = system_prompt or ""

        response = client.messages.create(
            model=self.settings.model,
            max_tokens=self.settings.max_tokens,
            temperature=temperature,
            system=system_text,
            messages=[{"role": "user", "content": prompt}],
            timeout=self.settings.timeout_seconds,
        )

        text_chunks: list[str] = []
        for block in response.content:
            if hasattr(block, "text") and block.text:
                text_chunks.append(block.text)

        result = "\n".join(text_chunks).strip()
        if not result:
            raise ValueError("Anthropic response did not contain text content")
        return result

    def _generate_openai(
        self,
        prompt: str,
        system_prompt: str | None,
        temperature: float,
    ) -> str:
        client = self._get_openai_client()
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = client.chat.completions.create(
            model=self.settings.model,
            temperature=temperature,
            max_tokens=self.settings.max_tokens,
            messages=messages,
        )

        content = response.choices[0].message.content
        result = content.strip() if content else ""
        if not result:
            raise ValueError("OpenAI response did not contain text content")
        return result

    def _generate_google(
        self,
        prompt: str,
        system_prompt: str | None,
        temperature: float,
    ) -> str:
        sdk = _load_google_sdk()
        combined_prompt = prompt if not system_prompt else f"{system_prompt}\n\n{prompt}"

        if sdk["legacy"]:
            model = self._get_google_legacy_model(sdk["genai_module"])
            response = model.generate_content(
                combined_prompt,
                generation_config=sdk["genai_module"].GenerationConfig(
                    temperature=temperature,
                    max_output_tokens=self.settings.max_tokens,
                ),
            )
        else:
            client = self._get_google_client(sdk["genai_module"])
            response = client.models.generate_content(
                model=self.settings.model,
                contents=combined_prompt,
                config=_build_google_generate_config(
                    types_module=sdk["types_module"],
                    temperature=temperature,
                    max_output_tokens=self.settings.max_tokens,
                ),
            )

        result = _extract_google_response_text(response)
        if not result:
            raise ValueError("Google response did not contain text content")
        return result

    def _get_anthropic_client(self) -> Anthropic:
        if self._anthropic_client is None:
            self._anthropic_client = Anthropic(
                api_key=self.settings.api_key,
                base_url=self.settings.base_url,
                timeout=self.settings.timeout_seconds,
            )
        return self._anthropic_client

    def _get_openai_client(self) -> OpenAI:
        if self._openai_client is None:
            self._openai_client = OpenAI(
                api_key=self.settings.api_key,
                base_url=self.settings.base_url,
                timeout=self.settings.timeout_seconds,
            )
        return self._openai_client

    def _get_google_client(self, genai_module: Any) -> Any:
        if self._google_client is None:
            self._google_client = genai_module.Client(api_key=self.settings.api_key)
        return self._google_client

    def _get_google_legacy_model(self, genai_module: Any) -> Any:
        if self._google_legacy_model is None:
            genai_module.configure(api_key=self.settings.api_key)
            self._google_legacy_model = genai_module.GenerativeModel(model_name=self.settings.model)
        return self._google_legacy_model

    def _build_settings(
        self,
        config: Mapping[str, object],
        provider_override: str | None,
        model_override: str | None,
        base_url_override: str | None,
        api_key_override: str | None,
    ) -> LLMSettings:
        llm_section = _get_mapping(config, "llm")

        env_provider = os.getenv("LLM_PROVIDER", "").strip().lower()
        provider_str = (
            provider_override
            or env_provider
            or _get_str(llm_section, "provider", "anthropic")
        ).strip().lower()
        provider = LLMProvider(provider_str)

        models_map = _get_mapping(llm_section, "models")
        env_model = os.getenv("LLM_MODEL", "").strip()
        if model_override:
            model = model_override
        elif env_model:
            model = env_model
        elif models_map:
            model = _get_str(models_map, provider.value, _default_model(provider))
        else:
            model = _get_str(llm_section, "model", _default_model(provider))

        temperature_hcl = _get_float(llm_section, "temperature_hcl", 0.0)
        temperature_report = _get_float(llm_section, "temperature_report", 0.2)
        max_tokens = _get_int(llm_section, "max_tokens", 8192)
        timeout_seconds = _get_int(llm_section, "timeout_seconds", 120)

        base_url: str | None
        if provider == LLMProvider.OPENAI:
            base_url = (
                base_url_override
                or _resolve_openai_base_url()
                or _get_optional_str(llm_section, "base_url")
            )
        else:
            base_url = None
        api_key = api_key_override or _resolve_api_key(provider)

        return LLMSettings(
            provider=provider,
            model=model,
            temperature_hcl=temperature_hcl,
            temperature_report=temperature_report,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            api_key=api_key,
            base_url=base_url,
        )


def _resolve_api_key(provider: LLMProvider) -> str:
    env_var = {
        LLMProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
        LLMProvider.OPENAI: "OPENAI_API_KEY",
        LLMProvider.GOOGLE: "GOOGLE_API_KEY",
    }[provider]

    value = os.getenv(env_var, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {env_var}")
    return value


def _load_google_sdk() -> dict[str, Any]:
    try:
        genai_module = importlib.import_module("google.genai")
        try:
            types_module = importlib.import_module("google.genai.types")
        except ModuleNotFoundError:
            types_module = None

        return {
            "legacy": False,
            "genai_module": genai_module,
            "types_module": types_module,
        }
    except ModuleNotFoundError:
        # Legacy fallback is only loaded when provider=google is used.
        try:
            legacy_module = importlib.import_module("google.generativeai")
            return {
                "legacy": True,
                "genai_module": legacy_module,
                "types_module": None,
            }
        except ModuleNotFoundError as exc:
            raise ValueError(
                "Google provider requires google-genai (preferred) or google-generativeai"
            ) from exc


def _build_google_generate_config(
    types_module: Any,
    temperature: float,
    max_output_tokens: int,
) -> Any:
    kwargs = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }

    if types_module is not None and hasattr(types_module, "GenerateContentConfig"):
        return types_module.GenerateContentConfig(**kwargs)
    return kwargs


def _extract_google_response_text(response: Any) -> str:
    direct_text = getattr(response, "text", None)
    if isinstance(direct_text, str) and direct_text.strip():
        return direct_text.strip()

    candidates = getattr(response, "candidates", None)
    if not isinstance(candidates, list):
        return ""

    chunks: list[str] = []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None)
        if not isinstance(parts, list):
            continue
        for part in parts:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())

    return "\n".join(chunks).strip()


def _resolve_openai_base_url() -> str | None:
    for env_var in ("OPENAI_BASE_URL", "LLM_BASE_URL"):
        value = os.getenv(env_var, "").strip()
        if value:
            return value

    return None


def _default_model(provider: LLMProvider) -> str:
    return {
        LLMProvider.ANTHROPIC: "claude-sonnet-4-5",
        LLMProvider.OPENAI: "gpt-4o",
        LLMProvider.GOOGLE: "gemini-2.0-flash",
    }[provider]


def _build_client_cache_key(
    config: Mapping[str, object],
    provider_override: str | None,
    model_override: str | None,
    base_url_override: str | None,
    api_key_override: str | None,
) -> str:
    payload = {
        "llm_config": _normalize_cache_value(_get_mapping(config, "llm")),
        "provider_override": provider_override or "",
        "model_override": model_override or "",
        "base_url_override": base_url_override or "",
        "api_key_override_sha256": _hash_secret(api_key_override),
        "env": {
            "LLM_PROVIDER": os.getenv("LLM_PROVIDER", "").strip(),
            "LLM_MODEL": os.getenv("LLM_MODEL", "").strip(),
            "OPENAI_BASE_URL": os.getenv("OPENAI_BASE_URL", "").strip(),
            "LLM_BASE_URL": os.getenv("LLM_BASE_URL", "").strip(),
            "ANTHROPIC_API_KEY_SHA256": _hash_secret(os.getenv("ANTHROPIC_API_KEY", "").strip()),
            "OPENAI_API_KEY_SHA256": _hash_secret(os.getenv("OPENAI_API_KEY", "").strip()),
            "GOOGLE_API_KEY_SHA256": _hash_secret(os.getenv("GOOGLE_API_KEY", "").strip()),
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _normalize_cache_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            normalized[str(key)] = _normalize_cache_value(item)
        return normalized

    if isinstance(value, set):
        return [_normalize_cache_value(item) for item in sorted(value, key=repr)]

    if isinstance(value, (list, tuple)):
        return [_normalize_cache_value(item) for item in value]

    return repr(value)


def _hash_secret(value: str | None) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _get_mapping(source: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = source.get(key)
    if isinstance(value, Mapping):
        return value
    return {}


def _get_str(source: Mapping[str, object], key: str, default: str) -> str:
    value = source.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _get_optional_str(source: Mapping[str, object], key: str) -> str | None:
    value = source.get(key)
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _get_int(source: Mapping[str, object], key: str, default: int) -> int:
    value = source.get(key)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return default


def _get_float(source: Mapping[str, object], key: str, default: float) -> float:
    value = source.get(key)
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default
