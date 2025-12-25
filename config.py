import json
import os
# config.py — runtime/model configuration for ERC agent

# --- LLM / runtime config ---
MAX_STEPS = int(os.getenv("AGNO_MAX_STEPS", "30"))
MAX_COMPLETION_TOKENS = int(os.getenv("AGNO_MAX_COMPLETION_TOKENS", "4096"))
PAGE_LIMIT_DEFAULT = int(os.getenv("AGNO_PAGE_LIMIT", "5"))
RETRY_COUNT = int(os.getenv("AGNO_RETRY_COUNT", "3"))
MAX_WORKERS = int(os.getenv("AGNO_MAX_WORKERS", "5"))  # Parallel tasks
RATE_LIMIT_RPS = float(os.getenv("AGNO_RATE_LIMIT_RPS", "3.0"))  # Requests per second per worker

# --- Model selection (OpenRouter) ---
MODEL_OPTIONS = [
    ("qwen/qwen3-235b-a22b-2507", {"order": ["Cerebras"], "allow_fallbacks": False}),
    ("x-ai/grok-4.1-fast", None),
    ("x-ai/grok-4.1", None),
    ("openai/gpt-4.1", None),
]

MODEL_ALIASES = {
    "grok-fast": "x-ai/grok-4.1-fast",
    "grok": "x-ai/grok-4.1",
    "qwen": "qwen/qwen3-235b-a22b-2507",
    "gpt": "openai/gpt-4.1",
    # Full aliases
    "grok-4.1-fast": "x-ai/grok-4.1-fast",
    "grok-4.1": "x-ai/grok-4.1",
    # "qwen3-235b": "qwen/qwen3-235b-a22b-2507",
    # "qwen3-235b-a22b-2507": "qwen/qwen3-235b-a22b-2507",
    "gpt-4.1": "openai/gpt-4.1",
}


def get_provider(model_id: str) -> dict | None:
    """Get provider config for a model id from MODEL_OPTIONS."""
    for mid, provider in MODEL_OPTIONS:
        if mid == model_id:
            return provider
    return None


# Defaults (override-safe) — use the first MODEL_OPTIONS entry only
DEFAULT_MODEL, DEFAULT_PROVIDER = MODEL_OPTIONS[0]


def resolve_model(choice: str | None) -> tuple[str, dict | None]:
    """
    Resolve CLI choice → (model_id, provider_body).
    - Only models declared in MODEL_OPTIONS are allowed.
    - If choice is None: use defaults.
    - Aliases expand to known MODEL_OPTIONS ids.
    """
    if not choice:
        return DEFAULT_MODEL, DEFAULT_PROVIDER

    model_id = MODEL_ALIASES.get(choice.lower(), choice)
    for mid, provider in MODEL_OPTIONS:
        if mid == model_id:
            return mid, provider
    raise ValueError(f"Model '{choice}' is not allowed. Allowed: {[m for m, _ in MODEL_OPTIONS]}")


# Initial values (may be overridden by CLI in main)
MODEL_ID, MODEL_PROVIDER_BODY = resolve_model(None)

MODEL_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# Architecture label reported to ERC platform
ARCHITECTURE_NAME = "OODA Loop Agent (direct)"

