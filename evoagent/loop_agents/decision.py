"""Uniform, auditable decision envelope for every local Agent loop."""
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class AgentDecision:
    action: str
    tool_name: Optional[str] = None
    arguments: Dict[str, Any] = field(default_factory=dict)
    reason_code: str = ""
    confidence: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_action(self, **final_payload: Any) -> Dict[str, Any]:
        if self.action == "tool":
            return {
                "action": "tool", "tool": self.tool_name or "",
                "arguments": dict(self.arguments),
                "reason_code": self.reason_code,
                "confidence": self.confidence,
            }
        return {"action": "final", **final_payload,
                "reason_code": self.reason_code,
                "confidence": self.confidence}


__all__ = ["AgentDecision"]
