"""Tests for statmon_chat.app — FastAPI routes."""

import json
import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from statmon_chat import app as app_module


def _parse_sse_events(response_text: str) -> list[dict]:
    """Parse SSE events from a streaming response."""
    events = []
    for chunk in response_text.split("\n\n"):
        chunk = chunk.strip()
        if chunk.startswith("data: "):
            events.append(json.loads(chunk[6:]))
    return events


def _build_test_app():
    """Build a copy of the app with a no-op lifespan for testing."""

    @asynccontextmanager
    async def noop_lifespan(app):
        yield

    test_app = FastAPI(title="test", lifespan=noop_lifespan)
    # Copy routes from the real app
    for route in app_module.app.routes:
        test_app.routes.append(route)
    return test_app


@pytest.fixture
def client():
    """Create a test client with mocked MCP pool and Anthropic client."""
    orig_pool = app_module._mcp_pool
    orig_chat = app_module._anthropic_chat
    orig_prompt_path = app_module._prompt_path
    orig_security_config = app_module._security_tools_config
    orig_sessions = app_module._sessions

    mock_pool = MagicMock()
    mock_pool.nodes = {"dns-node-a": MagicMock()}
    mock_pool.node_configs = {
        "dns-node-a": {
            "name": "dns-node-a",
            "mcp_url": "http://localhost:8100/mcp",
        }
    }
    mock_pool.get_node_list.return_value = [
        {"name": "dns-node-a", "mcp_url": "http://localhost:8100/mcp",
         "tools": ["dns_node_a__statmon"], "status": "connected"}
    ]
    mock_pool.build_anthropic_tools.return_value = [
        {"name": "dns_node_a__statmon", "description": "test", "input_schema": {}}
    ]
    mock_pool.check_health = AsyncMock(return_value={"dns-node-a": True})

    mock_chat = MagicMock()
    mock_chat.run_turn = AsyncMock(return_value="Mock response")

    app_module._mcp_pool = mock_pool
    app_module._anthropic_chat = mock_chat
    app_module._prompt_path = None
    app_module._security_tools_config = {}
    app_module._sessions = {}

    test_app = _build_test_app()
    with patch("statmon_chat.app.build_system_prompt", return_value="test prompt"):
        with TestClient(test_app) as tc:
            yield tc, mock_chat

    app_module._mcp_pool = orig_pool
    app_module._anthropic_chat = orig_chat
    app_module._prompt_path = orig_prompt_path
    app_module._security_tools_config = orig_security_config
    app_module._sessions = orig_sessions


class TestHealthEndpoint:
    def test_health(self, client):
        tc, _ = client
        response = tc.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["nodes_connected"] == 1
        assert data["nodes_configured"] == 1


class TestNodesEndpoint:
    def test_nodes(self, client):
        tc, _ = client
        response = tc.get("/api/nodes")
        assert response.status_code == 200
        data = response.json()
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["name"] == "dns-node-a"
        assert data["configured"] == 1
        assert data["connected"] == 1


class TestChatEndpoint:
    def test_chat_returns_response(self, client):
        tc, mock_chat = client
        response = tc.post(
            "/api/chat", json={"message": "Hello"}
        )
        assert response.status_code == 200
        events = _parse_sse_events(response.text)
        result = [e for e in events if e["type"] == "result"][0]
        assert result["response"] == "Mock response"
        assert "session_id" in result

    def test_chat_empty_message(self, client):
        tc, _ = client
        response = tc.post("/api/chat", json={"message": ""})
        assert response.status_code == 400

    def test_chat_session_persistence(self, client):
        tc, mock_chat = client
        r1 = tc.post("/api/chat", json={"message": "Hi"})
        events1 = _parse_sse_events(r1.text)
        session_id = [e for e in events1 if e["type"] == "result"][0]["session_id"]

        r2 = tc.post(
            "/api/chat",
            json={"message": "Follow up", "session_id": session_id},
        )
        events2 = _parse_sse_events(r2.text)
        result2 = [e for e in events2 if e["type"] == "result"][0]
        assert result2["session_id"] == session_id
        assert mock_chat.run_turn.call_count == 2
