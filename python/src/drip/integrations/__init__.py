"""
Drip SDK integrations with popular frameworks.

Available integrations:
- LangChain: DripCallbackHandler for tracking LLM/agent usage
"""

from __future__ import annotations

__all__: list[str] = []

# Lazy imports to avoid requiring all integration dependencies
def __getattr__(name: str) -> object:
    if name == "DripCallbackHandler":
        from .langchain import DripCallbackHandler
        return DripCallbackHandler
    if name == "AsyncDripCallbackHandler":
        from .langchain import AsyncDripCallbackHandler
        return AsyncDripCallbackHandler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
