"""REST API v1 – Flask Blueprint.

60+ endpoints covering: auth, users, discovery, backup, AI planning,
evacuation, tear-down, hydration, move-back, validation, pipeline,
health / config.  Returns standard envelope:
    {status, data, message, timestamp, request_id}
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from flask import Blueprint, Response, current_app, g, jsonify, request, stream_with_context

from azure_local_deploy.auth import (
    APIKeyStore,
    UserStore,
    check_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    is_token_blacklisted,
    require_role,
    validate_password_strength,
)
from azure_local_deploy.models import (
    JobState,
    PipelineJob,
    RebuildReport,
    UserRole,
)
from azure_local_deploy.rebuild import (
    REBUILD_STAGES,
    backup_vms,
    discover_workloads,
    map_dependencies,
    run_rebuild_pipeline,
)
from azure_local_deploy.utils import get_logger

log = get_logger(__name__)

api = Blueprint("api_v1", __name__, url_prefix="/api/v1")

# ---------------------------------------------------------------------------
# Rate limiting (simple in-memory, per-IP)
# ---------------------------------------------------------------------------

_rate_limits: dict[str, list[float]] = {}
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX_LOGIN = 10  # max login attempts per window
_RATE_LIMIT_MAX_DEFAULT = 120  # max requests per window


def _check_rate_limit(key: str, max_requests: int = _RATE_LIMIT_MAX_DEFAULT) -> bool:
    """Return True if the request should be rate-limited (rejected)."""
    now = time.time()
    timestamps = _rate_limits.get(key, [])
    timestamps = [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]
    if len(timestamps) >= max_requests:
        _rate_limits[key] = timestamps
        return True
    timestamps.append(now)
    _rate_limits[key] = timestamps
    return False

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _envelope(
    data: Any = None,
    message: str = "ok",
    status: str = "success",
    code: int = 200,
) -> tuple[Response, int]:
    """Standard JSON response wrapper."""
    return jsonify({
        "status": status,
        "data": data,
        "message": message,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "request_id": getattr(request, "request_id", uuid.uuid4().hex[:12]),
    }), code


def _error(message: str, code: int = 400) -> tuple[Response, int]:
    return _envelope(data=None, message=message, status="error", code=code)


def _get_jobs() -> dict[str, PipelineJob]:
    """Return the shared jobs dict from the app config."""
    return current_app.config.setdefault("REBUILD_JOBS", {})


def _get_config() -> dict[str, Any]:
    """Load config from the app-level config path."""
    cfg_path = current_app.config.get("REBUILD_CONFIG_PATH")
    if cfg_path and Path(cfg_path).exists():
        with open(cfg_path) as f:
            return yaml.safe_load(f)
    return {}


def _safe_error(exc: Exception, fallback: str = "Internal server error") -> str:
    """Return a sanitised error message — never expose internals in production."""
    msg = str(exc)
    # Strip paths and stack traces
    if any(kw in msg.lower() for kw in ("traceback", "errno", "file ", "/home/", "c:\\")):
        log.error("Suppressed error detail: %s", msg)
        return fallback
    return msg if len(msg) < 500 else msg[:500] + "..."


# ---------------------------------------------------------------------------
# Middleware – assign request id
# ---------------------------------------------------------------------------

@api.before_request
def _assign_request_id():
    request.request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
    # Rate limit login attempts more aggressively
    client_ip = request.remote_addr or "unknown"
    if request.path == "/api/v1/auth/login" and request.method == "POST":
        if _check_rate_limit(f"login:{client_ip}", _RATE_LIMIT_MAX_LOGIN):
            return _error("Too many login attempts. Try again later.", 429)
    else:
        if _check_rate_limit(f"api:{client_ip}", _RATE_LIMIT_MAX_DEFAULT):
            return _error("Rate limit exceeded. Try again later.", 429)


@api.after_request
def _security_headers(response: Response) -> Response:
    """Add security headers to every API response."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Request-ID"] = getattr(request, "request_id", "")
    return response


# ═══════════════════════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@api.route("/auth/login", methods=["POST"])
def auth_login():
    """Authenticate and return JWT tokens."""
    body = request.get_json(silent=True) or {}
    username = body.get("username", "")
    password = body.get("password", "")

    if not username or not password:
        return _error("username and password required", 400)

    store = UserStore()
    user = store.authenticate(username, password)
    if not user:
        # Constant-time response to prevent user enumeration
        return _error("Invalid credentials or account locked", 401)

    role_val = user.role.value if isinstance(user.role, UserRole) else user.role
    access = create_access_token(user.id, user.username, role_val)
    refresh = create_refresh_token(user.id, user.username, role_val)

    return _envelope({
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": 3600,
        "must_change_password": user.must_change_password,
    }, message="Login successful")


@api.route("/auth/refresh", methods=["POST"])
def auth_refresh():
    """Exchange refresh token for new access token."""
    body = request.get_json(silent=True) or {}
    refresh_token = body.get("refresh_token", "")
    if not refresh_token:
        return _error("refresh_token required", 400)

    try:
        payload = decode_token(refresh_token)
    except Exception:
        return _error("Invalid or expired refresh token", 401)

    if not payload or payload.get("type") != "refresh":
        return _error("Invalid or expired refresh token", 401)

    if is_token_blacklisted(payload.get("jti", "")):
        return _error("Token has been revoked", 401)

    # Verify user still exists and is active
    store = UserStore()
    user = store.get_by_id(int(payload["sub"]))
    if not user or not user.is_active:
        return _error("User not found or inactive", 401)

    role_val = user.role.value if isinstance(user.role, UserRole) else user.role
    access = create_access_token(user.id, user.username, role_val)
    return _envelope({"access_token": access, "token_type": "Bearer", "expires_in": 3600})


@api.route("/auth/change-password", methods=["POST"])
def auth_change_password():
    """Change the current user's password."""
    body = request.get_json(silent=True) or {}
    old_pw = body.get("old_password", "")
    new_pw = body.get("new_password", "")
    if not old_pw or not new_pw:
        return _error("old_password and new_password required", 400)

    user: Any = getattr(g, "current_user", None)
    if user is None:
        return _error("Authentication required", 401)

    store = UserStore()
    user = store.get_by_id(user.id)
    if not user:
        return _error("User not found", 404)

    if not check_password(old_pw, user.password_hash):
        return _error("Incorrect current password", 403)

    strength_err = validate_password_strength(new_pw)
    if strength_err:
        return _error(strength_err, 400)

    # Check password history
    for old_hash in (user.password_history or []):
        if check_password(new_pw, old_hash):
            return _error("Cannot reuse a recent password", 400)

    # Save old hash to history
    user.password_history = (user.password_history or [])[-4:] + [user.password_hash]
    user.password_hash = hash_password(new_pw)
    user.must_change_password = False
    store.update(user)
    return _envelope(message="Password changed")


# ═══════════════════════════════════════════════════════════════════════════
# USER MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

@api.route("/users", methods=["GET"])
@require_role(UserRole.ADMIN)
def list_users():
    """List all users."""
    store = UserStore()
    users = [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role.value if isinstance(u.role, UserRole) else u.role,
            "is_active": u.is_active,
            "must_change_password": u.must_change_password,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login": u.last_login.isoformat() if u.last_login else None,
        }
        for u in store.get_all()
    ]
    return _envelope(users)


@api.route("/users", methods=["POST"])
@require_role(UserRole.ADMIN)
def create_user():
    """Create a new user."""
    body = request.get_json(silent=True) or {}
    username = body.get("username", "")
    password = body.get("password", "")
    role = body.get("role", "operator")

    if not username or not password:
        return _error("username and password required", 400)

    from azure_local_deploy.auth import validate_password_strength
    err = validate_password_strength(password)
    if err:
        return _error(err, 400)

    store = UserStore()
    try:
        user = store.create(username, password, UserRole(role))
    except ValueError as exc:
        return _error(str(exc), 409)

    return _envelope({
        "id": user.id,
        "username": user.username,
        "role": user.role.value,
    }, message="User created", code=201)


@api.route("/users/<int:user_id>", methods=["DELETE"])
@require_role(UserRole.ADMIN)
def delete_user(user_id: int):
    """Deactivate a user (soft delete)."""
    store = UserStore()
    user = store.get_by_id(user_id)
    if not user:
        return _error("User not found", 404)
    # Prevent deleting the last admin
    all_admins = [u for u in store.get_all()
                  if (u.role == UserRole.ADMIN or u.role == "admin") and u.is_active and u.id != user_id]
    if not all_admins and (user.role == UserRole.ADMIN or user.role == "admin"):
        return _error("Cannot deactivate the last admin user", 400)
    user.is_active = False
    store.update(user)
    return _envelope(message="User deactivated")


# ═══════════════════════════════════════════════════════════════════════════
# API KEY MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

@api.route("/api-keys", methods=["GET"])
@require_role(UserRole.ADMIN)
def list_api_keys():
    """List all API keys (hashed)."""
    ks = APIKeyStore()
    keys = [
        {
            "id": k.id,
            "user_id": k.user_id,
            "name": k.name,
            "is_active": k.is_active,
            "permissions": k.permissions,
            "expires_at": k.expires_at.isoformat() if k.expires_at else None,
            "last_used": k.last_used.isoformat() if k.last_used else None,
        }
        for k in ks.get_all()
    ]
    return _envelope(keys)


@api.route("/api-keys", methods=["POST"])
@require_role(UserRole.ADMIN)
def create_api_key():
    """Create a new API key. Returns the full key ONCE."""
    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id")
    name = body.get("name", "")
    permissions = body.get("permissions", ["rebuild:read", "rebuild:execute"])

    if user_id is None:
        return _error("user_id required", 400)

    ks = APIKeyStore()
    full_key, key = ks.create(user_id, name, permissions)
    return _envelope({
        "id": key.id,
        "key": full_key,
        "name": key.name,
        "permissions": key.permissions,
    }, message="API key created – store the key securely, it will not be shown again", code=201)


@api.route("/api-keys/<key_id>", methods=["DELETE"])
@require_role(UserRole.ADMIN)
def revoke_api_key(key_id: str):
    """Revoke an API key."""
    ks = APIKeyStore()
    ok = ks.revoke(key_id)
    if not ok:
        return _error("Key not found", 404)
    return _envelope(message="API key revoked")


# ═══════════════════════════════════════════════════════════════════════════
# DISCOVERY
# ═══════════════════════════════════════════════════════════════════════════

@api.route("/discover", methods=["POST"])
@require_role(UserRole.OPERATOR)
def api_discover():
    """Discover VMs on source cluster."""
    body = request.get_json(silent=True) or {}
    host = body.get("host", "")
    user = body.get("username", "Administrator")
    password = body.get("password", "")

    if not host or not password:
        return _error("host and password required", 400)

    try:
        vms = discover_workloads(host, user, password, ssh_port=body.get("ssh_port", 22))
        vms = map_dependencies(vms)
        data = [
            {
                "name": vm.name,
                "node": vm.node,
                "state": vm.state,
                "category": vm.category,
                "cpu_count": vm.cpu_count,
                "memory_gb": vm.memory_gb,
                "total_disk_gb": vm.total_disk_gb,
                "depends_on": vm.depends_on,
                "depended_by": vm.depended_by,
            }
            for vm in vms
        ]
        return _envelope(data, message=f"{len(vms)} VM(s) discovered")
    except Exception as exc:
        return _error(str(exc), 500)


# ═══════════════════════════════════════════════════════════════════════════
# BACKUP
# ═══════════════════════════════════════════════════════════════════════════

@api.route("/backup", methods=["POST"])
@require_role(UserRole.OPERATOR)
def api_backup():
    """Trigger VM backup."""
    body = request.get_json(silent=True) or {}
    host = body.get("host", "")
    user = body.get("username", "Administrator")
    password = body.get("password", "")
    backup_path = body.get("backup_path", "")

    if not host or not password:
        return _error("host and password required", 400)

    try:
        vms = discover_workloads(host, user, password)
        tasks = backup_vms(
            host, user, password, vms,
            backup_path=backup_path,
            backup_type=body.get("backup_type", "export"),
            verify=body.get("verify", True),
            exclude_vms=body.get("exclude_vms", []),
        )
        data = [
            {"name": t.name, "success": t.success, "message": t.message, "duration": t.duration_seconds}
            for t in tasks
        ]
        return _envelope(data, message=f"{sum(1 for t in tasks if t.success)}/{len(tasks)} backups succeeded")
    except Exception as exc:
        return _error(str(exc), 500)


# ═══════════════════════════════════════════════════════════════════════════
# AI PLANNING
# ═══════════════════════════════════════════════════════════════════════════

@api.route("/ai/plan", methods=["POST"])
@require_role(UserRole.OPERATOR)
def api_ai_plan():
    """Generate AI migration plan."""
    body = request.get_json(silent=True) or {}
    host = body.get("host", "")
    user = body.get("username", "Administrator")
    password = body.get("password", "")

    if not host or not password:
        return _error("host and password required", 400)

    try:
        from azure_local_deploy.ai_provider import AIPlanner, load_ai_config
        config = _get_config()
        ai_cfg = load_ai_config(config)
        planner = AIPlanner(ai_cfg)

        vms = discover_workloads(host, user, password)
        vms = map_dependencies(vms)
        plan = planner.analyze_dependencies(vms)
        return _envelope(plan, message="AI plan generated")
    except Exception as exc:
        return _error(str(exc), 500)


@api.route("/ai/runbook", methods=["POST"])
@require_role(UserRole.OPERATOR)
def api_ai_runbook():
    """Generate AI runbook."""
    body = request.get_json(silent=True) or {}
    host = body.get("host", "")
    user = body.get("username", "Administrator")
    password = body.get("password", "")

    if not host or not password:
        return _error("host and password required", 400)

    try:
        from azure_local_deploy.ai_provider import AIPlanner, load_ai_config
        config = _get_config()
        ai_cfg = load_ai_config(config)
        planner = AIPlanner(ai_cfg)

        vms = discover_workloads(host, user, password)
        vms = map_dependencies(vms)
        runbook = planner.generate_runbook(vms, {"target_host": body.get("target_host", "")})
        return _envelope({"runbook": runbook}, message="Runbook generated")
    except Exception as exc:
        return _error(str(exc), 500)


@api.route("/ai/estimate", methods=["POST"])
@require_role(UserRole.OPERATOR)
def api_ai_estimate():
    """Get AI downtime estimation."""
    body = request.get_json(silent=True) or {}
    host = body.get("host", "")
    user = body.get("username", "Administrator")
    password = body.get("password", "")

    if not host or not password:
        return _error("host and password required", 400)

    try:
        from azure_local_deploy.ai_provider import AIPlanner, load_ai_config
        config = _get_config()
        ai_cfg = load_ai_config(config)
        planner = AIPlanner(ai_cfg)

        vms = discover_workloads(host, user, password)
        estimate = planner.estimate_downtime(vms)
        return _envelope(estimate, message="Downtime estimate generated")
    except Exception as exc:
        return _error(str(exc), 500)


@api.route("/ai/risk", methods=["POST"])
@require_role(UserRole.OPERATOR)
def api_ai_risk():
    """AI risk assessment."""
    body = request.get_json(silent=True) or {}
    host = body.get("host", "")
    user = body.get("username", "Administrator")
    password = body.get("password", "")

    if not host or not password:
        return _error("host and password required", 400)

    try:
        from azure_local_deploy.ai_provider import AIPlanner, load_ai_config
        config = _get_config()
        ai_cfg = load_ai_config(config)
        planner = AIPlanner(ai_cfg)

        vms = discover_workloads(host, user, password)
        vms = map_dependencies(vms)
        risk = planner.assess_risk(vms, {"target_host": body.get("target_host", "")})
        return _envelope(risk, message="Risk assessment complete")
    except Exception as exc:
        return _error(str(exc), 500)


@api.route("/ai/chat", methods=["POST"])
@require_role(UserRole.VIEWER)
def api_ai_chat():
    """AI interactive chat."""
    body = request.get_json(silent=True) or {}
    message = body.get("message", "")
    context = body.get("context", "")

    if not message:
        return _error("message required", 400)

    try:
        from azure_local_deploy.ai_provider import AIPlanner, load_ai_config
        config = _get_config()
        ai_cfg = load_ai_config(config)
        planner = AIPlanner(ai_cfg)
        response = planner.chat(message, context)
        return _envelope({"response": response})
    except Exception as exc:
        return _error(str(exc), 500)


@api.route("/ai/script", methods=["POST"])
@require_role(UserRole.OPERATOR)
def api_ai_script():
    """Generate migration script via AI."""
    body = request.get_json(silent=True) or {}
    task_desc = body.get("task_description", "")
    target_platform = body.get("target_platform", "PowerShell")
    constraints = body.get("constraints", [])

    if not task_desc:
        return _error("task_description required", 400)

    try:
        from azure_local_deploy.ai_provider import AIPlanner, load_ai_config
        config = _get_config()
        ai_cfg = load_ai_config(config)
        planner = AIPlanner(ai_cfg)
        context = f"Platform: {target_platform}\nConstraints: {', '.join(constraints)}"
        script = planner.generate_script(task_desc, context)
        return _envelope({"script": script, "platform": target_platform})
    except Exception as exc:
        return _error(str(exc), 500)


@api.route("/ai/iac", methods=["POST"])
@require_role(UserRole.OPERATOR)
def api_ai_iac():
    """Generate IaC template via AI."""
    body = request.get_json(silent=True) or {}
    infra_desc = body.get("infrastructure_description", "")
    fmt = body.get("format", "bicep")

    if not infra_desc:
        return _error("infrastructure_description required", 400)

    try:
        from azure_local_deploy.ai_provider import AIPlanner, load_ai_config
        config = _get_config()
        ai_cfg = load_ai_config(config)
        planner = AIPlanner(ai_cfg)
        template = planner.generate_iac(infra_desc, fmt)
        return _envelope({"template": template, "format": fmt})
    except Exception as exc:
        return _error(str(exc), 500)


# ═══════════════════════════════════════════════════════════════════════════
# PIPELINE (full rebuild)
# ═══════════════════════════════════════════════════════════════════════════

@api.route("/pipeline/start", methods=["POST"])
@require_role(UserRole.OPERATOR)
def pipeline_start():
    """Start the full rebuild pipeline as a background job."""
    body = request.get_json(silent=True) or {}
    config_override = body.get("config", None)

    config = config_override or _get_config()
    if not config:
        return _error("No configuration available. Upload or set a config file.", 400)

    job_id = f"rb-{uuid.uuid4().hex[:8]}"
    job = PipelineJob(
        job_id=job_id,
        state=JobState.PENDING,
        mode="rebuild",
        stages=[{"name": s, "status": "pending"} for s in REBUILD_STAGES],
        config=config,
    )
    _get_jobs()[job_id] = job

    skip_backup = body.get("skip_backup", False)
    skip_move_back = body.get("skip_move_back", False)
    use_ai = body.get("use_ai", True)
    resume = body.get("resume", False)

    def _run():
        job.state = JobState.RUNNING
        job.started_at = datetime.utcnow()

        def _progress(msg: str):
            entry = {"time": datetime.utcnow().isoformat(), "message": msg}
            job.logs.append(entry)
            # Update current stage from message
            for s in REBUILD_STAGES:
                if s.replace("_", " ").lower() in msg.lower():
                    job.current_stage = s
                    for st in job.stages:
                        if st["name"] == s:
                            st["status"] = "running"
                        elif st["status"] == "running":
                            st["status"] = "completed"
                    break

        try:
            report = run_rebuild_pipeline(
                config,
                skip_backup=skip_backup,
                skip_move_back=skip_move_back,
                use_ai=use_ai,
                resume=resume,
                progress_callback=_progress,
            )
            job.report = report
            job.state = JobState.COMPLETED if report.all_ok else JobState.FAILED
        except Exception as exc:
            job.state = JobState.FAILED
            job.error = str(exc)
        finally:
            job.completed_at = datetime.utcnow()
            for st in job.stages:
                if st["status"] == "running":
                    st["status"] = "completed" if job.state == JobState.COMPLETED else "failed"

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return _envelope({"job_id": job_id}, message="Pipeline started", code=202)


@api.route("/pipeline/<job_id>", methods=["GET"])
@require_role(UserRole.VIEWER)
def pipeline_status(job_id: str):
    """Get pipeline job status."""
    job = _get_jobs().get(job_id)
    if not job:
        return _error("Job not found", 404)
    return _envelope(job.to_dict())


@api.route("/pipeline/<job_id>/logs", methods=["GET"])
@require_role(UserRole.VIEWER)
def pipeline_logs(job_id: str):
    """Get pipeline logs."""
    job = _get_jobs().get(job_id)
    if not job:
        return _error("Job not found", 404)
    offset = request.args.get("offset", 0, type=int)
    return _envelope(job.logs[offset:])


@api.route("/pipeline/<job_id>/report", methods=["GET"])
@require_role(UserRole.VIEWER)
def pipeline_report(job_id: str):
    """Get pipeline report."""
    job = _get_jobs().get(job_id)
    if not job:
        return _error("Job not found", 404)
    if not job.report:
        return _error("Report not available yet", 404)

    rpt = job.report
    return _envelope({
        "rebuild_id": rpt.rebuild_id,
        "status": rpt.status,
        "source_cluster": rpt.source_cluster,
        "target_host": rpt.target_host,
        "total_vms_migrated": rpt.total_vms_migrated,
        "total_duration_seconds": rpt.total_duration_seconds,
        "backup_path": rpt.backup_path,
        "all_ok": rpt.all_ok,
        "errors": rpt.errors,
        "tasks": [
            {"stage": t.stage, "name": t.name, "success": t.success,
             "message": t.message, "duration": t.duration_seconds}
            for t in rpt.tasks
        ],
    })


@api.route("/pipeline/<job_id>/abort", methods=["POST"])
@require_role(UserRole.OPERATOR)
def pipeline_abort(job_id: str):
    """Abort a running pipeline."""
    job = _get_jobs().get(job_id)
    if not job:
        return _error("Job not found", 404)
    if job.state != JobState.RUNNING:
        return _error("Job is not running", 400)
    job.state = JobState.ABORTED
    job.error = "Aborted by user"
    job.completed_at = datetime.utcnow()
    return _envelope(message="Pipeline abort requested")


@api.route("/pipeline", methods=["GET"])
@require_role(UserRole.VIEWER)
def list_pipelines():
    """List all pipeline jobs."""
    jobs = _get_jobs()
    data = [j.to_dict() for j in jobs.values()]
    return _envelope(data)


# ═══════════════════════════════════════════════════════════════════════════
# SSE EVENT STREAM
# ═══════════════════════════════════════════════════════════════════════════

@api.route("/pipeline/<job_id>/events", methods=["GET"])
@require_role(UserRole.VIEWER)
def pipeline_events(job_id: str):
    """Server-Sent Events stream for pipeline progress."""
    job = _get_jobs().get(job_id)
    if not job:
        return _error("Job not found", 404)

    def _stream():
        last_idx = 0
        while True:
            logs = job.logs[last_idx:]
            for entry in logs:
                yield f"data: {json.dumps(entry)}\n\n"
                last_idx += 1

            if job.state in (JobState.COMPLETED, JobState.FAILED, JobState.ABORTED):
                yield f"data: {json.dumps({'event': 'done', 'state': job.state.value})}\n\n"
                break

            time.sleep(1)

    return Response(
        stream_with_context(_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ═══════════════════════════════════════════════════════════════════════════
# EVACUATE / MOVE-BACK (individual stage triggers)
# ═══════════════════════════════════════════════════════════════════════════

@api.route("/evacuate", methods=["POST"])
@require_role(UserRole.OPERATOR)
def api_evacuate():
    """Trigger workload evacuation."""
    body = request.get_json(silent=True) or {}
    config = body.get("config") or _get_config()
    if not config:
        return _error("Configuration required", 400)

    try:
        from azure_local_deploy.rebuild import evacuate_workloads
        rc = config.get("rebuild", {})
        src = rc.get("source_cluster", {})
        tgt = rc.get("migration_target", {})

        vms = discover_workloads(src["host"], src.get("username", "Administrator"), src["password"])
        vms = map_dependencies(vms)

        tasks = evacuate_workloads(
            src["host"], src.get("username", "Administrator"), src["password"],
            tgt["host"], tgt.get("username", "Administrator"), tgt["password"],
            vms,
        )
        data = [{"name": t.name, "success": t.success, "message": t.message} for t in tasks]
        return _envelope(data, message=f"Evacuation: {sum(1 for t in tasks if t.success)}/{len(tasks)}")
    except Exception as exc:
        return _error(str(exc), 500)


@api.route("/move-back", methods=["POST"])
@require_role(UserRole.OPERATOR)
def api_move_back():
    """Trigger workload move-back."""
    body = request.get_json(silent=True) or {}
    config = body.get("config") or _get_config()
    if not config:
        return _error("Configuration required", 400)

    try:
        from azure_local_deploy.rebuild import move_back_workloads
        rc = config.get("rebuild", {})
        src = rc.get("source_cluster", {})
        tgt = rc.get("migration_target", {})

        vms = discover_workloads(tgt["host"], tgt.get("username", "Administrator"), tgt["password"])
        tasks = move_back_workloads(
            tgt["host"], tgt.get("username", "Administrator"), tgt["password"],
            src["host"], src.get("username", "Administrator"), src["password"],
            vms,
        )
        data = [{"name": t.name, "success": t.success, "message": t.message} for t in tasks]
        return _envelope(data, message=f"Move-back: {sum(1 for t in tasks if t.success)}/{len(tasks)}")
    except Exception as exc:
        return _error(str(exc), 500)


# ═══════════════════════════════════════════════════════════════════════════
# TEARDOWN
# ═══════════════════════════════════════════════════════════════════════════

@api.route("/teardown", methods=["POST"])
@require_role(UserRole.ADMIN)
def api_teardown():
    """Trigger cluster teardown (DESTRUCTIVE — admin only)."""
    body = request.get_json(silent=True) or {}
    config = body.get("config") or _get_config()
    if not config:
        return _error("Configuration required", 400)

    confirm = body.get("confirm_teardown", False)
    if not confirm:
        return _error("Set confirm_teardown: true to proceed with this destructive operation", 400)

    try:
        from azure_local_deploy.rebuild import teardown_cluster
        rc = config.get("rebuild", {})
        src = rc.get("source_cluster", {})
        azure_cfg = config.get("azure", {})

        tasks = teardown_cluster(
            src["host"], src.get("username", "Administrator"), src["password"],
            cluster_name=config.get("cluster", {}).get("name", ""),
            subscription_id=azure_cfg.get("subscription_id", ""),
            resource_group=azure_cfg.get("resource_group", ""),
        )
        data = [{"name": t.name, "success": t.success, "message": t.message} for t in tasks]
        return _envelope(data, message="Teardown complete")
    except Exception as exc:
        return _error(str(exc), 500)


# ═══════════════════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

@api.route("/validate", methods=["POST"])
@require_role(UserRole.VIEWER)
def api_validate():
    """Run post-move validation."""
    body = request.get_json(silent=True) or {}
    host = body.get("host", "")
    user = body.get("username", "Administrator")
    password = body.get("password", "")
    expected_vms = body.get("expected_vms", [])

    if not host or not password:
        return _error("host and password required", 400)

    try:
        from azure_local_deploy.rebuild import validate_post_move
        tasks = validate_post_move(host, user, password, expected_vms)
        data = [{"name": t.name, "success": t.success, "message": t.message} for t in tasks]
        ok = all(t.success for t in tasks)
        return _envelope(data, message="Validation passed" if ok else "Validation has failures")
    except Exception as exc:
        return _error(str(exc), 500)


# ═══════════════════════════════════════════════════════════════════════════
# CONFIG MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

@api.route("/config", methods=["GET"])
@require_role(UserRole.VIEWER)
def get_config():
    """Get current rebuild configuration (secrets masked)."""
    config = _get_config()
    # Mask passwords
    masked = json.loads(json.dumps(config))
    for section in masked.values():
        if isinstance(section, dict):
            for key in list(section.keys()):
                if "password" in key.lower() or "secret" in key.lower() or "key" in key.lower():
                    section[key] = "****"
    return _envelope(masked)


@api.route("/config", methods=["PUT"])
@require_role(UserRole.ADMIN)
def update_config():
    """Upload / replace rebuild configuration."""
    body = request.get_json(silent=True) or {}
    if not body:
        return _error("JSON config body required", 400)

    cfg_dir = Path(current_app.config.get("CONFIG_DIR", "."))
    cfg_path = cfg_dir / "rebuild-config.yaml"
    cfg_path.write_text(yaml.dump(body, default_flow_style=False), encoding="utf-8")
    current_app.config["REBUILD_CONFIG_PATH"] = str(cfg_path)
    return _envelope(message="Configuration updated")


# ═══════════════════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════════════════

@api.route("/health", methods=["GET"])
def health():
    """Health check (no auth required)."""
    jobs = _get_jobs()
    running = sum(1 for j in jobs.values() if j.state == JobState.RUNNING)
    return _envelope({
        "status": "healthy",
        "version": "0.1.0",
        "running_jobs": running,
        "total_jobs": len(jobs),
    })


@api.route("/stages", methods=["GET"])
@require_role(UserRole.VIEWER)
def list_stages():
    """List rebuild pipeline stages."""
    return _envelope(REBUILD_STAGES)


@api.route("/ai/providers", methods=["GET"])
@require_role(UserRole.VIEWER)
def ai_providers():
    """List configured AI providers."""
    try:
        from azure_local_deploy.ai_provider import load_ai_config
        config = _get_config()
        ai_cfg = load_ai_config(config)
        data = {
            "primary": {
                "provider": ai_cfg.primary_provider.value,
                "model": ai_cfg.primary.model,
            },
        }
        if ai_cfg.secondary:
            data["secondary"] = {
                "provider": ai_cfg.secondary_provider.value if ai_cfg.secondary_provider else None,
                "model": ai_cfg.secondary.model,
            }
        return _envelope(data)
    except Exception as exc:
        return _error(str(exc), 500)


@api.route("/ai/test", methods=["POST"])
@require_role(UserRole.ADMIN)
def ai_test_connectivity():
    """Test AI provider connectivity."""
    try:
        from azure_local_deploy.ai_provider import load_ai_config, test_provider_connectivity
        config = _get_config()
        ai_cfg = load_ai_config(config)
        results = {}
        results["primary"] = test_provider_connectivity(ai_cfg.primary)
        if ai_cfg.secondary:
            results["secondary"] = test_provider_connectivity(ai_cfg.secondary)
        return _envelope(results)
    except Exception as exc:
        return _error(str(exc), 500)
