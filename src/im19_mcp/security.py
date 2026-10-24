"""Fail-closed authorization independent of provider API clients."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Decision(StrEnum):
    ALLOW = "ALLOW"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    SCOPE_REQUIRED = "SCOPE_REQUIRED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    DENY = "DENY"


@dataclass(frozen=True)
class Principal:
    subject: str
    provider: str
    account_id: str
    scopes: frozenset[str]


@dataclass(frozen=True)
class Policy:
    provider: str
    action: str
    scopes: frozenset[str]
    mutates: bool = False


@dataclass(frozen=True)
class Approval:
    subject: str
    provider: str
    account_id: str
    action: str
    resource_id: str
    payload_hash: str
    expires_at: float


def authorize(
    policy: Policy, principal: Principal | None, *,
    subject: str, account_id: str, now: float, resource_id: str = "",
    payload_hash: str = "", approval: Approval | None = None
) -> Decision:
    if not subject or not account_id or not policy.action:
        return Decision.DENY
    if principal is None:
        return Decision.AUTH_REQUIRED
    if not all((principal.subject, principal.provider, principal.account_id)):
        return Decision.DENY
    if (principal.subject, principal.provider, principal.account_id) != (subject, policy.provider, account_id):
        return Decision.DENY
    if not policy.scopes.issubset(principal.scopes):
        return Decision.SCOPE_REQUIRED
    if policy.mutates:
        if not resource_id or not payload_hash:
            return Decision.DENY
        if approval is None:
            return Decision.APPROVAL_REQUIRED
        exact = (
            approval.subject, approval.provider, approval.account_id,
            approval.action, approval.resource_id, approval.payload_hash,
        )
        requested = (subject, policy.provider, account_id, policy.action, resource_id, payload_hash)
        if exact != requested or approval.expires_at <= now:
            return Decision.DENY
    return Decision.ALLOW
