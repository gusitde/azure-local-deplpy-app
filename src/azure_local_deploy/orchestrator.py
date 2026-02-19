"""Top-level orchestrator that drives the full deployment pipeline.

Reads a YAML config file and executes each stage in order for every server.

Pipeline stages (in order):
     1. register_providers  – register required Azure resource providers
     2. validate_permissions – check Azure RBAC role assignments
     3. prepare_ad           – Active Directory OU/user/GPO preparation
     4. validate_nodes       – pre-flight hardware / BIOS / network checks
     5. environment_check    – Microsoft AzStackHci.EnvironmentChecker
     6. update_firmware      – Dell firmware update via iDRAC Redfish
     7. configure_bios       – set BIOS attributes for Azure Local
     8. deploy_os            – mount ISO & install the OS
     9. configure_network    – NIC naming, IP config, VLANs, Network ATC
    10. configure_proxy      – (optional) proxy settings for internet access
    11. configure_time       – NTP & timezone
    12. configure_security   – security settings (HVCI, BitLocker, etc.)
    13. deploy_agent         – Azure Arc agent registration
    14. provision_keyvault   – create Key Vault for cluster secrets
    15. cloud_witness        – provision cloud witness storage account
    16. deploy_cluster       – create / deploy Azure Local cluster
    17. post_deploy          – health monitoring, workload volumes, RDP
"""

from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any, Callable

from azure_local_deploy.idrac_client import IdracClient
from azure_local_deploy.deploy_os import deploy_os_image
from azure_local_deploy.configure_network import configure_network, NicConfig
from azure_local_deploy.configure_time import configure_time_server
from azure_local_deploy.deploy_agent import deploy_agent
from azure_local_deploy.deploy_cluster import deploy_cluster
from azure_local_deploy.update_firmware import (
    FirmwareTarget,
    update_firmware,
)
from azure_local_deploy.configure_bios import BiosProfile, configure_bios
from azure_local_deploy.validate_nodes import validate_all_nodes
from azure_local_deploy.environment_checker import run_environment_checker_all_nodes
from azure_local_deploy.docs_checker import check_docs, print_docs_report
from azure_local_deploy.register_providers import register_resource_providers
from azure_local_deploy.validate_permissions import validate_permissions
from azure_local_deploy.prepare_ad import prepare_active_directory, ADPrepConfig
from azure_local_deploy.configure_proxy import configure_proxy, ProxyConfig
from azure_local_deploy.configure_security import configure_security, SecurityProfile, RECOMMENDED_SECURITY
from azure_local_deploy.provision_keyvault import provision_keyvault
from azure_local_deploy.cloud_witness import provision_cloud_witness, configure_cluster_witness
from azure_local_deploy.post_deploy import run_post_deployment
from azure_local_deploy.utils import get_logger, require_keys

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_config(path: str | Path) -> dict[str, Any]:
    """Load and validate the YAML deployment config."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    require_keys(cfg, ["servers", "azure"], context="top-level config")
    require_keys(cfg["azure"], [
        "tenant_id", "subscription_id", "resource_group", "region",
    ], context="azure config")
    return cfg


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

STAGES = [
    "register_providers",
    "validate_permissions",
    "prepare_ad",
    "validate_nodes",
    "environment_check",
    "update_firmware",
    "configure_bios",
    "deploy_os",
    "configure_network",
    "configure_proxy",
    "configure_time",
    "configure_security",
    "deploy_agent",
    "provision_keyvault",
    "cloud_witness",
    "deploy_cluster",
    "post_deploy",
]


def run_pipeline(
    config: dict[str, Any],
    *,
    stages: list[str] | None = None,
    dry_run: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> None:
    """Execute the deployment pipeline for all servers in *config*.

    Parameters
    ----------
    config:
        Parsed YAML config dict.
    stages:
        Optional subset of stages to run.  Defaults to all.
    dry_run:
        If True, log what would happen without executing.
    progress_callback:
        Optional callable that receives progress messages (for the web UI).
    """
    _cb = progress_callback or (lambda msg: None)
    active_stages = stages or STAGES
    servers: list[dict] = config["servers"]
    azure_cfg: dict = config["azure"]
    global_cfg: dict = config.get("global", {})
    firmware_cfg: dict = config.get("firmware", {})
    bios_cfg: dict = config.get("bios", {})

    log.info("[bold magenta]===== Azure Local Deploy Pipeline =====[/]")
    _cb("Pipeline starting")
    log.info("Servers : %d", len(servers))
    log.info("Stages  : %s", ", ".join(active_stages))
    if dry_run:
        log.info("[yellow]DRY RUN – no changes will be made[/]")
        _cb("Dry run – no changes made")
        return

    # ------------------------------------------------------------------
    # Check online documentation (non-blocking, informational)
    # ------------------------------------------------------------------
    if global_cfg.get("check_docs", True):
        try:
            _cb("Checking Azure Local online documentation for recommendations …")
            docs_report = check_docs(progress_callback=_cb)
            print_docs_report(docs_report)
        except Exception as exc:
            log.warning("Docs checker failed (non-blocking): %s", exc)
            _cb(f"Docs check skipped: {exc}")

    # ------------------------------------------------------------------
    # Phase 1: Azure pre-flight (subscription-level)
    # ------------------------------------------------------------------

    # Register Azure resource providers
    if "register_providers" in active_stages:
        _cb("Stage: register_providers – registering Azure resource providers")
        try:
            register_resource_providers(
                subscription_id=azure_cfg["subscription_id"],
                wait=True,
                progress_callback=_cb,
            )
        except Exception as exc:
            log.error("Resource provider registration failed: %s", exc)
            raise

    # Validate RBAC permissions
    if "validate_permissions" in active_stages:
        _cb("Stage: validate_permissions – checking Azure RBAC roles")
        try:
            perm_report = validate_permissions(
                subscription_id=azure_cfg["subscription_id"],
                resource_group=azure_cfg["resource_group"],
            )
            if not perm_report.all_ok:
                missing = [c.role_name for c in perm_report.checks if not c.assigned]
                _cb(f"⚠ Missing roles: {', '.join(missing)}")
                if global_cfg.get("abort_on_validation_failure", True):
                    raise RuntimeError(f"Missing RBAC roles: {', '.join(missing)}")
            else:
                _cb("RBAC permissions validated ✔")
        except ImportError:
            log.warning("azure-mgmt-authorization not installed – skipping permission check")

    # Prepare Active Directory
    if "prepare_ad" in active_stages:
        ad_cfg = config.get("active_directory", {})
        cluster_cfg = config.get("cluster", {})
        if ad_cfg.get("enabled", True) and cluster_cfg.get("domain_fqdn"):
            _cb("Stage: prepare_ad – preparing Active Directory")
            ad_config = ADPrepConfig(
                ou_name=ad_cfg.get("ou_name", "AzureLocal"),
                deployment_user=ad_cfg.get("deployment_user", ""),
                deployment_password=ad_cfg.get("deployment_password", ""),
                domain_fqdn=cluster_cfg.get("domain_fqdn", ""),
            )
            host_for_ad = ad_cfg.get("dc_host", "")
            if host_for_ad:
                prepare_active_directory(
                    host=host_for_ad,
                    user=ad_cfg.get("dc_user", "Administrator"),
                    password=ad_cfg.get("dc_password", ""),
                    config=ad_config,
                    progress_callback=_cb,
                )
            else:
                _cb("AD prep skipped – no dc_host specified in active_directory config")
        else:
            _cb("AD prep skipped – AD-less deployment or domain_fqdn not set")

    # ------------------------------------------------------------------
    # Pre-flight validation
    # ------------------------------------------------------------------
    if "validate_nodes" in active_stages:
        _cb("Stage: validate_nodes – pre-flight checks")
        abort = global_cfg.get("abort_on_validation_failure", True)
        validate_all_nodes(
            servers,
            progress_callback=_cb,
            abort_on_failure=abort,
        )
    # ------------------------------------------------------------------
    # Environment Checker (Azure Local helper-script readiness checks)
    # ------------------------------------------------------------------
    if "environment_check" in active_stages:
        _cb("Stage: environment_check \u2013 Microsoft Environment Checker")
        env_cfg: dict = config.get("environment_checker", {})
        abort = global_cfg.get("abort_on_validation_failure", True)
        env_validators = env_cfg.get("validators", None)  # None = all 5
        run_environment_checker_all_nodes(
            servers,
            validators=env_validators,
            install_timeout=int(env_cfg.get("install_timeout", 300)),
            validator_timeout=int(env_cfg.get("validator_timeout", 600)),
            auto_uninstall=env_cfg.get("auto_uninstall", True),
            abort_on_failure=abort,
            progress_callback=_cb,
        )
    # ------------------------------------------------------------------
    # Per-server stages (firmware, BIOS, OS, network, time, agent)
    # ------------------------------------------------------------------
    node_hosts: list[dict[str, str]] = []

    for idx, server in enumerate(servers, 1):
        require_keys(server, ["idrac_host", "idrac_user", "idrac_password", "host_ip"], context=f"server #{idx}")

        log.info("\n[bold]──── Server %d/%d: %s ────[/]", idx, len(servers), server["idrac_host"])
        _cb(f"Processing server {idx}/{len(servers)}: {server['idrac_host']}")

        host_user = server.get("host_user", "Administrator")
        host_password = server.get("host_password", server["idrac_password"])
        ssh_port = int(server.get("ssh_port", 22))

        # -- Update firmware -----------------------------------------------
        if "update_firmware" in active_stages:
            _cb(f"Stage: update_firmware – {server['idrac_host']}")
            catalog_url = (
                server.get("firmware_catalog_url")
                or firmware_cfg.get("catalog_url", "")
            )
            # Build individual firmware targets from config
            fw_targets: list[FirmwareTarget] = []
            for t in server.get("firmware_targets", firmware_cfg.get("targets", [])):
                fw_targets.append(FirmwareTarget(
                    component=t.get("component", "Unknown"),
                    dup_url=t.get("dup_url", ""),
                    target_version=t.get("target_version", ""),
                    install_option=t.get("install_option", "NowAndReboot"),
                ))

            with IdracClient(server["idrac_host"], server["idrac_user"], server["idrac_password"]) as idrac:
                update_firmware(
                    idrac,
                    targets=fw_targets if fw_targets else None,
                    catalog_url=catalog_url,
                    apply_reboot=firmware_cfg.get("apply_reboot", True),
                    task_timeout=int(firmware_cfg.get("task_timeout", 3600)),
                    progress_callback=_cb,
                )

        # -- Configure BIOS ------------------------------------------------
        if "configure_bios" in active_stages:
            _cb(f"Stage: configure_bios – {server['idrac_host']}")
            custom_bios = server.get("bios_attributes", bios_cfg.get("attributes", {}))
            profile_name = bios_cfg.get("profile", "AzureLocal")

            with IdracClient(server["idrac_host"], server["idrac_user"], server["idrac_password"]) as idrac:
                configure_bios(
                    idrac,
                    profile=BiosProfile(name=profile_name),
                    custom_attributes=custom_bios if custom_bios else None,
                    apply_reboot=bios_cfg.get("apply_reboot", True),
                    task_timeout=int(bios_cfg.get("task_timeout", 1200)),
                    progress_callback=_cb,
                )

        # -- Deploy OS -----------------------------------------------------
        if "deploy_os" in active_stages:
            iso_url = server.get("iso_url") or global_cfg.get("iso_url", "")
            if not iso_url:
                raise ValueError(f"Server {server['idrac_host']}: no iso_url provided")
            _cb(f"Stage: deploy_os – {server['idrac_host']}")

            with IdracClient(server["idrac_host"], server["idrac_user"], server["idrac_password"]) as idrac:
                deploy_os_image(
                    idrac,
                    iso_url=iso_url,
                    host_ip=server["host_ip"],
                    host_user=host_user,
                    host_password=host_password,
                    install_timeout=int(server.get("install_timeout", 3600)),
                    ssh_port=ssh_port,
                )

        # -- Configure network ---------------------------------------------
        if "configure_network" in active_stages:
            _cb(f"Stage: configure_network – {server['idrac_host']}")
            nic_defs = server.get("nics", [])
            nics = [NicConfig(**n) for n in nic_defs]
            if nics:
                configure_network(server["host_ip"], host_user, host_password, nics, ssh_port=ssh_port)
            else:
                log.warning("No NIC definitions for server %s – skipping network config", server["idrac_host"])

        # -- Configure proxy (optional) ------------------------------------
        if "configure_proxy" in active_stages:
            proxy_cfg = config.get("proxy", {})
            http_proxy = proxy_cfg.get("http_proxy") or global_cfg.get("proxy_url", "")
            if http_proxy:
                _cb(f"Stage: configure_proxy – {server['idrac_host']}")
                proxy = ProxyConfig(
                    http_proxy=http_proxy,
                    https_proxy=proxy_cfg.get("https_proxy", http_proxy),
                    no_proxy=proxy_cfg.get("no_proxy", ""),
                )
                configure_proxy(
                    server["host_ip"], host_user, host_password,
                    config=proxy, ssh_port=ssh_port,
                )

        # -- Configure time ------------------------------------------------
        if "configure_time" in active_stages:
            _cb(f"Stage: configure_time – {server['idrac_host']}")
            ntp = server.get("ntp_servers") or global_cfg.get("ntp_servers", ["time.windows.com"])
            tz = server.get("timezone") or global_cfg.get("timezone")
            configure_time_server(
                server["host_ip"], host_user, host_password,
                ntp_servers=ntp, ssh_port=ssh_port, timezone=tz,
            )

        # -- Deploy Azure Local agent --------------------------------------
        if "deploy_agent" in active_stages:
            _cb(f"Stage: deploy_agent – {server['idrac_host']}")
            deploy_agent(
                server["host_ip"], host_user, host_password,
                tenant_id=azure_cfg["tenant_id"],
                subscription_id=azure_cfg["subscription_id"],
                resource_group=azure_cfg["resource_group"],
                region=azure_cfg["region"],
                arc_gateway_id=server.get("arc_gateway_id", ""),
                proxy_url=server.get("proxy_url", global_cfg.get("proxy_url", "")),
                ssh_port=ssh_port,
            )

        # -- Configure security settings -----------------------------------
        if "configure_security" in active_stages:
            _cb(f"Stage: configure_security – {server['idrac_host']}")
            sec_cfg = config.get("security", {})
            profile_name = sec_cfg.get("profile", "recommended")
            if profile_name == "recommended":
                sec_profile = RECOMMENDED_SECURITY
            else:
                sec_profile = SecurityProfile(**{
                    k: sec_cfg.get(k, True)
                    for k in SecurityProfile.__dataclass_fields__
                })
            configure_security(
                server["host_ip"], host_user, host_password,
                profile=sec_profile, ssh_port=ssh_port,
                progress_callback=_cb,
            )

        # Collect node info for the cluster stage
        node_hosts.append({
            "host": server["host_ip"],
            "user": host_user,
            "password": host_password,
            "arc_resource_id": server.get("arc_resource_id", ""),
        })

    # ------------------------------------------------------------------
    # Cluster-level stages
    # ------------------------------------------------------------------

    # Provision Key Vault for cluster secrets
    if "provision_keyvault" in active_stages:
        kv_cfg = config.get("keyvault", {})
        kv_name = kv_cfg.get("name", f"{cluster_cfg.get('name', 'azlocal')[:16]}-kv")
        _cb("Stage: provision_keyvault")
        try:
            provision_keyvault(
                subscription_id=azure_cfg["subscription_id"],
                resource_group=azure_cfg["resource_group"],
                vault_name=kv_name,
                region=azure_cfg["region"],
                tenant_id=azure_cfg["tenant_id"],
            )
            _cb(f"Key Vault '{kv_name}' provisioned ✔")
        except Exception as exc:
            log.warning("Key Vault provisioning failed (non-blocking): %s", exc)
            _cb(f"Key Vault provisioning failed: {exc}")

    # Provision cloud witness storage account
    if "cloud_witness" in active_stages:
        cw_cfg = config.get("cloud_witness", {})
        cw_name = cw_cfg.get("storage_account_name", "")
        if cw_name and node_hosts:
            _cb("Stage: cloud_witness")
            try:
                account_name, key = provision_cloud_witness(
                    subscription_id=azure_cfg["subscription_id"],
                    resource_group=azure_cfg["resource_group"],
                    storage_account_name=cw_name,
                    region=azure_cfg["region"],
                )
                primary = node_hosts[0]
                configure_cluster_witness(
                    host=primary["host"],
                    user=primary["user"],
                    password=primary["password"],
                    storage_account_name=account_name,
                    storage_account_key=key,
                )
                _cb("Cloud witness configured ✔")
            except Exception as exc:
                log.warning("Cloud witness setup failed (non-blocking): %s", exc)
                _cb(f"Cloud witness setup failed: {exc}")

    # Deploy cluster
    if "deploy_cluster" in active_stages:
        _cb("Stage: deploy_cluster")
        cluster_cfg = config.get("cluster", {})
        deploy_cluster(
            subscription_id=azure_cfg["subscription_id"],
            resource_group=azure_cfg["resource_group"],
            cluster_name=cluster_cfg.get("name", "azlocal-cluster"),
            region=azure_cfg["region"],
            tenant_id=azure_cfg["tenant_id"],
            node_hosts=node_hosts,
            domain_fqdn=cluster_cfg.get("domain_fqdn", ""),
            ou_path=cluster_cfg.get("ou_path", ""),
            cluster_ip=cluster_cfg.get("cluster_ip", ""),
            storage_network_list=cluster_cfg.get("storage_networks"),
            deployment_timeout=int(cluster_cfg.get("deployment_timeout", 7200)),
        )

    # Post-deployment tasks
    if "post_deploy" in active_stages:
        _cb("Stage: post_deploy")
        pd_cfg = config.get("post_deploy", {})
        cluster_cfg = config.get("cluster", {})
        try:
            pd_report = run_post_deployment(
                subscription_id=azure_cfg["subscription_id"],
                resource_group=azure_cfg["resource_group"],
                cluster_name=cluster_cfg.get("name", "azlocal-cluster"),
                node_hosts=node_hosts,
                enable_health_monitoring=pd_cfg.get("enable_health_monitoring", True),
                enable_rdp=pd_cfg.get("enable_rdp", False),
                create_workload_volumes=pd_cfg.get("create_workload_volumes", True),
                progress_callback=_cb,
            )
            if pd_report.all_ok:
                _cb("Post-deployment tasks completed ✔")
            else:
                failed = [t.name for t in pd_report.tasks if not t.success]
                _cb(f"⚠ Some post-deployment tasks failed: {', '.join(failed)}")
        except Exception as exc:
            log.warning("Post-deployment tasks failed (non-blocking): %s", exc)
            _cb(f"Post-deployment tasks failed: {exc}")

    log.info("\n[bold green]===== Pipeline complete =====[/]")
    _cb("Pipeline complete")
