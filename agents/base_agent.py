"""
Base class for all agents.
Subclasses only need to implement system_prompt and handle_message.
"""

import json
import time
import logging
import threading
from abc import ABC, abstractmethod
from typing import Optional
import sqlite3

from core.llm import LLMClient, LLMError
from core.db import get_thread_connection
from core.message_bus import MessageBus, Message, MsgType, Urgency

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    def __init__(
        self,
        name: str,
        db_path: str,
        llm: LLMClient,
        poll_interval: float = 5.0,
    ):
        self.name = name
        self.db_path = db_path
        self.llm = llm
        self.poll_interval = poll_interval

        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Conversation history for LLM context
        self._history: list[dict] = []
        self._max_history = 20

        # Main-thread connection (for setup calls before start())
        self._main_conn = get_thread_connection(db_path)
        self._main_bus = MessageBus(self._main_conn)

    # Convenience property: always use the correct connection for current thread
    @property
    def bus(self) -> MessageBus:
        conn = get_thread_connection(self.db_path)
        return MessageBus(conn)

    # ──────────────────────────────────────────
    # Subclass interface
    # ──────────────────────────────────────────

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        ...

    @abstractmethod
    def handle_message(self, msg: Message) -> Optional[str]:
        ...

    # ──────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────

    def start(self):
        if self._running:
            logger.warning(f"[{self.name}] already running")
            return

        self._running = True
        self._main_bus.set_agent_state(self.name, "running", {})
        self._main_bus.log_event(self.name, "started")

        self._thread = threading.Thread(
            target=self._loop,
            name=self.name,
            daemon=True,
        )
        self._thread.start()
        logger.info(f"[{self.name}] started")

    def stop(self):
        self._running = False
        self._main_bus.set_agent_state(self.name, "stopped", {})
        self._main_bus.log_event(self.name, "stopped")
        logger.info(f"[{self.name}] stopped")

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ──────────────────────────────────────────
    # Main loop (runs in background thread)
    # ──────────────────────────────────────────

    def _loop(self):
        # Each thread gets its own connection
        while self._running:
            try:
                messages = self.bus.receive(self.name)
                for msg in messages:
                    self._process(msg)
                # Heartbeat: update agent state every loop to signal liveness
                self.bus.conn.execute(
                    "UPDATE agent_states SET updated_at=datetime('now') WHERE agent_name=?",
                    (self.name,)
                )
                self.bus.conn.commit()
            except Exception as e:
                logger.error(f"[{self.name}] loop error: {e}")
                try:
                    self.bus.set_agent_state(self.name, "error", {})
                    self.bus.alert_hotl(
                        from_agent=self.name,
                        urgency=Urgency.HIGH,
                        title=f"{self.name} loop error",
                        body=str(e),
                    )
                except Exception:
                    pass
            time.sleep(self.poll_interval)

    def _process(self, msg: Message):
        logger.info(f"[{self.name}] processing [{msg.msg_type}] from={msg.from_agent}")
        try:
            result = self.handle_message(msg)
            self.bus.ack(msg.id)
            if result:
                self.bus.log_event(self.name, "message_handled", {
                    "msg_id": msg.id,
                    "from": msg.from_agent,
                    "type": msg.msg_type,
                    "result": str(result)[:200],
                })
        except Exception as e:
            logger.error(f"[{self.name}] message processing error (id={msg.id}): {e}")
            self.bus.log_event(self.name, "message_error", {
                "msg_id": msg.id,
                "error": str(e),
            })

    # ──────────────────────────────────────────
    # LLM helpers
    # ──────────────────────────────────────────

    def _heartbeat(self):
        """DB에 현재 시각 갱신 (Auditor 오탐 방지)."""
        try:
            self.bus.conn.execute(
                "UPDATE agent_states SET updated_at=datetime('now') WHERE agent_name=?",
                (self.name,)
            )
            self.bus.conn.commit()
        except Exception:
            pass

    def think(self, user_content: str, temperature: Optional[float] = None) -> str:
        self._heartbeat()  # LLM 호출 전 heartbeat
        self._history.append({"role": "user", "content": user_content})

        if len(self._history) > self._max_history * 2:
            self._history = self._history[:2] + self._history[-(self._max_history * 2 - 2):]

        try:
            response = self.llm.chat(
                system_prompt=self.system_prompt,
                messages=self._history,
                temperature=temperature,
            )
            self._history.append({"role": "assistant", "content": response})
            return response
        except LLMError as e:
            logger.error(f"[{self.name}] LLM error: {e}")
            raise

    def think_json(self, user_content: str, temperature: Optional[float] = None) -> dict:
        prompt = user_content + "\n\nRespond with pure JSON only. No markdown, no code blocks."

        for attempt in range(2):
            raw = self.think(prompt, temperature=temperature)
            try:
                clean = raw.strip()
                if clean.startswith("```"):
                    clean = clean.split("```")[1]
                    if clean.startswith("json"):
                        clean = clean[4:]
                return json.loads(clean.strip())
            except json.JSONDecodeError:
                if attempt == 0:
                    logger.warning(f"[{self.name}] JSON parse failed, retrying...")
                    self._history = self._history[:-1]
                else:
                    raise ValueError(f"LLM did not return valid JSON: {raw[:200]}")

    # ──────────────────────────────────────────
    # Convenience methods
    # ──────────────────────────────────────────

    def send(self, to: str, msg_type: str, payload: dict, priority: int = 5):
        return self.bus.send(Message(
            from_agent=self.name,
            to_agent=to,
            msg_type=msg_type,
            payload=payload,
            priority=priority,
        ))

    def alert(self, urgency: str, title: str, body: str):
        return self.bus.alert_hotl(self.name, urgency, title, body)

    def update_state(self, **kwargs):
        current = self.bus.get_agent_state(self.name) or {}
        # remove 'status' key that get_agent_state injects
        current.pop("status", None)
        current.update(kwargs)
        self.bus.set_agent_state(self.name, "running", current)

    def _get_state(self) -> dict:
        state = self.bus.get_agent_state(self.name) or {}
        state.pop("status", None)
        return state
