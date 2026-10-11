"""Run Untangle agents as A2A servers on AGNTCY SLIM.

    python agent_server.py architect      # one agent per terminal
    python agent_server.py all            # all three, each in its own process

Mirrors coffeeAgntcy Lungo's farm servers: an AgentCard advertising a SLIM
interface, an AgentExecutor wrapping the agent, served via AgntcyFactory.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from uuid import uuid4

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import Message, Part, Role, Task, TextPart, UnsupportedOperationError
from a2a.utils.errors import ServerError
from agntcy_app_sdk import AgntcyFactory

from agents import Agent
from transport import AGENTS_BY_ID, agent_card, handle_request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("untangle.agent_server")


class NegotiatorExecutor(AgentExecutor):
    def __init__(self, agent: Agent):
        self.agent = agent

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            request = json.loads(context.get_user_input())
            logger.info("%s received %s request", self.agent.id, request.get("op"))
            reply = await handle_request(self.agent, request)
        except Exception as err:
            # Model/provider errors may include credentials or private request contents.
            logger.error("%s failed (%s)", self.agent.id, type(err).__name__)
            reply = {"agent": self.agent.id, "error": "Agent request failed; no valid vote was recorded."}
        await event_queue.enqueue_event(
            Message(
                message_id=str(uuid4()),
                role=Role.agent,
                metadata={"name": self.agent.id},
                parts=[Part(TextPart(text=json.dumps(reply)))],
            )
        )
        logger.info("%s replied", self.agent.id)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> Task | None:
        raise ServerError(error=UnsupportedOperationError())


async def serve(agent: Agent) -> None:
    factory = AgntcyFactory(f"untangle.{agent.id}", enable_tracing=False)
    handler = DefaultRequestHandler(agent_executor=NegotiatorExecutor(agent), task_store=InMemoryTaskStore())
    card = agent_card(agent)
    session = factory.create_app_session()
    await session.add_a2a_card(card, handler).with_factory(factory).start(keep_alive=False)
    logger.info("%s serving on %s", agent.name, card.url)
    await session.start_all_sessions(keep_alive=True)


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    if which == "all":
        procs = [subprocess.Popen([sys.executable, __file__, agent_id]) for agent_id in AGENTS_BY_ID]
        try:
            for p in procs:
                p.wait()
        except KeyboardInterrupt:
            for p in procs:
                p.terminate()
        return
    if which not in AGENTS_BY_ID:
        sys.exit(f"usage: python agent_server.py {{{'|'.join(AGENTS_BY_ID)}|all}}")
    try:
        asyncio.run(serve(AGENTS_BY_ID[which]))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
