# Runner Fixtures

These fixtures drive the reproducible Isabelle runner smoke layer.

- `sessions/definitional`: definition-only toy session
- `sessions/locale-based`: locale-scoped assumption carrier
- `sessions/sorry-containing`: intentionally incomplete theorem
- `sessions/suspicious`: theorem body reused by adversarial harness checks
- `audit-request.json`: composite runner audit request
- `profile-request.json`: assurance-profile request that reuses the audit request

Tests write actual outputs to `.tmp/runner-fixture-results/` before comparing
them to the expected JSON fixtures in this directory.
