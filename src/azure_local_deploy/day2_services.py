"""Day 2 Services — Post-deployment workload provisioning for Azure Local.

After the Azure Local cluster is deployed and operational, Day 2 services
set up the infrastructure needed to run virtual machine workloads:

    1. Create logical networks (DHCP + Static IP).
    2. Upload VM images (Windows Server 2025, Windows 11).
    3. Create test VMs with credentials for operator access.

All operations are performed via SSH/PowerShell to a cluster node or via
the Azure SDK (Azure Resource Bridge / Arc VM Management).

Reference:
    https://learn.microsoft.com/en-us/azure/azure-local/manage/create-logical-networks
    https://learn.microsoft.com/en-us/azure/azure-local/manage/virtual-machine-image-azure-marketplace
    https://learn.microsoft.com/en-us/azure/azure-local/manage/create-arc-virtual-machines
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from azure_local_deploy.remote import run_powershell
from azure_local_deploy.utils import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class LogicalNetworkConfig:
    """Configuration for a logical network."""
    name: str
    vm_switch_name: str = "ConvergedSwitch(compute_management)"
    address_type: str = "DHCP"        # "DHCP" or "Static"
    address_prefix: str = ""          # e.g. "192.168.100.0/24" — required for Static
    gateway: str = ""                 # e.g. "192.168.100.1" — required for Static
    dns_servers: list[str] = field(default_factory=list)
    ip_pool_start: str = ""           # Start of IP pool — required for Static
    ip_pool_end: str = ""             # End of IP pool — required for Static
    vlan_id: int | None = None        # Optional VLAN tag


@dataclass
class VMImageConfig:
    """Configuration for a VM image to upload."""
    name: str
    image_path: str                    # UNC path or HTTP URL to VHDX / ISO
    os_type: str = "Windows"           # "Windows" or "Linux"


@dataclass
class TestVMConfig:
    """Configuration for a test virtual machine."""
    name: str
    logical_network: str               # Name of the logical network to attach
    image_name: str                    # Name of the uploaded VM image
    cpu_count: int = 4
    memory_gb: int = 8
    storage_gb: int = 128
    admin_username: str = "azurelocaladmin"
    admin_password: str = ""


@dataclass
class Day2Task:
    """A single Day 2 service task result."""
    name: str
    success: bool
    message: str = ""


@dataclass
class Day2Report:
    """Aggregated Day 2 service task results."""
    tasks: list[Day2Task] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(t.success for t in self.tasks)

    def add(self, task: Day2Task) -> None:
        self.tasks.append(task)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_day2_services(
    *,
    host: str,
    user: str,
    password: str,
    subscription_id: str,
    resource_group: str,
    custom_location_name: str,
    logical_networks: list[LogicalNetworkConfig] | None = None,
    vm_images: list[VMImageConfig] | None = None,
    test_vms: list[TestVMConfig] | None = None,
    ssh_port: int = 22,
    progress_callback: Callable[[str], None] | None = None,
) -> Day2Report:
    """Execute all Day 2 service tasks.

    Parameters
    ----------
    host:
        SSH target — one of the cluster nodes.
    user / password:
        Credentials for SSH (typically ``Administrator``).
    subscription_id / resource_group:
        Azure subscription and resource group of the cluster.
    custom_location_name:
        The Azure Custom Location name created during cluster deployment.
    logical_networks:
        List of logical networks to create.  Defaults to a DHCP and a Static
        network if ``None``.
    vm_images:
        List of VM images to upload.  Defaults to Windows Server 2025 and
        Windows 11 if ``None``.
    test_vms:
        List of test VMs to create.  Defaults to one per image if ``None``.
    ssh_port:
        SSH port on the cluster node (default 22).
    progress_callback:
        Optional callable invoked with status messages.
    """
    report = Day2Report()

    def _progress(msg: str) -> None:
        log.info(msg)
        if progress_callback:
            progress_callback(msg)

    # ---- Logical Networks -------------------------------------------------
    networks = logical_networks or _default_logical_networks()
    for net in networks:
        _progress(f"Creating logical network '{net.name}' ({net.address_type})...")
        task = _create_logical_network(host, user, password, net, ssh_port)
        report.add(task)

    # ---- VM Images --------------------------------------------------------
    images = vm_images or _default_vm_images()
    for img in images:
        _progress(f"Uploading VM image '{img.name}'...")
        task = _upload_vm_image(host, user, password, img, ssh_port)
        report.add(task)

    # ---- Test VMs ---------------------------------------------------------
    vms = test_vms or _default_test_vms(networks, images)
    for vm in vms:
        _progress(f"Creating test VM '{vm.name}'...")
        task = _create_test_vm(host, user, password, vm, ssh_port)
        report.add(task)

    return report


# ---------------------------------------------------------------------------
# Default configurations
# ---------------------------------------------------------------------------

def _default_logical_networks() -> list[LogicalNetworkConfig]:
    """Return the two default logical networks: DHCP + Static."""
    return [
        LogicalNetworkConfig(
            name="dhcp-logical-network",
            address_type="DHCP",
            vm_switch_name="ConvergedSwitch(compute_management)",
        ),
        LogicalNetworkConfig(
            name="static-logical-network",
            address_type="Static",
            address_prefix="192.168.200.0/24",
            gateway="192.168.200.1",
            dns_servers=["192.168.200.1"],
            ip_pool_start="192.168.200.100",
            ip_pool_end="192.168.200.200",
            vm_switch_name="ConvergedSwitch(compute_management)",
        ),
    ]


def _default_vm_images() -> list[VMImageConfig]:
    """Return the two default VM images."""
    return [
        VMImageConfig(
            name="windows-server-2025",
            image_path="",   # Operator must supply actual path or marketplace ref
            os_type="Windows",
        ),
        VMImageConfig(
            name="windows-11-enterprise",
            image_path="",   # Operator must supply actual path or marketplace ref
            os_type="Windows",
        ),
    ]


def _default_test_vms(
    networks: list[LogicalNetworkConfig],
    images: list[VMImageConfig],
) -> list[TestVMConfig]:
    """Return default test VMs — one attached to each image/network combo."""
    vms: list[TestVMConfig] = []
    if len(images) >= 1 and len(networks) >= 1:
        vms.append(TestVMConfig(
            name="test-vm-winserver2025",
            logical_network=networks[0].name,
            image_name=images[0].name,
            cpu_count=4,
            memory_gb=8,
            storage_gb=128,
            admin_username="azurelocaladmin",
            admin_password="",
        ))
    if len(images) >= 2 and len(networks) >= 1:
        net = networks[1] if len(networks) > 1 else networks[0]
        vms.append(TestVMConfig(
            name="test-vm-win11",
            logical_network=net.name,
            image_name=images[1].name,
            cpu_count=4,
            memory_gb=8,
            storage_gb=128,
            admin_username="azurelocaladmin",
            admin_password="",
        ))
    return vms


# ---------------------------------------------------------------------------
# Logical network creation
# ---------------------------------------------------------------------------

def _create_logical_network(
    host: str,
    user: str,
    password: str,
    config: LogicalNetworkConfig,
    ssh_port: int = 22,
) -> Day2Task:
    """Create a logical network on the Azure Local cluster via PowerShell."""
    try:
        # Check if network already exists
        check_script = (
            f"Get-MocVirtualNetwork -Name '{config.name}' -ErrorAction SilentlyContinue"
        )
        try:
            existing = run_powershell(host, user, password, check_script, port=ssh_port)
            if config.name in existing:
                return Day2Task(
                    name=f"Logical Network: {config.name}",
                    success=True,
                    message="Already exists — skipped.",
                )
        except RuntimeError:
            pass  # Doesn't exist, proceed to create

        # Build the creation command
        if config.address_type.upper() == "DHCP":
            script = _build_dhcp_network_script(config)
        else:
            script = _build_static_network_script(config)

        run_powershell(host, user, password, script, port=ssh_port, timeout=300)
        return Day2Task(
            name=f"Logical Network: {config.name}",
            success=True,
            message=f"{config.address_type} network created on {config.vm_switch_name}.",
        )
    except Exception as exc:
        log.error("Failed to create logical network %s: %s", config.name, exc)
        return Day2Task(
            name=f"Logical Network: {config.name}",
            success=False,
            message=str(exc),
        )


def _build_dhcp_network_script(config: LogicalNetworkConfig) -> str:
    """Build PowerShell script for a DHCP logical network."""
    vlan_param = f" -VlanId {config.vlan_id}" if config.vlan_id else ""
    return (
        f"New-MocNetworkSetting -Name '{config.name}-settings' -VmSwitchName '{config.vm_switch_name}'{vlan_param}; "
        f"New-MocVirtualNetwork -Name '{config.name}' -NetworkSettings (Get-MocNetworkSetting -Name '{config.name}-settings')"
    )


def _build_static_network_script(config: LogicalNetworkConfig) -> str:
    """Build PowerShell script for a Static IP logical network."""
    dns_list = "', '".join(config.dns_servers) if config.dns_servers else ""
    dns_param = f" -DnsServers @('{dns_list}')" if dns_list else ""
    vlan_param = f" -VlanId {config.vlan_id}" if config.vlan_id else ""

    return (
        f"$subnet = New-MocNetworkSetting "
        f"-Name '{config.name}-settings' "
        f"-VmSwitchName '{config.vm_switch_name}' "
        f"-AddressPrefix '{config.address_prefix}' "
        f"-DefaultGateway '{config.gateway}'{dns_param}{vlan_param} "
        f"-IpPoolStart '{config.ip_pool_start}' -IpPoolEnd '{config.ip_pool_end}'; "
        f"New-MocVirtualNetwork -Name '{config.name}' -NetworkSettings $subnet"
    )


# ---------------------------------------------------------------------------
# VM image upload
# ---------------------------------------------------------------------------

def _upload_vm_image(
    host: str,
    user: str,
    password: str,
    config: VMImageConfig,
    ssh_port: int = 22,
) -> Day2Task:
    """Upload a VM image to the Azure Local cluster image gallery."""
    try:
        # Check if image already exists
        check_script = (
            f"Get-MocGalleryImage -Name '{config.name}' -ErrorAction SilentlyContinue"
        )
        try:
            existing = run_powershell(host, user, password, check_script, port=ssh_port)
            if config.name in existing:
                return Day2Task(
                    name=f"VM Image: {config.name}",
                    success=True,
                    message="Already exists — skipped.",
                )
        except RuntimeError:
            pass  # Doesn't exist, proceed to upload

        if not config.image_path:
            return Day2Task(
                name=f"VM Image: {config.name}",
                success=False,
                message="No image path or marketplace reference provided. "
                        "Set image_path in the configuration.",
            )

        # Upload via Add-MocGalleryImage
        script = (
            f"Add-MocGalleryImage "
            f"-Name '{config.name}' "
            f"-ImagePath '{config.image_path}' "
            f"-OsType '{config.os_type}' "
            f"-CloudInitType 'NoCloud'"
        )

        run_powershell(host, user, password, script, port=ssh_port, timeout=1800)
        return Day2Task(
            name=f"VM Image: {config.name}",
            success=True,
            message=f"Image uploaded from {config.image_path}.",
        )
    except Exception as exc:
        log.error("Failed to upload VM image %s: %s", config.name, exc)
        return Day2Task(
            name=f"VM Image: {config.name}",
            success=False,
            message=str(exc),
        )


# ---------------------------------------------------------------------------
# Test VM creation
# ---------------------------------------------------------------------------

def _create_test_vm(
    host: str,
    user: str,
    password: str,
    config: TestVMConfig,
    ssh_port: int = 22,
) -> Day2Task:
    """Create a test VM on the Azure Local cluster."""
    try:
        # Check if VM already exists
        check_script = (
            f"Get-MocVirtualMachine -Name '{config.name}' -ErrorAction SilentlyContinue"
        )
        try:
            existing = run_powershell(host, user, password, check_script, port=ssh_port)
            if config.name in existing:
                return Day2Task(
                    name=f"Test VM: {config.name}",
                    success=True,
                    message="Already exists — skipped.",
                )
        except RuntimeError:
            pass  # Doesn't exist, proceed

        if not config.admin_password:
            return Day2Task(
                name=f"Test VM: {config.name}",
                success=False,
                message="No admin_password provided. Set admin_password in the "
                        "configuration so you can log in to the VM.",
            )

        # Create the VM via PowerShell
        memory_mb = config.memory_gb * 1024
        storage_bytes = config.storage_gb * 1024 * 1024 * 1024

        script = (
            f"$secPassword = ConvertTo-SecureString '{config.admin_password}' -AsPlainText -Force; "
            f"$cred = New-Object System.Management.Automation.PSCredential('{config.admin_username}', $secPassword); "
            f"New-MocVirtualMachine "
            f"-Name '{config.name}' "
            f"-ImageName '{config.image_name}' "
            f"-VirtualNetworkName '{config.logical_network}' "
            f"-VmSize Custom "
            f"-CpuCount {config.cpu_count} "
            f"-MemoryMB {memory_mb} "
            f"-StoragePathSize {storage_bytes} "
            f"-Credential $cred"
        )

        run_powershell(host, user, password, script, port=ssh_port, timeout=600)

        return Day2Task(
            name=f"Test VM: {config.name}",
            success=True,
            message=(
                f"VM created — {config.cpu_count} vCPU, {config.memory_gb} GB RAM, "
                f"{config.storage_gb} GB disk on '{config.logical_network}'. "
                f"Login: {config.admin_username} / (password from config)."
            ),
        )
    except Exception as exc:
        log.error("Failed to create test VM %s: %s", config.name, exc)
        return Day2Task(
            name=f"Test VM: {config.name}",
            success=False,
            message=str(exc),
        )


# ---------------------------------------------------------------------------
# Convenience: list existing resources
# ---------------------------------------------------------------------------

def list_logical_networks(
    host: str, user: str, password: str, *, ssh_port: int = 22,
) -> str:
    """Return a formatted list of logical networks on the cluster."""
    return run_powershell(
        host, user, password,
        "Get-MocVirtualNetwork | Format-Table Name, Status -AutoSize",
        port=ssh_port,
    )


def list_vm_images(
    host: str, user: str, password: str, *, ssh_port: int = 22,
) -> str:
    """Return a formatted list of VM images on the cluster."""
    return run_powershell(
        host, user, password,
        "Get-MocGalleryImage | Format-Table Name, OsType, Status -AutoSize",
        port=ssh_port,
    )


def list_vms(
    host: str, user: str, password: str, *, ssh_port: int = 22,
) -> str:
    """Return a formatted list of VMs on the cluster."""
    return run_powershell(
        host, user, password,
        "Get-MocVirtualMachine | Format-Table Name, PowerState, CpuCount, MemoryMB -AutoSize",
        port=ssh_port,
    )
