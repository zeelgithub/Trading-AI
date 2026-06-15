"""
State machine -- core layer.

Bot states and the legal transitions between them. The critical safety property:
once HALTED (e.g. by a kill-switch trip or a reconciliation mismatch) the bot
does NOT self-resume -- only an explicit manual reset() returns it to IDLE.

Boundary: places orders NO.
"""

from __future__ import annotations

from enum import Enum


class State(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    HALTED = "halted"
    LIQUIDATING = "liquidating"


# Allowed transitions. HALTED -> IDLE is reachable ONLY via reset() (manual).
_ALLOWED: dict[State, set[State]] = {
    State.IDLE: {State.RUNNING, State.HALTED},
    State.RUNNING: {State.RUNNING, State.IDLE, State.HALTED, State.LIQUIDATING},
    State.LIQUIDATING: {State.HALTED},
    State.HALTED: set(),  # terminal until reset()
}


class IllegalTransition(RuntimeError):
    pass


class StateMachine:
    def __init__(self) -> None:
        self._state = State.IDLE
        self.halt_reason: str | None = None

    @property
    def state(self) -> State:
        return self._state

    @property
    def is_halted(self) -> bool:
        return self._state == State.HALTED

    def to(self, target: State) -> None:
        if target not in _ALLOWED[self._state]:
            raise IllegalTransition(f"{self._state.value} -> {target.value} not allowed")
        self._state = target

    def halt(self, reason: str) -> None:
        """Force HALTED from any non-terminal state and record why."""
        if self._state == State.HALTED:
            return
        self.halt_reason = reason
        self._state = State.HALTED

    def reset(self) -> None:
        """Manual reset after a halt. The deliberate human-in-the-loop step."""
        if self._state != State.HALTED:
            raise IllegalTransition(f"reset() only valid from HALTED, not {self._state.value}")
        self._state = State.IDLE
        self.halt_reason = None
