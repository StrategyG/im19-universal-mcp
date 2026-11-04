"""Conformance tests for Cataran's proposed platform event envelope v1."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "cataran" / "contracts" / "v1" / "platform-event.schema.json"
FIXTURE_PATH = ROOT / "cataran" / "contracts" / "v1" / "fixtures" / "platform-event.valid.json"


class PlatformEventSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(
            cls.schema,
            format_checker=FormatChecker(),
        )

    def assert_valid(self, event: dict[str, Any]) -> None:
        errors = list(self.validator.iter_errors(event))
        self.assertEqual(errors, [], msg="\n".join(error.message for error in errors))

    def assert_invalid(self, event: dict[str, Any]) -> None:
        self.assertFalse(self.validator.is_valid(event))

    def test_schema_is_valid_draft_2020_12(self) -> None:
        Draft202012Validator.check_schema(self.schema)

    def test_canonical_fixture_is_valid(self) -> None:
        self.assert_valid(self.fixture)

    def test_nullable_project_and_service_actor_are_supported(self) -> None:
        event = copy.deepcopy(self.fixture)
        event["project_id"] = None
        event["service_actor"] = None
        event["trace_context"] = None
        self.assert_valid(event)

    def test_valid_workload_actor_and_w3c_trace_context(self) -> None:
        event = copy.deepcopy(self.fixture)
        event["service_actor"] = {
            "service": "im19",
            "identity": "spiffe://cataran/im19",
        }
        event["trace_context"] = {
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "tracestate": "vendor=value",
        }
        self.assert_valid(event)

    def test_required_fields_cannot_be_omitted(self) -> None:
        for field in ("tenant_id", "task_id", "correlation_id", "sequence", "payload"):
            with self.subTest(field=field):
                event = copy.deepcopy(self.fixture)
                del event[field]
                self.assert_invalid(event)

    def test_unknown_top_level_and_identity_fields_are_rejected(self) -> None:
        event = copy.deepcopy(self.fixture)
        event["access_token"] = "must-never-be-present"
        self.assert_invalid(event)

        event = copy.deepcopy(self.fixture)
        event["principal"]["role"] = "platform_admin"
        self.assert_invalid(event)

    def test_blank_tenant_and_project_identifiers_are_rejected(self) -> None:
        for field in ("tenant_id", "project_id"):
            with self.subTest(field=field):
                event = copy.deepcopy(self.fixture)
                event[field] = ""
                self.assert_invalid(event)

    def test_uuid_and_timestamp_formats_are_enforced(self) -> None:
        event = copy.deepcopy(self.fixture)
        event["task_id"] = "not-a-uuid"
        self.assert_invalid(event)

        event = copy.deepcopy(self.fixture)
        event["occurred_at"] = "2026-11-05T10:00:00+07:00"
        self.assert_invalid(event)

    def test_non_positive_sequence_and_major_version_are_rejected(self) -> None:
        event = copy.deepcopy(self.fixture)
        event["sequence"] = 0
        self.assert_invalid(event)

        event = copy.deepcopy(self.fixture)
        event["contract_version"] = "2.0.0"
        self.assert_invalid(event)

    def test_message_type_and_payload_are_constrained(self) -> None:
        event = copy.deepcopy(self.fixture)
        event["message_type"] = "Task.Queued"
        self.assert_invalid(event)

        event = copy.deepcopy(self.fixture)
        event["payload"] = ["queued"]
        self.assert_invalid(event)

    def test_credential_like_fields_are_rejected_recursively(self) -> None:
        forbidden_keys = (
            "access_token",
            "Authorization",
            "refreshToken",
            "api_key",
            "clientSecret",
            "private_key",
            "password",
            "cookie",
            "credential",
            "secret",
        )
        for key in forbidden_keys:
            with self.subTest(key=key):
                event = copy.deepcopy(self.fixture)
                event["payload"] = {
                    "metadata": [
                        {
                            "nested": {
                                key: "must-never-be-serialized",
                            }
                        }
                    ]
                }
                self.assert_invalid(event)

    def test_safe_nested_payload_is_accepted(self) -> None:
        event = copy.deepcopy(self.fixture)
        event["payload"] = {
            "metadata": {
                "source": "notebook",
                "attempt": 2,
                "labels": ["verified", "internal"],
                "context": None,
            }
        }
        self.assert_valid(event)

    def test_issuer_must_be_https_without_userinfo_query_or_fragment(self) -> None:
        for issuer in (
            "http://identity.example.test/",
            "https://user:pass@identity.example.test/",
            "https://identity.example.test/?token=secret",
            "https://identity.example.test/#fragment",
        ):
            with self.subTest(issuer=issuer):
                event = copy.deepcopy(self.fixture)
                event["principal"]["issuer"] = issuer
                self.assert_invalid(event)

    def test_invalid_traceparent_is_rejected(self) -> None:
        event = copy.deepcopy(self.fixture)
        event["trace_context"] = {"traceparent": "not-a-traceparent"}
        self.assert_invalid(event)


if __name__ == "__main__":
    unittest.main()
