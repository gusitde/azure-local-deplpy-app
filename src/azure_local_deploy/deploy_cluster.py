"""Create (or validate) an Azure Local cluster via the Azure management plane.

Uses the Azure SDK for Python to:
    1. Validate prerequisites on each Arc-enabled node.
    2. Create the Azure Local cluster resource.
    3. Deploy the cluster (runs the cloud-orchestrated deployment).
    4. Poll until the deployment completes.
"""

from __future__ import annotations

import time
from typing import Any

from azure.identity import DefaultAzureCredential
from azure.mgmt.azurestackhci import AzureStackHCIClient

from azure_local_deploy.remote import run_powershell
from azure_local_deploy.utils import get_logger, require_keys

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def deploy_cluster(
    *,
    subscription_id: str,
    resource_group: str,
    cluster_name: str,
    region: str,
    tenant_id: str,
    node_hosts: list[dict[str, str]],
    domain_fqdn: str = "",
    ou_path: str = "",
    cluster_ip: str = "",
    storage_network_list: list[dict] | None = None,
    deployment_timeout: int = 7200,
) -> dict[str, Any]:
    """Orchestrate end-to-end Azure Local cluster creation.

    Parameters
    ----------
    subscription_id / resource_group / cluster_name / region:
        Azure resource coordinates.
    tenant_id:
        AAD tenant.
    node_hosts:
        List of dicts with keys ``host``, ``user``, ``password`` for SSH access
        to each node, plus ``arc_resource_id`` (Azure resource id of the Arc
        machine).
    domain_fqdn / ou_path:
        Active Directory info for the cluster (optional for AD-less deployments).
    cluster_ip:
        Static IP for the failover-cluster name object.
    storage_network_list:
        Optional per-node storage networks.
    deployment_timeout:
        Max seconds to wait for deployment to finish.
    """
    log.info("[bold]== Stage: Cluster Deployment ==[/]")
    log.info("Cluster: %s  RG: %s  Region: %s", cluster_name, resource_group, region)

    # --- Authenticate -----------------------------------------------------
    credential = DefaultAzureCredential()
    hci_client = AzureStackHCIClient(credential, subscription_id)

    # --- 1. Validate nodes ------------------------------------------------
    log.info("Validating %d node(s) …", len(node_hosts))
    for node in node_hosts:
        _validate_node(node)

    # --- 2. Create or update the cluster resource -------------------------
    log.info("Creating Azure Local cluster resource [cyan]%s[/] …", cluster_name)
    arc_node_ids = [n["arc_resource_id"] for n in node_hosts]

    cluster_payload: dict[str, Any] = {
        "location": region,
        "properties": {
            "aadTenantId": tenant_id,
        },
    }

    poller = hci_client.clusters.begin_create_or_update(
        resource_group_name=resource_group,
        cluster_name=cluster_name,
        cluster=cluster_payload,
    )
    cluster_result = poller.result()
    log.info("Cluster resource created – id: %s", cluster_result.id)

    # --- 3. Trigger deployment --------------------------------------------
    log.info("Initiating cluster deployment …")

    deployment_settings: dict[str, Any] = {
        "properties": {
            "arcNodeResourceIds": arc_node_ids,
            "deploymentMode": "Deploy",
            "deploymentConfiguration": {
                "version": "10.0.0.0",
                "scaleUnits": [
                    {
                        "deploymentData": {
                            "securitySettings": {
                                "hvciProtection": True,
                                "drtmProtection": True,
                                "driftControlEnforced": True,
                                "credentialGuardEnforced": True,
                                "smbSigningEnforced": True,
                                "smbClusterEncryption": True,
                                "sideChannelMitigationEnforced": True,
                                "bitlockerBootVolume": True,
                                "bitlockerDataVolumes": True,
                                "wdacEnforced": True,
                            },
                            "observability": {
                                "streamingDataClient": True,
                                "euLocation": False,
                                "episodicDataUpload": True,
                            },
                            "cluster": {
                                "name": cluster_name,
                                "azureServiceEndpoint": "core.windows.net",
                            },
                            "namingPrefix": cluster_name[:8],
                            "domainFqdn": domain_fqdn,
                            "adouPath": ou_path,
                        }
                    }
                ],
            },
        },
    }

    if cluster_ip:
        deployment_settings["properties"]["deploymentConfiguration"]["scaleUnits"][0][
            "deploymentData"]["cluster"]["clusterIp"] = cluster_ip

    deploy_poller = hci_client.deployment_settings.begin_create_or_update(
        resource_group_name=resource_group,
        cluster_name=cluster_name,
        deployment_settings_name="default",
        resource=deployment_settings,
    )

    # --- 4. Poll deployment -----------------------------------------------
    log.info("Polling deployment (timeout=%ds) …", deployment_timeout)
    result = deploy_poller.result(timeout=deployment_timeout)
    status = getattr(result, "provisioning_state", "Unknown")
    log.info("Deployment finished – provisioning state: [bold]%s[/]", status)

    if status not in ("Succeeded", "succeeded"):
        raise RuntimeError(f"Cluster deployment ended with status: {status}")

    log.info("[bold green]Cluster deployment succeeded![/]")
    return {"cluster_name": cluster_name, "status": status, "resource_id": cluster_result.id}


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _validate_node(node: dict[str, str]) -> None:
    """Run quick pre-flight checks on one node."""
    require_keys(node, ["host", "user", "password", "arc_resource_id"], context="node definition")

    host, user, password = node["host"], node["user"], node["password"]
    log.info("  Validating node %s …", host)

    # Check Arc agent
    result = run_powershell(
        host, user, password,
        "& \"$env:ProgramFiles\\AzureConnectedMachineAgent\\azcmagent.exe\" show -j",
    )
    if '"Connected"' not in result:
        raise RuntimeError(f"Node {host} Arc agent is not Connected. Output: {result[:300]}")

    # Check basic hardware readiness
    run_powershell(
        host, user, password,
        "Get-PhysicalDisk | Select-Object DeviceId, MediaType, Size | Format-Table -AutoSize",
    )
    log.info("  ✔ Node %s passed pre-flight checks", host)
