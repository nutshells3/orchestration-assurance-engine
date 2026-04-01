"""Lightweight HTTP server wrapping the certification API.

Provides four endpoints:

- ``POST /api/certify``  -- full pipeline via ``certified()``
- ``POST /api/verify``   -- proof-only via ``verify_only()``
- ``GET  /api/config``   -- current ``UnifiedConfig``
- ``GET  /api/health``   -- liveness probe
"""

from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import asdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

from .certification_api import (
    CertificationResult,
    CertificationVerdict,
    VerificationResult,
    certified,
    get_config,
    verify_only,
)


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _json_default(obj: Any) -> Any:
    """Fallback serialiser for dataclasses and enums."""
    if hasattr(obj, "value"):
        return obj.value
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, default=_json_default, ensure_ascii=False)


def _certification_result_to_dict(result: CertificationResult) -> dict[str, Any]:
    d: dict[str, Any] = {
        "verdict": result.verdict.value,
        "claim_id": result.claim_id,
        "project_id": result.project_id,
        "gate": result.gate,
        "assurance_profile": result.assurance_profile,
        "dual_formalization": result.dual_formalization,
        "audit": result.audit,
        "verification_a": asdict(result.verification_a) if result.verification_a else None,
        "verification_b": asdict(result.verification_b) if result.verification_b else None,
        "errors": result.errors,
    }
    return d


def _verification_result_to_dict(result: VerificationResult) -> dict[str, Any]:
    return asdict(result)


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class _CertificationHandler(BaseHTTPRequestHandler):
    """Handles requests for the certification HTTP API."""

    def _send_json(self, status: int, body: Any) -> None:
        payload = _json_dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw)

    # -- routing -----------------------------------------------------------

    def do_GET(self) -> None:
        path = self.path.split("?")[0].rstrip("/")
        if path == "/api/health":
            self._handle_health()
        elif path == "/api/config":
            self._handle_get_config()
        else:
            self._send_json(404, {"error": "not_found", "path": self.path})

    def do_POST(self) -> None:
        path = self.path.split("?")[0].rstrip("/")
        if path == "/api/certify":
            self._handle_certify()
        elif path == "/api/verify":
            self._handle_verify()
        else:
            self._send_json(404, {"error": "not_found", "path": self.path})

    # -- handlers ----------------------------------------------------------

    def _handle_health(self) -> None:
        self._send_json(200, {"status": "ok"})

    def _handle_get_config(self) -> None:
        try:
            uc = get_config()
            self._send_json(200, asdict(uc))
        except FileNotFoundError as exc:
            self._send_json(404, {"error": "config_not_found", "detail": str(exc)})
        except Exception as exc:
            self._send_json(500, {
                "error": "config_load_error",
                "detail": str(exc),
                "traceback": traceback.format_exc(),
            })

    def _handle_certify(self) -> None:
        try:
            body = self._read_json_body()
        except Exception as exc:
            self._send_json(400, {"error": "invalid_json", "detail": str(exc)})
            return

        claim_text = body.get("claim_text") or body.get("claim") or ""
        if not claim_text:
            self._send_json(400, {"error": "missing_field", "detail": "claim_text is required"})
            return

        config_overrides = body.get("config_overrides")
        domain = str(body.get("domain", "development"))
        project_name = body.get("project_name")

        try:
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    certified(
                        claim_text,
                        config_overrides=config_overrides,
                        domain=domain,
                        project_name=project_name,
                    )
                )
            finally:
                loop.close()

            status = 200 if result.verdict != CertificationVerdict.ERROR else 500
            self._send_json(status, _certification_result_to_dict(result))
        except Exception as exc:
            self._send_json(500, {
                "error": "certify_failed",
                "detail": str(exc),
                "traceback": traceback.format_exc(),
            })

    def _handle_verify(self) -> None:
        try:
            body = self._read_json_body()
        except Exception as exc:
            self._send_json(400, {"error": "invalid_json", "detail": str(exc)})
            return

        proof_source = body.get("proof_source") or body.get("source") or ""
        if not proof_source:
            self._send_json(400, {"error": "missing_field", "detail": "proof_source is required"})
            return

        backend = body.get("backend")
        config_overrides = body.get("config_overrides")

        try:
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    verify_only(
                        proof_source,
                        backend=backend,
                        config_overrides=config_overrides,
                    )
                )
            finally:
                loop.close()

            status = 200 if result.success else 422
            self._send_json(status, _verification_result_to_dict(result))
        except Exception as exc:
            self._send_json(500, {
                "error": "verify_failed",
                "detail": str(exc),
                "traceback": traceback.format_exc(),
            })

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stderr logging; callers can add their own."""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def serve(port: int = 8321, *, bind: str = "127.0.0.1") -> None:
    """Start the certification HTTP server.

    Blocks until interrupted.  Default port matches
    ``UnifiedConfig.http_api_port``.
    """
    server = HTTPServer((bind, port), _CertificationHandler)
    print(f"certification-http listening on {bind}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Certification HTTP server")
    parser.add_argument("--port", type=int, default=8321)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()
    serve(port=args.port, bind=args.bind)
