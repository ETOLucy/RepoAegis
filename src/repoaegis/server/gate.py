"""The approval gate: policy in, envelope out, exactly one transition.

Every guarded step goes through ``request``. The policy decides the envelope's
starting status -- ``allow`` and ``deny`` are recorded as decided-by-policy so
the audit trail is identical whether a human or a rule answered, and only
``ask`` leaves a pending envelope for someone to resolve.

Concurrency lives in one place: ``ApprovalRepo.decide`` and
``ApprovalRepo.expire_due`` are single atomic UPDATEs, so of the many parties
that may answer one gate -- two browser tabs, a retry, the expiry sweep -- only
one moves the row, and only that winner applies the effect on the task.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog

from repoaegis.server.models import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Decision,
    TaskStatus,
    payload_digest,
)
from repoaegis.server.policy import Outcome, Policy
from repoaegis.server.state import TaskMachine
from repoaegis.server.storage import ApprovalRepo

log = structlog.get_logger(__name__)

_START: dict[Outcome, ApprovalStatus] = {
    Outcome.ALLOW: ApprovalStatus.APPROVED,
    Outcome.DENY: ApprovalStatus.REJECTED,
    Outcome.ASK: ApprovalStatus.PENDING,
}

_TARGET: dict[Decision, ApprovalStatus] = {
    Decision.APPROVE: ApprovalStatus.APPROVED,
    Decision.REJECT: ApprovalStatus.REJECTED,
}


class ApprovalNotFound(LookupError):
    pass


class PayloadMismatch(ValueError):
    """The content changed between showing it to a human and answering."""


class ApprovalClosed(RuntimeError):
    """Already decided. ``idempotent`` marks a repeat of the same answer."""

    def __init__(self, approval: Approval, *, idempotent: bool) -> None:
        super().__init__(f"approval {approval.id} is {approval.status.value}")
        self.approval = approval
        self.idempotent = idempotent


class ApprovalGate:
    def __init__(
        self,
        approvals: ApprovalRepo,
        machine: TaskMachine,
        *,
        policy: Policy,
        ttl_seconds: float,
        on_task_rejected: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._approvals = approvals
        self._machine = machine
        self._policy = policy
        self._ttl = ttl_seconds
        # A task nobody will work on should not keep a checkout on disk.
        self._on_task_rejected = on_task_rejected

    async def request(
        self, task_id: str, kind: ApprovalKind, payload: dict[str, object], *, subject: str
    ) -> Approval:
        verdict = self._policy.decide(kind.value, subject)
        status = _START[verdict.outcome]
        approval = await self._approvals.create(
            task_id=task_id,
            kind=kind,
            subject=subject,
            payload=dict(payload),
            payload_hash=payload_digest(dict(payload)),
            status=status,
            policy=verdict.policy,
            reason=verdict.reason,
            decided_by=None if status is ApprovalStatus.PENDING else f"policy:{verdict.policy}",
            ttl_seconds=self._ttl,
        )
        await self._machine.emit(
            task_id,
            "approval.requested",
            {
                "approval_id": approval.id,
                "kind": kind.value,
                "subject": subject,
                "payload_hash": approval.payload_hash,
                "outcome": verdict.outcome.value,
                "policy": verdict.policy,
                "reason": verdict.reason,
                "expires_at": approval.expires_at.isoformat(),
            },
        )
        if not approval.is_open:
            await self._settle(approval)
        return approval

    async def decide(
        self,
        approval_id: str,
        decision: Decision,
        *,
        actor: str,
        payload_hash: str | None = None,
    ) -> Approval:
        current = await self._approvals.get(approval_id)
        if current is None:
            raise ApprovalNotFound(approval_id)
        if payload_hash is not None and payload_hash != current.payload_hash:
            raise PayloadMismatch(
                f"approval {approval_id} covers {current.payload_hash}, not {payload_hash}"
            )
        target = _TARGET[decision]
        decided = await self._approvals.decide(approval_id, to=target, decided_by=actor)
        if decided is None:
            # Someone else answered, or the sweep expired it, between our read and write.
            latest = await self._approvals.get(approval_id)
            assert latest is not None
            raise ApprovalClosed(latest, idempotent=latest.status is target)
        await self._settle(decided)
        return decided

    async def sweep_expired(self) -> list[Approval]:
        expired = await self._approvals.expire_due()
        for approval in expired:
            await self._settle(approval)
        return expired

    async def guard(self, approval_id: str, payload: dict[str, object]) -> Approval:
        """Re-check just before executing: right envelope, still approved, same bytes."""
        approval = await self._approvals.get(approval_id)
        if approval is None:
            raise ApprovalNotFound(approval_id)
        if approval.status is not ApprovalStatus.APPROVED:
            raise ApprovalClosed(approval, idempotent=False)
        if approval.payload_hash != payload_digest(dict(payload)):
            raise PayloadMismatch(f"approval {approval_id} does not cover this payload")
        return approval

    async def _settle(self, approval: Approval) -> None:
        """Turn a decided envelope into its effect. Only the winning writer gets here."""
        await self._machine.emit(
            approval.task_id,
            "approval.decided",
            {
                "approval_id": approval.id,
                "kind": approval.kind.value,
                "status": approval.status.value,
                "decided_by": approval.decided_by,
                "reason": approval.reason,
            },
        )
        # Which gate this is decides where the task goes next. A call-level gate
        # (shell, push) resumes the executor instead and moves nothing.
        granted = {
            ApprovalKind.PLAN: TaskStatus.SOLVING,
            ApprovalKind.PATCH: TaskStatus.DELIVERING,
        }.get(approval.kind)
        if granted is None:
            return
        to = granted if approval.status is ApprovalStatus.APPROVED else TaskStatus.REJECTED
        await self._machine.advance(
            approval.task_id, to, payload={"approval_id": approval.id, "reason": approval.reason}
        )
        if to is TaskStatus.REJECTED and self._on_task_rejected is not None:
            await self._on_task_rejected(approval.task_id)
