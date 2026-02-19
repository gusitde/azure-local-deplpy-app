"""Install and register the Azure Local agent (Azure Arc) on each node.

The agent connects each server to Azure Arc, which is a prerequisite for
creating the Azure Local cluster resource in Azure.

For Azure Local (Azure Stack HCI), Microsoft recommends using the
``Invoke-AzStackHciArcInitialization`` cmdlet which handles agent install,
Arc registration, and required extension deployment in a single step.

Reference:
    https://learn.microsoft.com/en-us/azure/azure-local/deploy/deployment-without-azure-arc-gateway
    https://learn.microsoft.com/en-us/azure/azure-local/manage/add-server
"""

from __future__ import annotations

from azure_local_deploy.remote import run_powershell
from azure_local_deploy.utils import get_logger, require_keys

log = get_logger(__name__)


def deploy_agent(
    host: str,
    user: str,
    password: str,
    *,
    tenant_id: str,
    subscription_id: str,
    resource_group: str,
    region: str,
    arc_gateway_id: str = "",
    proxy_url: str = "",
    ssh_port: int = 22,
    use_hci_init: bool = True,
) -> None:
    """Download, install, and register the Azure Connected Machine agent.

    When *use_hci_init* is ``True`` (default), uses Microsoft's recommended
    ``Invoke-AzStackHciArcInitialization`` cmdlet which installs the agent,
    registers with Arc, **and** deploys required Arc extensions for Azure
    Local.  This is the method documented at:
    https://learn.microsoft.com/en-us/azure/azure-local/deploy/deployment-without-azure-arc-gateway

    When *use_hci_init* is ``False``, falls back to direct ``azcmagent
    connect`` registration (useful for non-Azure Local Arc scenarios).

    Parameters
    ----------
    host / user / password:
        SSH credentials for the node.
    tenant_id:
        Azure AD tenant id.
    subscription_id:
        Azure subscription id where the Arc resource will be created.
    resource_group:
        Target resource group.
    region:
        Azure region (e.g. "eastus").
    arc_gateway_id:
        Optional Arc gateway resource id (for proxy/private-link scenarios).
    proxy_url:
        Optional HTTPS proxy URL.
    use_hci_init:
        When True, use Invoke-AzStackHciArcInitialization (recommended for
        Azure Local).  When False, use raw azcmagent connect.
    """
    log.info("[bold]== Stage: Azure Local Agent Deployment ==[/] on %s", host)

    if use_hci_init:
        _deploy_via_hci_init(
            host, user, password,
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            resource_group=resource_group,
            region=region,
            proxy_url=proxy_url,
            ssh_port=ssh_port,
        )
    else:
        _deploy_via_azcmagent(
            host, user, password,
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            resource_group=resource_group,
            region=region,
            arc_gateway_id=arc_gateway_id,
            proxy_url=proxy_url,
            ssh_port=ssh_port,
        )

    # Verify regardless of method
    _verify_arc_status(host, user, password, ssh_port)
    log.info("[bold green]Agent deployment complete[/] on %s", host)


# ---------------------------------------------------------------------------
# Registration methods
# ---------------------------------------------------------------------------


def _deploy_via_hci_init(
    host: str,
    user: str,
    password: str,
    *,
    tenant_id: str,
    subscription_id: str,
    resource_group: str,
    region: str,
    proxy_url: str = "",
    ssh_port: int = 22,
) -> None:
    """Use ``Invoke-AzStackHciArcInitialization`` for Azure Local registration.

    This is the Microsoft-recommended method that:
    1. Installs the Azure Connected Machine agent
    2. Registers the machine with Azure Arc
    3. Installs required Arc extensions for Azure Local

    Reference:
        https://learn.microsoft.com/en-us/azure/azure-local/deploy/deployment-without-azure-arc-gateway
    """
    # 1. Install prerequisites (Az.StackHCI module)
    log.info("Installing Az.StackHCI module for Invoke-AzStackHciArcInitialization …")
    prereq_cmds = [
        "Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -ErrorAction SilentlyContinue",
        "Set-PSRepository -Name PSGallery -InstallationPolicy Trusted",
        "Install-Module -Name Az.StackHCI -Force -AllowClobber -ErrorAction Stop",
    ]
    run_powershell(host, user, password, "; ".join(prereq_cmds), port=ssh_port)

    # 2. Run Invoke-AzStackHciArcInitialization
    log.info("Running Invoke-AzStackHciArcInitialization on %s …", host)
    proxy_part = f" -Proxy '{proxy_url}'" if proxy_url else ""

    init_cmd = (
        f"Invoke-AzStackHciArcInitialization "
        f"-TenantId '{tenant_id}' "
        f"-SubscriptionID '{subscription_id}' "
        f"-ResourceGroup '{resource_group}' "
        f"-Region '{region}' "
        f"-Cloud 'AzureCloud'"
        f"{proxy_part}"
    )
    run_powershell(host, user, password, init_cmd, port=ssh_port, timeout=600)

    log.info("  ✔ Invoke-AzStackHciArcInitialization completed on %s", host)


def _deploy_via_azcmagent(
    host: str,
    user: str,
    password: str,
    *,
    tenant_id: str,
    subscription_id: str,
    resource_group: str,
    region: str,
    arc_gateway_id: str = "",
    proxy_url: str = "",
    ssh_port: int = 22,
) -> None:
    """Fallback: install agent manually and register via ``azcmagent connect``."""
    # 1. Install prerequisites
    log.info("Installing NuGet provider & Az.StackHCI module …")
    prereq_cmds = [
        "Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -ErrorAction SilentlyContinue",
        "Set-PSRepository -Name PSGallery -InstallationPolicy Trusted",
        "Install-Module -Name Az.StackHCI -Force -AllowClobber -ErrorAction Stop",
    ]
    run_powershell(host, user, password, "; ".join(prereq_cmds), port=ssh_port)

    # 2. Download & install the Azure Connected Machine agent
    log.info("Downloading Azure Connected Machine agent …")
    install_cmds = [
        "$ProgressPreference = 'SilentlyContinue'",
        "Invoke-WebRequest -Uri 'https://aka.ms/AzureConnectedMachineAgent' -OutFile C:\\Temp\\AzureConnectedMachineAgent.msi",
        "Start-Process msiexec.exe -ArgumentList '/i C:\\Temp\\AzureConnectedMachineAgent.msi /qn /norestart' -Wait -NoNewWindow",
    ]
    run_powershell(host, user, password, "; ".join(install_cmds), port=ssh_port)

    # 3. Register with Azure Arc
    log.info("Registering node with Azure Arc via azcmagent connect …")
    proxy_part = f" --proxy-url '{proxy_url}'" if proxy_url else ""
    gw_part = f" --gateway-id '{arc_gateway_id}'" if arc_gateway_id else ""

    register_cmd = (
        f"& \"$env:ProgramFiles\\AzureConnectedMachineAgent\\azcmagent.exe\" connect "
        f"--tenant-id '{tenant_id}' "
        f"--subscription-id '{subscription_id}' "
        f"--resource-group '{resource_group}' "
        f"--location '{region}' "
        f"--cloud 'AzureCloud'"
        f"{proxy_part}{gw_part}"
    )
    run_powershell(host, user, password, register_cmd, port=ssh_port)


def _verify_arc_status(host: str, user: str, password: str, port: int) -> None:
    """Check that the Arc agent is connected."""
    result = run_powershell(
        host, user, password,
        "& \"$env:ProgramFiles\\AzureConnectedMachineAgent\\azcmagent.exe\" show -j",
        port=port,
    )
    if '"status": "Connected"' in result or '"status":"Connected"' in result:
        log.info("  ✔ Arc agent is [bold green]Connected[/] on %s", host)
    else:
        log.warning("  ✘ Arc agent status unclear on %s – check manually:\n%s", host, result[:500])
