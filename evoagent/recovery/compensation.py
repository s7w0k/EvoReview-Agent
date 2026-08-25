"""Compensation handlers for reversible side-effect tools."""
from typing import Any, Callable, Dict, Optional


class CompensationHandler:
    """Registry of compensation (rollback) callbacks for side-effect tools."""

    def __init__(self):
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {}

    def register(self, tool_name: str, handler: Callable[[Dict[str, Any]], Any]) -> None:
        self._handlers[tool_name] = handler

    def compensates(self, tool_name: str) -> bool:
        return tool_name in self._handlers

    def compensate(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise KeyError("no compensation handler for tool: %s" % tool_name)
        return handler(dict(arguments or {}))

    def list(self) -> list:
        return sorted(self._handlers)