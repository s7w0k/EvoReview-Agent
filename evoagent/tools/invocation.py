"""Side-effect tool invocation guard with idempotency protection.

A non-idempotent side-effect tool must never be blindly re-executed.  Each
logical invocation is keyed and tracked through a state machine.  ``FAILED`` and
``UNKNOWN`` states are terminal and block automatic replay.
"""
import enum
import threading
from typing import Any, Dict, Optional


class InvocationState(str, enum.Enum):
    REQUESTED = "REQUESTED"
    AUTHORIZED = "AUTHORIZED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


TERMINAL = {InvocationState.SUCCEEDED, InvocationState.FAILED, InvocationState.UNKNOWN}
BLOCK_REPLAY = {InvocationState.FAILED, InvocationState.UNKNOWN}


class UnknownInvocationError(RuntimeError):
    """A non-idempotent invocation is in an unknown state and must not be replayed."""


class ToolInvocationGuard:
    """Track idempotency keys so side-effect tools are executed once."""

    def __init__(self):
        self._states: Dict[str, InvocationState] = {}
        self._lock = threading.RLock()

    def begin(self, idempotency_key: str) -> InvocationState:
        with self._lock:
            current = self._states.get(idempotency_key)
            if current is None:
                self._states[idempotency_key] = InvocationState.REQUESTED
                return InvocationState.REQUESTED
            if current in BLOCK_REPLAY:
                raise UnknownInvocationError(
                    "invocation %s left in %s; not auto-replaying" % (idempotency_key, current.value)
                )
            return current

    def transition(self, idempotency_key: str, state: InvocationState) -> None:
        with self._lock:
            self._states[idempotency_key] = state

    def authorize(self, key: str) -> None:
        self.transition(key, InvocationState.AUTHORIZED)

    def running(self, key: str) -> None:
        self.transition(key, InvocationState.RUNNING)

    def succeed(self, key: str) -> None:
        self.transition(key, InvocationState.SUCCEEDED)

    def fail(self, key: str) -> None:
        self.transition(key, InvocationState.FAILED)

    def mark_unknown(self, key: str) -> None:
        self.transition(key, InvocationState.UNKNOWN)

    def state(self, key: str) -> Optional[InvocationState]:
        with self._lock:
            return self._states.get(key)