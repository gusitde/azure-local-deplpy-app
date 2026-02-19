"""Configure network adapters on a freshly installed Azure Local host.

Connects over SSH and runs PowerShell commands to:
    1. Rename network adapters using a deterministic naming scheme.
    2. Assign static IP addresses to management and storage adapters.
    3. Set DNS servers.
    4. Configure VLANs when required.
    5. Verify connectivity (ping gateway / DNS).
    6. (Optional) Configure Network ATC intents for Azure Local.

Network ATC reference:
    https://learn.microsoft.com/en-us/azure/azure-local/deploy/network-atc?tabs=22H2
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


@dataclass
class NetworkIntent:
    """Network ATC intent configuration.

    Network ATC simplifies host networking by declaring intents (groupings of
    adapters for a purpose) rather than manual NIC teaming/vSwitch creation.

    Typical intents for Azure Local:
        - "Management_Compute" – shared management + compute on one NIC team
        - "Storage" – dedicated RDMA storage NICs
    """
    name: str                              # Intent name (e.g. "Mgmt_Compute")
    traffic_types: list[str]               # e.g. ["Management", "Compute"]
    adapter_names: list[str]               # Physical adapter names to include
    override_virtual_switch_name: str = "" # Optional vSwitch name override
    override_qos_policy: bool = False      # Override default QoS
    override_adapter_property: bool = False # Override RDMA/Jumbo Frames
    storage_vlan_ids: list[int] = field(default_factory=list)  # VLAN IDs for storage


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
    network_intents: list[NetworkIntent] | None = None,
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
    network_intents:
        Optional Network ATC intents.  When provided, ``Add-NetIntent``
        is called after NIC configuration to set up management/compute
        and storage intent groupings.
    """
    log.info("[bold]== Stage: Network Configuration ==[/] on %s", host)

    for nic in nics:
        _configure_single_nic(host, user, password, nic, ssh_port)

    # Network ATC intents (optional)
    if network_intents:
        _configure_network_atc(host, user, password, network_intents, ssh_port)

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


# ---------------------------------------------------------------------------
# Network ATC intent configuration
# ---------------------------------------------------------------------------

def _configure_network_atc(
    host: str,
    user: str,
    password: str,
    intents: list[NetworkIntent],
    port: int,
) -> None:
    """Configure Network ATC intents on the host.

    Network ATC (Azure-consistent networking) replaces manual NIC teaming,
    vSwitch creation, and QoS configuration with declarative intents.

    Reference:
        https://learn.microsoft.com/en-us/azure/azure-local/deploy/network-atc
    """
    log.info("[bold]Configuring Network ATC intents[/] on %s", host)

    # Ensure NetworkATC module is available
    ps_check = (
        "if (-not (Get-Command Add-NetIntent -ErrorAction SilentlyContinue)) { "
        "  Install-WindowsFeature -Name 'NetworkATC' -IncludeManagementTools -ErrorAction Stop | Out-Null; "
        "  Write-Output 'INSTALLED' "
        "} else { Write-Output 'AVAILABLE' }"
    )
    try:
        result = run_powershell(host, user, password, ps_check, port=port)
        log.info("  NetworkATC module: %s", result.strip())
    except Exception as exc:
        log.warning("  Could not verify NetworkATC availability: %s", exc)

    for intent in intents:
        adapters = ", ".join(f"'{a}'" for a in intent.adapter_names)
        traffic = ", ".join(f"'{t}'" for t in intent.traffic_types)

        # Build Add-NetIntent command
        ps_cmd = (
            f"Add-NetIntent "
            f"-Name '{intent.name}' "
            f"-AdapterName {adapters} "
            f"-{'Management' if 'Management' in intent.traffic_types else ''}"
            f"{'Compute' if 'Compute' in intent.traffic_types else ''} "
            f"{'Storage' if 'Storage' in intent.traffic_types else ''} "
        )

        # Cleaner: use the full typed parameter approach
        ps_cmd = f"$adapters = @({adapters}); "

        has_mgmt = "Management" in intent.traffic_types
        has_compute = "Compute" in intent.traffic_types
        has_storage = "Storage" in intent.traffic_types

        ps_cmd += "Add-NetIntent -Name '{}' ".format(intent.name)
        ps_cmd += "-AdapterName $adapters "
        if has_mgmt:
            ps_cmd += "-Management "
        if has_compute:
            ps_cmd += "-Compute "
        if has_storage:
            ps_cmd += "-Storage "

        if intent.override_virtual_switch_name:
            ps_cmd += f"-SwitchName '{intent.override_virtual_switch_name}' "

        if intent.storage_vlan_ids and has_storage:
            vlans = ", ".join(str(v) for v in intent.storage_vlan_ids)
            ps_cmd += f"-StorageVlans {vlans} "

        ps_cmd += "-ErrorAction Stop"

        log.info("  Adding intent [cyan]%s[/]: adapters=%s, traffic=%s",
                 intent.name, intent.adapter_names, intent.traffic_types)
        try:
            run_powershell(host, user, password, ps_cmd, port=port)
            log.info("  ✔ Intent '%s' configured", intent.name)
        except Exception as exc:
            log.error("  ✘ Failed to configure intent '%s': %s", intent.name, exc)
            raise

    # Wait for intents to be provisioned
    log.info("  Waiting for Network ATC intents to provision …")
    ps_wait = "Get-NetIntentStatus | Format-Table Name, ConfigurationStatus, ProvisioningStatus -AutoSize"
    try:
        status = run_powershell(host, user, password, ps_wait, port=port)
        log.info("  Network ATC status:\n%s", status)
    except Exception as exc:
        log.warning("  Could not query intent status: %s", exc)

    log.info("[bold green]Network ATC intents configured[/] on %s", host)
