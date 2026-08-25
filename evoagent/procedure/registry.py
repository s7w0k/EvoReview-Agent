"""Versioned registry for procedure skills.

A procedure skill goes through a strict lifecycle before it can ever run for a
real review:

    DRAFT -> VALIDATED -> SHADOW -> ACTIVE
       |  /-> REJECTED
       |         /-> ROLLED_BACK (from ACTIVE)

Only skills that are ACTIVE (or SHADOW, when explicitly permitted) are eligible
for execution.  A rejected or rolled-back candidate can never reach ACTIVE
without a new version.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from .schema import ProcedureSkill


class SkillStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


# Statuses a skill may be *run in*.
RUNNABLE = {SkillStatus.ACTIVE}


@dataclass
class ProcedureSkillVersion:
    """A single immutable version of a procedure skill."""

    skill_name: str
    version: int
    status: SkillStatus
    content: ProcedureSkill
    parent_version: Optional[int] = None
    source_hypothesis_id: Optional[str] = None
    created_at: str = ""
    activated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "version": self.version,
            "status": self.status.value,
            "parent_version": self.parent_version,
            "source_hypothesis_id": self.source_hypothesis_id,
            "created_at": self.created_at,
            "activated_at": self.activated_at,
            "content": self.content.to_dict(),
        }


class ProcedureSkillConflict(Exception):
    """Raised when registering a version that already exists or is invalid."""


class ProcedureNotActive(Exception):
    """Raised when a skill that is not ACTIVE is requested for execution."""


class ProcedureRegistry:
    """Holds the full version history and current state of procedure skills."""

    def __init__(self):
        # skill_name -> {version: ProcedureSkillVersion}
        self._versions: Dict[str, Dict[int, ProcedureSkillVersion]] = {}
        self._active: Dict[str, ProcedureSkillVersion] = {}

    # -- registration -------------------------------------------------------

    def register(
        self,
        content: ProcedureSkill,
        *,
        status: SkillStatus = SkillStatus.DRAFT,
        parent_version: Optional[int] = None,
        source_hypothesis_id: Optional[str] = None,
        created_at: str = "",
    ) -> ProcedureSkillVersion:
        """Register a new version.  Returns the created version record."""
        existing = self._versions.get(content.name, {})
        if content.version in existing:
            raise ProcedureSkillConflict(
                f"version {content.version} of {content.name!r} already registered")

        record = ProcedureSkillVersion(
            skill_name=content.name,
            version=content.version,
            status=status,
            content=content,
            parent_version=parent_version,
            source_hypothesis_id=source_hypothesis_id,
            created_at=created_at,
        )
        existing[content.version] = record
        self._versions[content.name] = existing

        if status is SkillStatus.ACTIVE:
            self._active[content.name] = record
        if status is SkillStatus.ROLLED_BACK:
            # A rolled-back record must not remain the active version.
            self._active.pop(content.name, None)
        return record

    # -- lifecycle transitions ----------------------------------------------

    def validate(self, name: str, version: int) -> None:
        """Mark a DRAFT as VALIDATED (in place)."""
        record = self._require(name, version)
        if record.status is not SkillStatus.DRAFT:
            raise ProcedureSkillConflict(
                f"only DRAFT can be validated, got {record.status.value}")
        record.status = SkillStatus.VALIDATED

    def shadow(self, name: str, version: int) -> None:
        """Move a VALIDATED skill to SHADOW (observing, not deciding)."""
        record = self._require(name, version)
        if record.status not in (SkillStatus.VALIDATED, SkillStatus.SHADOW):
            raise ProcedureSkillConflict(
                f"cannot shadow {name!r} v{version} from {record.status.value}")
        record.status = SkillStatus.SHADOW

    def activate(self, name: str, version: int) -> None:
        """Promote a SHADOW / VALIDATED skill to ACTIVE.  Returns the record."""
        record = self._require(name, version)
        if record.status not in (
                SkillStatus.SHADOW, SkillStatus.VALIDATED, SkillStatus.ACTIVE):
            raise ProcedureSkillConflict(
                f"cannot activate {name!r} v{version} from {record.status.value}")
        record.status = SkillStatus.ACTIVE
        self._active[name] = record
        return record

    def reject(self, name: str, version: int) -> None:
        """Mark a candidate as REJECTED; it can never be activated as-is."""
        record = self._require(name, version)
        if record.status is SkillStatus.ACTIVE or record.status is SkillStatus.ROLLED_BACK:
            raise ProcedureSkillConflict(
                f"cannot reject {name!r} v{version} once it is {record.status.value}")
        record.status = SkillStatus.REJECTED

    def rollback(self, name: str) -> ProcedureSkillVersion:
        """Roll the active skill back to its previous ACTIVE version.

        The current ACTIVE version becomes ROLLED_BACK and the latest previous
        ACTIVE version (if any) is re-promoted.  An explicit ``record`` is not
        required -- only the name is needed.

        Returns the newly promoted version, or raises ``ProcedureSkillConflict``
        when there is no prior version to restore.
        """
        current = self._active.get(name)
        if current is None:
            raise ProcedureSkillConflict(f"no active version for {name!r}")

        # Find a valid CARRIER to step back to: an earlier ACTIVE/VALIDATED record.
        previous = self._previous_active(name, exclude_version=current.version)
        if previous is None:
            raise ProcedureSkillConflict(
                f"cannot rollback {name!r}: no earlier valid version exists")

        current.status = SkillStatus.ROLLED_BACK
        self._active.pop(name, None)
        previous.status = SkillStatus.ACTIVE
        self._active[name] = previous
        return previous

    def _previous_active(self, name: str, exclude_version: int) -> Optional[ProcedureSkillVersion]:
        candidates = [
            record for record in self._versions.get(name, {}).values()
            if record.version < exclude_version
            and record.status is not SkillStatus.REJECTED
            and record.status is not SkillStatus.ROLLED_BACK
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda record: record.version)

    # -- lookup -------------------------------------------------------------

    def get(self, name: str, version: int) -> Optional[ProcedureSkillVersion]:
        return self._versions.get(name, {}).get(version)

    def active(self, name: str) -> Optional[ProcedureSkillVersion]:
        return self._active.get(name)

    def active_skill(self, name: str) -> ProcedureSkill:
        """Return the ACTIVE skill content, raising if none/inactive."""
        record = self.active(name)
        if record is None:
            raise ProcedureNotActive(f"no active skill registered for {name!r}")
        return record.content

    def versions(self, name: str) -> list:
        records = self._versions.get(name, {})
        return [records[version] for version in sorted(records)]

    def running_records(self) -> list:
        """Every version whose status permits isolated observation/replay."""
        return [
            record
            for group in self._versions.values()
            for record in group.values()
            if record.status is not SkillStatus.REJECTED
        ]

    def _require(self, name: str, version: int) -> ProcedureSkillVersion:
        record = self.get(name, version)
        if record is None:
            raise ProcedureSkillConflict(
                f"skill {name!r} version {version} is not registered")
        return record