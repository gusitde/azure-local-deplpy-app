"""Install and register the Azure Local agent (Azure Arc) on each node.

The agent connects each server to Azure Arc, which is a prerequisite for
creating the Azure Local cluster resource in Azure.
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
) -> None:
    """Download, install, and register the Azure Connected Machine agent.

    This performs the equivalent of running the Azure Arc onboarding script
    on each Azure Local node.

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
    """
    log.info("[bold]== Stage: Azure Local Agent Deployment ==[/] on %s", host)

    # 1. Install prerequisites ---------------------------------------------
    log.info("Installing NuGet provider & Az.StackHCI module …")
    prereq_cmds = [
        "Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -ErrorAction SilentlyContinue",
        "Set-PSRepository -Name PSGallery -InstallationPolicy Trusted",
        "Install-Module -Name Az.StackHCI -Force -AllowClobber -ErrorAction Stop",
    ]
    run_powershell(host, user, password, "; ".join(prereq_cmds), port=ssh_port)

    # 2. Download & install the Azure Connected Machine agent ---------------
    log.info("Downloading Azure Connected Machine agent …")
    install_cmds = [
        "$ProgressPreference = 'SilentlyContinue'",
        "Invoke-WebRequest -Uri 'https://aka.ms/AzureConnectedMachineAgent' -OutFile C:\\Temp\\AzureConnectedMachineAgent.msi",
        "Start-Process msiexec.exe -ArgumentList '/i C:\\Temp\\AzureConnectedMachineAgent.msi /qn /norestart' -Wait -NoNewWindow",
    ]
    run_powershell(host, user, password, "; ".join(install_cmds), port=ssh_port)

    # 3. Register with Azure Arc -------------------------------------------
    log.info("Registering node with Azure Arc …")
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

    # 4. Verify registration -----------------------------------------------
    _verify_arc_status(host, user, password, ssh_port)

    log.info("[bold green]Agent deployment complete[/] on %s", host)


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
