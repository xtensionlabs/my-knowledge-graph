"""Dashboard API endpoint tests — read-only endpoints for M5b consumption."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session

from synapse.graph import operations
from synapse.graph.db import get_engine
from synapse.graph.models import ApiUsage, CaptureLog, NodeType


@pytest.fixture(autouse=True)
def _stub_embeddings():  # type: ignore[no-untyped-def]
    with patch.object(operations, "_embed_and_upsert", lambda node: None):
        yield


@pytest.fixture
async def client(fastapi_app):  # type: ignore[no-untyped-def]
    """AsyncClient with the dashboard API key header pre-set (conftest provides the key)."""
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"x-synapse-api-key": "test-browser-key"},
    ) as c:
        yield c


@pytest.fixture
async def unauth_client(fastapi_app):  # type: ignore[no-untyped-def]
    """AsyncClient WITHOUT the API key — for testing auth rejection."""
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_dashboard_rejects_missing_api_key(unauth_client) -> None:  # type: ignore[no-untyped-def]
    resp = await unauth_client.get("/dashboard/overview")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_rejects_wrong_api_key(unauth_client) -> None:  # type: ignore[no-untyped-def]
    resp = await unauth_client.get(
        "/dashboard/overview",
        headers={"x-synapse-api-key": "wrong-key"},
    )
    assert resp.status_code == 401


# ── /dashboard/overview ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_overview_returns_shape(client) -> None:  # type: ignore[no-untyped-def]
    resp = await client.get("/dashboard/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert "generated_at" in body
    assert "graph" in body and isinstance(body["graph"]["nodes"], int)
    assert "capture" in body and "total" in body["capture"]
    assert "recent_agent_runs" in body


@pytest.mark.asyncio
async def test_overview_counts_recent_captures(client) -> None:  # type: ignore[no-untyped-def]
    with Session(get_engine()) as s:
        for i in range(3):
            s.add(CaptureLog(
                id=str(uuid.uuid4()), source="manual",
                inbox_filename=f"x-{i}.md",
                created_at=datetime.now(tz=timezone.utc),
                size_bytes=100,
            ))
        s.commit()
    resp = await client.get("/dashboard/overview")
    assert resp.json()["capture"]["last_24h"] == 3


# ── /dashboard/graph ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_graph_returns_nodes_and_edges(client) -> None:  # type: ignore[no-untyped-def]
    a = operations.create_node(type=NodeType.CONCEPT, title="X")
    b = operations.create_node(type=NodeType.CONCEPT, title="Y")
    operations.create_edge(source_node_id=a.id, target_node_id=b.id, relation_type="applies_to")
    resp = await client.get("/dashboard/graph")
    body = resp.json()
    assert len(body["nodes"]) >= 2
    assert len(body["edges"]) >= 1
    node = next(n for n in body["nodes"] if n["title"] == "X")
    assert "centrality" in node and "freshness" in node


@pytest.mark.asyncio
async def test_graph_filters_by_type(client) -> None:  # type: ignore[no-untyped-def]
    operations.create_node(type=NodeType.CONCEPT, title="concept_only")
    operations.create_node(type=NodeType.BUILD, title="build_only")
    resp = await client.get("/dashboard/graph?types=CONCEPT")
    body = resp.json()
    titles = [n["title"] for n in body["nodes"]]
    assert "concept_only" in titles
    assert "build_only" not in titles


# ── /dashboard/communities ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_communities_endpoint_returns_shape(client) -> None:  # type: ignore[no-untyped-def]
    # Build a triangle so at least one community emerges.
    titles = ["c1", "c2", "c3"]
    nodes = [operations.create_node(type=NodeType.CONCEPT, title=t) for t in titles]
    for i in range(3):
        for j in range(i + 1, 3):
            operations.create_edge(
                source_node_id=nodes[i].id, target_node_id=nodes[j].id,
                relation_type="applies_to", weight=3.0,
            )
    resp = await client.get("/dashboard/communities")
    body = resp.json()
    assert "communities" in body
    assert len(body["communities"]) >= 1
    c0 = body["communities"][0]
    assert "hubs" in c0
    assert "size" in c0


# ── /dashboard/agents ─────────────────────────────────────────────────────────


# ── /dashboard/inbox ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inbox_endpoint_lists_pending_items(client) -> None:  # type: ignore[no-untyped-def]
    from synapse.capture.inbox import write_to_inbox

    write_to_inbox(source="manual", content="first capture content")
    write_to_inbox(source="browser", content="second capture content")

    resp = await client.get("/dashboard/inbox")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    sources = {it["source"] for it in body["items"]}
    assert sources == {"manual", "browser"}
    for item in body["items"]:
        assert item["filename"].endswith(".md")
        assert item["size_bytes"] > 0


@pytest.mark.asyncio
async def test_inbox_endpoint_returns_empty_when_inbox_empty(client) -> None:  # type: ignore[no-untyped-def]
    resp = await client.get("/dashboard/inbox")
    body = resp.json()
    assert body == {"total": 0, "items": []}


# ── POST /dashboard/librarian/run ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_librarian_returns_summary_on_empty_inbox(client) -> None:  # type: ignore[no-untyped-def]
    """With an empty inbox the librarian returns ok with 'nothing to do' — no LLM call."""
    resp = await client.post("/dashboard/librarian/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "nothing to do" in body["summary"].lower() or "empty" in body["summary"].lower()


@pytest.mark.asyncio
async def test_trigger_librarian_requires_api_key(unauth_client) -> None:  # type: ignore[no-untyped-def]
    resp = await unauth_client.post("/dashboard/librarian/run")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_agents_endpoint_returns_per_agent_rollup(client) -> None:  # type: ignore[no-untyped-def]
    with Session(get_engine()) as s:
        s.add(ApiUsage(
            id=str(uuid.uuid4()), agent="librarian", model="claude-sonnet-4-5",
            input_tokens=100, output_tokens=50, cost_usd=0.001, latency_ms=300,
            succeeded=True, created_at=datetime.now(tz=timezone.utc),
        ))
        s.add(ApiUsage(
            id=str(uuid.uuid4()), agent="critic", model="claude-opus-4-7",
            input_tokens=200, output_tokens=100, cost_usd=0.01, latency_ms=2000,
            succeeded=True, created_at=datetime.now(tz=timezone.utc),
        ))
        s.commit()

    resp = await client.get("/dashboard/agents")
    body = resp.json()
    agents = {a["agent"]: a for a in body["agents"]}
    assert "librarian" in agents and "critic" in agents
    assert agents["librarian"]["runs_7d"] == 1
    assert agents["critic"]["latest_model"] == "claude-opus-4-7"
