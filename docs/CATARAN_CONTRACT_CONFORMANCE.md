# Cataran Contract Conformance — Provisional Snapshot

This repository pins the common event envelope only from UGAI's proposed Platform Contracts v1.

- Upstream: StrategyG/Universal-Generative-AI PR #34 (Draft, not accepted or merged)
- Upstream commit: `a4b3311f752699db7c411989bcdc7025431d38fd`
- Schema Git blob SHA-1: `5940df93b20712127123a351cb6fdc94649682ee`
- Fixture Git blob SHA-1: `75f2b6e6a91536226e3e4f81c4a3dc382501e9e1`
- Validator: `jsonschema==4.26.0`

CI validates schema syntax, the golden fixture, invalid identifiers/versions, nested credential-like property-key rejection and trace context.

This snapshot does not prove full platform-contract conformance, runtime integration, tenant isolation or production readiness. Update only through a reviewed PR recording exact upstream commit and blob digests; never fetch a floating latest schema during CI.
