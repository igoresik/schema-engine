"""BYOK configuration: credentials and endpoints live only on the user's machine.

Resolution order, highest first:

1. Explicit overrides (CLI flags / function arguments)
2. Process environment variables
3. A ``.env`` file in the working directory
4. ``$XDG_CONFIG_HOME/schema-engine/config.json`` (default ``~/.config/...``)
5. Built-in provider presets

Nothing here ever sends a key anywhere; the provider clients use it directly
against the provider's own endpoint.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

APP_NAME = "schema-engine"
ENV_PREFIX = "SCHEMA_ENGINE_"


class ConfigError(ValueError):
    """Raised for invalid or incomplete configuration."""


@dataclass(frozen=True)
class ProviderPreset:
    kind: str  # "ollama" or "openai" (OpenAI-compatible chat completions)
    base_url: str
    default_model: str
    key_env: str | None  # env var holding the API key; None = no key needed


PRESETS: dict[str, ProviderPreset] = {
    "ollama": ProviderPreset("ollama", "http://localhost:11434", "llama3.1", None),
    "openai": ProviderPreset(
        "openai", "https://api.openai.com/v1", "gpt-4o-mini", "OPENAI_API_KEY"
    ),
    "groq": ProviderPreset(
        "openai", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "GROQ_API_KEY"
    ),
    "openrouter": ProviderPreset(
        "openai", "https://openrouter.ai/api/v1", "openai/gpt-4o-mini", "OPENROUTER_API_KEY"
    ),
    "gemini": ProviderPreset(
        "openai",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "gemini-2.0-flash",
        "GEMINI_API_KEY",
    ),
}
DEFAULT_PROVIDER = "ollama"


@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    base_url: str
    api_key: str | None = field(repr=False)  # keep keys out of logs and tracebacks
    timeout: float = 60.0
    max_retries: int = 2

    @property
    def preset(self) -> ProviderPreset:
        return PRESETS[self.provider]

    def redacted(self) -> dict[str, Any]:
        """Settings safe to print: the key is reduced to a short fingerprint."""
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key": redact(self.api_key),
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }


def redact(secret: str | None) -> str | None:
    if not secret:
        return None
    if len(secret) <= 8:
        return "***"
    return f"{secret[:3]}…{secret[-4:]}"


def config_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME / "config.json"


def parse_dotenv(text: str) -> dict[str, str]:
    """Minimal ``.env`` parser: KEY=VALUE lines, ``export`` prefix, quotes, comments."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            values[key] = value
    return values


def load_settings(
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    env: Mapping[str, str] | None = None,
    dotenv_path: Path | None = Path(".env"),
    config_file: Path | None = None,
) -> Settings:
    """Resolve settings from overrides, environment, ``.env`` and the config file."""
    process_env = dict(os.environ if env is None else env)
    dotenv = (
        parse_dotenv(dotenv_path.read_text(encoding="utf-8"))
        if dotenv_path is not None and dotenv_path.is_file()
        else {}
    )
    merged_env = {**dotenv, **process_env}  # real environment beats .env
    file_cfg = _read_config_file(config_file or config_path(process_env))

    def pick(override: Any, env_key: str, file_key: str) -> Any:
        if override not in (None, ""):
            return override
        if merged_env.get(env_key):
            return merged_env[env_key]
        return file_cfg.get(file_key)

    name = (pick(provider, f"{ENV_PREFIX}PROVIDER", "provider") or DEFAULT_PROVIDER).lower()
    if name not in PRESETS:
        raise ConfigError(f"Unknown provider '{name}'. Choose one of: {', '.join(PRESETS)}.")
    preset = PRESETS[name]
    section = file_cfg.get("providers", {}).get(name, {})

    resolved_url = (
        base_url
        or merged_env.get(f"{ENV_PREFIX}BASE_URL")
        or (merged_env.get("OLLAMA_BASE_URL") if name == "ollama" else None)
        or section.get("base_url")
        or preset.base_url
    )
    resolved_key = api_key or (merged_env.get(preset.key_env) if preset.key_env else None)
    resolved_key = resolved_key or section.get("api_key")

    settings = Settings(
        provider=name,
        model=model
        or merged_env.get(f"{ENV_PREFIX}MODEL")
        or section.get("model")
        or file_cfg.get("model")
        or preset.default_model,
        base_url=str(resolved_url).rstrip("/"),
        api_key=resolved_key or None,
    )
    timeout = merged_env.get(f"{ENV_PREFIX}TIMEOUT") or file_cfg.get("timeout")
    if timeout:
        try:
            settings = replace(settings, timeout=float(timeout))
        except ValueError as exc:
            raise ConfigError(f"Invalid timeout '{timeout}'.") from exc
    return settings


def require_key(settings: Settings) -> str | None:
    """Fail early with a clear message when a cloud provider has no key."""
    if settings.preset.key_env and not settings.api_key:
        raise ConfigError(
            f"No API key for '{settings.provider}'. Set {settings.preset.key_env} in your "
            f"environment or .env file, or add it to {config_path()}."
        )
    return settings.api_key


def _read_config_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object.")
    return data
