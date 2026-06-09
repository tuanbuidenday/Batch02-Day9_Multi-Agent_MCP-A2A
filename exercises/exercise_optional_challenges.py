"""Optional challenges: financial agent, memory, custom API tool, and retry.

This file is intentionally self-contained so the mandatory exercises remain
easy to read. It can run even when OpenRouter credits are exhausted because
LLM calls use retry plus deterministic fallbacks.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Annotated, TypedDict
from urllib.parse import urlencode

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from common.llm import get_llm


MEMORY_PATH = Path(__file__).with_name(".conversation_memory.json")


def _last_wins(left: str | None, right: str | None) -> str:
    return right if right is not None else (left or "")


class State(TypedDict):
    question: str
    memory_summary: str
    law_analysis: Annotated[str, _last_wins]
    financial_analysis: Annotated[str, _last_wins]
    api_research: Annotated[str, _last_wins]
    final_response: str


class ConversationMemory:
    """Tiny JSON-backed conversation memory for the exercise."""

    def __init__(self, path: Path = MEMORY_PATH, max_items: int = 6) -> None:
        self.path = path
        self.max_items = max_items

    def load(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def summarize(self) -> str:
        items = self.load()[-self.max_items :]
        if not items:
            return "No previous conversation."
        lines = []
        for idx, item in enumerate(items, start=1):
            question = item.get("question", "")
            answer = item.get("answer", "")
            lines.append(f"{idx}. Q: {question}\n   A: {answer[:240]}")
        return "\n".join(lines)

    def append(self, question: str, answer: str) -> None:
        items = self.load()
        items.append({"question": question, "answer": answer})
        items = items[-self.max_items :]
        self.path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def invoke_llm_with_retry(prompt: str, fallback: str, attempts: int = 2) -> str:
    """Call the LLM with retry; return fallback if the provider fails."""
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            llm = get_llm()
            response = llm.invoke([HumanMessage(content=prompt)])
            return str(response.content)
        except Exception as exc:  # noqa: BLE001 - exercise demonstrates robust fallback
            last_error = str(exc)
            if attempt < attempts:
                time.sleep(0.5 * attempt)
    return f"{fallback}\n\n[LLM unavailable after {attempts} attempts: {last_error}]"


def estimate_financial_exposure(question: str) -> str:
    """Deterministic financial estimate used by financial_agent."""
    q = question.lower()
    exposure: list[tuple[str, str]] = []

    if any(keyword in q for keyword in ["contract", "hợp đồng", "breach"]):
        exposure.append(
            (
                "Contract damages",
                "Direct damages, foreseeable consequential damages, attorney fees if the contract allows them.",
            )
        )
    if any(keyword in q for keyword in ["tax", "thuế", "irs"]):
        exposure.append(
            (
                "Tax exposure",
                "Back taxes, interest, civil fraud penalties up to 75% of underpayment, and possible criminal fines.",
            )
        )
    if any(keyword in q for keyword in ["data", "privacy", "gdpr", "dữ liệu"]):
        exposure.append(
            (
                "Privacy exposure",
                "Regulatory fines, breach notification costs, forensic investigation, monitoring, and class actions.",
            )
        )
    if not exposure:
        exposure.append(("General exposure", "Legal fees, settlement costs, operational disruption, and reputation loss."))

    lines = ["Financial exposure estimate:"]
    lines.extend(f"- {label}: {detail}" for label, detail in exposure)
    lines.append("- Mitigation budget: preserve evidence, hire counsel, notify regulators if required, and fund remediation.")
    return "\n".join(lines)


@tool
def lookup_public_legal_source(query: str) -> str:
    """Search a public legal API and return a compact result.

    Uses CourtListener's public search endpoint. If the API/network is not
    available, the tool returns a useful fallback instead of failing the graph.
    """
    params = urlencode({"q": query, "type": "o"})
    url = f"https://www.courtlistener.com/api/rest/v4/search/?{params}"
    try:
        with httpx.Client(timeout=8.0) as client:
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001 - exercise demonstrates graceful tool failure
        return (
            "Public legal API lookup unavailable. "
            f"Fallback research note for '{query}': check statutes, regulator guidance, "
            f"and leading cases manually. Error: {exc}"
        )

    results = payload.get("results") or []
    if not results:
        return f"No public API search results found for '{query}'."

    first = results[0]
    name = first.get("caseName") or first.get("caseNameFull") or "Untitled result"
    snippet = (first.get("snippet") or "").replace("\n", " ").strip()
    absolute_url = first.get("absolute_url") or ""
    link = f"https://www.courtlistener.com{absolute_url}" if absolute_url else url
    return f"Public legal source: {name}\nURL: {link}\nSnippet: {snippet[:500]}"


def memory_loader(state: State) -> dict:
    memory = ConversationMemory()
    return {"memory_summary": memory.summarize()}


def law_agent(state: State) -> dict:
    fallback = (
        "General legal analysis: identify contractual duties, potential statutory violations, "
        "available remedies, regulator exposure, and evidence preservation needs."
    )
    prompt = f"""You are a legal analyst.

Conversation memory:
{state.get('memory_summary', 'No previous conversation.')}

Question:
{state['question']}

Give a concise legal issue summary."""
    return {"law_analysis": invoke_llm_with_retry(prompt, fallback)}


def route_optional_agents(state: State) -> list[Send]:
    q = state["question"].lower()
    tasks: list[Send] = []
    if any(keyword in q for keyword in ["damage", "thiệt hại", "fine", "penalty", "revenue", "tax", "data", "breach"]):
        tasks.append(Send("financial_agent", state))
    if any(keyword in q for keyword in ["case", "án lệ", "statute", "law", "contract", "privacy", "gdpr"]):
        tasks.append(Send("custom_api_agent", state))
    return tasks if tasks else [Send("aggregate_results", state)]


def financial_agent(state: State) -> dict:
    """Challenge 1: specialist agent for financial harm and remediation costs."""
    local_estimate = estimate_financial_exposure(state["question"])
    prompt = f"""You are a financial damages analyst.

Question: {state['question']}
Legal analysis: {state.get('law_analysis', '')}

Refine this estimate into practical financial exposure:
{local_estimate}"""
    result = invoke_llm_with_retry(prompt, local_estimate)
    return {"financial_analysis": result}


def custom_api_agent(state: State) -> dict:
    """Challenge 3: use a custom tool that calls a public legal API."""
    query = state["question"]
    result = lookup_public_legal_source.invoke({"query": query})
    return {"api_research": result}


def aggregate_results(state: State) -> dict:
    sections = [
        f"Memory:\n{state.get('memory_summary', '')}",
        f"Legal Analysis:\n{state.get('law_analysis', '')}",
    ]
    if state.get("financial_analysis"):
        sections.append(f"Financial Analysis:\n{state['financial_analysis']}")
    if state.get("api_research"):
        sections.append(f"Public API Research:\n{state['api_research']}")

    combined = "\n\n---\n\n".join(sections)
    fallback = combined
    prompt = f"""Synthesize the following optional challenge outputs into a short final answer:

{combined}"""
    answer = invoke_llm_with_retry(prompt, fallback)
    ConversationMemory().append(state["question"], answer)
    return {"final_response": answer}


def build_graph():
    graph = StateGraph(State)

    graph.add_node("memory_loader", memory_loader)
    graph.add_node("law_agent", law_agent)
    graph.add_node("financial_agent", financial_agent)
    graph.add_node("custom_api_agent", custom_api_agent)
    graph.add_node("aggregate_results", aggregate_results)

    graph.add_edge(START, "memory_loader")
    graph.add_edge("memory_loader", "law_agent")
    graph.add_conditional_edges(
        "law_agent",
        route_optional_agents,
        ["financial_agent", "custom_api_agent", "aggregate_results"],
    )
    graph.add_edge("financial_agent", "aggregate_results")
    graph.add_edge("custom_api_agent", "aggregate_results")
    graph.add_edge("aggregate_results", END)

    return graph.compile()


async def main() -> None:
    load_dotenv()

    question = (
        "A vendor breached a contract, exposed customer data, and caused tax penalties. "
        "What financial exposure and public legal sources should we consider?"
    )

    graph = build_graph()
    result = await graph.ainvoke(
        {
            "question": question,
            "memory_summary": "",
            "law_analysis": "",
            "financial_analysis": "",
            "api_research": "",
            "final_response": "",
        }
    )

    print("=" * 70)
    print("OPTIONAL CHALLENGES RESULT")
    print("=" * 70)
    print(result["final_response"])


if __name__ == "__main__":
    asyncio.run(main())
