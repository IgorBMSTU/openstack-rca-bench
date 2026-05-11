"""Unified LLM client supporting multiple OpenAI-compatible providers."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

try:
    import openai
except ImportError as _import_err:
    openai = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_BASE_URLS = {
    "qwen": "<your-llm-endpoint>/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "glm": None,
    "kimi": None,
    "openai": None,
}

DEFAULT_MODELS = {
    "qwen": "qwen3-coder-30b-a3b",
    "deepseek": "deepseek-chat",
    "glm": "glm-4",
    "kimi": "moonshot-v1-8k",
    "openai": None,
}

PROVIDER_ENV_KEYS = {
    "qwen": ("QWEN_API_KEY", "QWEN_BASE_URL"),
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"),
    "glm": ("GLM_API_KEY", "GLM_BASE_URL"),
    "kimi": ("KIMI_API_KEY", "KIMI_BASE_URL"),
    "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
}

CACHE_DIR = Path(__file__).resolve().parent.parent / "results" / "cache"

MAX_RETRIES = 5
BASE_DELAY_SECONDS = 2.0


class LLMClient:
    """OpenAI-compatible LLM client with retry, caching, and JSON-mode support."""

    def __init__(
        self,
        provider: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        """Initialize the LLM client.

        Args:
            provider: Provider nickname (qwen, deepseek, glm, kimi, openai).
                If None, inferred from environment variables.
            api_key: Explicit API key. Overrides environment variable.
            base_url: Explicit base URL. Overrides environment variable and default.
            model: Explicit model name. Overrides provider default.
        """
        self.provider, self.api_key, self.base_url, self.model = self._resolve_config(
            provider, api_key, base_url, model
        )

        if not self.api_key:
            raise ValueError(
                f"No API key found for provider '{self.provider}'. "
                f"Set the corresponding environment variable or pass api_key explicitly."
            )

        if openai is None:
            raise ImportError(
                "The 'openai' package is not installed. "
                "Install it with: pip install openai>=1.0.0"
            )

        self.client = openai.OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=120.0,
        )
        logger.info(
            "LLMClient initialized: provider=%s model=%s base_url=%s",
            self.provider,
            self.model,
            self.base_url,
        )

    def _resolve_config(
        self,
        provider: str | None,
        api_key: str | None,
        base_url: str | None,
        model: str | None,
    ) -> tuple[str, str | None, str | None, str | None]:
        """Resolve provider configuration from explicit args and environment."""
        # Determine provider
        if provider is None:
            for prov, (key_env, _) in PROVIDER_ENV_KEYS.items():
                if os.getenv(key_env):
                    provider = prov
                    break
        if provider is None:
            provider = "openai"

        provider = provider.lower()
        key_env, url_env = PROVIDER_ENV_KEYS.get(
            provider, ("OPENAI_API_KEY", "OPENAI_BASE_URL")
        )

        # Resolve API key
        resolved_api_key = api_key or os.getenv(key_env)

        # Resolve base URL
        resolved_base_url = base_url or os.getenv(url_env) or DEFAULT_BASE_URLS.get(provider)

        # Resolve model
        resolved_model = model or DEFAULT_MODELS.get(provider)

        return provider, resolved_api_key, resolved_base_url, resolved_model

    def _cache_path(self, prompt: str, system_prompt: str | None, **kwargs: Any) -> Path:
        """Compute cache file path based on prompt hash."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "provider": self.provider,
            "model": self.model,
            "prompt": prompt,
            "system_prompt": system_prompt,
            **kwargs,
        }
        payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()[:16]
        safe_model = (self.model or "unknown").replace("/", "_")
        filename = f"{self.provider}_{safe_model}_{digest}.json"
        return CACHE_DIR / filename

    def _load_cache(self, cache_path: Path) -> dict[str, Any] | None:
        """Load cached response if it exists."""
        if cache_path.exists():
            try:
                with cache_path.open("r", encoding="utf-8") as f:
                    cached = json.load(f)
                logger.debug("Cache hit: %s", cache_path.name)
                return cached
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load cache %s: %s", cache_path, exc)
        return None

    def _save_cache(self, cache_path: Path, response: dict[str, Any]) -> None:
        """Save response to cache."""
        try:
            with cache_path.open("w", encoding="utf-8") as f:
                json.dump(response, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.warning("Failed to save cache %s: %s", cache_path, exc)

    def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        json_mode: bool = True,
    ) -> dict[str, Any]:
        """Send a completion request to the LLM with retry and caching.

        Args:
            prompt: The user prompt.
            system_prompt: Optional system prompt.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            json_mode: Whether to request JSON mode.

        Returns:
            A dict. If JSON mode succeeds, the parsed JSON is under key 'parsed'.
            The raw response text is always under key 'text'.
            Additional keys: 'model', 'provider', 'cached', 'tokens'.
        """
        cache_path = self._cache_path(
            prompt, system_prompt, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode
        )
        cached = self._load_cache(cache_path)
        if cached is not None:
            cached.setdefault("cached", True)
            return cached

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if json_mode:
            request_kwargs["response_format"] = {"type": "json_object"}

        last_exception: Exception | None = None
        raw_text: str = ""
        usage: dict[str, Any] | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self.client.chat.completions.create(**request_kwargs)
                if response.choices:
                    raw_text = response.choices[0].message.content or ""
                if response.usage:
                    usage = {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens,
                    }
                break
            except openai.APIStatusError as exc:
                last_exception = exc
                # If JSON mode is unsupported, fall back on the last attempt
                if json_mode and exc.status_code == 400 and attempt == MAX_RETRIES:
                    logger.warning(
                        "JSON mode likely unsupported by %s; retrying without json_mode",
                        self.provider,
                    )
                    request_kwargs.pop("response_format", None)
                    try:
                        response = self.client.chat.completions.create(**request_kwargs)
                        if response.choices:
                            raw_text = response.choices[0].message.content or ""
                        if response.usage:
                            usage = {
                                "prompt_tokens": response.usage.prompt_tokens,
                                "completion_tokens": response.usage.completion_tokens,
                                "total_tokens": response.usage.total_tokens,
                            }
                        last_exception = None
                        break
                    except Exception as fallback_exc:
                        last_exception = fallback_exc
                logger.warning(
                    "Attempt %d/%d failed for %s: %s",
                    attempt,
                    MAX_RETRIES,
                    self.provider,
                    exc,
                )
                if attempt < MAX_RETRIES:
                    delay = BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                    logger.info("Retrying in %.1f seconds...", delay)
                    time.sleep(delay)
            except openai.APIConnectionError as exc:
                last_exception = exc
                logger.warning(
                    "Attempt %d/%d connection error for %s: %s",
                    attempt,
                    MAX_RETRIES,
                    self.provider,
                    exc,
                )
                if attempt < MAX_RETRIES:
                    delay = BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                    logger.info("Retrying in %.1f seconds...", delay)
                    time.sleep(delay)
            except Exception as exc:
                last_exception = exc
                logger.warning(
                    "Attempt %d/%d unexpected error for %s: %s",
                    attempt,
                    MAX_RETRIES,
                    self.provider,
                    exc,
                )
                if attempt < MAX_RETRIES:
                    delay = BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                    logger.info("Retrying in %.1f seconds...", delay)
                    time.sleep(delay)

        if last_exception is not None:
            logger.error(
                "All %d attempts failed for %s: %s",
                MAX_RETRIES,
                self.provider,
                last_exception,
            )
            return {
                "error": str(last_exception),
                "text": raw_text,
                "parsed": None,
                "model": self.model,
                "provider": self.provider,
                "cached": False,
                "tokens": usage,
            }

        parsed: dict[str, Any] | None = None
        if raw_text:
            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                logger.warning("Failed to parse response as JSON: %s", exc)
                # Try to extract JSON from markdown code block
                parsed = _extract_json_from_markdown(raw_text)

        result: dict[str, Any] = {
            "text": raw_text,
            "parsed": parsed,
            "model": self.model,
            "provider": self.provider,
            "cached": False,
            "tokens": usage,
        }

        self._save_cache(cache_path, result)
        return result


def _extract_json_from_markdown(text: str) -> dict[str, Any] | None:
    """Attempt to extract JSON from a markdown code block."""
    import re

    # Look for ```json ... ``` or ``` ... ``` blocks
    pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    matches = re.findall(pattern, text)
    for match in matches:
        match = match.strip()
        if match:
            try:
                return json.loads(match)
            except json.JSONDecodeError:
                continue
    return None
