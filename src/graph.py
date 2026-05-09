"""LangGraph wiring.

Topology:

    START
      │
      ▼
    [rag] ─── load brand manual once
      │
      ▼
    [planner] ◀──── retry up to MAX_REVISIONS times
      │              ▲
      ▼              │ "replan"
    [guardrail] ─────┘
      │  "continue"
      ▼
    [tool_use] ─── Seedream → Seedance → Seed Speech
      │
      ▼
    [eval] ─── CTR estimate + final brand-safety self-check
      │
      ▼
    END

The conditional edge after `guardrail` is the only loop in the graph; every
other edge is linear. Keeping the topology shallow keeps debugging easy.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes.eval import eval_node
from .nodes.guardrail import guardrail_node, guardrail_router
from .nodes.planner import planner_node
from .nodes.rag import rag_node
from .nodes.tool_use import tool_use_node
from .state import AgentState


def build_graph():
    g = StateGraph(AgentState)

    g.add_node("rag", rag_node)
    g.add_node("planner", planner_node)
    g.add_node("guardrail", guardrail_node)
    g.add_node("tool_use", tool_use_node)
    g.add_node("eval", eval_node)

    g.add_edge(START, "rag")
    g.add_edge("rag", "planner")
    g.add_edge("planner", "guardrail")
    g.add_conditional_edges(
        "guardrail",
        guardrail_router,
        {"replan": "planner", "continue": "tool_use"},
    )
    g.add_edge("tool_use", "eval")
    g.add_edge("eval", END)

    return g.compile()
