"""Top-level orchestrator that drives the full deployment pipeline.

Reads a YAML config file and executes each stage in order for every server.
"""

from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any

from azure_local_deploy.idrac_client import IdracClient
from azure_local_deploy.deploy_os import deploy_os_image
from azure_local_deploy.configure_network import configure_network, NicConfig
from azure_local_deploy.configure_time import configure_time_server
from azure_local_deploy.deploy_agent import deploy_agent
from azure_local_deploy.deploy_cluster import deploy_cluster
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
    "deploy_os",
    "configure_network",
    "configure_time",
    "deploy_agent",
    "deploy_cluster",
]


def run_pipeline(
    config: dict[str, Any],
    *,
    stages: list[str] | None = None,
    dry_run: bool = False,
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
    """
    active_stages = stages or STAGES
    servers: list[dict] = config["servers"]
    azure_cfg: dict = config["azure"]
    global_cfg: dict = config.get("global", {})

    log.info("[bold magenta]===== Azure Local Deploy Pipeline =====[/]")
    log.info("Servers : %d", len(servers))
    log.info("Stages  : %s", ", ".join(active_stages))
    if dry_run:
        log.info("[yellow]DRY RUN – no changes will be made[/]")
        return

    # ------------------------------------------------------------------
    # Per-server stages (OS, network, time, agent)
    # ------------------------------------------------------------------
    node_hosts: list[dict[str, str]] = []

    for idx, server in enumerate(servers, 1):
        require_keys(server, ["idrac_host", "idrac_user", "idrac_password", "host_ip"], context=f"server #{idx}")

        log.info("\n[bold]──── Server %d/%d: %s ────[/]", idx, len(servers), server["idrac_host"])

        host_user = server.get("host_user", "Administrator")
        host_password = server.get("host_password", server["idrac_password"])
        ssh_port = int(server.get("ssh_port", 22))

        # -- Deploy OS -----------------------------------------------------
        if "deploy_os" in active_stages:
            iso_url = server.get("iso_url") or global_cfg.get("iso_url", "")
            if not iso_url:
                raise ValueError(f"Server {server['idrac_host']}: no iso_url provided")

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
            nic_defs = server.get("nics", [])
            nics = [NicConfig(**n) for n in nic_defs]
            if nics:
                configure_network(server["host_ip"], host_user, host_password, nics, ssh_port=ssh_port)
            else:
                log.warning("No NIC definitions for server %s – skipping network config", server["idrac_host"])

        # -- Configure time ------------------------------------------------
        if "configure_time" in active_stages:
            ntp = server.get("ntp_servers") or global_cfg.get("ntp_servers", ["time.windows.com"])
            tz = server.get("timezone") or global_cfg.get("timezone")
            configure_time_server(
                server["host_ip"], host_user, host_password,
                ntp_servers=ntp, ssh_port=ssh_port, timezone=tz,
            )

        # -- Deploy Azure Local agent --------------------------------------
        if "deploy_agent" in active_stages:
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

        # Collect node info for the cluster stage
        node_hosts.append({
            "host": server["host_ip"],
            "user": host_user,
            "password": host_password,
            "arc_resource_id": server.get("arc_resource_id", ""),
        })

    # ------------------------------------------------------------------
    # Cluster-level stage
    # ------------------------------------------------------------------
    if "deploy_cluster" in active_stages:
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

    log.info("\n[bold green]===== Pipeline complete =====[/]")
