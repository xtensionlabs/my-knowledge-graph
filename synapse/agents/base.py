"""Shared base + types for Synapse agents.

Every agent inherits a small protocol so the gateway's tool registry can
discover and invoke them uniformly. M1 implements `Librarian`; M2-M6 add the
rest.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentResult:
    """Standardized return shape for `Agent.run()`."""

    agent: str
    ok: bool
    summary: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class Agent(ABC):
    """Protocol every Synapse agent implements."""

    name: str  # set by subclass

    @abstractmethod
    async def run(self, **kwargs: Any) -> AgentResult:
        """Execute the agent. Returns an `AgentResult`."""
        ...
