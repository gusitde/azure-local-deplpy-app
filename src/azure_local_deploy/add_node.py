"""Add a node to an existing Azure Local cluster.

Workflow:
    1. Deploy OS on the new server via iDRAC (same as new-cluster flow).
    2. Configure network adapters to match existing cluster topology.
    3. Configure NTP to match existing cluster time source.
    4. Install & register Azure Arc agent.
    5. Use the Azure SDK to add the Arc-enabled node to the existing cluster.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from azure.identity import DefaultAzureCredential
from azure.mgmt.azurestackhci import AzureStackHCIClient

from azure_local_deploy.idrac_client import IdracClient
from azure_local_deploy.deploy_os import deploy_os_image
from azure_local_deploy.configure_network import configure_network, NicConfig
from azure_local_deploy.configure_time import configure_time_server
from azure_local_deploy.deploy_agent import deploy_agent
from azure_local_deploy.remote import run_powershell
from azure_local_deploy.utils import get_logger, require_keys

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_node_to_cluster(
    *,
    subscription_id: str,
    resource_group: str,
    cluster_name: str,
    region: str,
    tenant_id: str,
    new_node: dict[str, str],
    deployment_timeout: int = 3600,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Add a prepared & Arc-registered node to an existing Azure Local cluster.

    Parameters
    ----------
    subscription_id / resource_group / cluster_name / region / tenant_id:
        Azure coordinates of the *existing* cluster.
    new_node:
        Dict with ``host``, ``user``, ``password``, ``arc_resource_id``.
    deployment_timeout:
        Max seconds to wait for the add-node operation.
    progress_callback:
        Optional callable that receives progress messages.
    """
    _cb = progress_callback or (lambda msg: None)

    require_keys(new_node, ["host", "user", "password", "arc_resource_id"], context="new node")
    arc_id = new_node["arc_resource_id"]

    _cb(f"Adding node {new_node['host']} (Arc ID: {arc_id}) to cluster {cluster_name}")
    log.info("[bold]== Stage: Add Node to Cluster ==[/]")

    # Authenticate
    credential = DefaultAzureCredential()
    hci_client = AzureStackHCIClient(credential, subscription_id)

    # Validate node readiness
    _validate_new_node(new_node)
    _cb("Node pre-flight validation passed")

    # Get existing cluster to read current node list
    log.info("Fetching existing cluster [cyan]%s[/] …", cluster_name)
    cluster = hci_client.clusters.get(resource_group, cluster_name)
    _cb(f"Cluster {cluster_name} found – status: {getattr(cluster, 'provisioning_state', '?')}")

    # Trigger the add-node operation via update deployment settings
    log.info("Initiating add-node operation …")
    _cb("Initiating add-node operation via Azure API …")

    # The add-node uses the same deployment-settings endpoint with mode=AddNode
    add_node_payload: dict[str, Any] = {
        "properties": {
            "arcNodeResourceIds": [arc_id],
            "deploymentMode": "AddNode",
        },
    }

    poller = hci_client.deployment_settings.begin_create_or_update(
        resource_group_name=resource_group,
        cluster_name=cluster_name,
        deployment_settings_name="default",
        resource=add_node_payload,
    )

    log.info("Polling add-node operation (timeout=%ds) …", deployment_timeout)
    _cb("Waiting for add-node operation to complete …")

    result = poller.result(timeout=deployment_timeout)
    status = getattr(result, "provisioning_state", "Unknown")

    if status not in ("Succeeded", "succeeded"):
        raise RuntimeError(f"Add-node operation ended with status: {status}")

    _cb(f"Node successfully added to cluster {cluster_name}")
    log.info("[bold green]Node added to cluster %s successfully![/]", cluster_name)

    return {
        "cluster_name": cluster_name,
        "added_node": new_node["host"],
        "arc_resource_id": arc_id,
        "status": status,
    }


# ---------------------------------------------------------------------------
# Full add-node pipeline (from bare metal to cluster member)
# ---------------------------------------------------------------------------

def run_add_node_pipeline(
    config: dict[str, Any],
    *,
    progress_callback: Callable[[str], None] | None = None,
) -> None:
    """Full pipeline: deploy OS → configure → register Arc → add to cluster.

    Reads the same YAML config format but uses the ``add_node`` section
    to identify the existing cluster.
    """
    _cb = progress_callback or (lambda msg: None)

    azure_cfg = config["azure"]
    global_cfg = config.get("global", {})
    add_cfg = config.get("add_node", {})
    servers = config["servers"]

    require_keys(add_cfg, ["existing_cluster_name"], context="add_node config")

    existing_cluster = add_cfg["existing_cluster_name"]
    existing_rg = add_cfg.get("existing_cluster_resource_group", azure_cfg["resource_group"])

    _cb(f"Add-node pipeline: {len(servers)} new node(s) → cluster '{existing_cluster}'")
    log.info("[bold magenta]===== Add Node Pipeline =====[/]")

    for idx, server in enumerate(servers, 1):
        require_keys(server, ["idrac_host", "idrac_user", "idrac_password", "host_ip"],
                      context=f"new node #{idx}")

        host_user = server.get("host_user", "Administrator")
        host_password = server.get("host_password", server["idrac_password"])
        ssh_port = int(server.get("ssh_port", 22))

        _cb(f"── Processing new node {idx}/{len(servers)}: {server['idrac_host']} ──")

        # 1. Deploy OS
        iso_url = server.get("iso_url") or global_cfg.get("iso_url", "")
        if iso_url:
            _cb("Stage: deploy_os – Deploying OS image …")
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
            _cb("Stage: deploy_os – complete")

        # 2. Configure network
        nic_defs = server.get("nics", [])
        if nic_defs:
            _cb("Stage: configure_network – Configuring NICs …")
            nics = [NicConfig(**n) for n in nic_defs]
            configure_network(server["host_ip"], host_user, host_password, nics, ssh_port=ssh_port)
            _cb("Stage: configure_network – complete")

        # 3. Configure time
        ntp = server.get("ntp_servers") or global_cfg.get("ntp_servers", ["time.windows.com"])
        tz = server.get("timezone") or global_cfg.get("timezone")
        _cb("Stage: configure_time – Setting NTP …")
        configure_time_server(server["host_ip"], host_user, host_password,
                              ntp_servers=ntp, ssh_port=ssh_port, timezone=tz)
        _cb("Stage: configure_time – complete")

        # 4. Deploy Arc agent
        _cb("Stage: deploy_agent – Installing Azure Arc agent …")
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
        _cb("Stage: deploy_agent – complete")

        # 5. Add to cluster
        arc_resource_id = server.get("arc_resource_id", "")
        if not arc_resource_id:
            # Try to discover the Arc resource id from the agent itself
            _cb("Discovering Arc resource ID from agent …")
            arc_resource_id = _discover_arc_id(server["host_ip"], host_user, host_password, ssh_port)
            _cb(f"Discovered Arc ID: {arc_resource_id}")

        _cb("Stage: add_node – Adding node to cluster …")
        add_node_to_cluster(
            subscription_id=azure_cfg["subscription_id"],
            resource_group=existing_rg,
            cluster_name=existing_cluster,
            region=azure_cfg["region"],
            tenant_id=azure_cfg["tenant_id"],
            new_node={
                "host": server["host_ip"],
                "user": host_user,
                "password": host_password,
                "arc_resource_id": arc_resource_id,
            },
            progress_callback=progress_callback,
        )
        _cb(f"Node {server['host_ip']} successfully added to cluster '{existing_cluster}'")

    _cb("Add-node pipeline complete")
    log.info("[bold green]===== Add Node Pipeline complete =====[/]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_new_node(node: dict[str, str]) -> None:
    """Quick pre-flight check on the new node."""
    host, user, password = node["host"], node["user"], node["password"]
    log.info("Validating new node %s …", host)

    result = run_powershell(
        host, user, password,
        "& \"$env:ProgramFiles\\AzureConnectedMachineAgent\\azcmagent.exe\" show -j",
    )
    if '"Connected"' not in result:
        raise RuntimeError(f"Node {host}: Arc agent is not Connected. Output: {result[:300]}")

    log.info("  ✔ New node %s passed pre-flight checks", host)


def _discover_arc_id(host: str, user: str, password: str, port: int = 22) -> str:
    """Query the Arc agent on the host to get its Azure resource id."""
    import json as _json

    result = run_powershell(
        host, user, password,
        "& \"$env:ProgramFiles\\AzureConnectedMachineAgent\\azcmagent.exe\" show -j",
        port=port,
    )
    try:
        data = _json.loads(result)
        return data.get("resourceId", data.get("id", ""))
    except Exception:
        # Try to extract from plain text
        for line in result.splitlines():
            if "resourceId" in line or "/machines/" in line:
                return line.split(":", 1)[-1].strip().strip('"').strip(",")
    raise RuntimeError(f"Could not discover Arc resource ID on {host}")
