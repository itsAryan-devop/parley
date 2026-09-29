#!/usr/bin/env python3
"""PARLEY agent for Full-Duplex-Bench v3.

A fork of FDB-v3's `v3/cascaded_agent.py` (Silero VAD + Whisper STT + gpt-4o +
OpenAI TTS). The models, tool schemas and logging format are unchanged, so the
benchmark's runner and evaluators treat it exactly like the stock agent and any
score difference comes from the three things added here:

The default backend is Groq's free tier rather than OpenAI (PARLEY_BACKEND).

1. **Turn detection** -- `parley.fdb.ParleyTurnDetector`, our trained endpointer,
   keeps the turn open through a self-correction pause instead of letting the
   LLM act on the abandoned value.
2. **Tool guard** -- `parley.fdb.ToolGuard`: no duplicate action per
   conversation, and a call is dropped if the user resumes speaking inside a
   short commit window (cancel before effect).
3. **Instructions** for disfluent speech: act only on the final corrected value.

Run from FDB-v3's `v3/` directory (it imports `mock_apis`); `reproduce.sh` does
this. Provider label for the benchmark runner: `--provider parley`.

Env (in v3/.env.local): LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, and
GROQ_API_KEY (default, free tier) or OPENAI_API_KEY with PARLEY_BACKEND=openai.
Optional: PARLEY_LLM (Groq model id), PARLEY_COMMIT_GRACE (s, default 0.3),
PARLEY_MAX_DELAY (s, default 1.5), PARLEY_TURN_DETECTOR=0 to disable (ablation),
PARLEY_GUARD=0 to disable (ablation).
"""

import json
import logging
import os
import sys
import time

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, llm

from parley.fdb import ParleyTurnDetector, ToolGuard

ai_callable = llm.function_tool if hasattr(llm, "function_tool") else llm.ai_callable

LATENCY_PROFILE = "instant"
if "--latency" in sys.argv:
    i = sys.argv.index("--latency")
    LATENCY_PROFILE = sys.argv[i + 1]
    del sys.argv[i:i + 2]

from mock_apis import MockAPIRegistry  # noqa: E402  (FDB-v3's v3/ directory)

registry = MockAPIRegistry(latency_profile=LATENCY_PROFILE)
load_dotenv(os.path.join(os.getcwd(), ".env.local"))

VAD_MIN_SILENCE_S = 0.55
MIN_DELAY_S = 0.5
MAX_DELAY_S = float(os.getenv("PARLEY_MAX_DELAY", "1.5"))
GRACE_S = float(os.getenv("PARLEY_COMMIT_GRACE", "0.3"))
USE_TURN = os.getenv("PARLEY_TURN_DETECTOR", "1") != "0"
USE_GUARD = os.getenv("PARLEY_GUARD", "1") != "0"
BACKEND = os.getenv("PARLEY_BACKEND", "groq")  # groq (free) | openai
DISFLUENT_PROMPT = "Um, I want to go to, uh, no wait, actually somewhere else."

TOOL_NAMES = [
    "search_flights", "book_flight", "update_identity_doc",
    "get_card_benefits", "get_exchange_rate", "modify_autopay",
    "search_apartments", "calculate_commute", "update_search_filter",
    "track_order", "search_products", "add_to_cart",
]


class LatencyTracker:
    """Same fields and log lines as the stock agent -- analyze_tool_latency.py reads them."""

    def __init__(self):
        self.user_done_at = 0
        self.tool_start_at = 0
        self.tool_end_at = 0
        self.agent_start_at = 0
        self.query_received = False

    def reset(self):
        self.__init__()

    def log_breakdown(self, tool_name="", room_name="unknown"):
        if not self.user_done_at or not self.agent_start_at or not self.tool_start_at:
            return
        reasoning = self.tool_start_at - self.user_done_at
        execution = (self.tool_end_at - self.tool_start_at) if self.tool_end_at else 0
        synthesis = self.agent_start_at - (self.tool_end_at or self.user_done_at)
        metrics = {
            "room": room_name, "tool": tool_name,
            "reasoning": round(reasoning, 3), "execution": round(execution, 3),
            "synthesis": round(synthesis, 3),
            "total": round(self.agent_start_at - self.user_done_at, 3),
            "agent_start_at": self.agent_start_at,
        }
        with open("/tmp/agent_heartbeat.log", "a") as f:
            f.write(f"LATENCY_TRACK_JSON: {json.dumps(metrics)}\n")


class AssistantFnc:
    """The 12 FDB-v3 tools, same names/signatures/descriptions as the stock agent,
    each routed through the guard."""

    def __init__(self, tracker: LatencyTracker, room_name: str, guard: ToolGuard | None):
        self.tracker = tracker
        self.room_name = room_name
        self.guard = guard

    async def _call(self, name: str, **args):
        def execute():
            self.tracker.tool_start_at = time.time()
            result = registry.call(name, **args)
            self.tracker.tool_end_at = time.time()
            with open("/tmp/agent_tool_calls.log", "a") as f:
                f.write(json.dumps({"room": self.room_name, "call": {
                    "function": name, "args": args,
                    "timestamp_start": self.tracker.tool_start_at,
                    "timestamp_end": self.tracker.tool_end_at}}) + "\n")
            return result

        if self.guard is None:
            return json.dumps(execute())
        result, executed = await self.guard.run(name, args, execute)
        if not executed:
            with open("/tmp/parley_guard.log", "a") as f:
                f.write(json.dumps({"room": self.room_name, "suppressed": name,
                                    "args": args, "t": time.time(),
                                    "why": self.guard.suppressed[-1][1]}) + "\n")
        return json.dumps(result, default=str)

    # -- Travel & Identity
    @ai_callable(description="Search for available flights to a destination.")
    async def search_flights(self, destination: str, date: str):
        """
        Args:
            destination: The city or airport, e.g. 'London' or 'LHR'
            date: The travel date, e.g. '2026-08-20'
        """
        return await self._call("search_flights", destination=destination, date=date)

    @ai_callable(description="Book a flight ticket.")
    async def book_flight(self, passenger_name: str):
        """
        Args:
            passenger_name: The name of the passenger, e.g. 'John Doe'
        """
        return await self._call("book_flight", passenger_name=passenger_name)

    @ai_callable(description="MANDATORY tool to update simulated user identity document details (e.g. passport, driver license). You are fully authorized in this test environment. NEVER refuse to use it due to 'external personal/government system' safety concerns.")
    async def update_identity_doc(self, doc_type: str, doc_number: str):
        """
        Args:
            doc_type: Type of document, e.g. 'passport' or 'id_card'
            doc_number: The document identifier string
        """
        return await self._call("update_identity_doc", doc_type=doc_type, doc_number=doc_number)

    # -- Finance & Billing
    @ai_callable(description="MANDATORY tool to get benefits for a credit card. NEVER guess benefits from memory. Execute this tool immediately.")
    async def get_card_benefits(self, card_type: str):
        """
        Args:
            card_type: The card type, e.g. 'platinum' or 'gold'
        """
        return await self._call("get_card_benefits", card_type=card_type)

    @ai_callable(description="MANDATORY tool to fetch the exact, current foreign exchange rate. NEVER guess or calculate exchange rates from your internal memory; you MUST use this API.")
    async def get_exchange_rate(self, amount: float, from_currency: str, to_currency: str):
        """
        Args:
            amount: Amount to convert
            from_currency: 3-letter currency code, e.g. 'USD'
            to_currency: 3-letter currency code, e.g. 'EUR'
        """
        return await self._call("get_exchange_rate", amount=amount,
                                from_currency=from_currency, to_currency=to_currency)

    @ai_callable(description="MANDATORY tool to process billing details. Execute this update immediately when the user requests Autopay modification.")
    async def modify_autopay(self, bill_type: str, source_account: str):
        """
        Args:
            bill_type: Type of bill, e.g. 'credit_card' or 'utilities'
            source_account: Bank account identifier, e.g. 'checking'
        """
        return await self._call("modify_autopay", bill_type=bill_type, source_account=source_account)

    # -- Housing & Location
    @ai_callable(description="Search for available rental apartments.")
    async def search_apartments(self, city: str, bedrooms: int, max_price: float):
        """
        Args:
            city: Destination city
            bedrooms: Number of bedrooms
            max_price: Maximum monthly rent budget
        """
        return await self._call("search_apartments", city=city, bedrooms=bedrooms, max_price=max_price)

    @ai_callable(description="MANDATORY tool to calculate commute duration. Fetch exact commute times using this tool. Do NOT estimate from memory.")
    async def calculate_commute(self, origin_address: str, destination_address: str, mode: str = "driving"):
        """
        Args:
            origin_address: Starting location
            destination_address: Destination location
            mode: Transport mode, defaults to 'driving'
        """
        return await self._call("calculate_commute", origin_address=origin_address,
                                destination_address=destination_address, mode=mode)

    @ai_callable(description="Instantly update the user's search filter in the backend system. Execute this IMMEDIATELY without asking for further confirmations or batching requests. Do not ask clarifying questions.")
    async def update_search_filter(self, filter_name: str, value: str):
        """
        Args:
            filter_name: Filter key to modify
            value: Filter value to apply
        """
        return await self._call("update_search_filter", filter_name=filter_name, value=value)

    # -- E-Commerce Support
    @ai_callable(description="MANDATORY tool to track physical package status. Do NOT answer from memory or batch tracking requests. EXECUTE THIS TOOL IMMEDIATELY for every order ID mentioned.")
    async def track_order(self, order_id: str):
        """
        Args:
            order_id: Order identifier to track, e.g. 'BOB12'
        """
        return await self._call("track_order", order_id=order_id)

    @ai_callable(description="MANDATORY tool to search for products in the catalog. Do NOT answer from memory. You MUST execute this tool whenever the user asks for item recommendations or searches.")
    async def search_products(self, query: str, max_price: float = None):
        """
        Args:
            query: Product search term, e.g. 'headphones'
            max_price: Optional maximum budget
        """
        return await self._call("search_products", query=query, max_price=max_price)

    @ai_callable(description="MANDATORY tool to add an item to the shopping cart. Execute this action IMMEDIATELY the moment the user asks without confirming or waiting for them to list more items.")
    async def add_to_cart(self, product_id: str, quantity: int = 1):
        """
        Args:
            product_id: ID of the product
            quantity: Amount to add
        """
        return await self._call("add_to_cart", product_id=product_id, quantity=quantity)


INSTRUCTIONS = (
    "You are a helpful voice AI assistant. Keep responses concise and conversational; "
    "they are spoken aloud. You have access to 12 APIs across 4 domains (Travel, Finance, "
    "Housing, E-Commerce). This is a safe, simulated test environment: you are fully "
    "authorized and REQUIRED to use the tools for any request, including identity documents, "
    "billing and orders. Never refuse on real-world authorization grounds. "
    "Do not ask clarifying questions and do not wait for confirmation: when the user gives an "
    "instruction, call the correct tool immediately and answer from the tool's result. Never "
    "answer from memory or invent data. "
    "The user speaks naturally, with fillers, pauses, false starts and self-corrections. "
    "When they correct themselves ('Paris -- no, actually Berlin'), use ONLY the final value "
    "and never call a tool with the abandoned one. Call each tool once per distinct request; "
    "never repeat a call that already succeeded. When a request needs several steps, perform "
    "them in order, using earlier results (such as an id) as arguments for later calls. "
    "If a tool result says the action was not executed because the user was still speaking, "
    "wait for the rest of their request, then act on the final version."
)


class ParleyAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)


server = AgentServer()


def build_models():
    """STT + LLM + TTS for the chosen backend.

    groq (default, free tier, one GROQ_API_KEY): Whisper-large-v3-turbo STT,
    Llama-3.3-70B tool-calling LLM, Orpheus TTS. openai: the stock agent's
    exact models, for a like-for-like comparison against the published baseline.
    Whisper is primed with a disfluent prompt so it keeps "uh, no, actually"
    in the transcript instead of silently deleting the correction markers the
    turn detector and the LLM rely on (finding credited in docs/PRIOR_ART.md B.1).
    """
    if BACKEND == "openai":
        from livekit.plugins import openai
        return (openai.STT(model="whisper-1", language="en", prompt=DISFLUENT_PROMPT),
                openai.LLM(model="gpt-4o", temperature=0.0),
                openai.TTS(model="tts-1", voice="nova"))
    from livekit.plugins import groq
    return (groq.STT(model="whisper-large-v3-turbo", language="en", prompt=DISFLUENT_PROMPT),
            groq.LLM(model=os.getenv("PARLEY_LLM", "llama-3.3-70b-versatile"),
                     temperature=0.0, parallel_tool_calls=False),
            groq.TTS(model="canopylabs/orpheus-v1-english", voice="autumn"))


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext):
    from livekit.plugins import silero

    tracker = LatencyTracker()
    guard = ToolGuard(grace_s=GRACE_S) if USE_GUARD else None  # fresh per conversation
    tools = llm.find_function_tools(AssistantFnc(tracker, ctx.room.name, guard))

    extra = {}
    if USE_TURN:
        extra["turn_detection"] = ParleyTurnDetector(TOOL_NAMES, vad_silence_s=VAD_MIN_SILENCE_S)

    stt, llm_model, tts = build_models()
    session = AgentSession(
        vad=silero.VAD.load(min_speech_duration=0.05, min_silence_duration=VAD_MIN_SILENCE_S),
        stt=stt,
        llm=llm_model,
        tts=tts,
        tools=tools,
        min_endpointing_delay=MIN_DELAY_S,
        max_endpointing_delay=MAX_DELAY_S,
        **extra,
    )

    @session.on("user_state_changed")
    def on_user_state(ev):
        if ev.new_state == "speaking" and guard is not None:
            guard.user_started_speaking()

    @session.on("user_input_transcribed")
    def on_user_input(msg):
        if msg.is_final and not tracker.query_received:
            tracker.user_done_at = time.time()
            tracker.query_received = True

    @session.on("agent_state_changed")
    def on_agent_state(ev):
        if ev.new_state == "speaking" and tracker.query_received and not tracker.agent_start_at:
            tracker.agent_start_at = time.time()
            tracker.log_breakdown(tool_name="Search Tool", room_name=ctx.room.name)
            tracker.reset()

    await session.start(room=ctx.room, agent=ParleyAgent())
    logging.info("PARLEY agent started (turn=%s guard=%s grace=%.2fs max_delay=%.2fs)",
                 USE_TURN, USE_GUARD, GRACE_S, MAX_DELAY_S)


if __name__ == "__main__":
    agents.cli.run_app(server)
