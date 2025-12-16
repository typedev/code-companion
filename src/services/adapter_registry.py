"""Registry for AI CLI history adapters."""

from .history_adapter import HistoryAdapter
from .adapters import ClaudeHistoryAdapter


# Register all available adapters
# Key is the provider ID used in settings
ADAPTERS: dict[str, type[HistoryAdapter]] = {
    "claude": ClaudeHistoryAdapter,
    # Future adapters:
    # "gemini": GeminiHistoryAdapter,
    # "codex": CodexHistoryAdapter,
}


def get_adapter(provider: str) -> HistoryAdapter:
    """Get adapter instance by provider name.

    Args:
        provider: Provider ID (e.g., "claude", "gemini")

    Returns:
        HistoryAdapter instance

    Raises:
        ValueError: If provider is unknown
    """
    adapter_class = ADAPTERS.get(provider)
    if adapter_class is None:
        raise ValueError(f"Unknown AI provider: {provider}")
    return adapter_class()


def get_available_adapters() -> list[tuple[str, str]]:
    """Get list of available adapters.

    Returns:
        List of (provider_id, display_name) tuples for adapters that are available
    """
    available = []
    for provider_id, adapter_class in ADAPTERS.items():
        if adapter_class.is_available():
            available.append((provider_id, adapter_class.name))
    return available


def get_all_adapters() -> list[tuple[str, str]]:
    """Get list of all registered adapters (available or not).

    Returns:
        List of (provider_id, display_name) tuples
    """
    return [(provider_id, cls.name) for provider_id, cls in ADAPTERS.items()]
