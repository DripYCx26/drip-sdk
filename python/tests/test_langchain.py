"""Tests for the Drip LangChain integration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from drip.integrations.langchain import (
    ANTHROPIC_PRICING,
    OPENAI_PRICING,
    AsyncDripCallbackHandler,
    DripCallbackHandler,
    calculate_cost,
    get_model_pricing,
)

# =============================================================================
# Mock LangChain Types (to avoid requiring langchain as a dependency)
# =============================================================================


@dataclass
class MockLLMResult:
    """Mock LLMResult for testing."""

    llm_output: dict[str, Any] | None = None


@dataclass
class MockAgentAction:
    """Mock AgentAction for testing."""

    tool: str
    tool_input: str
    log: str | None = None


@dataclass
class MockAgentFinish:
    """Mock AgentFinish for testing."""

    return_values: dict[str, Any]
    log: str = ""


@dataclass
class MockDocument:
    """Mock Document for testing."""

    page_content: str
    metadata: dict[str, Any] | None = None


# =============================================================================
# Cost Calculation Tests
# =============================================================================


class TestCostCalculation:
    """Tests for cost calculation functions."""

    def test_get_openai_model_pricing(self) -> None:
        """Should return pricing for OpenAI models."""
        pricing = get_model_pricing("gpt-4o")
        assert pricing is not None
        assert "input" in pricing
        assert "output" in pricing
        assert pricing["input"] == 2.50
        assert pricing["output"] == 10.00

    def test_get_anthropic_model_pricing(self) -> None:
        """Should return pricing for Anthropic models."""
        pricing = get_model_pricing("claude-3-5-sonnet")
        assert pricing is not None
        assert pricing["input"] == 3.00
        assert pricing["output"] == 15.00

    def test_get_model_pricing_case_insensitive(self) -> None:
        """Should match models case-insensitively."""
        pricing = get_model_pricing("GPT-4O")
        assert pricing is not None
        assert pricing["input"] == 2.50

    def test_get_model_pricing_unknown(self) -> None:
        """Should return None for unknown models."""
        pricing = get_model_pricing("unknown-model-xyz")
        assert pricing is None

    def test_calculate_cost_openai(self) -> None:
        """Should calculate cost for OpenAI models."""
        # gpt-4o: $2.50/1M input, $10.00/1M output
        cost = calculate_cost("gpt-4o", input_tokens=1000, output_tokens=500)
        assert cost is not None
        # Expected: (1000/1M * 2.50) + (500/1M * 10.00)
        #         = 0.0025 + 0.005 = 0.0075
        assert cost == pytest.approx(0.0075)

    def test_calculate_cost_anthropic(self) -> None:
        """Should calculate cost for Anthropic models."""
        # claude-3-5-sonnet: $3.00/1M input, $15.00/1M output
        cost = calculate_cost("claude-3-5-sonnet", input_tokens=2000, output_tokens=1000)
        assert cost is not None
        # Expected: (2000/1M * 3.00) + (1000/1M * 15.00)
        #         = 0.006 + 0.015 = 0.021
        assert cost == pytest.approx(0.021)

    def test_calculate_cost_unknown_model(self) -> None:
        """Should return None for unknown models."""
        cost = calculate_cost("unknown-model", input_tokens=1000, output_tokens=500)
        assert cost is None

    def test_calculate_cost_zero_tokens(self) -> None:
        """Should handle zero tokens."""
        cost = calculate_cost("gpt-4o", input_tokens=0, output_tokens=0)
        assert cost == 0.0

    def test_pricing_dictionaries_exist(self) -> None:
        """Should have pricing for common models."""
        assert "gpt-4o" in OPENAI_PRICING
        assert "gpt-4o-mini" in OPENAI_PRICING
        assert "gpt-3.5-turbo" in OPENAI_PRICING
        assert "claude-3-5-sonnet" in ANTHROPIC_PRICING
        assert "claude-3-opus" in ANTHROPIC_PRICING
        assert "claude-3-haiku" in ANTHROPIC_PRICING


# =============================================================================
# DripCallbackHandler Initialization Tests
# =============================================================================


class TestDripCallbackHandlerInit:
    """Tests for DripCallbackHandler initialization."""

    @patch("drip.integrations.langchain.Drip")
    def test_init_with_api_key(self, mock_drip: MagicMock) -> None:
        """Should initialize with API key."""
        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
            workflow="test_workflow",
        )
        mock_drip.assert_called_once_with(api_key="drip_sk_test", base_url=None)
        assert handler._customer_id == "cus_123"
        assert handler._workflow == "test_workflow"

    @patch("drip.integrations.langchain.Drip")
    def test_init_default_workflow(self, mock_drip: MagicMock) -> None:
        """Should use 'langchain' as default workflow."""
        handler = DripCallbackHandler(api_key="drip_sk_test")
        assert handler._workflow == "langchain"

    @patch("drip.integrations.langchain.Drip")
    def test_customer_id_property_error(self, mock_drip: MagicMock) -> None:
        """Should raise error when customer_id not set."""
        handler = DripCallbackHandler(api_key="drip_sk_test")
        with pytest.raises(ValueError, match="customer_id must be set"):
            _ = handler.customer_id

    @patch("drip.integrations.langchain.Drip")
    def test_customer_id_property_setter(self, mock_drip: MagicMock) -> None:
        """Should allow setting customer_id."""
        handler = DripCallbackHandler(api_key="drip_sk_test")
        handler.customer_id = "cus_456"
        assert handler.customer_id == "cus_456"


# =============================================================================
# LLM Callback Tests
# =============================================================================


class TestLLMCallbacks:
    """Tests for LLM callback methods."""

    @patch("drip.integrations.langchain.Drip")
    def test_on_llm_start(self, mock_drip_class: MagicMock) -> None:
        """Should track LLM start."""
        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )
        run_id = uuid.uuid4()

        handler.on_llm_start(
            serialized={"name": "gpt-4o"},
            prompts=["Hello, world!"],
            run_id=run_id,
        )

        assert str(run_id) in handler._llm_calls
        state = handler._llm_calls[str(run_id)]
        assert state.model == "gpt-4o"
        assert state.prompts == ["Hello, world!"]

    @patch("drip.integrations.langchain.Drip")
    def test_on_llm_end(self, mock_drip_class: MagicMock) -> None:
        """Should emit event on LLM end."""
        mock_client = MagicMock()
        mock_drip_class.return_value = mock_client
        mock_client.start_run.return_value = MagicMock(id="run_123")

        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )

        run_id = uuid.uuid4()
        handler.on_llm_start(
            serialized={"name": "gpt-4o"},
            prompts=["Hello"],
            run_id=run_id,
        )

        response = MockLLMResult(
            llm_output={
                "token_usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                }
            }
        )

        handler.on_llm_end(response=response, run_id=run_id)

        # Should have called emit_event
        mock_client.emit_event.assert_called_once()
        call_kwargs = mock_client.emit_event.call_args[1]
        assert call_kwargs["event_type"] == "llm.completion"
        assert call_kwargs["quantity"] == 30
        assert call_kwargs["units"] == "tokens"

    @patch("drip.integrations.langchain.Drip")
    def test_on_llm_error_with_emit(self, mock_drip_class: MagicMock) -> None:
        """Should emit error event when emit_on_error is True."""
        mock_client = MagicMock()
        mock_drip_class.return_value = mock_client
        mock_client.start_run.return_value = MagicMock(id="run_123")

        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
            emit_on_error=True,
        )

        run_id = uuid.uuid4()
        handler.on_llm_start(
            serialized={"name": "gpt-4o"},
            prompts=["Hello"],
            run_id=run_id,
        )

        error = ValueError("Test error")
        handler.on_llm_error(error=error, run_id=run_id)

        mock_client.emit_event.assert_called_once()
        call_kwargs = mock_client.emit_event.call_args[1]
        assert call_kwargs["event_type"] == "llm.error"

    @patch("drip.integrations.langchain.Drip")
    def test_on_llm_error_without_emit(self, mock_drip_class: MagicMock) -> None:
        """Should not emit event when emit_on_error is False."""
        mock_client = MagicMock()
        mock_drip_class.return_value = mock_client

        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
            emit_on_error=False,
        )

        run_id = uuid.uuid4()
        handler.on_llm_start(
            serialized={"name": "gpt-4o"},
            prompts=["Hello"],
            run_id=run_id,
        )

        error = ValueError("Test error")
        handler.on_llm_error(error=error, run_id=run_id)

        mock_client.emit_event.assert_not_called()


# =============================================================================
# Tool Callback Tests
# =============================================================================


class TestToolCallbacks:
    """Tests for tool callback methods."""

    @patch("drip.integrations.langchain.Drip")
    def test_on_tool_start(self, mock_drip_class: MagicMock) -> None:
        """Should track tool start."""
        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )
        run_id = uuid.uuid4()

        handler.on_tool_start(
            serialized={"name": "calculator"},
            input_str="2 + 2",
            run_id=run_id,
        )

        assert str(run_id) in handler._tool_calls
        state = handler._tool_calls[str(run_id)]
        assert state.tool_name == "calculator"
        assert state.input_str == "2 + 2"

    @patch("drip.integrations.langchain.Drip")
    def test_on_tool_end(self, mock_drip_class: MagicMock) -> None:
        """Should emit event on tool end."""
        mock_client = MagicMock()
        mock_drip_class.return_value = mock_client
        mock_client.start_run.return_value = MagicMock(id="run_123")

        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )

        run_id = uuid.uuid4()
        handler.on_tool_start(
            serialized={"name": "calculator"},
            input_str="2 + 2",
            run_id=run_id,
        )

        handler.on_tool_end(output="4", run_id=run_id)

        mock_client.emit_event.assert_called_once()
        call_kwargs = mock_client.emit_event.call_args[1]
        assert call_kwargs["event_type"] == "tool.call"
        assert call_kwargs["quantity"] == 1
        assert call_kwargs["units"] == "calls"

    @patch("drip.integrations.langchain.Drip")
    def test_on_tool_error(self, mock_drip_class: MagicMock) -> None:
        """Should emit error event on tool error."""
        mock_client = MagicMock()
        mock_drip_class.return_value = mock_client
        mock_client.start_run.return_value = MagicMock(id="run_123")

        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )

        run_id = uuid.uuid4()
        handler.on_tool_start(
            serialized={"name": "calculator"},
            input_str="invalid",
            run_id=run_id,
        )

        handler.on_tool_error(error=ValueError("Invalid input"), run_id=run_id)

        mock_client.emit_event.assert_called_once()
        call_kwargs = mock_client.emit_event.call_args[1]
        assert call_kwargs["event_type"] == "tool.error"


# =============================================================================
# Chain Callback Tests
# =============================================================================


class TestChainCallbacks:
    """Tests for chain callback methods."""

    @patch("drip.integrations.langchain.Drip")
    def test_on_chain_start(self, mock_drip_class: MagicMock) -> None:
        """Should track chain start."""
        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )
        run_id = uuid.uuid4()

        handler.on_chain_start(
            serialized={"name": "LLMChain"},
            inputs={"input": "test"},
            run_id=run_id,
        )

        assert str(run_id) in handler._chain_calls
        state = handler._chain_calls[str(run_id)]
        assert state.chain_type == "LLMChain"
        assert state.inputs == {"input": "test"}

    @patch("drip.integrations.langchain.Drip")
    def test_on_chain_end(self, mock_drip_class: MagicMock) -> None:
        """Should emit event on chain end."""
        mock_client = MagicMock()
        mock_drip_class.return_value = mock_client
        mock_client.start_run.return_value = MagicMock(id="run_123")

        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )

        run_id = uuid.uuid4()
        handler.on_chain_start(
            serialized={"name": "LLMChain"},
            inputs={"input": "test"},
            run_id=run_id,
        )

        handler.on_chain_end(outputs={"output": "result"}, run_id=run_id)

        mock_client.emit_event.assert_called_once()
        call_kwargs = mock_client.emit_event.call_args[1]
        assert call_kwargs["event_type"] == "chain.execution"


# =============================================================================
# Agent Callback Tests
# =============================================================================


class TestAgentCallbacks:
    """Tests for agent callback methods."""

    @patch("drip.integrations.langchain.Drip")
    def test_on_agent_action(self, mock_drip_class: MagicMock) -> None:
        """Should track agent action."""
        mock_client = MagicMock()
        mock_drip_class.return_value = mock_client
        mock_client.start_run.return_value = MagicMock(id="run_123")

        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )

        run_id = uuid.uuid4()
        action = MockAgentAction(
            tool="search",
            tool_input="query",
            log="Searching...",
        )

        handler.on_agent_action(action=action, run_id=run_id)

        assert str(run_id) in handler._agent_calls
        state = handler._agent_calls[str(run_id)]
        assert len(state.actions) == 1
        assert state.actions[0]["tool"] == "search"

        # Should emit action event
        mock_client.emit_event.assert_called_once()
        call_kwargs = mock_client.emit_event.call_args[1]
        assert call_kwargs["event_type"] == "agent.action"

    @patch("drip.integrations.langchain.Drip")
    def test_on_agent_finish(self, mock_drip_class: MagicMock) -> None:
        """Should emit finish event."""
        mock_client = MagicMock()
        mock_drip_class.return_value = mock_client
        mock_client.start_run.return_value = MagicMock(id="run_123")

        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )

        run_id = uuid.uuid4()
        action = MockAgentAction(tool="search", tool_input="query")
        handler.on_agent_action(action=action, run_id=run_id)

        finish = MockAgentFinish(return_values={"output": "final result"})
        handler.on_agent_finish(finish=finish, run_id=run_id)

        # Should have emitted both action and finish events
        assert mock_client.emit_event.call_count == 2


# =============================================================================
# Retriever Callback Tests
# =============================================================================


class TestRetrieverCallbacks:
    """Tests for retriever callback methods."""

    @patch("drip.integrations.langchain.Drip")
    def test_on_retriever_start(self, mock_drip_class: MagicMock) -> None:
        """Should track retriever start."""
        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )
        run_id = uuid.uuid4()

        handler.on_retriever_start(
            serialized={"name": "VectorStore"},
            query="search query",
            run_id=run_id,
        )

        assert str(run_id) in handler._tool_calls
        state = handler._tool_calls[str(run_id)]
        assert state.tool_name == "retriever:VectorStore"

    @patch("drip.integrations.langchain.Drip")
    def test_on_retriever_end(self, mock_drip_class: MagicMock) -> None:
        """Should emit event with document count."""
        mock_client = MagicMock()
        mock_drip_class.return_value = mock_client
        mock_client.start_run.return_value = MagicMock(id="run_123")

        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )

        run_id = uuid.uuid4()
        handler.on_retriever_start(
            serialized={"name": "VectorStore"},
            query="search query",
            run_id=run_id,
        )

        documents = [
            MockDocument(page_content="doc1"),
            MockDocument(page_content="doc2"),
            MockDocument(page_content="doc3"),
        ]

        handler.on_retriever_end(documents=documents, run_id=run_id)

        mock_client.emit_event.assert_called_once()
        call_kwargs = mock_client.emit_event.call_args[1]
        assert call_kwargs["event_type"] == "retriever.query"
        assert call_kwargs["quantity"] == 3
        assert call_kwargs["units"] == "documents"


# =============================================================================
# Run Management Tests
# =============================================================================


class TestRunManagement:
    """Tests for run management."""

    @patch("drip.integrations.langchain.Drip")
    def test_start_run_manual(self, mock_drip_class: MagicMock) -> None:
        """Should manually start a run."""
        mock_client = MagicMock()
        mock_drip_class.return_value = mock_client
        mock_client.record_run.return_value = MagicMock(run=MagicMock(id="run_123"))

        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )

        run_id = handler.start_run(
            external_run_id="ext_123",
            correlation_id="corr_456",
            metadata={"key": "value"},
        )

        assert run_id == "run_123"
        assert handler.run_id == "run_123"
        mock_client.record_run.assert_called_once()

    @patch("drip.integrations.langchain.Drip")
    def test_end_run_manual(self, mock_drip_class: MagicMock) -> None:
        """Should manually end a run."""
        mock_client = MagicMock()
        mock_drip_class.return_value = mock_client
        mock_client.record_run.return_value = MagicMock(run=MagicMock(id="run_123"))

        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )

        handler.start_run()
        handler.end_run(status="COMPLETED")

        mock_client.end_run.assert_called_once_with(
            run_id="run_123",
            status="COMPLETED",
            error_message=None,
        )
        assert handler.run_id is None

    @patch("drip.integrations.langchain.Drip")
    def test_auto_create_run(self, mock_drip_class: MagicMock) -> None:
        """Should auto-create run when needed."""
        mock_client = MagicMock()
        mock_drip_class.return_value = mock_client
        mock_client.start_run.return_value = MagicMock(id="auto_run_123")

        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
            auto_create_run=True,
        )

        run_id = uuid.uuid4()
        handler.on_llm_start(
            serialized={"name": "gpt-4o"},
            prompts=["test"],
            run_id=run_id,
        )

        response = MockLLMResult(llm_output={"token_usage": {"total_tokens": 10}})
        handler.on_llm_end(response=response, run_id=run_id)

        # Should have auto-created a run
        mock_client.start_run.assert_called_once()

    @patch("drip.integrations.langchain.Drip")
    def test_no_auto_create_run(self, mock_drip_class: MagicMock) -> None:
        """Should raise error when no run and auto_create disabled."""
        mock_client = MagicMock()
        mock_drip_class.return_value = mock_client

        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
            auto_create_run=False,
        )

        run_id = uuid.uuid4()
        handler.on_llm_start(
            serialized={"name": "gpt-4o"},
            prompts=["test"],
            run_id=run_id,
        )

        response = MockLLMResult(llm_output={"token_usage": {"total_tokens": 10}})

        with pytest.raises(ValueError, match="No active run"):
            handler.on_llm_end(response=response, run_id=run_id)


# =============================================================================
# Async Callback Handler Tests
# =============================================================================


class TestAsyncDripCallbackHandler:
    """Tests for AsyncDripCallbackHandler."""

    @patch("drip.integrations.langchain.AsyncDrip")
    def test_async_init(self, mock_async_drip: MagicMock) -> None:
        """Should initialize async handler."""
        handler = AsyncDripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
            workflow="async_workflow",
        )
        mock_async_drip.assert_called_once_with(api_key="drip_sk_test", base_url=None)
        assert handler._customer_id == "cus_123"
        assert handler._workflow == "async_workflow"

    @pytest.mark.asyncio
    @patch("drip.integrations.langchain.AsyncDrip")
    async def test_async_on_llm_start(self, mock_async_drip: MagicMock) -> None:
        """Should track LLM start asynchronously."""
        handler = AsyncDripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )
        run_id = uuid.uuid4()

        await handler.on_llm_start(
            serialized={"name": "gpt-4o"},
            prompts=["Hello"],
            run_id=run_id,
        )

        assert str(run_id) in handler._llm_calls
        state = handler._llm_calls[str(run_id)]
        assert state.model == "gpt-4o"

    @pytest.mark.asyncio
    @patch("drip.integrations.langchain.AsyncDrip")
    async def test_async_on_llm_end(self, mock_async_drip: MagicMock) -> None:
        """Should emit event asynchronously."""
        mock_client = MagicMock()
        mock_async_drip.return_value = mock_client
        mock_client.start_run = AsyncMock(return_value=MagicMock(id="run_123"))
        mock_client.emit_event = AsyncMock()

        handler = AsyncDripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )

        run_id = uuid.uuid4()
        await handler.on_llm_start(
            serialized={"name": "gpt-4o"},
            prompts=["Hello"],
            run_id=run_id,
        )

        response = MockLLMResult(
            llm_output={"token_usage": {"total_tokens": 100}}
        )
        await handler.on_llm_end(response=response, run_id=run_id)

        mock_client.emit_event.assert_called_once()

    @pytest.mark.asyncio
    @patch("drip.integrations.langchain.AsyncDrip")
    async def test_async_start_run(self, mock_async_drip: MagicMock) -> None:
        """Should start run asynchronously."""
        mock_client = MagicMock()
        mock_async_drip.return_value = mock_client
        mock_client.record_run = AsyncMock(
            return_value=MagicMock(run=MagicMock(id="async_run_123"))
        )

        handler = AsyncDripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )

        run_id = await handler.start_run()
        assert run_id == "async_run_123"

    @pytest.mark.asyncio
    @patch("drip.integrations.langchain.AsyncDrip")
    async def test_async_end_run(self, mock_async_drip: MagicMock) -> None:
        """Should end run asynchronously."""
        mock_client = MagicMock()
        mock_async_drip.return_value = mock_client
        mock_client.record_run = AsyncMock(
            return_value=MagicMock(run=MagicMock(id="async_run_123"))
        )
        mock_client.end_run = AsyncMock()

        handler = AsyncDripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )

        await handler.start_run()
        await handler.end_run(status="FAILED", error_message="Test error")

        mock_client.end_run.assert_called_once_with(
            run_id="async_run_123",
            status="FAILED",
            error_message="Test error",
        )

    @pytest.mark.asyncio
    @patch("drip.integrations.langchain.AsyncDrip")
    async def test_async_on_tool_start_end(self, mock_async_drip: MagicMock) -> None:
        """Should track tool calls asynchronously."""
        mock_client = MagicMock()
        mock_async_drip.return_value = mock_client
        mock_client.start_run = AsyncMock(return_value=MagicMock(id="run_123"))
        mock_client.emit_event = AsyncMock()

        handler = AsyncDripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )

        run_id = uuid.uuid4()
        await handler.on_tool_start(
            serialized={"name": "calculator"},
            input_str="2+2",
            run_id=run_id,
        )
        await handler.on_tool_end(output="4", run_id=run_id)

        mock_client.emit_event.assert_called_once()
        call_kwargs = mock_client.emit_event.call_args[1]
        assert call_kwargs["event_type"] == "tool.call"


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @patch("drip.integrations.langchain.Drip")
    def test_on_llm_end_without_start(self, mock_drip_class: MagicMock) -> None:
        """Should handle LLM end without start gracefully."""
        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )

        response = MockLLMResult(llm_output=None)
        # Should not raise
        handler.on_llm_end(response=response, run_id=uuid.uuid4())

    @patch("drip.integrations.langchain.Drip")
    def test_on_tool_end_without_start(self, mock_drip_class: MagicMock) -> None:
        """Should handle tool end without start gracefully."""
        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )
        # Should not raise
        handler.on_tool_end(output="result", run_id=uuid.uuid4())

    @patch("drip.integrations.langchain.Drip")
    def test_on_chain_end_without_start(self, mock_drip_class: MagicMock) -> None:
        """Should handle chain end without start gracefully."""
        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )
        # Should not raise
        handler.on_chain_end(outputs={}, run_id=uuid.uuid4())

    @patch("drip.integrations.langchain.Drip")
    def test_long_input_truncation(self, mock_drip_class: MagicMock) -> None:
        """Should truncate long inputs."""
        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )
        run_id = uuid.uuid4()
        long_input = "x" * 2000

        handler.on_tool_start(
            serialized={"name": "tool"},
            input_str=long_input,
            run_id=run_id,
        )

        state = handler._tool_calls[str(run_id)]
        assert len(state.input_str) == 1000

    @patch("drip.integrations.langchain.Drip")
    def test_metadata_merging(self, mock_drip_class: MagicMock) -> None:
        """Should merge base metadata with event metadata."""
        mock_client = MagicMock()
        mock_drip_class.return_value = mock_client
        mock_client.start_run.return_value = MagicMock(id="run_123")

        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
            metadata={"base_key": "base_value"},
        )

        run_id = uuid.uuid4()
        handler.on_tool_start(
            serialized={"name": "tool"},
            input_str="test",
            run_id=run_id,
        )
        handler.on_tool_end(output="result", run_id=run_id)

        call_kwargs = mock_client.emit_event.call_args[1]
        metadata = call_kwargs["metadata"]
        assert "base_key" in metadata
        assert metadata["base_key"] == "base_value"
        assert "tool_name" in metadata

    @patch("drip.integrations.langchain.Drip")
    def test_model_name_fallback(self, mock_drip_class: MagicMock) -> None:
        """Should handle missing model name."""
        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )
        run_id = uuid.uuid4()

        # No name in serialized
        handler.on_llm_start(
            serialized={"id": ["module", "MyLLM"]},
            prompts=["test"],
            run_id=run_id,
        )

        state = handler._llm_calls[str(run_id)]
        assert state.model == "MyLLM"

    @patch("drip.integrations.langchain.Drip")
    def test_llm_output_nested_token_usage(self, mock_drip_class: MagicMock) -> None:
        """Should handle nested token_usage in llm_output."""
        mock_client = MagicMock()
        mock_drip_class.return_value = mock_client
        mock_client.start_run.return_value = MagicMock(id="run_123")

        handler = DripCallbackHandler(
            api_key="drip_sk_test",
            customer_id="cus_123",
        )

        run_id = uuid.uuid4()
        handler.on_llm_start(
            serialized={"name": "gpt-4o"},
            prompts=["test"],
            run_id=run_id,
        )

        # Token usage directly at top level
        response = MockLLMResult(
            llm_output={
                "prompt_tokens": 50,
                "completion_tokens": 100,
                "total_tokens": 150,
            }
        )
        handler.on_llm_end(response=response, run_id=run_id)

        call_kwargs = mock_client.emit_event.call_args[1]
        assert call_kwargs["quantity"] == 150
