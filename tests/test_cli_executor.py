"""Tests for statmon_mcp.cli_executor — subprocess execution."""

import os
import pytest
from statmon_mcp.cli_executor import run_cli

MOCK_CLI = os.path.join(os.path.dirname(__file__), "..", "mock-cli", "statmon")


@pytest.fixture(autouse=True)
def set_node_name(monkeypatch):
    monkeypatch.setenv("NODE_NAME", "test-node")


@pytest.mark.asyncio
async def test_basic_command():
    result = await run_cli(MOCK_CLI, "querystore.top-clients max-results 3", timeout=10)
    assert result["status"] == "success"
    assert result["exit_code"] == 0
    assert "result" in result
    assert len(result["result"]["results"]) == 3
    assert result["execution_time_ms"] >= 0


@pytest.mark.asyncio
async def test_count_command():
    result = await run_cli(MOCK_CLI, "querystore.count duration 300", timeout=10)
    assert result["status"] == "success"
    assert "count" in result["result"]


@pytest.mark.asyncio
async def test_group_count_command():
    result = await run_cli(
        MOCK_CLI,
        "querystore.group-count duration 300 group-by 'result-code' order 'descending'",
        timeout=10,
    )
    assert result["status"] == "success"
    assert "groups" in result["result"]


@pytest.mark.asyncio
async def test_filter_with_s_expression():
    result = await run_cli(
        MOCK_CLI,
        'querystore.top-clients duration 3600 filter "(query-type (true (A AAAA)))"',
        timeout=10,
    )
    assert result["status"] == "success"
    assert result["exit_code"] == 0


@pytest.mark.asyncio
async def test_complex_s_expression_filter():
    result = await run_cli(
        MOCK_CLI,
        'querystore.count duration 3600 filter "(and ( (result-code (true (nxdomain))) (client-network (true ((netblock 10.0.0.0/24)))) ))"',
        timeout=10,
    )
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_unknown_command():
    result = await run_cli(MOCK_CLI, "querystore.nonexistent", timeout=10)
    assert result["status"] == "error"
    assert result["exit_code"] != 0


@pytest.mark.asyncio
async def test_binary_not_found():
    result = await run_cli("/nonexistent/binary", "querystore.count", timeout=10)
    assert result["status"] == "error"
    assert "not found" in result["error"].lower()


@pytest.mark.asyncio
async def test_timeout():
    result = await run_cli("sleep", "10", timeout=1)
    assert result["status"] == "error"
    assert "timed out" in result["error"].lower()
