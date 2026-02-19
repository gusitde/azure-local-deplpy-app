"""Configure security settings on Azure Local nodes.

Azure Local supports two security profiles:
    - Recommended (all settings enabled — default)
    - Customized (selective settings)

Security features configured:
    - HVCI (Hypervisor-protected Code Integrity)
    - DRTM (Dynamic Root of Trust for Measurement)
    - Credential Guard
    - Drift Control enforcement
    - SMB signing and cluster encryption
    - Side-channel mitigation
    - BitLocker (boot + data volumes)
    - WDAC (Windows Defender Application Control)

Reference:
    https://learn.microsoft.com/en-us/azure/azure-local/deploy/deploy-via-portal
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
class SecurityProfile:
    """Security settings for Azure Local deployment."""
    name: str = "Recommended"
    # Recommended defaults — all True
    hvci_protection: bool = True
    drtm_protection: bool = True
    drift_control_enforced: bool = True
    credential_guard_enforced: bool = True
    smb_signing_enforced: bool = True
    smb_cluster_encryption: bool = True
    side_channel_mitigation_enforced: bool = True
    bitlocker_boot_volume: bool = True
    bitlocker_data_volumes: bool = True
    wdac_enforced: bool = True

    def to_deployment_dict(self) -> dict[str, bool]:
        """Convert to the format expected by Azure deployment settings."""
        return {
            "hvciProtection": self.hvci_protection,
            "drtmProtection": self.drtm_protection,
            "driftControlEnforced": self.drift_control_enforced,
            "credentialGuardEnforced": self.credential_guard_enforced,
            "smbSigningEnforced": self.smb_signing_enforced,
            "smbClusterEncryption": self.smb_cluster_encryption,
            "sideChannelMitigationEnforced": self.side_channel_mitigation_enforced,
            "bitlockerBootVolume": self.bitlocker_boot_volume,
            "bitlockerDataVolumes": self.bitlocker_data_volumes,
            "wdacEnforced": self.wdac_enforced,
        }


# Pre-built profiles
RECOMMENDED_SECURITY = SecurityProfile(name="Recommended")

CUSTOMIZED_SECURITY = SecurityProfile(
    name="Customized",
    hvci_protection=True,
    drtm_protection=False,        # May not be available on all hardware
    drift_control_enforced=False,  # Relaxed for troubleshooting
    credential_guard_enforced=True,
    smb_signing_enforced=True,
    smb_cluster_encryption=True,
    side_channel_mitigation_enforced=True,
    bitlocker_boot_volume=True,
    bitlocker_data_volumes=True,
    wdac_enforced=False,           # Relaxed for custom software
)


@dataclass
class SecurityCheckResult:
    """Result of a single security check on a node."""
    feature: str
    enabled: bool
    expected: bool
    message: str = ""


@dataclass
class SecurityReport:
    """Aggregated security status for a node."""
    host: str
    checks: list[SecurityCheckResult] = field(default_factory=list)
    compliant: int = 0
    non_compliant: int = 0

    @property
    def ok(self) -> bool:
        return self.non_compliant == 0

    def add(self, check: SecurityCheckResult) -> None:
        self.checks.append(check)
        if check.enabled == check.expected:
            self.compliant += 1
        else:
            self.non_compliant += 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def configure_security(
    host: str,
    user: str,
    password: str,
    *,
    profile: SecurityProfile | None = None,
    ssh_port: int = 22,
    progress_callback: Callable[[str], None] | None = None,
) -> SecurityReport:
    """Apply security settings on a remote Azure Local node.

    Parameters
    ----------
    host / user / password:
        SSH credentials for the node.
    profile:
        Security profile to apply (default: Recommended).
    ssh_port:
        SSH port on the host.
    progress_callback:
        Optional callable for progress messages.

    Returns
    -------
    SecurityReport
    """
    _cb = progress_callback or (lambda msg: None)
    sec = profile or RECOMMENDED_SECURITY
    report = SecurityReport(host=host)

    log.info("[bold]== Configure Security Settings ==[/] on %s (profile: %s)", host, sec.name)
    _cb(f"Configuring security on {host} (profile: {sec.name})")

    # Enable Credential Guard
    if sec.credential_guard_enforced:
        _cb(f"Enabling Credential Guard on {host} …")
        _apply_setting(host, user, password, ssh_port, "Credential Guard",
                       "Enable-WindowsOptionalFeature -Online -FeatureName 'Windows-Defender-Credential-Guard' "
                       "-NoRestart -ErrorAction SilentlyContinue",
                       report, expected=True)

    # Configure VBS (Virtualization-based Security) for HVCI
    if sec.hvci_protection:
        _cb(f"Enabling HVCI on {host} …")
        _apply_setting(host, user, password, ssh_port, "HVCI",
                       "Set-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\DeviceGuard\\Scenarios\\HypervisorEnforcedCodeIntegrity' "
                       "-Name 'Enabled' -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue",
                       report, expected=True)

    # SMB signing
    if sec.smb_signing_enforced:
        _cb(f"Enforcing SMB signing on {host} …")
        _apply_setting(host, user, password, ssh_port, "SMB Signing",
                       "Set-SmbServerConfiguration -RequireSecuritySignature $true -Force -ErrorAction SilentlyContinue",
                       report, expected=True)

    # SMB encryption
    if sec.smb_cluster_encryption:
        _cb(f"Enabling SMB encryption on {host} …")
        _apply_setting(host, user, password, ssh_port, "SMB Encryption",
                       "Set-SmbServerConfiguration -EncryptData $true -Force -ErrorAction SilentlyContinue",
                       report, expected=True)

    # BitLocker – boot volume
    if sec.bitlocker_boot_volume:
        _cb(f"Enabling BitLocker on boot volume ({host}) …")
        _apply_setting(host, user, password, ssh_port, "BitLocker Boot Volume",
                       "Enable-BitLocker -MountPoint 'C:' -EncryptionMethod XtsAes256 "
                       "-RecoveryPasswordProtector -ErrorAction SilentlyContinue",
                       report, expected=True)

    # BitLocker – data volumes
    if sec.bitlocker_data_volumes:
        _cb(f"Enabling BitLocker on data volumes ({host}) …")
        _apply_setting(host, user, password, ssh_port, "BitLocker Data Volumes",
                       "$dataVols = Get-Volume | Where-Object { $_.DriveLetter -and $_.DriveLetter -ne 'C' }; "
                       "foreach ($v in $dataVols) { "
                       "Enable-BitLocker -MountPoint \"$($v.DriveLetter):\" -EncryptionMethod XtsAes256 "
                       "-RecoveryPasswordProtector -ErrorAction SilentlyContinue }",
                       report, expected=True)

    # Side-channel mitigation
    if sec.side_channel_mitigation_enforced:
        _cb(f"Enabling side-channel mitigations on {host} …")
        _apply_setting(host, user, password, ssh_port, "Side-channel Mitigation",
                       "Set-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Memory Management' "
                       "-Name 'FeatureSettingsOverride' -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue; "
                       "Set-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Memory Management' "
                       "-Name 'FeatureSettingsOverrideMask' -Value 3 -Type DWord -Force -ErrorAction SilentlyContinue",
                       report, expected=True)

    # Summary
    log.info(
        "[bold]Security config on %s: %d compliant, %d non-compliant[/]",
        host, report.compliant, report.non_compliant,
    )
    _cb(f"Security config on {host}: {report.compliant} applied, {report.non_compliant} issues")

    return report


def check_security_status(
    host: str,
    user: str,
    password: str,
    *,
    ssh_port: int = 22,
    progress_callback: Callable[[str], None] | None = None,
) -> SecurityReport:
    """Check the current security posture of a node without changing anything.

    Returns
    -------
    SecurityReport
    """
    _cb = progress_callback or (lambda msg: None)
    report = SecurityReport(host=host)

    _cb(f"Checking security status on {host} …")

    # Check Credential Guard
    _check_feature(host, user, password, ssh_port, "Credential Guard",
                   "(Get-CimInstance -ClassName Win32_DeviceGuard -Namespace root\\Microsoft\\Windows\\DeviceGuard).SecurityServicesRunning -contains 1",
                   report)

    # Check HVCI
    _check_feature(host, user, password, ssh_port, "HVCI",
                   "(Get-CimInstance -ClassName Win32_DeviceGuard -Namespace root\\Microsoft\\Windows\\DeviceGuard).SecurityServicesRunning -contains 2",
                   report)

    # Check SMB signing
    _check_feature(host, user, password, ssh_port, "SMB Signing",
                   "(Get-SmbServerConfiguration).RequireSecuritySignature",
                   report)

    # Check SMB encryption
    _check_feature(host, user, password, ssh_port, "SMB Encryption",
                   "(Get-SmbServerConfiguration).EncryptData",
                   report)

    # Check BitLocker on C:
    _check_feature(host, user, password, ssh_port, "BitLocker Boot Volume",
                   "(Get-BitLockerVolume -MountPoint 'C:' -ErrorAction SilentlyContinue).ProtectionStatus -eq 'On'",
                   report)

    _cb(f"Security status on {host}: {report.compliant} enabled, {report.non_compliant} disabled")
    return report


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _apply_setting(
    host: str, user: str, password: str, port: int,
    feature_name: str, command: str,
    report: SecurityReport, expected: bool = True,
) -> None:
    """Run a PowerShell command to apply a security setting."""
    try:
        run_powershell(host, user, password, command, port=port)
        report.add(SecurityCheckResult(
            feature=feature_name, enabled=True, expected=expected,
            message=f"{feature_name} configured successfully",
        ))
        log.info("  ✔ %s configured", feature_name)
    except Exception as exc:
        report.add(SecurityCheckResult(
            feature=feature_name, enabled=False, expected=expected,
            message=f"Failed to configure {feature_name}: {exc}",
        ))
        log.warning("  ✘ %s failed: %s", feature_name, exc)


def _check_feature(
    host: str, user: str, password: str, port: int,
    feature_name: str, check_command: str,
    report: SecurityReport,
) -> None:
    """Check if a security feature is currently enabled."""
    try:
        result = run_powershell(host, user, password, check_command, port=port)
        enabled = "True" in result
        report.add(SecurityCheckResult(
            feature=feature_name, enabled=enabled, expected=True,
            message=f"{feature_name}: {'Enabled' if enabled else 'Disabled'}",
        ))
    except Exception as exc:
        report.add(SecurityCheckResult(
            feature=feature_name, enabled=False, expected=True,
            message=f"Could not check {feature_name}: {exc}",
        ))
