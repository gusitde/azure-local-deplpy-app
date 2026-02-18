"""Configure network adapters on a freshly installed Azure Local host.

Connects over SSH and runs PowerShell commands to:
    1. Rename network adapters using a deterministic naming scheme.
    2. Assign static IP addresses to management and storage adapters.
    3. Set DNS servers.
    4. Configure VLANs when required.
    5. Verify connectivity (ping gateway / DNS).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from azure_local_deploy.remote import run_powershell
from azure_local_deploy.utils import get_logger, require_keys

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class NicConfig:
    """Configuration for a single network adapter."""
    adapter_name: str              # Final adapter name (e.g. "Mgmt", "Storage1")
    mac_address: str               # MAC used to identify the physical NIC
    ip_address: str                # Static IPv4 address
    prefix_length: int = 24        # Subnet prefix (CIDR)
    gateway: str = ""              # Default gateway (only for mgmt NIC)
    dns_servers: list[str] = field(default_factory=list)
    vlan_id: int | None = None     # Optional VLAN tag


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def configure_network(
    host: str,
    user: str,
    password: str,
    nics: list[NicConfig],
    *,
    ssh_port: int = 22,
) -> None:
    """Apply network configuration on a remote Azure Local host.

    Parameters
    ----------
    host:
        IP / hostname reachable via SSH.
    user / password:
        Admin credentials on the target host.
    nics:
        List of NIC configurations to apply.
    """
    log.info("[bold]== Stage: Network Configuration ==[/] on %s", host)

    for nic in nics:
        _configure_single_nic(host, user, password, nic, ssh_port)

    # Final connectivity check
    _verify_connectivity(host, user, password, nics, ssh_port)
    log.info("[bold green]Network configuration complete[/] on %s", host)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _configure_single_nic(
    host: str,
    user: str,
    password: str,
    nic: NicConfig,
    port: int,
) -> None:
    log.info("Configuring NIC [cyan]%s[/] (MAC=%s) → IP %s/%d",
             nic.adapter_name, nic.mac_address, nic.ip_address, nic.prefix_length)

    # 1. Rename adapter by MAC
    ps_rename = (
        f"$a = Get-NetAdapter | Where-Object {{ $_.MacAddress -replace '-',':' -eq '{nic.mac_address.upper()}' }}; "
        f"if ($a) {{ Rename-NetAdapter -Name $a.Name -NewName '{nic.adapter_name}' -Confirm:$false }}"
    )
    run_powershell(host, user, password, ps_rename, port=port)

    # 2. Remove any existing IP on this adapter
    ps_clear = (
        f"Get-NetIPAddress -InterfaceAlias '{nic.adapter_name}' -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        f"Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue; "
        f"Remove-NetRoute -InterfaceAlias '{nic.adapter_name}' -Confirm:$false -ErrorAction SilentlyContinue"
    )
    run_powershell(host, user, password, ps_clear, port=port)

    # 3. Assign static IP
    gw_part = f" -DefaultGateway '{nic.gateway}'" if nic.gateway else ""
    ps_ip = (
        f"New-NetIPAddress -InterfaceAlias '{nic.adapter_name}' "
        f"-IPAddress '{nic.ip_address}' -PrefixLength {nic.prefix_length}"
        f"{gw_part}"
    )
    run_powershell(host, user, password, ps_ip, port=port)

    # 4. DNS
    if nic.dns_servers:
        servers = ",".join(f"'{s}'" for s in nic.dns_servers)
        ps_dns = f"Set-DnsClientServerAddress -InterfaceAlias '{nic.adapter_name}' -ServerAddresses {servers}"
        run_powershell(host, user, password, ps_dns, port=port)

    # 5. VLAN
    if nic.vlan_id is not None:
        ps_vlan = (
            f"Set-NetAdapter -Name '{nic.adapter_name}' -VlanID {nic.vlan_id} -Confirm:$false "
            f"-ErrorAction SilentlyContinue; "
            f"# Dell NICs may need: Set-NetAdapterAdvancedProperty -Name '{nic.adapter_name}' "
            f"-RegistryKeyword 'VlanID' -RegistryValue {nic.vlan_id}"
        )
        run_powershell(host, user, password, ps_vlan, port=port)


def _verify_connectivity(
    host: str,
    user: str,
    password: str,
    nics: list[NicConfig],
    port: int,
) -> None:
    """Ping each NIC's gateway (if set) from the remote host."""
    for nic in nics:
        if nic.gateway:
            log.info("Verifying connectivity from %s via %s → gw %s",
                     host, nic.adapter_name, nic.gateway)
            result = run_powershell(
                host, user, password,
                f"Test-Connection -ComputerName '{nic.gateway}' -Count 2 -Quiet",
                port=port,
            )
            if "True" in result:
                log.info("  ✔ Gateway %s reachable from %s", nic.gateway, nic.adapter_name)
            else:
                log.warning("  ✘ Gateway %s NOT reachable from %s", nic.gateway, nic.adapter_name)
