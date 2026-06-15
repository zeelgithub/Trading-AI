"""Tests for the bot state machine and its no-self-resume safety property."""

from __future__ import annotations

import pytest

from src.core.state_machine import IllegalTransition, State, StateMachine


def test_normal_cycle_transitions():
    sm = StateMachine()
    assert sm.state == State.IDLE
    sm.to(State.RUNNING)
    sm.to(State.IDLE)
    assert sm.state == State.IDLE


def test_halt_is_terminal_until_reset():
    sm = StateMachine()
    sm.to(State.RUNNING)
    sm.halt("kill switch")
    assert sm.is_halted and sm.halt_reason == "kill switch"
    # No transition out of HALTED is allowed...
    with pytest.raises(IllegalTransition):
        sm.to(State.RUNNING)
    # ...except an explicit manual reset.
    sm.reset()
    assert sm.state == State.IDLE and sm.halt_reason is None


def test_reset_only_from_halted():
    sm = StateMachine()
    with pytest.raises(IllegalTransition):
        sm.reset()


def test_illegal_transition_rejected():
    sm = StateMachine()
    with pytest.raises(IllegalTransition):
        sm.to(State.LIQUIDATING)  # not allowed straight from IDLE
