"""Flask web application – deployment wizard UI.

Provides a step-by-step wizard for:
    1. New cluster deployment (full pipeline)
    2. Add node to an existing cluster

Streams real-time progress to the browser via Socket.IO.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from flask_socketio import SocketIO, emit

from azure_local_deploy.orchestrator import STAGES, load_config, run_pipeline
from azure_local_deploy.add_node import run_add_node_pipeline
from azure_local_deploy.utils import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(config_dir: str | None = None) -> tuple[Flask, SocketIO]:
    """Create and configure the Flask application."""
    template_dir = Path(__file__).parent / "templates"
    static_dir = Path(__file__).parent / "static"

    app = Flask(
        __name__,
        template_folder=str(template_dir),
        static_folder=str(static_dir),
    )
    app.secret_key = os.environ.get("ALD_SECRET_KEY", uuid.uuid4().hex)
    app.config["CONFIG_DIR"] = config_dir or str(Path.cwd())

    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

    # Store active deployment jobs: {job_id: {status, logs, ...}}
    app.config["JOBS"] = {}

    _register_routes(app)
    _register_socket_events(app, socketio)

    return app, socketio


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _register_routes(app: Flask) -> None:  # noqa: C901

    # -- Home page ---------------------------------------------------------
    @app.route("/")
    def index():
        return render_template("index.html")

    # -- New Cluster Wizard ------------------------------------------------
    @app.route("/wizard/new-cluster")
    def wizard_new_cluster():
        session["wizard_mode"] = "new_cluster"
        session.setdefault("wizard_data", {})
        return redirect(url_for("wizard_step", step=1))

    # -- Add Node Wizard ---------------------------------------------------
    @app.route("/wizard/add-node")
    def wizard_add_node():
        session["wizard_mode"] = "add_node"
        session.setdefault("wizard_data", {})
        return redirect(url_for("wizard_step", step=1))

    # -- Wizard steps (shared route, template varies by mode & step) -------
    @app.route("/wizard/step/<int:step>", methods=["GET", "POST"])
    def wizard_step(step: int):
        mode = session.get("wizard_mode", "new_cluster")
        data = session.get("wizard_data", {})

        if request.method == "POST":
            # Merge form data into session
            data.update(request.form.to_dict(flat=False))
            # Flatten single-value lists
            data = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in data.items()}
            session["wizard_data"] = data
            session.modified = True

            # Determine next step
            max_steps = _max_steps(mode)
            if step < max_steps:
                return redirect(url_for("wizard_step", step=step + 1))
            else:
                return redirect(url_for("wizard_review"))

        template = f"wizard_{mode}_step{step}.html"
        return render_template(template, step=step, data=data, mode=mode)

    # -- Review & confirm --------------------------------------------------
    @app.route("/wizard/review")
    def wizard_review():
        mode = session.get("wizard_mode", "new_cluster")
        data = session.get("wizard_data", {})
        config = _build_config_from_wizard(mode, data)
        return render_template("wizard_review.html", mode=mode, data=data, config=config)

    # -- Launch deployment -------------------------------------------------
    @app.route("/wizard/deploy", methods=["POST"])
    def wizard_deploy():
        mode = session.get("wizard_mode", "new_cluster")
        data = session.get("wizard_data", {})
        config = _build_config_from_wizard(mode, data)

        job_id = uuid.uuid4().hex[:12]
        app.config["JOBS"][job_id] = {
            "status": "pending",
            "mode": mode,
            "created": datetime.utcnow().isoformat(),
            "logs": [],
        }

        # Save generated config to disk
        cfg_path = Path(app.config["CONFIG_DIR"]) / f"deploy-{job_id}.yaml"
        cfg_path.write_text(yaml.dump(config, default_flow_style=False), encoding="utf-8")

        session["active_job"] = job_id
        session.modified = True

        return redirect(url_for("wizard_progress", job_id=job_id))

    # -- Progress page -----------------------------------------------------
    @app.route("/wizard/progress/<job_id>")
    def wizard_progress(job_id: str):
        job = app.config["JOBS"].get(job_id)
        if not job:
            return "Job not found", 404
        return render_template("wizard_progress.html", job_id=job_id, job=job)

    # -- API: Get job status -----------------------------------------------
    @app.route("/api/jobs/<job_id>")
    def api_job_status(job_id: str):
        job = app.config["JOBS"].get(job_id)
        if not job:
            return jsonify({"error": "not found"}), 404
        return jsonify(job)

    # -- API: Download generated config ------------------------------------
    @app.route("/api/config/<job_id>")
    def api_config(job_id: str):
        cfg_path = Path(app.config["CONFIG_DIR"]) / f"deploy-{job_id}.yaml"
        if not cfg_path.exists():
            return "Config not found", 404
        return cfg_path.read_text(encoding="utf-8"), 200, {"Content-Type": "text/yaml"}

    # -- Reset wizard ------------------------------------------------------
    @app.route("/wizard/reset")
    def wizard_reset():
        session.pop("wizard_data", None)
        session.pop("wizard_mode", None)
        return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Socket.IO events
# ---------------------------------------------------------------------------

def _register_socket_events(app: Flask, socketio: SocketIO) -> None:

    @socketio.on("start_deployment")
    def handle_start(data: dict):
        job_id = data.get("job_id", "")
        job = app.config["JOBS"].get(job_id)
        if not job:
            emit("deploy_error", {"message": "Job not found"})
            return

        cfg_path = Path(app.config["CONFIG_DIR"]) / f"deploy-{job_id}.yaml"
        if not cfg_path.exists():
            emit("deploy_error", {"message": "Config file not found"})
            return

        mode = job["mode"]
        job["status"] = "running"

        def _run():
            try:
                config = load_config(str(cfg_path))
                if mode == "add_node":
                    run_add_node_pipeline(
                        config,
                        progress_callback=lambda msg: _emit_log(socketio, job_id, job, msg),
                    )
                else:
                    run_pipeline(
                        config,
                        progress_callback=lambda msg: _emit_log(socketio, job_id, job, msg),
                    )
                job["status"] = "completed"
                socketio.emit("deploy_complete", {"job_id": job_id}, namespace="/")
            except Exception as exc:
                job["status"] = "failed"
                job["error"] = str(exc)
                socketio.emit("deploy_error", {"job_id": job_id, "message": str(exc)}, namespace="/")

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        emit("deploy_started", {"job_id": job_id})


def _emit_log(socketio: SocketIO, job_id: str, job: dict, message: str) -> None:
    """Append log line and push to browser."""
    entry = {"time": datetime.utcnow().isoformat(), "message": message}
    job["logs"].append(entry)
    socketio.emit("deploy_log", {"job_id": job_id, **entry}, namespace="/")


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------

def _max_steps(mode: str) -> int:
    """Return number of wizard steps per mode."""
    if mode == "add_node":
        return 5
    return 6  # new_cluster


def _build_config_from_wizard(mode: str, data: dict) -> dict[str, Any]:
    """Convert wizard form data to a deployment config dict."""
    # Parse server count & build server list
    server_count = int(data.get("server_count", 1))

    servers = []
    for i in range(1, server_count + 1):
        prefix = f"server_{i}_"
        nic_count = int(data.get(f"{prefix}nic_count", 1))
        nics = []
        for n in range(1, nic_count + 1):
            np = f"{prefix}nic_{n}_"
            nic = {
                "adapter_name": data.get(f"{np}name", f"NIC{n}"),
                "mac_address": data.get(f"{np}mac", ""),
                "ip_address": data.get(f"{np}ip", ""),
                "prefix_length": int(data.get(f"{np}prefix", 24)),
            }
            gw = data.get(f"{np}gateway", "")
            if gw:
                nic["gateway"] = gw
            dns = data.get(f"{np}dns", "")
            if dns:
                nic["dns_servers"] = [s.strip() for s in dns.split(",") if s.strip()]
            vlan = data.get(f"{np}vlan", "")
            if vlan:
                nic["vlan_id"] = int(vlan)
            nics.append(nic)

        servers.append({
            "idrac_host": data.get(f"{prefix}idrac_host", ""),
            "idrac_user": data.get(f"{prefix}idrac_user", "root"),
            "idrac_password": data.get(f"{prefix}idrac_password", ""),
            "host_ip": data.get(f"{prefix}host_ip", ""),
            "host_user": data.get(f"{prefix}host_user", "Administrator"),
            "host_password": data.get(f"{prefix}host_password", ""),
            "ssh_port": int(data.get(f"{prefix}ssh_port", 22)),
            "arc_resource_id": data.get(f"{prefix}arc_resource_id", ""),
            "nics": nics,
        })

    config: dict[str, Any] = {
        "global": {
            "iso_url": data.get("iso_url", ""),
            "ntp_servers": [s.strip() for s in data.get("ntp_servers", "time.windows.com").split(",")],
            "timezone": data.get("timezone", "UTC"),
            "proxy_url": data.get("proxy_url", ""),
        },
        "azure": {
            "tenant_id": data.get("tenant_id", ""),
            "subscription_id": data.get("subscription_id", ""),
            "resource_group": data.get("resource_group", ""),
            "region": data.get("region", "eastus"),
        },
        "servers": servers,
    }

    if mode == "new_cluster":
        config["cluster"] = {
            "name": data.get("cluster_name", ""),
            "cluster_ip": data.get("cluster_ip", ""),
            "domain_fqdn": data.get("domain_fqdn", ""),
            "ou_path": data.get("ou_path", ""),
            "deployment_timeout": int(data.get("deployment_timeout", 7200)),
        }
    elif mode == "add_node":
        config["add_node"] = {
            "existing_cluster_name": data.get("existing_cluster_name", ""),
            "existing_cluster_resource_group": data.get("existing_cluster_rg", data.get("resource_group", "")),
        }

    return config
