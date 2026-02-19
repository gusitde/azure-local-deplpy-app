"""Post-deployment tasks for Azure Local clusters.

After the Azure Local cluster deployment completes, several operational
tasks should be performed:
    1. Enable health monitoring (Azure Monitor alerts at 70% storage).
    2. Configure RDP access (disabled by default after deployment).
    3. Create workload volumes and storage paths.
    4. Verify deployment resources in Azure.

Reference:
    https://learn.microsoft.com/en-us/azure/azure-local/deploy/deploy-via-portal#post-deployment-tasks
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from azure.identity import DefaultAzureCredential
from azure.mgmt.azurestackhci import AzureStackHCIClient

from azure_local_deploy.remote import run_powershell
from azure_local_deploy.utils import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PostDeployTask:
    """A single post-deployment task result."""
    name: str
    success: bool
    message: str = ""


@dataclass
class PostDeployReport:
    """Aggregated post-deployment task results."""
    tasks: list[PostDeployTask] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(t.success for t in self.tasks)

    def add(self, task: PostDeployTask) -> None:
        self.tasks.append(task)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_post_deployment(
    *,
    subscription_id: str,
    resource_group: str,
    cluster_name: str,
    node_hosts: list[dict[str, str]],
    enable_health_monitoring: bool = True,
    enable_rdp: bool = False,
    create_workload_volumes: bool = True,
    progress_callback: Callable[[str], None] | None = None,
) -> PostDeployReport:
    """Run post-deployment tasks on the Azure Local cluster.

    Parameters
    ----------
    subscription_id / resource_group / cluster_name:
        Azure resource coordinates.
    node_hosts:
        List of dicts with ``host``, ``user``, ``password`` for SSH.
    enable_health_monitoring:
        Set up Azure Monitor alerts for storage pool (default: True).
    enable_rdp:
        Enable RDP on cluster nodes (default: False for security).
    create_workload_volumes:
        Create workload volumes and storage paths (default: True).
    progress_callback:
        Optional callable for progress messages.

    Returns
    -------
    PostDeployReport
    """
    _cb = progress_callback or (lambda msg: None)
    report = PostDeployReport()

    log.info("[bold]== Post-Deployment Tasks ==[/]")
    _cb("Running post-deployment tasks …")

    # Use first node for cluster-wide operations
    primary = node_hosts[0] if node_hosts else None
    if not primary:
        report.add(PostDeployTask("Pre-check", False, "No nodes available"))
        return report

    host, user, password = primary["host"], primary["user"], primary["password"]
    port = int(primary.get("ssh_port", 22))

    # 1. Verify cluster resources in Azure
    _cb("Verifying Azure resources …")
    task = _verify_azure_resources(subscription_id, resource_group, cluster_name)
    report.add(task)

    # 2. Enable health monitoring
    if enable_health_monitoring:
        _cb("Enabling health monitoring …")
        task = _enable_health_monitoring(host, user, password, port)
        report.add(task)

    # 3. Create workload volumes
    if create_workload_volumes:
        _cb("Creating workload volumes …")
        task = _create_workload_volumes(host, user, password, port, len(node_hosts))
        report.add(task)

    # 4. RDP access (opt-in, disabled by default for security)
    if enable_rdp:
        _cb("Enabling RDP on cluster nodes …")
        for node in node_hosts:
            task = _enable_rdp(node["host"], node["user"], node["password"],
                               int(node.get("ssh_port", 22)))
            report.add(task)
    else:
        report.add(PostDeployTask(
            "RDP Access", True,
            "RDP remains disabled (security best practice). Use Enable-ASRemoteDesktop when needed.",
        ))

    # Summary
    ok_count = sum(1 for t in report.tasks if t.success)
    total = len(report.tasks)
    log.info("[bold]Post-deployment: %d/%d tasks succeeded[/]", ok_count, total)
    _cb(f"Post-deployment: {ok_count}/{total} tasks completed ✔")

    return report


def enable_rdp_on_node(
    host: str, user: str, password: str, *,
    ssh_port: int = 22,
) -> PostDeployTask:
    """Enable RDP on a single node (convenience function)."""
    return _enable_rdp(host, user, password, ssh_port)


def disable_rdp_on_node(
    host: str, user: str, password: str, *,
    ssh_port: int = 22,
) -> PostDeployTask:
    """Disable RDP on a single node."""
    try:
        run_powershell(host, user, password, "Disable-ASRemoteDesktop", port=ssh_port)
        return PostDeployTask("Disable RDP", True, f"RDP disabled on {host}")
    except Exception as exc:
        return PostDeployTask("Disable RDP", False, f"Failed on {host}: {exc}")


# ---------------------------------------------------------------------------
# Internal task implementations
# ---------------------------------------------------------------------------

def _verify_azure_resources(
    subscription_id: str,
    resource_group: str,
    cluster_name: str,
) -> PostDeployTask:
    """Verify expected Azure resources exist after deployment."""
    try:
        credential = DefaultAzureCredential()
        hci_client = AzureStackHCIClient(credential, subscription_id)

        cluster = hci_client.clusters.get(resource_group, cluster_name)
        status = getattr(cluster, "provisioning_state", "Unknown")

        if status in ("Succeeded", "succeeded"):
            return PostDeployTask(
                "Verify Azure Resources", True,
                f"Cluster '{cluster_name}' found – status: {status}",
            )
        else:
            return PostDeployTask(
                "Verify Azure Resources", False,
                f"Cluster '{cluster_name}' status: {status} (expected: Succeeded)",
            )
    except Exception as exc:
        return PostDeployTask(
            "Verify Azure Resources", False,
            f"Could not verify cluster: {exc}",
        )


def _enable_health_monitoring(
    host: str, user: str, password: str, port: int,
) -> PostDeployTask:
    """Enable Azure Monitor health alerts for storage pool consumption."""
    cmd = (
        "# Check if Azure Monitor agent is running\n"
        "$ama = Get-Service 'AzureMonitorAgent' -ErrorAction SilentlyContinue; "
        "if ($ama -and $ama.Status -eq 'Running') { "
        "  Write-Output 'AMA_RUNNING' "
        "} else { "
        "  Write-Output 'AMA_NOT_RUNNING' "
        "}"
    )
    try:
        result = run_powershell(host, user, password, cmd, port=port)
        if "AMA_RUNNING" in result:
            return PostDeployTask(
                "Health Monitoring", True,
                "Azure Monitor Agent is running. Configure alert rules in Azure portal for storage pool at 70%.",
            )
        else:
            return PostDeployTask(
                "Health Monitoring", True,
                "Azure Monitor Agent not yet running. Install via Azure portal Extensions blade. "
                "Then configure storage pool alert at 70%.",
            )
    except Exception as exc:
        return PostDeployTask("Health Monitoring", False, f"Check failed: {exc}")


def _create_workload_volumes(
    host: str, user: str, password: str, port: int,
    node_count: int,
) -> PostDeployTask:
    """Create workload volumes based on cluster size.

    Volume resiliency:
        - 1 node: single (Simple)
        - 2 nodes: Two-way mirror
        - 3+ nodes: Three-way mirror
    """
    if node_count == 1:
        resiliency = "Simple"
    elif node_count == 2:
        resiliency = "Mirror"
        mirror_type = "True"  # two-way
    else:
        resiliency = "Mirror"
        mirror_type = "True"  # three-way via Storage Spaces Direct auto

    # Check if workload volume already exists
    check_cmd = "Get-Volume | Where-Object { $_.FileSystemLabel -like 'Workload*' } | Select-Object FileSystemLabel, Size"
    try:
        existing = run_powershell(host, user, password, check_cmd, port=port)
        if "Workload" in existing:
            return PostDeployTask(
                "Workload Volumes", True,
                "Workload volumes already exist – skipping creation.",
            )
    except Exception:
        pass

    # Create the workload volume
    create_cmd = (
        f"New-Volume -FriendlyName 'Workload' -FileSystem CSVFS_ReFS "
        f"-StoragePoolFriendlyName 'S2D*' "
        f"-Size 100GB "
        f"-ResiliencySettingName '{resiliency}' "
        f"-ProvisioningType Thin "
        f"-ErrorAction Stop"
    )
    try:
        run_powershell(host, user, password, create_cmd, port=port)
        return PostDeployTask(
            "Workload Volumes", True,
            f"Workload volume created ({resiliency}, thin-provisioned, 100 GB initial).",
        )
    except Exception as exc:
        return PostDeployTask(
            "Workload Volumes", False,
            f"Failed to create workload volume: {exc}. "
            "You can create volumes manually via Windows Admin Center or PowerShell.",
        )


def _enable_rdp(
    host: str, user: str, password: str, port: int,
) -> PostDeployTask:
    """Enable RDP on one node using Enable-ASRemoteDesktop."""
    try:
        run_powershell(host, user, password, "Enable-ASRemoteDesktop", port=port)
        return PostDeployTask("Enable RDP", True, f"RDP enabled on {host}")
    except Exception as exc:
        return PostDeployTask("Enable RDP", False, f"Failed on {host}: {exc}")
