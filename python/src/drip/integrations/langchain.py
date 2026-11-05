"""
Drip LangChain integration.

This module provides callback handlers for tracking LangChain LLM, tool,
chain, and agent usage with automatic billing through the Drip API.

Example:
    >>> from drip.integrations.langchain import DripCallbackHandler
    >>> from langchain.llms import OpenAI
    >>>
    >>> handler = DripCallbackHandler(
    ...     api_key="sk_test_...",
    ...     customer_id=customer.id,
    ...     workflow="chatbot"
    ... )
    >>>
    >>> llm = OpenAI(callbacks=[handler])
    >>> response = llm("Hello, world!")
    >>>
    >>> # Usage is automatically tracked and billed
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.agents import AgentAction, AgentFinish
    from langchain_core.documents import Document
    from langchain_core.messages import BaseMessage
    from langchain_core.outputs import LLMResult

from ..client import AsyncDrip, Drip
from ..utils import generate_idempotency_key

# =============================================================================
# Cost Calculation
# =============================================================================

# OpenAI pricing per 1M tokens (as of late 2024)
OPENAI_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "gpt-4": {"input": 30.00, "output": 60.00},
    "gpt-4-32k": {"input": 60.00, "output": 120.00},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    "gpt-3.5-turbo-16k": {"input": 3.00, "output": 4.00},
    # Embedding models
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
    "text-embedding-3-large": {"input": 0.13, "output": 0.0},
    "text-embedding-ada-002": {"input": 0.10, "output": 0.0},
}

# Anthropic pricing per 1M tokens
ANTHROPIC_PRICING: dict[str, dict[str, float]] = {
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-opus": {"input": 15.00, "output": 75.00},
    "claude-3-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
    "claude-2.1": {"input": 8.00, "output": 24.00},
    "claude-2.0": {"input": 8.00, "output": 24.00},
    "claude-instant-1.2": {"input": 0.80, "output": 2.40},
}


def get_model_pricing(model_name: str) -> dict[str, float] | None:
    """
    Get pricing for a model by name.

    Args:
        model_name: The model name/identifier.

    Returns:
        Dict with 'input' and 'output' costs per 1M tokens, or None if unknown.
    """
    model_lower = model_name.lower()

    # Check OpenAI models
    for key, pricing in OPENAI_PRICING.items():
        if key in model_lower:
            return pricing

    # Check Anthropic models
    for key, pricing in ANTHROPIC_PRICING.items():
        if key in model_lower:
            return pricing

    return None


def calculate_cost(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    """
    Calculate the cost for a model invocation.

    Args:
        model_name: The model name.
        input_tokens: Number of input/prompt tokens.
        output_tokens: Number of output/completion tokens.

    Returns:
        Cost in USD, or None if pricing is unknown.
    """
    pricing = get_model_pricing(model_name)
    if pricing is None:
        return None

    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]

    return input_cost + output_cost


# =============================================================================
# Tracking State
# =============================================================================


@dataclass
class LLMCallState:
    """State for tracking an LLM call."""

    run_id: str
    model: str
    start_time: float
    prompts: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    error: str | None = None


@dataclass
class ToolCallState:
    """State for tracking a tool call."""

    run_id: str
    tool_name: str
    start_time: float
    input_str: str = ""
    output_str: str = ""
    error: str | None = None


@dataclass
class ChainCallState:
    """State for tracking a chain execution."""

    run_id: str
    chain_type: str
    start_time: float
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class AgentCallState:
    """State for tracking agent execution."""

    run_id: str
    start_time: float
    actions: list[dict[str, Any]] = field(default_factory=list)
    final_output: str | None = None
    error: str | None = None


# =============================================================================
# Sync Callback Handler
# =============================================================================


class DripCallbackHandler:
    """
    LangChain callback handler for Drip usage tracking.

    This handler automatically tracks LLM calls, tool usage, chain executions,
    and agent actions, emitting events to the Drip API for billing.

    Example:
        >>> from drip.integrations.langchain import DripCallbackHandler
        >>> from langchain.llms import OpenAI
        >>>
        >>> handler = DripCallbackHandler(
        ...     api_key="sk_test_...",
        ...     customer_id=customer.id,
        ...     workflow="chatbot"
        ... )
        >>>
        >>> llm = OpenAI(callbacks=[handler])
        >>> response = llm("Hello!")

    Args:
        api_key: Drip API key (or set DRIP_API_KEY env var).
        customer_id: The customer ID to bill.
        workflow: Workflow name or ID for grouping runs.
        base_url: Optional custom API base URL.
        auto_create_run: Whether to auto-create runs (default True).
        emit_on_error: Whether to emit events on errors (default True).
        metadata: Additional metadata to attach to events.
    """

    def __init__(
        self,
        api_key: str | None = None,
        customer_id: str | None = None,
        workflow: str | None = None,
        base_url: str | None = None,
        auto_create_run: bool = True,
        emit_on_error: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._client = Drip(api_key=api_key, base_url=base_url)
        self._customer_id = customer_id
        self._workflow = workflow or "langchain"
        self._auto_create_run = auto_create_run
        self._emit_on_error = emit_on_error
        self._base_metadata = metadata or {}

        # Active tracking state
        self._current_run_id: str | None = None
        self._llm_calls: dict[str, LLMCallState] = {}
        self._tool_calls: dict[str, ToolCallState] = {}
        self._chain_calls: dict[str, ChainCallState] = {}
        self._agent_calls: dict[str, AgentCallState] = {}

    @property
    def customer_id(self) -> str:
        """Get the customer ID, raising if not set."""
        if self._customer_id is None:
            raise ValueError("customer_id must be set before using the handler")
        return self._customer_id

    @customer_id.setter
    def customer_id(self, value: str) -> None:
        """Set the customer ID."""
        self._customer_id = value

    @property
    def run_id(self) -> str | None:
        """Get the current run ID."""
        return self._current_run_id

    def start_run(
        self,
        external_run_id: str | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Manually start a new run.

        Args:
            external_run_id: Optional external run ID.
            correlation_id: Optional correlation ID for tracing.
            metadata: Additional metadata.

        Returns:
            The created run ID.
        """
        result = self._client.record_run(
            customer_id=self.customer_id,
            workflow=self._workflow,
            events=[],
            status="COMPLETED",
            external_run_id=external_run_id,
            correlation_id=correlation_id,
            metadata={**self._base_metadata, **(metadata or {})},
        )
        self._current_run_id = result.run.id
        return self._current_run_id

    def end_run(
        self,
        status: str = "COMPLETED",
        error_message: str | None = None,
    ) -> None:
        """
        Manually end the current run.

        Args:
            status: Final status (COMPLETED, FAILED, CANCELLED, TIMEOUT).
            error_message: Optional error message for failed runs.
        """
        if self._current_run_id:
            self._client.end_run(
                run_id=self._current_run_id,
                status=status,
                error_message=error_message,
            )
            self._current_run_id = None

    def _ensure_run(self) -> str:
        """Ensure a run exists, creating one if auto_create_run is enabled."""
        if self._current_run_id is None:
            if self._auto_create_run:
                result = self._client.start_run(
                    customer_id=self.customer_id,
                    workflow_id=self._workflow,
                    metadata=self._base_metadata,
                )
                self._current_run_id = result.id
            else:
                raise ValueError("No active run. Call start_run() first.")
        return self._current_run_id

    def _emit_event(
        self,
        event_type: str,
        quantity: float = 1,
        units: str | None = None,
        description: str | None = None,
        cost_units: float | None = None,
        metadata: dict[str, Any] | None = None,
        idempotency_suffix: str | None = None,
    ) -> None:
        """Emit an event to the Drip API."""
        run_id = self._ensure_run()

        idempotency_key = None
        if idempotency_suffix:
            idempotency_key = generate_idempotency_key(
                customer_id=self.customer_id,
                step_name=f"{event_type}:{idempotency_suffix}",
                run_id=run_id,
            )

        self._client.emit_event(
            run_id=run_id,
            event_type=event_type,
            quantity=quantity,
            units=units,
            description=description,
            cost_units=cost_units,
            idempotency_key=idempotency_key,
            metadata={**self._base_metadata, **(metadata or {})},
        )

    # =========================================================================
    # LLM Callbacks
    # =========================================================================

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when LLM starts running."""
        model_name = serialized.get("name", serialized.get("id", ["unknown"])[-1])

        self._llm_calls[str(run_id)] = LLMCallState(
            run_id=str(run_id),
            model=model_name,
            start_time=time.time(),
            prompts=prompts,
        )

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when LLM ends running."""
        state = self._llm_calls.pop(str(run_id), None)
        if state is None:
            return

        latency_ms = int((time.time() - state.start_time) * 1000)

        # Extract token usage
        token_usage = response.llm_output or {}
        if "token_usage" in token_usage:
            token_usage = token_usage["token_usage"]

        input_tokens = token_usage.get("prompt_tokens", 0)
        output_tokens = token_usage.get("completion_tokens", 0)
        total_tokens = token_usage.get("total_tokens", input_tokens + output_tokens)

        # Calculate cost
        cost = calculate_cost(state.model, input_tokens, output_tokens)

        # Emit event
        self._emit_event(
            event_type="llm.completion",
            quantity=total_tokens,
            units="tokens",
            description=f"LLM call to {state.model}",
            cost_units=cost,
            metadata={
                "model": state.model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": latency_ms,
                "prompt_count": len(state.prompts),
            },
            idempotency_suffix=str(run_id),
        )

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when LLM errors."""
        state = self._llm_calls.pop(str(run_id), None)
        if state is None:
            return

        if self._emit_on_error:
            latency_ms = int((time.time() - state.start_time) * 1000)
            self._emit_event(
                event_type="llm.error",
                quantity=1,
                units="errors",
                description=f"LLM error: {type(error).__name__}",
                metadata={
                    "model": state.model,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "latency_ms": latency_ms,
                },
                idempotency_suffix=str(run_id),
            )

    def on_llm_new_token(
        self,
        token: str,
        *,
        chunk: Any | None = None,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called on new LLM token (streaming)."""
        # We track tokens at completion, not per-token
        pass

    # =========================================================================
    # Chat Model Callbacks
    # =========================================================================

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when chat model starts running."""
        model_name = serialized.get("name", serialized.get("id", ["unknown"])[-1])

        # Convert messages to string representation for tracking
        prompts = [str(msg_list) for msg_list in messages]

        self._llm_calls[str(run_id)] = LLMCallState(
            run_id=str(run_id),
            model=model_name,
            start_time=time.time(),
            prompts=prompts,
        )

    # =========================================================================
    # Tool Callbacks
    # =========================================================================

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when tool starts running."""
        tool_name = serialized.get("name", "unknown_tool")

        self._tool_calls[str(run_id)] = ToolCallState(
            run_id=str(run_id),
            tool_name=tool_name,
            start_time=time.time(),
            input_str=input_str[:1000],  # Truncate long inputs
        )

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when tool ends running."""
        state = self._tool_calls.pop(str(run_id), None)
        if state is None:
            return

        latency_ms = int((time.time() - state.start_time) * 1000)

        self._emit_event(
            event_type="tool.call",
            quantity=1,
            units="calls",
            description=f"Tool: {state.tool_name}",
            metadata={
                "tool_name": state.tool_name,
                "latency_ms": latency_ms,
                "input_preview": state.input_str[:200],
                "output_preview": str(output)[:200],
            },
            idempotency_suffix=str(run_id),
        )

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when tool errors."""
        state = self._tool_calls.pop(str(run_id), None)
        if state is None:
            return

        if self._emit_on_error:
            latency_ms = int((time.time() - state.start_time) * 1000)
            self._emit_event(
                event_type="tool.error",
                quantity=1,
                units="errors",
                description=f"Tool error: {state.tool_name}",
                metadata={
                    "tool_name": state.tool_name,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "latency_ms": latency_ms,
                },
                idempotency_suffix=str(run_id),
            )

    # =========================================================================
    # Chain Callbacks
    # =========================================================================

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when chain starts running."""
        chain_type = serialized.get("name", serialized.get("id", ["unknown"])[-1])

        self._chain_calls[str(run_id)] = ChainCallState(
            run_id=str(run_id),
            chain_type=chain_type,
            start_time=time.time(),
            inputs=inputs,
        )

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when chain ends running."""
        state = self._chain_calls.pop(str(run_id), None)
        if state is None:
            return

        latency_ms = int((time.time() - state.start_time) * 1000)

        self._emit_event(
            event_type="chain.execution",
            quantity=1,
            units="executions",
            description=f"Chain: {state.chain_type}",
            metadata={
                "chain_type": state.chain_type,
                "latency_ms": latency_ms,
                "input_keys": list(state.inputs.keys()),
                "output_keys": list(outputs.keys()),
            },
            idempotency_suffix=str(run_id),
        )

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when chain errors."""
        state = self._chain_calls.pop(str(run_id), None)
        if state is None:
            return

        if self._emit_on_error:
            latency_ms = int((time.time() - state.start_time) * 1000)
            self._emit_event(
                event_type="chain.error",
                quantity=1,
                units="errors",
                description=f"Chain error: {state.chain_type}",
                metadata={
                    "chain_type": state.chain_type,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "latency_ms": latency_ms,
                },
                idempotency_suffix=str(run_id),
            )

    # =========================================================================
    # Agent Callbacks
    # =========================================================================

    def on_agent_action(
        self,
        action: AgentAction,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when agent takes an action."""
        run_id_str = str(run_id)

        if run_id_str not in self._agent_calls:
            self._agent_calls[run_id_str] = AgentCallState(
                run_id=run_id_str,
                start_time=time.time(),
            )

        self._agent_calls[run_id_str].actions.append({
            "tool": action.tool,
            "tool_input": str(action.tool_input)[:500],
            "log": action.log[:500] if action.log else None,
        })

        # Emit action event
        self._emit_event(
            event_type="agent.action",
            quantity=1,
            units="actions",
            description=f"Agent action: {action.tool}",
            metadata={
                "tool": action.tool,
                "action_count": len(self._agent_calls[run_id_str].actions),
            },
            idempotency_suffix=f"{run_id}:{len(self._agent_calls[run_id_str].actions)}",
        )

    def on_agent_finish(
        self,
        finish: AgentFinish,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when agent finishes."""
        state = self._agent_calls.pop(str(run_id), None)

        latency_ms = 0
        action_count = 0

        if state:
            latency_ms = int((time.time() - state.start_time) * 1000)
            action_count = len(state.actions)

        self._emit_event(
            event_type="agent.finish",
            quantity=action_count or 1,
            units="actions",
            description="Agent run completed",
            metadata={
                "latency_ms": latency_ms,
                "action_count": action_count,
                "output_preview": str(finish.return_values)[:500],
            },
            idempotency_suffix=str(run_id),
        )

    # =========================================================================
    # Retriever Callbacks
    # =========================================================================

    def on_retriever_start(
        self,
        serialized: dict[str, Any],
        query: str,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when retriever starts running."""
        # Track as a tool call
        retriever_name = serialized.get("name", "retriever")
        self._tool_calls[str(run_id)] = ToolCallState(
            run_id=str(run_id),
            tool_name=f"retriever:{retriever_name}",
            start_time=time.time(),
            input_str=query[:1000],
        )

    def on_retriever_end(
        self,
        documents: Sequence[Document],
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when retriever ends running."""
        state = self._tool_calls.pop(str(run_id), None)
        if state is None:
            return

        latency_ms = int((time.time() - state.start_time) * 1000)

        self._emit_event(
            event_type="retriever.query",
            quantity=len(documents),
            units="documents",
            description=f"Retriever: {state.tool_name}",
            metadata={
                "retriever": state.tool_name,
                "query_preview": state.input_str[:200],
                "document_count": len(documents),
                "latency_ms": latency_ms,
            },
            idempotency_suffix=str(run_id),
        )

    def on_retriever_error(
        self,
        error: BaseException,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when retriever errors."""
        state = self._tool_calls.pop(str(run_id), None)
        if state is None:
            return

        if self._emit_on_error:
            latency_ms = int((time.time() - state.start_time) * 1000)
            self._emit_event(
                event_type="retriever.error",
                quantity=1,
                units="errors",
                description=f"Retriever error: {state.tool_name}",
                metadata={
                    "retriever": state.tool_name,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "latency_ms": latency_ms,
                },
                idempotency_suffix=str(run_id),
            )

    # =========================================================================
    # Text/Embedding Callbacks
    # =========================================================================

    def on_text(
        self,
        text: str,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when arbitrary text is received."""
        # Optional: track text events if needed
        pass


# =============================================================================
# Async Callback Handler
# =============================================================================


class AsyncDripCallbackHandler:
    """
    Async LangChain callback handler for Drip usage tracking.

    This is the async version of DripCallbackHandler for use with
    async LangChain operations.

    Example:
        >>> from drip.integrations.langchain import AsyncDripCallbackHandler
        >>> from langchain.llms import OpenAI
        >>>
        >>> handler = AsyncDripCallbackHandler(
        ...     api_key="sk_test_...",
        ...     customer_id=customer.id,
        ...     workflow="chatbot"
        ... )
        >>>
        >>> llm = OpenAI(callbacks=[handler])
        >>> response = await llm.agenerate(["Hello!"])
    """

    def __init__(
        self,
        api_key: str | None = None,
        customer_id: str | None = None,
        workflow: str | None = None,
        base_url: str | None = None,
        auto_create_run: bool = True,
        emit_on_error: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._client = AsyncDrip(api_key=api_key, base_url=base_url)
        self._customer_id = customer_id
        self._workflow = workflow or "langchain"
        self._auto_create_run = auto_create_run
        self._emit_on_error = emit_on_error
        self._base_metadata = metadata or {}

        # Active tracking state
        self._current_run_id: str | None = None
        self._llm_calls: dict[str, LLMCallState] = {}
        self._tool_calls: dict[str, ToolCallState] = {}
        self._chain_calls: dict[str, ChainCallState] = {}
        self._agent_calls: dict[str, AgentCallState] = {}

    @property
    def customer_id(self) -> str:
        """Get the customer ID."""
        if self._customer_id is None:
            raise ValueError("customer_id must be set before using the handler")
        return self._customer_id

    @customer_id.setter
    def customer_id(self, value: str) -> None:
        """Set the customer ID."""
        self._customer_id = value

    @property
    def run_id(self) -> str | None:
        """Get the current run ID."""
        return self._current_run_id

    async def start_run(
        self,
        external_run_id: str | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Manually start a new run."""
        result = await self._client.record_run(
            customer_id=self.customer_id,
            workflow=self._workflow,
            events=[],
            status="COMPLETED",
            external_run_id=external_run_id,
            correlation_id=correlation_id,
            metadata={**self._base_metadata, **(metadata or {})},
        )
        self._current_run_id = result.run.id
        return self._current_run_id

    async def end_run(
        self,
        status: str = "COMPLETED",
        error_message: str | None = None,
    ) -> None:
        """Manually end the current run."""
        if self._current_run_id:
            await self._client.end_run(
                run_id=self._current_run_id,
                status=status,
                error_message=error_message,
            )
            self._current_run_id = None

    async def _ensure_run(self) -> str:
        """Ensure a run exists."""
        if self._current_run_id is None:
            if self._auto_create_run:
                result = await self._client.start_run(
                    customer_id=self.customer_id,
                    workflow_id=self._workflow,
                    metadata=self._base_metadata,
                )
                self._current_run_id = result.id
            else:
                raise ValueError("No active run. Call start_run() first.")
        return self._current_run_id

    async def _emit_event(
        self,
        event_type: str,
        quantity: float = 1,
        units: str | None = None,
        description: str | None = None,
        cost_units: float | None = None,
        metadata: dict[str, Any] | None = None,
        idempotency_suffix: str | None = None,
    ) -> None:
        """Emit an event to the Drip API."""
        run_id = await self._ensure_run()

        idempotency_key = None
        if idempotency_suffix:
            idempotency_key = generate_idempotency_key(
                customer_id=self.customer_id,
                step_name=f"{event_type}:{idempotency_suffix}",
                run_id=run_id,
            )

        await self._client.emit_event(
            run_id=run_id,
            event_type=event_type,
            quantity=quantity,
            units=units,
            description=description,
            cost_units=cost_units,
            idempotency_key=idempotency_key,
            metadata={**self._base_metadata, **(metadata or {})},
        )

    # =========================================================================
    # Async LLM Callbacks
    # =========================================================================

    async def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when LLM starts running."""
        model_name = serialized.get("name", serialized.get("id", ["unknown"])[-1])

        self._llm_calls[str(run_id)] = LLMCallState(
            run_id=str(run_id),
            model=model_name,
            start_time=time.time(),
            prompts=prompts,
        )

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when LLM ends running."""
        state = self._llm_calls.pop(str(run_id), None)
        if state is None:
            return

        latency_ms = int((time.time() - state.start_time) * 1000)

        token_usage = response.llm_output or {}
        if "token_usage" in token_usage:
            token_usage = token_usage["token_usage"]

        input_tokens = token_usage.get("prompt_tokens", 0)
        output_tokens = token_usage.get("completion_tokens", 0)
        total_tokens = token_usage.get("total_tokens", input_tokens + output_tokens)

        cost = calculate_cost(state.model, input_tokens, output_tokens)

        await self._emit_event(
            event_type="llm.completion",
            quantity=total_tokens,
            units="tokens",
            description=f"LLM call to {state.model}",
            cost_units=cost,
            metadata={
                "model": state.model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": latency_ms,
                "prompt_count": len(state.prompts),
            },
            idempotency_suffix=str(run_id),
        )

    async def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when LLM errors."""
        state = self._llm_calls.pop(str(run_id), None)
        if state is None:
            return

        if self._emit_on_error:
            latency_ms = int((time.time() - state.start_time) * 1000)
            await self._emit_event(
                event_type="llm.error",
                quantity=1,
                units="errors",
                description=f"LLM error: {type(error).__name__}",
                metadata={
                    "model": state.model,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "latency_ms": latency_ms,
                },
                idempotency_suffix=str(run_id),
            )

    async def on_llm_new_token(
        self,
        token: str,
        *,
        chunk: Any | None = None,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called on new LLM token."""
        pass

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when chat model starts running."""
        model_name = serialized.get("name", serialized.get("id", ["unknown"])[-1])
        prompts = [str(msg_list) for msg_list in messages]

        self._llm_calls[str(run_id)] = LLMCallState(
            run_id=str(run_id),
            model=model_name,
            start_time=time.time(),
            prompts=prompts,
        )

    # =========================================================================
    # Async Tool Callbacks
    # =========================================================================

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when tool starts running."""
        tool_name = serialized.get("name", "unknown_tool")

        self._tool_calls[str(run_id)] = ToolCallState(
            run_id=str(run_id),
            tool_name=tool_name,
            start_time=time.time(),
            input_str=input_str[:1000],
        )

    async def on_tool_end(
        self,
        output: Any,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when tool ends running."""
        state = self._tool_calls.pop(str(run_id), None)
        if state is None:
            return

        latency_ms = int((time.time() - state.start_time) * 1000)

        await self._emit_event(
            event_type="tool.call",
            quantity=1,
            units="calls",
            description=f"Tool: {state.tool_name}",
            metadata={
                "tool_name": state.tool_name,
                "latency_ms": latency_ms,
                "input_preview": state.input_str[:200],
                "output_preview": str(output)[:200],
            },
            idempotency_suffix=str(run_id),
        )

    async def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when tool errors."""
        state = self._tool_calls.pop(str(run_id), None)
        if state is None:
            return

        if self._emit_on_error:
            latency_ms = int((time.time() - state.start_time) * 1000)
            await self._emit_event(
                event_type="tool.error",
                quantity=1,
                units="errors",
                description=f"Tool error: {state.tool_name}",
                metadata={
                    "tool_name": state.tool_name,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "latency_ms": latency_ms,
                },
                idempotency_suffix=str(run_id),
            )

    # =========================================================================
    # Async Chain Callbacks
    # =========================================================================

    async def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when chain starts running."""
        chain_type = serialized.get("name", serialized.get("id", ["unknown"])[-1])

        self._chain_calls[str(run_id)] = ChainCallState(
            run_id=str(run_id),
            chain_type=chain_type,
            start_time=time.time(),
            inputs=inputs,
        )

    async def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when chain ends running."""
        state = self._chain_calls.pop(str(run_id), None)
        if state is None:
            return

        latency_ms = int((time.time() - state.start_time) * 1000)

        await self._emit_event(
            event_type="chain.execution",
            quantity=1,
            units="executions",
            description=f"Chain: {state.chain_type}",
            metadata={
                "chain_type": state.chain_type,
                "latency_ms": latency_ms,
                "input_keys": list(state.inputs.keys()),
                "output_keys": list(outputs.keys()),
            },
            idempotency_suffix=str(run_id),
        )

    async def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when chain errors."""
        state = self._chain_calls.pop(str(run_id), None)
        if state is None:
            return

        if self._emit_on_error:
            latency_ms = int((time.time() - state.start_time) * 1000)
            await self._emit_event(
                event_type="chain.error",
                quantity=1,
                units="errors",
                description=f"Chain error: {state.chain_type}",
                metadata={
                    "chain_type": state.chain_type,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "latency_ms": latency_ms,
                },
                idempotency_suffix=str(run_id),
            )

    # =========================================================================
    # Async Agent Callbacks
    # =========================================================================

    async def on_agent_action(
        self,
        action: AgentAction,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when agent takes an action."""
        run_id_str = str(run_id)

        if run_id_str not in self._agent_calls:
            self._agent_calls[run_id_str] = AgentCallState(
                run_id=run_id_str,
                start_time=time.time(),
            )

        self._agent_calls[run_id_str].actions.append({
            "tool": action.tool,
            "tool_input": str(action.tool_input)[:500],
            "log": action.log[:500] if action.log else None,
        })

        await self._emit_event(
            event_type="agent.action",
            quantity=1,
            units="actions",
            description=f"Agent action: {action.tool}",
            metadata={
                "tool": action.tool,
                "action_count": len(self._agent_calls[run_id_str].actions),
            },
            idempotency_suffix=f"{run_id}:{len(self._agent_calls[run_id_str].actions)}",
        )

    async def on_agent_finish(
        self,
        finish: AgentFinish,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when agent finishes."""
        state = self._agent_calls.pop(str(run_id), None)

        latency_ms = 0
        action_count = 0

        if state:
            latency_ms = int((time.time() - state.start_time) * 1000)
            action_count = len(state.actions)

        await self._emit_event(
            event_type="agent.finish",
            quantity=action_count or 1,
            units="actions",
            description="Agent run completed",
            metadata={
                "latency_ms": latency_ms,
                "action_count": action_count,
                "output_preview": str(finish.return_values)[:500],
            },
            idempotency_suffix=str(run_id),
        )

    # =========================================================================
    # Async Retriever Callbacks
    # =========================================================================

    async def on_retriever_start(
        self,
        serialized: dict[str, Any],
        query: str,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when retriever starts running."""
        retriever_name = serialized.get("name", "retriever")
        self._tool_calls[str(run_id)] = ToolCallState(
            run_id=str(run_id),
            tool_name=f"retriever:{retriever_name}",
            start_time=time.time(),
            input_str=query[:1000],
        )

    async def on_retriever_end(
        self,
        documents: Sequence[Document],
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when retriever ends running."""
        state = self._tool_calls.pop(str(run_id), None)
        if state is None:
            return

        latency_ms = int((time.time() - state.start_time) * 1000)

        await self._emit_event(
            event_type="retriever.query",
            quantity=len(documents),
            units="documents",
            description=f"Retriever: {state.tool_name}",
            metadata={
                "retriever": state.tool_name,
                "query_preview": state.input_str[:200],
                "document_count": len(documents),
                "latency_ms": latency_ms,
            },
            idempotency_suffix=str(run_id),
        )

    async def on_retriever_error(
        self,
        error: BaseException,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when retriever errors."""
        state = self._tool_calls.pop(str(run_id), None)
        if state is None:
            return

        if self._emit_on_error:
            latency_ms = int((time.time() - state.start_time) * 1000)
            await self._emit_event(
                event_type="retriever.error",
                quantity=1,
                units="errors",
                description=f"Retriever error: {state.tool_name}",
                metadata={
                    "retriever": state.tool_name,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "latency_ms": latency_ms,
                },
                idempotency_suffix=str(run_id),
            )

    async def on_text(
        self,
        text: str,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when arbitrary text is received."""
        pass


__all__ = [
    "DripCallbackHandler",
    "AsyncDripCallbackHandler",
    "calculate_cost",
    "get_model_pricing",
    "OPENAI_PRICING",
    "ANTHROPIC_PRICING",
]
