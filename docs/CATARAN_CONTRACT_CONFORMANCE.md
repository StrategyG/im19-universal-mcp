# Cataran Contract Conformance — Provisional Snapshot

This repository pins the common event envelope only from UGAI's proposed Platform Contracts v1.

- Upstream: StrategyG/Universal-Generative-AI PR #33 (Draft, not accepted or merged)
- Upstream commit: `0a8c70daa6bfd32a90dfb6b734bdafe0b66a40ce`
- Schema Git blob SHA-1: `b12c771430c56bd933de48b7c180e347e53db170`
- Fixture Git blob SHA-1: `fcf9b50bed62fa27dfcad59dd04ddfc46fc52009`
- Validator: `jsonschema==4.26.0`

CI validates schema syntax, the golden fixture, invalid identifiers/versions, nested credential-like property-key rejection and trace context.

This snapshot does not prove full platform-contract conformance, runtime integration, tenant isolation or production readiness. Update only through a reviewed PR recording exact upstream commit and blob digests; never fetch a floating latest schema during CI.
