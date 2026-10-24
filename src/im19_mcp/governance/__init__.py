"""Persistent, fail-closed authorization primitives for future mutation adapters.

Do not expose grant issuance through MCP tools. The trusted account portal
must authenticate the human and supply verifiable approval evidence.
"""
from im19_mcp.governance.approvals import (
    ApprovalDenied,
    ApprovalLedger,
    DuplicateExecution,
    ExecutionReservation,
    payload_sha256,
)

__all__ = [
    "ApprovalDenied", "ApprovalLedger", "DuplicateExecution",
    "ExecutionReservation", "payload_sha256",
]
