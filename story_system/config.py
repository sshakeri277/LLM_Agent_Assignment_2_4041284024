"""Settings from environment variables (and an optional .env file); the model client.

Provider: AvalAI, as the assignment requires (`AVALAI_API_KEY`, `AVALAI_BASE_URL`, `AVALAI_MODEL`). OpenAI directly
is available only on explicit opt-in (`LLM_PROVIDER=openai` with `OPENAI_API_KEY`); an OpenAI key alone is never
used in place of the AvalAI key. Both speak the OpenAI Chat Completions API.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Dict, MutableMapping, Optional

log = logging.getLogger("story_system.config")

DEFAULT_BASE_URL = "https://api.avalai.ir/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
MAX_REVISIONS_LIMIT = 2  # assignment §4: at most two Author revisions


@dataclass
class Settings:
    api_key: str = ""
    provider: str = "avalai"            # "avalai" or "openai"
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    creative_temperature: float = 0.8   # Author and Storyteller
    editor_temperature: float = 0.2     # Editor: consistent judgements
    max_revisions: int = 2
    concurrency: int = 2                # stories processed in parallel
    request_timeout: float = 180.0
    retry_delay: float = 2.0


_ENV = {  # variable -> (field, type)
    "AVALAI_API_KEY": ("api_key", str),
    "AVALAI_BASE_URL": ("base_url", str),
    "AVALAI_MODEL": ("model", str),
    "AGENT_CREATIVE_TEMPERATURE": ("creative_temperature", float),
    "AGENT_EDITOR_TEMPERATURE": ("editor_temperature", float),
    "AGENT_MAX_REVISIONS": ("max_revisions", int),
    "AGENT_CONCURRENCY": ("concurrency", int),
    "AGENT_REQUEST_TIMEOUT": ("request_timeout", float),
    "AGENT_RETRY_DELAY": ("retry_delay", float),
}


def load_dotenv(path: str, env: MutableMapping[str, str]) -> None:
    """Minimal .env reader (KEY=VALUE lines); never overrides variables that are already set."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            value = value.strip().strip("'\"")
            if key and key not in env:
                env[key] = value


def load_settings(env: Optional[Dict[str, str]] = None, **overrides) -> Settings:
    env = os.environ if env is None else env
    s = Settings()
    if str(env.get("LLM_PROVIDER") or "").strip().lower() == "openai":
        s.provider = "openai"
        s.api_key = str(env.get("OPENAI_API_KEY") or "").strip()
        s.base_url = (env.get("OPENAI_BASE_URL") or OPENAI_BASE_URL).strip()
        s.model = (env.get("OPENAI_MODEL") or env.get("AVALAI_MODEL") or DEFAULT_MODEL).strip()
        env = {k: v for k, v in env.items() if k not in ("AVALAI_API_KEY", "AVALAI_BASE_URL", "AVALAI_MODEL")}
    for var, (name, typ) in _ENV.items():
        raw = env.get(var)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            setattr(s, name, typ(str(raw).strip()))
        except ValueError:
            log.warning("ignoring %s=%r (not a valid value); using the default %r", var, raw, getattr(s, name))
    for name, value in overrides.items():
        if value is not None:
            setattr(s, name, value)
    s.max_revisions = max(0, min(MAX_REVISIONS_LIMIT, s.max_revisions))
    s.concurrency = max(1, s.concurrency)
    s.base_url = s.base_url.rstrip("/")
    return s


def missing_key_message(settings: Settings) -> str:
    if settings.provider == "openai":
        return "OPENAI_API_KEY is not set (LLM_PROVIDER=openai). Set it in the environment or in .env."
    return ("AVALAI_API_KEY is not set. Set your AvalAI key in the environment "
            "(Linux/macOS: export AVALAI_API_KEY=\"...\"; Windows: set AVALAI_API_KEY=...) or in a .env file next "
            "to main.py (see .env.example). An OPENAI_API_KEY is not used unless LLM_PROVIDER=openai is set.")


def create_model_client(settings: Settings, role: str):
    """An AutoGen OpenAI-compatible client (AvalAI or OpenAI). `role` is "creative" or "editor"."""
    from autogen_ext.models.openai import OpenAIChatCompletionClient
    from autogen_ext.models.openai._model_info import get_info

    temperature = settings.editor_temperature if role == "editor" else settings.creative_temperature
    kwargs = dict(model=settings.model, api_key=settings.api_key, base_url=settings.base_url,
                  temperature=temperature, timeout=settings.request_timeout, max_retries=3)
    try:
        get_info(settings.model)
    except KeyError:  # a model name AutoGen does not know: describe what the agents need from it
        kwargs["model_info"] = {"vision": False, "function_calling": True, "json_output": True,
                                "structured_output": True, "family": "unknown"}
    return OpenAIChatCompletionClient(**kwargs)
