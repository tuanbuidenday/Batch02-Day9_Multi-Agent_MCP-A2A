"""End-to-end test client for the Legal Multi-Agent System.

Sends a legal question to the Customer Agent and prints the response.
"""

import asyncio
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

CUSTOMER_AGENT_URL = os.getenv("CUSTOMER_AGENT_URL", "http://localhost:10100")

QUESTION = (
    "If a company breaks a contract and avoids taxes, "
    "what are the legal and regulatory consequences?"
)


def _part_text(part: object) -> str:
    inner = getattr(part, "root", part)
    return getattr(inner, "text", "") or ""


def _message_text(message: object | None) -> str:
    if message is None:
        return ""
    return "".join(_part_text(part) for part in (getattr(message, "parts", []) or []))


def _extract_response_text(response: object) -> str:
    """Extract text from successful artifacts, direct messages, or failed task status."""
    root = getattr(response, "root", response)
    result = getattr(root, "result", None)
    if result is None:
        return ""

    chunks: list[str] = []

    artifacts = getattr(result, "artifacts", None)
    if artifacts:
        for artifact in artifacts:
            for part in getattr(artifact, "parts", []) or []:
                chunks.append(_part_text(part))

    for part in getattr(result, "parts", []) or []:
        chunks.append(_part_text(part))

    status = getattr(result, "status", None)
    if status is not None:
        chunks.append(_message_text(getattr(status, "message", None)))

    if not any(chunks):
        for msg in getattr(result, "history", []) or []:
            chunks.append(_message_text(msg))

    return "".join(chunks)


async def main() -> None:
    print(f"Connecting to Customer Agent at {CUSTOMER_AGENT_URL}")
    print(f"Question: {QUESTION}")
    print("-" * 60)

    async with httpx.AsyncClient(timeout=300.0) as http_client:
        # Resolve agent card
        card_url = f"{CUSTOMER_AGENT_URL}/.well-known/agent.json"
        try:
            card_resp = await http_client.get(card_url)
            card_resp.raise_for_status()
        except Exception as e:
            print(f"ERROR: Could not reach Customer Agent at {card_url}")
            print(f"  {e}")
            print("Make sure all services are running (./start_all.sh)")
            sys.exit(1)

        from a2a.types import AgentCard, Message, Part, Role, TextPart
        from a2a.client import A2AClient
        from uuid import uuid4

        agent_card = AgentCard.model_validate(card_resp.json())
        print(f"Connected to agent: {agent_card.name} v{agent_card.version}")
        print("-" * 60)

        # Build the legacy A2AClient
        client = A2AClient(httpx_client=http_client, agent_card=agent_card)

        # Construct the message
        from a2a.types import SendMessageRequest, MessageSendParams as MSP
        message = Message(
            role=Role.user,
            parts=[Part(root=TextPart(text=QUESTION))],
            message_id=str(uuid4()),
        )
        request = SendMessageRequest(
            id=str(uuid4()),
            params=MSP(message=message),
        )

        print("Sending request (this may take 30-60s while agents chain)...\n")
        response = await client.send_message(request)

        result_text = _extract_response_text(response)

        if result_text:
            print("RESPONSE:")
            print("=" * 60)
            print(result_text)
            print("=" * 60)
        else:
            print("No text response received. Raw response:")
            print(response)


if __name__ == "__main__":
    asyncio.run(main())
