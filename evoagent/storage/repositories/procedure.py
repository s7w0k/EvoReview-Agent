"""Persistent procedure repository (plan section 9.4)."""
from typing import Any, Dict, List

from .base import PersistentRepository


class ProcedureRepository(PersistentRepository):
    """Persists procedure skills, versions and deployments."""

    table = "procedure_skills"

    def save_skill(self, name: str, skill: Dict[str, Any]) -> None:
        self.save(name, skill)

    def save_version(self, name: str, version: int, content: Dict[str, Any]) -> None:
        key = "%s::%d" % (name, version)
        self.store.save("procedure_skill_versions", key, {
            "name": name, "version": version, "content": content,
        })

    def deploy(self, name: str, row: Dict[str, Any]) -> None:
        self.store.save("procedure_deployments", name,
                        dict(row, name=name))

    def deployments(self) -> List[Dict[str, Any]]:
        return self.store.all("procedure_deployments")


__all__ = ["ProcedureRepository"]