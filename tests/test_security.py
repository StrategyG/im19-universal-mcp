import pytest

from im19_mcp.security import Approval, Decision, Policy, Principal, authorize


def principal(scopes=()):
    return Principal("u1", "google", "channel1", frozenset(scopes))


def decide(policy, p=None, **kwargs):
    return authorize(policy, principal() if p is None else p, subject="u1", account_id="channel1",
                     now=100, **kwargs)


def test_read_only_allowed():
    assert decide(Policy("google", "youtube_get_channel", frozenset())) == Decision.ALLOW


def test_scope_denied():
    assert decide(Policy("google", "youtube_get_analytics", frozenset({"analytics"}))) == Decision.SCOPE_REQUIRED


def test_missing_identity_denied():
    assert authorize(Policy("google", "read", frozenset()), None,
                     subject="u1", account_id="channel1", now=100) == Decision.AUTH_REQUIRED


@pytest.mark.parametrize("p", [
    Principal("u2", "google", "channel1", frozenset()),
    Principal("u1", "meta", "channel1", frozenset()),
    Principal("u1", "google", "channel2", frozenset()),
])
def test_cross_boundary_denied(p):
    assert decide(Policy("google", "read", frozenset()), p=p) == Decision.DENY


def test_mutation_needs_exact_approval():
    policy = Policy("google", "youtube_publish", frozenset(), True)
    assert decide(policy, resource_id="video1", payload_hash="abc") == Decision.APPROVAL_REQUIRED
    ok = Approval("u1", "google", "channel1", "youtube_publish", "video1", "abc", 101)
    assert decide(policy, resource_id="video1", payload_hash="abc", approval=ok) == Decision.ALLOW
    assert decide(policy, resource_id="video2", payload_hash="abc", approval=ok) == Decision.DENY
    assert decide(policy, resource_id="video1", payload_hash="def", approval=ok) == Decision.DENY
    assert authorize(policy, principal(), subject="u1", account_id="channel1",
                     now=101, resource_id="video1", payload_hash="abc", approval=ok) == Decision.DENY


def test_mutation_without_payload_is_denied():
    assert decide(Policy("google", "delete", frozenset(), True)) == Decision.DENY

@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), True, -1])
def test_approval_with_invalid_expiry_is_never_allowed(invalid):
    from im19_mcp.security import Approval, Decision, Policy, Principal, authorize
    policy = Policy("drive", "write", frozenset(), mutates=True)
    principal = Principal("alice", "drive", "account", frozenset())
    approval = Approval("alice", "drive", "account", "write", "doc", "hash", invalid)
    assert authorize(policy, principal, subject="alice", account_id="account", now=100,
                     resource_id="doc", payload_hash="hash", approval=approval) == Decision.DENY
