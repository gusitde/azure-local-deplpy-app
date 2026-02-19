"""Flask web application – deployment wizard UI.

Provides a step-by-step wizard for:
    1. New cluster deployment (full pipeline)
    2. Add node to an existing cluster
    3. Rebuild cluster (14-stage pipeline)

Streams real-time progress to the browser via Socket.IO.
Registers the REST API v1 blueprint with authentication.
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

    # Persistent secret key — generate once, reuse across restarts
    secret_file = Path.home() / ".azure-local-deploy" / "flask_secret.key"
    if os.environ.get("ALD_SECRET_KEY"):
        app.secret_key = os.environ["ALD_SECRET_KEY"]
    elif secret_file.exists():
        app.secret_key = secret_file.read_text().strip()
    else:
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        key = uuid.uuid4().hex + uuid.uuid4().hex  # 64 hex chars
        secret_file.write_text(key)
        try:
            secret_file.chmod(0o600)
        except OSError:
            pass
        app.secret_key = key

    app.config["CONFIG_DIR"] = config_dir or str(Path.cwd())
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max request size

    # Secure session cookies
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    socketio = SocketIO(app, cors_allowed_origins=None, async_mode="threading")  # None = same-origin only

    # Store active deployment jobs: {job_id: {status, logs, ...}}
    app.config["JOBS"] = {}

    # Register REST API v1 blueprint with auth middleware
    from azure_local_deploy.api import api as api_blueprint
    from azure_local_deploy.auth import init_auth
    app.register_blueprint(api_blueprint)
    init_auth(app)

    _register_routes(app)
    _register_socket_events(app, socketio)

    # Security headers for web routes
    @app.after_request
    def _add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

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

    # -- Day 2 Services Wizard ---------------------------------------------
    @app.route("/wizard/day2")
    def wizard_day2():
        session["wizard_mode"] = "day2"
        session.setdefault("wizard_data", {})
        return redirect(url_for("wizard_day2_step", step=1))

    @app.route("/wizard/day2/step/<int:step>", methods=["GET", "POST"])
    def wizard_day2_step(step: int):
        data = session.get("wizard_data", {})

        if request.method == "POST":
            data.update(request.form.to_dict(flat=False))
            data = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in data.items()}
            session["wizard_data"] = data
            session.modified = True

            if step < 3:
                return redirect(url_for("wizard_day2_step", step=step + 1))
            else:
                # Final step — run Day 2 services
                return redirect(url_for("wizard_day2_run"))

        template = f"wizard_day2_step{step}.html"
        return render_template(template, step=step, data=data, mode="day2")

    @app.route("/wizard/day2/run")
    def wizard_day2_run():
        """Execute Day 2 services from wizard data and show results."""
        from azure_local_deploy.day2_services import (
            LogicalNetworkConfig,
            TestVMConfig,
            VMImageConfig,
            run_day2_services,
        )

        data = session.get("wizard_data", {})

        # Build network configs from form
        networks = [
            LogicalNetworkConfig(
                name=data.get("dhcp_network_name", "dhcp-logical-network"),
                address_type="DHCP",
                vm_switch_name=data.get("dhcp_vm_switch", "ConvergedSwitch(compute_management)"),
                vlan_id=int(data["dhcp_vlan_id"]) if data.get("dhcp_vlan_id") else None,
            ),
            LogicalNetworkConfig(
                name=data.get("static_network_name", "static-logical-network"),
                address_type="Static",
                address_prefix=data.get("static_address_prefix", "192.168.200.0/24"),
                gateway=data.get("static_gateway", "192.168.200.1"),
                dns_servers=[s.strip() for s in data.get("static_dns", "").split(",") if s.strip()],
                ip_pool_start=data.get("static_ip_pool_start", "192.168.200.100"),
                ip_pool_end=data.get("static_ip_pool_end", "192.168.200.200"),
                vm_switch_name=data.get("static_vm_switch", "ConvergedSwitch(compute_management)"),
                vlan_id=int(data["static_vlan_id"]) if data.get("static_vlan_id") else None,
            ),
        ]

        images = [
            VMImageConfig(
                name=data.get("image1_name", "windows-server-2025"),
                image_path=data.get("image1_path", ""),
                os_type=data.get("image1_os_type", "Windows"),
            ),
            VMImageConfig(
                name=data.get("image2_name", "windows-11-enterprise"),
                image_path=data.get("image2_path", ""),
                os_type=data.get("image2_os_type", "Windows"),
            ),
        ]

        vms = [
            TestVMConfig(
                name=data.get("vm1_name", "test-vm-winserver2025"),
                logical_network=data.get("vm1_network", networks[0].name),
                image_name=data.get("vm1_image", images[0].name),
                cpu_count=int(data.get("vm1_cpu", 4)),
                memory_gb=int(data.get("vm1_memory", 8)),
                storage_gb=int(data.get("vm1_disk", 128)),
                admin_username=data.get("vm1_admin_user", "azurelocaladmin"),
                admin_password=data.get("vm1_admin_password", ""),
            ),
            TestVMConfig(
                name=data.get("vm2_name", "test-vm-win11"),
                logical_network=data.get("vm2_network", networks[1].name),
                image_name=data.get("vm2_image", images[1].name),
                cpu_count=int(data.get("vm2_cpu", 4)),
                memory_gb=int(data.get("vm2_memory", 8)),
                storage_gb=int(data.get("vm2_disk", 128)),
                admin_username=data.get("vm2_admin_user", "azurelocaladmin"),
                admin_password=data.get("vm2_admin_password", ""),
            ),
        ]

        # For the Day 2 wizard, we need a cluster node to SSH into.
        # Use the session data or return an error page.
        host = data.get("server_1_host_ip", data.get("host_ip", ""))
        user = data.get("server_1_host_user", data.get("host_user", "Administrator"))
        password = data.get("server_1_host_password", data.get("host_password", ""))
        sub_id = data.get("subscription_id", "")
        rg = data.get("resource_group", "")

        report = run_day2_services(
            host=host,
            user=user,
            password=password,
            subscription_id=sub_id,
            resource_group=rg,
            custom_location_name=data.get("custom_location_name", ""),
            logical_networks=networks,
            vm_images=images,
            test_vms=vms,
        )

        return render_template("wizard_day2_results.html", report=report, data=data)

    # -- Rebuild Cluster Wizard --------------------------------------------
    @app.route("/wizard/rebuild")
    def wizard_rebuild():
        session["wizard_mode"] = "rebuild"
        session.setdefault("wizard_data", {})
        return redirect(url_for("wizard_rebuild_step", step=1))

    @app.route("/wizard/rebuild/step/<int:step>", methods=["GET", "POST"])
    def wizard_rebuild_step(step: int):
        data = session.get("wizard_data", {})

        if request.method == "POST":
            data.update(request.form.to_dict(flat=False))
            data = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in data.items()}
            session["wizard_data"] = data
            session.modified = True

            max_steps = 7
            if step < max_steps:
                return redirect(url_for("wizard_rebuild_step", step=step + 1))
            # Step 7 is the execution page — no redirect beyond it
            return redirect(url_for("wizard_rebuild_step", step=7))

        template = f"wizard_rebuild_step{step}.html"
        return render_template(template, step=step, data=data, mode="rebuild")


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
        return 9   # +2: Security & Pre-Add Checks, Review with post-add options
    if mode == "rebuild":
        return 7   # AI Provider, Source, Target, Backup, Discovery, Review, Execute
    return 12  # +4: AD Prep, Security, Key Vault & Witness, Post-Deploy


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
            "check_docs": data.get("check_docs") == "true",
            "abort_on_validation_failure": data.get("abort_on_validation_failure") == "true",
        },
        "azure": {
            "tenant_id": data.get("tenant_id", ""),
            "subscription_id": data.get("subscription_id", ""),
            "resource_group": data.get("resource_group", ""),
            "region": data.get("region", "eastus"),
        },
        "servers": servers,
    }

    # Firmware configuration
    fw_targets = []
    for comp in ("bios", "idrac", "nic", "raid"):
        url = data.get(f"fw_{comp}_url", "")
        if url:
            fw_targets.append({
                "component": data.get(f"fw_{comp}_component", comp.upper()),
                "dup_url": url,
                "target_version": data.get(f"fw_{comp}_version", ""),
                "install_option": data.get(f"fw_{comp}_install", "NowAndReboot"),
            })

    config["firmware"] = {
        "catalog_url": data.get("firmware_catalog_url", ""),
        "targets": fw_targets,
        "apply_reboot": True,
        "task_timeout": int(data.get("firmware_task_timeout", 3600)),
    }

    # BIOS configuration
    bios_attrs: dict[str, str] = {}
    if data.get("bios_sys_profile"):
        bios_attrs["SysProfile"] = data["bios_sys_profile"]
    if data.get("bios_proc_cstates"):
        bios_attrs["ProcCStates"] = data["bios_proc_cstates"]
    if data.get("bios_logical_proc"):
        bios_attrs["LogicalProc"] = data["bios_logical_proc"]

    config["bios"] = {
        "profile": data.get("bios_profile", "AzureLocal"),
        "apply_reboot": data.get("bios_apply_reboot", "true") == "true",
        "task_timeout": int(data.get("bios_task_timeout", 1200)),
        "attributes": bios_attrs,
    }

    if mode == "new_cluster":
        config["cluster"] = {
            "name": data.get("cluster_name", ""),
            "cluster_ip": data.get("cluster_ip", ""),
            "domain_fqdn": data.get("domain_fqdn", ""),
            "ou_path": data.get("ou_path", ""),
            "deployment_timeout": int(data.get("deployment_timeout", 7200)),
        }

        # Active Directory preparation
        config["active_directory"] = {
            "enabled": data.get("ad_enabled", "true") == "true",
            "ou_name": data.get("ad_ou_name", "AzureLocal"),
            "deployment_user": data.get("ad_deployment_user", ""),
            "deployment_password": data.get("ad_deployment_password", ""),
            "dc_host": data.get("ad_dc_host", ""),
            "dc_user": data.get("ad_dc_user", "Administrator"),
            "dc_password": data.get("ad_dc_password", ""),
        }

        # Security profile
        config["security"] = {
            "profile": data.get("security_profile", "recommended"),
            "hvci": data.get("sec_hvci", "true") == "true",
            "drtm": data.get("sec_drtm", "true") == "true",
            "credential_guard": data.get("sec_credential_guard", "true") == "true",
            "smb_signing": data.get("sec_smb_signing", "true") == "true",
            "smb_encryption": data.get("sec_smb_encryption", "true") == "true",
            "side_channel": data.get("sec_side_channel", "true") == "true",
            "bitlocker_boot": data.get("sec_bitlocker_boot", "true") == "true",
            "bitlocker_data": data.get("sec_bitlocker_data", "true") == "true",
            "wdac": data.get("sec_wdac", "true") == "true",
            "drift_control": data.get("sec_drift_control", "true") == "true",
        }

        # Key Vault
        config["keyvault"] = {
            "name": data.get("keyvault_name", ""),
        }

        # Cloud witness
        config["cloud_witness"] = {
            "storage_account_name": data.get("cloud_witness_storage_account", ""),
        }

        # Proxy (optional)
        config["proxy"] = {
            "http_proxy": data.get("http_proxy", ""),
            "https_proxy": data.get("https_proxy", ""),
            "no_proxy": data.get("no_proxy", ""),
        }

        # Post-deploy options
        config["post_deploy"] = {
            "enable_health_monitoring": data.get("pd_health_monitoring", "true") == "true",
            "enable_rdp": data.get("pd_enable_rdp") == "true",
            "create_workload_volumes": data.get("pd_create_volumes", "true") == "true",
        }

    elif mode == "add_node":
        config["add_node"] = {
            "existing_cluster_name": data.get("existing_cluster_name", ""),
            "existing_cluster_resource_group": data.get("existing_cluster_rg", data.get("resource_group", "")),
            "existing_node": {
                "host": data.get("existing_node_ip", ""),
                "user": data.get("existing_node_user", "Administrator"),
                "password": data.get("existing_node_password", ""),
                "ssh_port": int(data.get("existing_node_ssh_port", 22)),
            },
        }

        config["security"] = {
            "profile": data.get("security_profile", "recommended"),
        }

        # Proxy (optional)
        config["proxy"] = {
            "http_proxy": data.get("http_proxy", ""),
            "https_proxy": data.get("https_proxy", ""),
            "no_proxy": data.get("no_proxy", ""),
        }

        # Validation options
        config["validation"] = {
            "run_pre_flight": data.get("run_pre_flight", "true") == "true",
            "abort_on_failure": data.get("abort_on_val_failure", "true") == "true",
        }

    return config
