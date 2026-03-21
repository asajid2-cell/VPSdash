from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, jsonify, request

from .config import load_config
from .host_agent import HostAgentRuntime, materialize_via_runtime
from .security import utc_now, verify_json_signature_with_key


def create_host_agent_app(root: Path | str | None = None) -> Flask:
    root = Path(root or Path(__file__).resolve().parent.parent)
    config = load_config(root)
    runtime = HostAgentRuntime(config)
    app = Flask(__name__)

    def _verified_payload() -> dict[str, Any]:
        payload = request.get_json(silent=True) or {}
        timestamp = int(request.headers.get("X-VpsH-Agent-Timestamp") or 0)
        signature = request.headers.get("X-VpsH-Agent-Signature", "")
        if not timestamp or abs(int(utc_now().timestamp()) - timestamp) > 300:
            abort(401, description="Agent request timestamp expired.")
        if not verify_json_signature_with_key(config.agent_secret_key, payload, signature):
            abort(403, description="Agent request signature invalid.")
        return payload.get("body") or {}

    @app.get("/healthz")
    def healthz() -> Any:
        return jsonify({"ok": True, "service": "vpsdash-host-agent"})

    @app.post("/inventory")
    def inventory() -> Any:
        body = _verified_payload()
        host = body.get("host") or {}
        return jsonify({"snapshot": runtime.capture_inventory(host)})

    @app.post("/execute")
    def execute() -> Any:
        body = _verified_payload()
        host = body.get("host") or {}
        results = runtime.execute_task_plan(
            host,
            list(body.get("steps") or []),
            dry_run=bool(body.get("dry_run")),
            signature=str(body.get("signature") or ""),
            policy=str(body.get("policy") or ""),
            target_type=str(body.get("target_type") or ""),
            target_id=str(body.get("target_id") or ""),
        )
        return jsonify({"results": results})

    @app.post("/materialize")
    def materialize() -> Any:
        body = _verified_payload()
        host = body.get("host") or {}
        source_path = str(body.get("source_path") or "")
        content = materialize_via_runtime(config, host, source_path)
        filename = Path(str(source_path).replace("~", "")).name or "artifact.bin"
        return Response(
            content,
            mimetype="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return app

