from __future__ import annotations

import os
import secrets
import sys
import logging
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from werkzeug.exceptions import HTTPException
from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_from_directory, session, url_for

from .authz import has_role
from .logging_utils import configure_file_logging
from .service import VpsDashService

DEVICE_COOKIE_NAME = "vpsdash_device"
LEGACY_DEVICE_COOKIE_NAME = "vps_harmonizer_device"


def _resource_root() -> Path:
    runtime_root = getattr(sys, "_MEIPASS", None)
    if runtime_root:
        return Path(runtime_root)
    return Path(__file__).resolve().parent.parent


def _state_root() -> Path:
    runtime_root = getattr(sys, "_MEIPASS", None)
    if runtime_root:
        if sys.platform == "win32":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path.home() / ".local" / "share"
        root = base / "VPSdash"
        root.mkdir(parents=True, exist_ok=True)
        return root
    return Path(__file__).resolve().parent.parent


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _device_payload() -> dict[str, str]:
    user_agent = request.headers.get("User-Agent", "")
    fingerprint_source = request.headers.get("X-Device-Fingerprint") or f"{user_agent}|{request.remote_addr or ''}"
    return {
        "fingerprint_source": fingerprint_source,
        "device_name": request.headers.get("X-Device-Name") or request.user_agent.platform or "browser",
        "user_agent": user_agent,
        "ip_address": request.remote_addr or "",
    }


def _ensure_csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(24)
        session["csrf_token"] = token
    return token


def _validate_csrf() -> None:
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        if token != session.get("csrf_token"):
            abort(403, description="Invalid CSRF token.")


def login_required(fn: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrapper


def api_login_required(fn: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not session.get("user_id"):
            return jsonify({"error": "authentication required"}), 401
        return fn(*args, **kwargs)

    return wrapper


def api_role_required(*roles: str, require_mfa: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not session.get("user_id"):
                return jsonify({"error": "authentication required"}), 401
            user = getattr(g, "current_user", None)
            if not user or not has_role(user, *roles):
                return jsonify({"error": "forbidden", "required_roles": list(roles)}), 403
            if require_mfa:
                _sensitive_action_guard()
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def _sensitive_action_guard() -> None:
    mfa_at = session.get("mfa_at")
    if not mfa_at:
        abort(403, description="Recent MFA required.")
    last_mfa = datetime.fromisoformat(mfa_at)
    if _now() - last_mfa > timedelta(minutes=int(session.get("recent_mfa_minutes", 15))):
        abort(403, description="MFA freshness window expired.")


def create_app(root: Path | str | None = None, *, resource_root: Path | str | None = None) -> Flask:
    runtime_log_path = configure_file_logging("web")
    state_root = Path(root) if root else _state_root()
    resource_root_path = Path(resource_root) if resource_root else _resource_root()
    static_dir = resource_root_path / "static"
    template_dir = resource_root_path / "templates_web"
    app = Flask(__name__, static_folder=str(static_dir), static_url_path="/static", template_folder=str(template_dir))
    service = VpsDashService(state_root, resource_root=resource_root_path)
    app.secret_key = service.platform.config.flask_secret_key
    secure_cookie = service.platform.config.public_base_url.startswith("https://")
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=secure_cookie,
    )
    app.logger.setLevel(logging.INFO)
    app.logger.info("Web control plane created with log file at %s", runtime_log_path)

    def _web_bootstrap_data() -> dict[str, Any]:
        data = service.platform.bootstrap()
        data["local_machine"] = service.local_machine()
        return data

    def _queued_task_response(task: dict[str, Any], payload: dict[str, Any]) -> Any:
        if payload.get("launch"):
            task = service.launch_platform_task(
                int(task["id"]),
                actor=g.current_user["username"],
                dry_run=bool(payload.get("dry_run", False)),
            )
        return jsonify({"task": task})

    @app.before_request
    def _load_context() -> None:
        _ensure_csrf_token()
        if request.endpoint != "static":
            _validate_csrf()
        g.current_user = None
        user_id = session.get("user_id")
        if user_id:
            g.current_user = service.get_platform_user(int(user_id))

    @app.context_processor
    def _context() -> dict[str, Any]:
        return {
            "csrf_token": session.get("csrf_token", ""),
            "current_user": getattr(g, "current_user", None),
            "product_name": "VPSdash",
            "email_login_verification_enabled": service.platform.config.email_login_verification_enabled,
            "embedded_mode": request.args.get("embedded") == "1",
        }

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException) -> Any:
        if request.path.startswith("/api/"):
            return jsonify({"error": exc.description or exc.name, "status": exc.code}), exc.code
        return exc

    @app.errorhandler(Exception)
    def _unexpected_error(exc: Exception) -> Any:
        if request.path.startswith("/api/"):
            app.logger.exception("Unhandled API error")
            message = str(exc).strip() or "Internal server error."
            return jsonify({"error": message, "status": 500}), 500
        app.logger.exception("Unhandled request error")
        return "Internal Server Error", 500

    @app.get("/")
    def index() -> Any:
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return redirect(url_for("dashboard"))

    @app.route("/login", methods=["GET", "POST"])
    def login() -> Any:
        if request.method == "GET":
            return render_template("login.html", error=None)

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        result = service.authenticate_user(username, password, _device_payload(), actor="web")
        if result.challenge_required:
            session["pending_challenge_id"] = result.challenge_id
            session["pending_username"] = username
            return redirect(url_for("verify_login"))
        if not result.ok:
            return render_template("login.html", error=result.message), 401

        session["user_id"] = int(result.user["id"])
        session["mfa_at"] = _now().isoformat()
        session["recent_mfa_minutes"] = service.platform.config.recent_mfa_minutes
        response = redirect(url_for("dashboard"))
        if result.trusted_device_token:
            response.set_cookie(DEVICE_COOKIE_NAME, result.trusted_device_token, httponly=True, samesite="Lax", secure=secure_cookie)
        return response

    @app.route("/verify", methods=["GET", "POST"])
    def verify_login() -> Any:
        if not service.platform.config.email_login_verification_enabled:
            session.pop("pending_challenge_id", None)
            session.pop("pending_username", None)
            return redirect(url_for("login"))
        challenge_id = session.get("pending_challenge_id")
        if not challenge_id:
            return redirect(url_for("login"))
        if request.method == "GET":
            return render_template("verify.html", error=None)

        code = request.form.get("code", "").strip()
        result = service.verify_login_challenge(int(challenge_id), code, _device_payload(), actor="web")
        if not result.ok:
            return render_template("verify.html", error=result.message), 401
        session.pop("pending_challenge_id", None)
        session.pop("pending_username", None)
        session["user_id"] = int(result.user["id"])
        session["mfa_at"] = _now().isoformat()
        session["recent_mfa_minutes"] = service.platform.config.recent_mfa_minutes
        response = redirect(url_for("dashboard"))
        if result.trusted_device_token:
            response.set_cookie(DEVICE_COOKIE_NAME, result.trusted_device_token, httponly=True, samesite="Lax", secure=secure_cookie)
        return response

    @app.get("/logout")
    def logout() -> Any:
        session.clear()
        response = redirect(url_for("login"))
        response.delete_cookie(DEVICE_COOKIE_NAME)
        response.delete_cookie(LEGACY_DEVICE_COOKIE_NAME)
        return response

    @app.get("/dashboard")
    @login_required
    def dashboard() -> Any:
        data = _web_bootstrap_data()
        return render_template("dashboard.html", data=data)

    @app.get("/api/bootstrap")
    @api_role_required("viewer")
    def bootstrap() -> Any:
        return jsonify(_web_bootstrap_data())

    @app.get("/api/users")
    @api_role_required("viewer")
    def list_users() -> Any:
        return jsonify({"users": service.list_users()})

    @app.post("/api/users")
    @api_role_required("owner", require_mfa=True)
    def create_user() -> Any:
        payload = request.get_json(silent=True) or {}
        return jsonify({"user": service.create_user(payload, actor=g.current_user["username"])})

    @app.post("/api/hosts")
    @api_role_required("operator")
    def upsert_host() -> Any:
        payload = request.get_json(silent=True) or {}
        return jsonify({"host": service.upsert_platform_host(payload, actor=g.current_user["username"])})

    @app.post("/api/hosts/<int:host_id>/inventory")
    @api_role_required("operator")
    def capture_host_inventory(host_id: int) -> Any:
        return jsonify({"host": service.capture_platform_host_inventory(host_id, actor=g.current_user["username"])})

    @app.post("/api/hosts/<int:host_id>/prepare")
    @api_role_required("operator", require_mfa=True)
    def queue_prepare_host(host_id: int) -> Any:
        payload = request.get_json(silent=True) or {}
        task = service.queue_prepare_platform_host(host_id, actor=g.current_user["username"])
        return _queued_task_response(task, payload)

    @app.delete("/api/hosts/<int:host_id>")
    @api_role_required("operator", require_mfa=True)
    def delete_host(host_id: int) -> Any:
        service.delete_platform_host(host_id, actor=g.current_user["username"])
        return jsonify({"ok": True})

    @app.post("/api/networks")
    @api_role_required("operator")
    def upsert_network() -> Any:
        payload = request.get_json(silent=True) or {}
        return jsonify({"network": service.upsert_network(payload, actor=g.current_user["username"])})

    @app.post("/api/networks/<int:network_id>/apply")
    @api_role_required("operator", require_mfa=True)
    def queue_network_apply(network_id: int) -> Any:
        payload = request.get_json(silent=True) or {}
        task = service.queue_network_apply(network_id, actor=g.current_user["username"])
        return _queued_task_response(task, payload)

    @app.post("/api/networks/<int:network_id>/runtime-delete")
    @api_role_required("operator", require_mfa=True)
    def queue_network_runtime_delete(network_id: int) -> Any:
        payload = request.get_json(silent=True) or {}
        task = service.queue_network_delete_runtime(network_id, actor=g.current_user["username"])
        return _queued_task_response(task, payload)

    @app.delete("/api/networks/<int:network_id>")
    @api_role_required("operator", require_mfa=True)
    def delete_network(network_id: int) -> Any:
        service.delete_platform_network(network_id, actor=g.current_user["username"])
        return jsonify({"ok": True})

    @app.post("/api/providers")
    @api_role_required("owner", require_mfa=True)
    def upsert_provider() -> Any:
        payload = request.get_json(silent=True) or {}
        return jsonify({"provider": service.upsert_backup_provider(payload, actor=g.current_user["username"])})

    @app.post("/api/providers/<int:provider_id>/test")
    @api_role_required("operator")
    def test_provider(provider_id: int) -> Any:
        return jsonify(service.test_backup_provider(provider_id))

    @app.delete("/api/providers/<int:provider_id>")
    @api_role_required("owner", require_mfa=True)
    def delete_provider(provider_id: int) -> Any:
        service.delete_platform_backup_provider(provider_id, actor=g.current_user["username"])
        return jsonify({"ok": True})

    @app.post("/api/doplets")
    @api_role_required("operator")
    def upsert_doplet() -> Any:
        payload = request.get_json(silent=True) or {}
        return jsonify({"doplet": service.upsert_doplet(payload, actor=g.current_user["username"])})

    @app.post("/api/doplets/<int:doplet_id>/create")
    @api_role_required("operator", require_mfa=True)
    def queue_create_doplet(doplet_id: int) -> Any:
        payload = request.get_json(silent=True) or {}
        task = service.queue_doplet_create(doplet_id, actor=g.current_user["username"])
        return _queued_task_response(task, payload)

    @app.post("/api/doplets/<int:doplet_id>/lifecycle/<action>")
    @api_role_required("operator", require_mfa=True)
    def queue_doplet_lifecycle(doplet_id: int, action: str) -> Any:
        payload = request.get_json(silent=True) or {}
        task = service.queue_doplet_lifecycle(doplet_id, action, actor=g.current_user["username"])
        return _queued_task_response(task, payload)

    @app.get("/api/doplets/<int:doplet_id>/terminal")
    @api_role_required("viewer")
    def describe_doplet_terminal(doplet_id: int) -> Any:
        return jsonify({"terminal": service.describe_doplet_terminal(doplet_id)})

    @app.post("/api/doplets/<int:doplet_id>/open-terminal")
    @api_role_required("operator", require_mfa=True)
    def open_doplet_terminal(doplet_id: int) -> Any:
        return jsonify({"terminal": service.open_doplet_terminal(doplet_id, actor=g.current_user["username"])})

    @app.post("/api/doplets/<int:doplet_id>/resize")
    @api_role_required("operator", require_mfa=True)
    def queue_resize_doplet(doplet_id: int) -> Any:
        payload = request.get_json(silent=True) or {}
        task = service.queue_doplet_resize(doplet_id, payload, actor=g.current_user["username"])
        return _queued_task_response(task, payload)

    @app.post("/api/doplets/<int:doplet_id>/backup")
    @api_role_required("operator", require_mfa=True)
    def queue_backup(doplet_id: int) -> Any:
        payload = request.get_json(silent=True) or {}
        provider_ids = payload.get("provider_ids")
        task = service.queue_doplet_backup(doplet_id, provider_ids=provider_ids, actor=g.current_user["username"])
        return _queued_task_response(task, payload)

    @app.post("/api/doplets/<int:doplet_id>/snapshot")
    @api_role_required("operator", require_mfa=True)
    def queue_snapshot(doplet_id: int) -> Any:
        payload = request.get_json(silent=True) or {}
        task = service.queue_doplet_snapshot(
            doplet_id,
            snapshot_name=payload.get("snapshot_name"),
            actor=g.current_user["username"],
        )
        return _queued_task_response(task, payload)

    @app.post("/api/doplets/<int:doplet_id>/clone")
    @api_role_required("operator", require_mfa=True)
    def queue_clone(doplet_id: int) -> Any:
        payload = request.get_json(silent=True) or {}
        task = service.queue_doplet_clone(doplet_id, payload, actor=g.current_user["username"])
        return _queued_task_response(task, payload)

    @app.delete("/api/doplets/<int:doplet_id>")
    @api_role_required("operator", require_mfa=True)
    def delete_doplet(doplet_id: int) -> Any:
        service.delete_platform_doplet(doplet_id, actor=g.current_user["username"])
        return jsonify({"ok": True})

    @app.get("/api/backups")
    @api_role_required("viewer")
    def list_backups() -> Any:
        return jsonify({"backups": service.list_platform_backups()})

    @app.post("/api/backups/<int:backup_id>/verify")
    @api_role_required("operator", require_mfa=True)
    def verify_backup(backup_id: int) -> Any:
        return jsonify(service.verify_platform_backup(backup_id, actor=g.current_user["username"]))

    @app.get("/api/snapshots")
    @api_role_required("viewer")
    def list_snapshots() -> Any:
        return jsonify({"snapshots": service.list_platform_snapshots()})

    @app.post("/api/snapshots/<int:snapshot_id>/restore")
    @api_role_required("operator", require_mfa=True)
    def restore_snapshot(snapshot_id: int) -> Any:
        payload = request.get_json(silent=True) or {}
        task = service.queue_snapshot_restore(snapshot_id, payload, actor=g.current_user["username"])
        return _queued_task_response(task, payload)

    @app.get("/api/tasks")
    @api_role_required("viewer")
    def list_tasks() -> Any:
        return jsonify({"tasks": service.list_platform_tasks()})

    @app.post("/api/tasks/<int:task_id>/run")
    @api_role_required("operator", require_mfa=True)
    def run_task(task_id: int) -> Any:
        payload = request.get_json(silent=True) or {}
        return jsonify({"task": service.run_platform_task(task_id, actor=g.current_user["username"], dry_run=bool(payload.get("dry_run", False)))})

    @app.post("/api/tasks/<int:task_id>/launch")
    @api_role_required("operator", require_mfa=True)
    def launch_task(task_id: int) -> Any:
        payload = request.get_json(silent=True) or {}
        return jsonify({"task": service.launch_platform_task(task_id, actor=g.current_user["username"], dry_run=bool(payload.get("dry_run", False)))})

    @app.post("/api/tasks/<int:task_id>/cancel")
    @api_role_required("operator", require_mfa=True)
    def cancel_task(task_id: int) -> Any:
        return jsonify({"task": service.cancel_platform_task(task_id, actor=g.current_user["username"])})

    @app.post("/api/tasks/<int:task_id>/retry")
    @api_role_required("operator", require_mfa=True)
    def retry_task(task_id: int) -> Any:
        return jsonify({"task": service.retry_platform_task(task_id, actor=g.current_user["username"])})

    @app.post("/api/maintenance/backups/run")
    @api_role_required("operator", require_mfa=True)
    def run_backup_scheduler() -> Any:
        return jsonify(service.run_backup_scheduler(actor=g.current_user["username"]))

    @app.post("/api/maintenance/backups/prune")
    @api_role_required("owner", require_mfa=True)
    def prune_backups() -> Any:
        return jsonify(service.prune_platform_backups(actor=g.current_user["username"]))

    @app.get("/api/hosts/<int:host_id>/acceptance")
    @api_role_required("viewer")
    def host_acceptance(host_id: int) -> Any:
        return jsonify(service.host_acceptance_report(host_id))

    @app.get("/api/audit")
    @api_role_required("viewer")
    def list_audit() -> Any:
        return jsonify({"audit": service.list_platform_audit()})

    @app.get("/legacy")
    def legacy() -> Any:
        return send_from_directory(static_dir, "index.html")

    return app

