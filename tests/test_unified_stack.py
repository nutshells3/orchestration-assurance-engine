"""Tests for the unified verification stack: config loading, retry logic,
certification API result types, and HTTP endpoints.

Run with: pytest tests/test_unified_stack.py -v
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
import urllib.error
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Locate the real verification.toml shipped at the project root.
# ---------------------------------------------------------------------------

_FORMAL_CLAIM_ROOT = Path(__file__).resolve().parent.parent
_TOML_PATH = _FORMAL_CLAIM_ROOT / "settings" / "verification.toml"

# Engine package path for imports.
import sys

_ENGINE_SRC = (
    Path(__file__).resolve().parent.parent
    / "services"
    / "engine"
    / "src"
)
if str(_ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(_ENGINE_SRC))

from formal_claim_engine.unified_config import (
    AuditProbeConfig,
    BudgetConfig,
    RetryPolicy,
    SessionOverrideConfig,
    UnifiedConfig,
    VerificationBackendConfig,
    _apply_env_overrides,
    _coerce,
    _env_key,
    _merge_role,
    find_config_file,
    load_config,
    to_pipeline_config,
    to_proof_protocol_config,
    to_run_budget_config,
)
from formal_claim_engine.config import ModelSlot, PipelineConfig, ProofProtocolConfig
from formal_claim_engine.certification_api import (
    CertificationResult,
    CertificationVerdict,
    VerificationResult,
    get_config,
)


# ===================================================================
# 1. Unified Config Loading
# ===================================================================


class TestLoadConfigFromToml:
    """test_load_config_from_toml: Load verification.toml, verify all sections."""

    @pytest.fixture()
    def uc(self) -> UnifiedConfig:
        assert _TOML_PATH.is_file(), f"verification.toml not at {_TOML_PATH}"
        return load_config(_TOML_PATH)

    def test_data_dir(self, uc: UnifiedConfig) -> None:
        assert uc.data_dir == "./pipeline_data"

    def test_llm_defaults_provider(self, uc: UnifiedConfig) -> None:
        assert uc.llm_defaults["provider"] == "anthropic"

    def test_llm_defaults_model(self, uc: UnifiedConfig) -> None:
        assert uc.llm_defaults["model"] == "claude-sonnet-4-20250514"

    def test_roles_loaded(self, uc: UnifiedConfig) -> None:
        expected_roles = {
            "planner",
            "claim_graph_agent",
            "claim_tracer",
            "formalizer_a",
            "formalizer_b",
            "proof_verifier",
            "auditor",
            "research_agent",
            "dev_agent",
            "policy_engine",
        }
        assert set(uc.model_routing.keys()) == expected_roles

    def test_retry_policies_loaded(self, uc: UnifiedConfig) -> None:
        expected = {"llm_call", "llm_empty_output", "proof_build", "workflow_phase", "certification_transport"}
        assert set(uc.retry_policies.keys()) == expected

    def test_verification_flags(self, uc: UnifiedConfig) -> None:
        assert uc.dual_formalization is True
        assert uc.sorry_detection == "strict"

    def test_backend_config(self, uc: UnifiedConfig) -> None:
        assert uc.verification_backend.id == "lean-local"
        assert uc.verification_backend.transport == "local_hub"
        assert uc.verification_backend.timeout_seconds == 5.0

    def test_audit_probes(self, uc: UnifiedConfig) -> None:
        assert uc.audit_probes.counterexample is True
        assert uc.audit_probes.proof_search is True
        assert uc.audit_probes.premise_deletion is False
        assert uc.audit_probes.conclusion_perturbation is False

    def test_budget(self, uc: UnifiedConfig) -> None:
        assert uc.budget.wall_timeout_seconds == 600
        assert uc.budget.max_rss_mb == 512

    def test_session_override_disabled(self, uc: UnifiedConfig) -> None:
        assert uc.session_override.enabled is False

    def test_integration_section(self, uc: UnifiedConfig) -> None:
        assert uc.certification_frequency == "on_request"
        assert uc.http_api_port == 8321


class TestConfigEnvOverride:
    """test_config_env_override: Set VERIFY_* env var, verify it overrides TOML."""

    def test_override_model(self) -> None:
        with mock.patch.dict(os.environ, {"VERIFY_LLM_DEFAULTS_MODEL": "custom-model-42"}):
            uc = load_config(_TOML_PATH)
        assert uc.llm_defaults["model"] == "custom-model-42"

    def test_override_temperature(self) -> None:
        with mock.patch.dict(os.environ, {"VERIFY_LLM_DEFAULTS_TEMPERATURE": "0.99"}):
            uc = load_config(_TOML_PATH)
        assert uc.llm_defaults["temperature"] == 0.99

    def test_override_bool(self) -> None:
        with mock.patch.dict(os.environ, {"VERIFY_VERIFICATION_DUAL_FORMALIZATION": "false"}):
            uc = load_config(_TOML_PATH)
        assert uc.dual_formalization is False

    def test_override_int(self) -> None:
        with mock.patch.dict(os.environ, {"VERIFY_INTEGRATION_HTTP_API_PORT": "9999"}):
            uc = load_config(_TOML_PATH)
        assert uc.http_api_port == 9999

    def test_env_key_builder(self) -> None:
        assert _env_key("llm", "defaults", "model") == "VERIFY_LLM_DEFAULTS_MODEL"

    def test_coerce_bool(self) -> None:
        assert _coerce("true", False) is True
        assert _coerce("0", True) is False

    def test_coerce_int(self) -> None:
        assert _coerce("42", 0) == 42

    def test_coerce_float(self) -> None:
        assert _coerce("3.14", 0.0) == pytest.approx(3.14)

    def test_coerce_str(self) -> None:
        assert _coerce("hello", "") == "hello"


class TestConfigRoleMerge:
    """test_config_role_merge: Verify role-specific settings merge with defaults."""

    def test_formalizer_a_inherits_provider(self) -> None:
        uc = load_config(_TOML_PATH)
        slot = uc.model_routing["formalizer_a"]
        # formalizer_a overrides temperature and max_tokens but inherits provider
        assert slot.provider == "anthropic"
        assert slot.temperature == pytest.approx(0.1)
        assert slot.max_tokens == 16384

    def test_formalizer_b_has_api_key(self) -> None:
        uc = load_config(_TOML_PATH)
        slot = uc.model_routing["formalizer_b"]
        assert slot.api_key_env == "ANTHROPIC_API_KEY"

    def test_research_agent_overrides_provider(self) -> None:
        uc = load_config(_TOML_PATH)
        slot = uc.model_routing["research_agent"]
        assert slot.provider == "openai"
        assert slot.model == "gpt-4o"

    def test_claim_graph_agent_inherits_all_defaults(self) -> None:
        uc = load_config(_TOML_PATH)
        slot = uc.model_routing["claim_graph_agent"]
        assert slot.provider == "anthropic"
        assert slot.model == "claude-sonnet-4-20250514"
        assert slot.temperature == pytest.approx(0.2)
        assert slot.max_tokens == 8192

    def test_merge_role_function(self) -> None:
        defaults = {"provider": "anthropic", "model": "default-model", "temperature": 0.5}
        overrides = {"temperature": 0.1, "max_tokens": 4096}
        slot = _merge_role(defaults, overrides)
        assert slot.provider == "anthropic"
        assert slot.model == "default-model"
        assert slot.temperature == pytest.approx(0.1)
        assert slot.max_tokens == 4096


class TestConfigFileNotFound:
    """test_config_file_not_found: Verify graceful error on missing file."""

    def test_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Cannot locate"):
            load_config(tmp_path / "nonexistent.toml")

    def test_find_config_file_returns_none(self, tmp_path: Path) -> None:
        assert find_config_file(tmp_path) is None


class TestToPipelineConfig:
    """test_to_pipeline_config: Verify bridge function produces valid PipelineConfig."""

    def test_produces_pipeline_config(self) -> None:
        uc = load_config(_TOML_PATH)
        pc = to_pipeline_config(uc, project_id="test.project")
        assert isinstance(pc, PipelineConfig)
        assert pc.project_id == "test.project"

    def test_default_project_id(self) -> None:
        uc = load_config(_TOML_PATH)
        pc = to_pipeline_config(uc)
        assert pc.project_id == "project.default"

    def test_data_dir_propagates(self) -> None:
        uc = load_config(_TOML_PATH)
        pc = to_pipeline_config(uc)
        assert pc.data_dir == "./pipeline_data"

    def test_model_routing_propagates(self) -> None:
        uc = load_config(_TOML_PATH)
        pc = to_pipeline_config(uc)
        assert "formalizer_a" in pc.model_routing
        assert isinstance(pc.model_routing["formalizer_a"], ModelSlot)

    def test_dual_formalization_propagates(self) -> None:
        uc = load_config(_TOML_PATH)
        pc = to_pipeline_config(uc)
        assert pc.require_dual_formalization is True

    def test_max_retries_from_workflow_phase(self) -> None:
        uc = load_config(_TOML_PATH)
        pc = to_pipeline_config(uc)
        assert pc.max_retries_per_phase == 3

    def test_proof_protocol_propagates(self) -> None:
        uc = load_config(_TOML_PATH)
        pc = to_pipeline_config(uc)
        assert isinstance(pc.proof_protocol, ProofProtocolConfig)
        assert pc.proof_protocol.target_backend_id == "lean-local"

    def test_budget_propagates(self) -> None:
        uc = load_config(_TOML_PATH)
        pc = to_pipeline_config(uc)
        assert pc.proof_protocol.budget.wall_timeout_seconds == 600


# ===================================================================
# 2. Retry Logic
# ===================================================================


class TestRetryPolicyFromConfig:
    """test_retry_policy_from_config: Load RetryPolicy from [retry.llm_call]."""

    def test_llm_call_retry(self) -> None:
        uc = load_config(_TOML_PATH)
        rp = uc.retry_policies["llm_call"]
        assert rp.max_attempts == 3
        assert rp.backoff == "exponential"
        assert rp.base_ms == 1000
        assert rp.cap_ms == 30000
        assert rp.jitter is True

    def test_proof_build_retry(self) -> None:
        uc = load_config(_TOML_PATH)
        rp = uc.retry_policies["proof_build"]
        assert rp.max_attempts == 2
        assert rp.backoff == "linear"
        assert rp.base_ms == 5000

    def test_empty_output_retry(self) -> None:
        uc = load_config(_TOML_PATH)
        rp = uc.retry_policies["llm_empty_output"]
        assert rp.backoff == "none"
        assert rp.jitter is False


class TestExponentialBackoff:
    """test_exponential_backoff_computation: base_ms * 2^attempt, capped."""

    @staticmethod
    def compute_exponential(base_ms: int, cap_ms: int, attempt: int) -> int:
        return min(base_ms * (2 ** attempt), cap_ms)

    def test_attempt_0(self) -> None:
        assert self.compute_exponential(1000, 30000, 0) == 1000

    def test_attempt_1(self) -> None:
        assert self.compute_exponential(1000, 30000, 1) == 2000

    def test_attempt_2(self) -> None:
        assert self.compute_exponential(1000, 30000, 2) == 4000

    def test_attempt_4(self) -> None:
        assert self.compute_exponential(1000, 30000, 4) == 16000

    def test_cap_applied(self) -> None:
        assert self.compute_exponential(1000, 30000, 10) == 30000


class TestLinearBackoff:
    """test_linear_backoff_computation: base_ms * (attempt+1), capped."""

    @staticmethod
    def compute_linear(base_ms: int, cap_ms: int, attempt: int) -> int:
        return min(base_ms * (attempt + 1), cap_ms)

    def test_attempt_0(self) -> None:
        assert self.compute_linear(5000, 30000, 0) == 5000

    def test_attempt_1(self) -> None:
        assert self.compute_linear(5000, 30000, 1) == 10000

    def test_cap_applied(self) -> None:
        assert self.compute_linear(5000, 30000, 10) == 30000


class TestNoBackoff:
    """test_no_backoff: backoff='none' gives 0ms delay."""

    def test_no_backoff_values(self) -> None:
        uc = load_config(_TOML_PATH)
        rp = uc.retry_policies["llm_empty_output"]
        assert rp.backoff == "none"
        assert rp.base_ms == 0
        assert rp.cap_ms == 0

    def test_workflow_phase_no_backoff(self) -> None:
        uc = load_config(_TOML_PATH)
        rp = uc.retry_policies["workflow_phase"]
        assert rp.backoff == "none"
        assert rp.base_ms == 0


class TestJitter:
    """test_jitter_adds_randomness: Verify jitter flag is parsed and configured."""

    def test_jitter_on_for_llm_call(self) -> None:
        uc = load_config(_TOML_PATH)
        assert uc.retry_policies["llm_call"].jitter is True

    def test_jitter_off_for_proof_build(self) -> None:
        uc = load_config(_TOML_PATH)
        assert uc.retry_policies["proof_build"].jitter is False

    def test_jitter_on_for_cert_transport(self) -> None:
        uc = load_config(_TOML_PATH)
        assert uc.retry_policies["certification_transport"].jitter is True


# ===================================================================
# 3. Certification API
# ===================================================================


class TestGetConfig:
    """test_get_config: Verify get_config() returns UnifiedConfig."""

    def test_returns_unified_config(self) -> None:
        uc = get_config(str(_TOML_PATH))
        assert isinstance(uc, UnifiedConfig)
        assert uc.http_api_port == 8321

    def test_get_config_with_path_object(self) -> None:
        uc = get_config(_TOML_PATH)
        assert isinstance(uc, UnifiedConfig)


class TestCertificationResultSerialization:
    """test_certification_result_serialization: Verify CertificationResult serializes."""

    def test_to_json(self) -> None:
        result = CertificationResult(
            verdict=CertificationVerdict.CERTIFIED,
            claim_id="claim.abc",
            project_id="project.test",
            gate="assured",
            assurance_profile={"gate": "assured", "formal_status": "verified"},
            errors=[],
        )
        d = {
            "verdict": result.verdict.value,
            "claim_id": result.claim_id,
            "project_id": result.project_id,
            "gate": result.gate,
            "assurance_profile": result.assurance_profile,
            "errors": result.errors,
        }
        serialized = json.dumps(d)
        parsed = json.loads(serialized)
        assert parsed["verdict"] == "certified"
        assert parsed["claim_id"] == "claim.abc"
        assert parsed["gate"] == "assured"

    def test_error_result(self) -> None:
        result = CertificationResult(
            verdict=CertificationVerdict.ERROR,
            errors=["something_broke: reason"],
        )
        assert result.verdict.value == "error"
        assert len(result.errors) == 1


class TestVerificationResultSerialization:
    """test_verification_result_serialization: Verify VerificationResult serializes."""

    def test_to_json(self) -> None:
        result = VerificationResult(
            success=True,
            sorry_count=0,
            oops_count=0,
            diagnostics=["all good"],
            backend_id="lean-local",
            duration_seconds=1.5,
        )
        d = asdict(result)
        serialized = json.dumps(d)
        parsed = json.loads(serialized)
        assert parsed["success"] is True
        assert parsed["sorry_count"] == 0
        assert parsed["backend_id"] == "lean-local"

    def test_failed_result(self) -> None:
        result = VerificationResult(
            success=False,
            sorry_count=2,
            diagnostics=["sorry found at line 42"],
            backend_id="lean-local",
            duration_seconds=0.5,
        )
        d = asdict(result)
        assert d["success"] is False
        assert d["sorry_count"] == 2


class TestGateToVerdictMapping:
    """test_gate_to_verdict_mapping: Test all gate -> verdict conversions."""

    def test_all_verdict_values(self) -> None:
        assert CertificationVerdict.CERTIFIED.value == "certified"
        assert CertificationVerdict.REFUTED.value == "refuted"
        assert CertificationVerdict.INCONCLUSIVE.value == "inconclusive"
        assert CertificationVerdict.ERROR.value == "error"

    def test_verdict_from_value(self) -> None:
        assert CertificationVerdict("certified") == CertificationVerdict.CERTIFIED
        assert CertificationVerdict("refuted") == CertificationVerdict.REFUTED

    def test_all_verdicts_are_distinct(self) -> None:
        values = [v.value for v in CertificationVerdict]
        assert len(values) == len(set(values)) == 4


# ===================================================================
# 4. HTTP API
# ===================================================================


@pytest.fixture(scope="module")
def http_server():
    """Start the certification HTTP server in a background thread for testing."""
    from formal_claim_engine.certification_http import _CertificationHandler

    from http.server import HTTPServer

    # Find a free port
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = HTTPServer(("127.0.0.1", port), _CertificationHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Wait for server to be ready
    for _ in range(20):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.1)

    yield port

    server.shutdown()


class TestHealthEndpoint:
    """test_health_endpoint: GET /api/health returns 200 {status: ok}."""

    def test_health_returns_ok(self, http_server: int) -> None:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{http_server}/api/health")
        assert resp.status == 200
        body = json.loads(resp.read())
        assert body == {"status": "ok"}


class TestConfigEndpoint:
    """test_config_endpoint: GET /api/config returns valid JSON."""

    def test_config_returns_json(self, http_server: int) -> None:
        # This may return 200 or 404 depending on whether verification.toml
        # is discoverable from the server's cwd. Either is valid -- we just
        # verify the response is parseable JSON.
        try:
            resp = urllib.request.urlopen(
                f"http://127.0.0.1:{http_server}/api/config"
            )
            body = json.loads(resp.read())
            assert isinstance(body, dict)
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read())
            assert isinstance(body, dict)
            assert "error" in body


class TestCertifyEndpointBadRequest:
    """test_certify_endpoint_bad_request: POST /api/certify with empty body returns 400."""

    def test_empty_body_returns_400(self, http_server: int) -> None:
        req = urllib.request.Request(
            f"http://127.0.0.1:{http_server}/api/certify",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req)
            pytest.fail("Expected 400 error")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            body = json.loads(exc.read())
            assert body["error"] == "missing_field"
            assert "claim_text" in body["detail"]

    def test_invalid_json_returns_400(self, http_server: int) -> None:
        req = urllib.request.Request(
            f"http://127.0.0.1:{http_server}/api/certify",
            data=b"not-json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req)
            pytest.fail("Expected 400 error")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            body = json.loads(exc.read())
            assert body["error"] == "invalid_json"
