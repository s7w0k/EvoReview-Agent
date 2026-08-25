"""Shared persistence layer (plan section 9 / Phase 5).

Hosts the table-oriented :class:`JSONFileStore` and the ``repositories`` package
that gives every closed-loop component a durable home.
"""
from .json_store import JSONFileStore

__all__ = ["JSONFileStore"]