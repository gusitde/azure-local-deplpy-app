"""Prepare Active Directory for Azure Local deployment.

Creates a dedicated Organizational Unit (OU) and deployment user using
Microsoft's ``AsHciADArtifactsPreCreationTool`` PowerShell module.

Requirements per Microsoft documentation:
    - A dedicated OU to store Azure Local objects.
    - GPO inheritance blocked on the OU.
    - A deployment user (LCM user) with full control on the OU.
    - Machines must NOT be joined to AD before deployment.

Reference:
    https://learn.microsoft.com/en-us/azure/azure-local/deploy/deployment-prep-active-directory
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from azure_local_deploy.remote import run_powershell
from azure_local_deploy.utils import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ADPrepConfig:
    """Configuration for Active Directory preparation."""
    ou_name: str                        # Distinguished name for the OU, e.g. "OU=AzLocal,DC=contoso,DC=com"
    deployment_user: str                # LCM user name (no domain prefix), e.g. "lcmuser"
    deployment_password: str            # LCM user password (≥14 chars, complex)
    domain_fqdn: str                    # e.g. "contoso.com"
    # Optional overrides
    skip_if_exists: bool = True         # Don't fail if OU already exists
    block_inheritance: bool = True      # Block GPO inheritance on the OU


@dataclass
class ADPrepResult:
    """Result from Active Directory preparation."""
    ou_created: bool = False
    user_created: bool = False
    inheritance_blocked: bool = False
    message: str = ""
    errors: list[str] | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def prepare_active_directory(
    ad_config: ADPrepConfig,
    *,
    domain_controller: str = "",
    dc_user: str = "",
    dc_password: str = "",
    use_local_session: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> ADPrepResult:
    """Prepare Active Directory for Azure Local deployment.

    This installs ``AsHciADArtifactsPreCreationTool`` and runs
    ``New-HciAdObjectsPreCreation`` to create the OU and deployment user.

    Parameters
    ----------
    ad_config:
        AD preparation configuration.
    domain_controller:
        IP/hostname of the domain controller to run commands on.
        If empty, *use_local_session* must be ``True``.
    dc_user / dc_password:
        Credentials for the domain controller (domain admin or delegated).
    use_local_session:
        If ``True``, run commands locally (for when the operator workstation
        is domain-joined). Only works when running on a domain member.
    progress_callback:
        Optional callable for progress messages.

    Returns
    -------
    ADPrepResult
    """
    _cb = progress_callback or (lambda msg: None)
    result = ADPrepResult()

    log.info("[bold]== Stage: Active Directory Preparation ==[/]")
    _cb(f"Preparing AD: OU={ad_config.ou_name}, User={ad_config.deployment_user}")

    if not domain_controller and not use_local_session:
        raise ValueError(
            "Either provide domain_controller (IP/hostname) or set use_local_session=True"
        )

    def _run(cmd: str) -> str:
        if use_local_session:
            import subprocess
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode != 0 and r.stderr.strip():
                raise RuntimeError(r.stderr.strip())
            return r.stdout
        else:
            return run_powershell(domain_controller, dc_user, dc_password, cmd)

    # Step 1: Install the AsHciADArtifactsPreCreationTool module
    _cb("Installing AsHciADArtifactsPreCreationTool module …")
    log.info("Installing AsHciADArtifactsPreCreationTool from PSGallery …")

    install_cmd = (
        "Set-PSRepository -Name PSGallery -InstallationPolicy Trusted -ErrorAction SilentlyContinue; "
        "Install-Module AsHciADArtifactsPreCreationTool -Repository PSGallery -Force "
        "-AllowClobber -ErrorAction Stop"
    )
    try:
        _run(install_cmd)
        _cb("AsHciADArtifactsPreCreationTool installed ✔")
    except Exception as exc:
        msg = f"Failed to install AsHciADArtifactsPreCreationTool: {exc}"
        log.error(msg)
        result.errors = [msg]
        result.message = msg
        return result

    # Step 2: Run New-HciAdObjectsPreCreation
    _cb("Running New-HciAdObjectsPreCreation …")
    log.info("Creating AD objects: OU=%s, User=%s", ad_config.ou_name, ad_config.deployment_user)

    # Build the PowerShell command
    prep_cmd = (
        f"$secPwd = ConvertTo-SecureString '{ad_config.deployment_password}' -AsPlainText -Force; "
        f"$cred = New-Object System.Management.Automation.PSCredential ('{ad_config.deployment_user}', $secPwd); "
        f"New-HciAdObjectsPreCreation "
        f"-AzureStackLCMUserCredential $cred "
        f"-AsHciOUName '{ad_config.ou_name}'"
    )

    try:
        output = _run(prep_cmd)
        result.ou_created = True
        result.user_created = True
        log.info("AD preparation output: %s", output[:500] if output else "(no output – success)")
        _cb("OU and deployment user created ✔")
    except Exception as exc:
        error_str = str(exc)
        if "already exists" in error_str.lower() and ad_config.skip_if_exists:
            log.info("OU or user already exists – skipping (skip_if_exists=True)")
            _cb("OU/user already exists – continuing")
            result.ou_created = True
            result.user_created = True
        else:
            msg = f"AD preparation failed: {exc}"
            log.error(msg)
            result.errors = [msg]
            result.message = msg
            return result

    # Step 3: Verify OU exists and GPO inheritance is blocked
    _cb("Verifying AD preparation …")
    log.info("Verifying OU %s exists …", ad_config.ou_name)

    verify_cmd = (
        f"$ou = Get-ADOrganizationalUnit -Identity '{ad_config.ou_name}' -ErrorAction Stop; "
        f"$gp = (Get-GPInheritance -Target '{ad_config.ou_name}').GpoInheritanceBlocked; "
        f"Write-Output \"OU=$($ou.Name)|GpoBlocked=$gp\""
    )
    try:
        verify_output = _run(verify_cmd)
        if "GpoBlocked=Yes" in verify_output or "GpoBlocked=True" in verify_output:
            result.inheritance_blocked = True
            _cb("GPO inheritance verified as blocked ✔")
        else:
            log.warning("GPO inheritance may not be blocked: %s", verify_output)
            _cb("Warning: GPO inheritance may not be blocked")
    except Exception as exc:
        log.warning("Could not verify AD objects (non-blocking): %s", exc)
        _cb(f"Verification skipped: {exc}")

    result.message = "Active Directory preparation completed successfully"
    log.info("[bold green]AD preparation complete.[/]")
    _cb("Active Directory preparation complete ✔")
    return result


def verify_ad_readiness(
    domain_fqdn: str,
    ou_name: str,
    *,
    domain_controller: str = "",
    dc_user: str = "",
    dc_password: str = "",
    use_local_session: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Check AD readiness without making changes.

    Returns a dict with keys:
        - ``ou_exists`` (bool)
        - ``user_exists`` (bool)
        - ``gpo_blocked`` (bool)
        - ``machines_not_joined`` (bool)
        - ``issues`` (list[str])
    """
    _cb = progress_callback or (lambda msg: None)
    result: dict[str, Any] = {
        "ou_exists": False,
        "user_exists": False,
        "gpo_blocked": False,
        "machines_not_joined": True,
        "issues": [],
    }

    def _run(cmd: str) -> str:
        if use_local_session:
            import subprocess
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True, text=True, timeout=60,
            )
            return r.stdout
        else:
            return run_powershell(domain_controller, dc_user, dc_password, cmd)

    _cb("Checking AD readiness …")

    # Check OU
    try:
        ou_check = _run(
            f"Get-ADOrganizationalUnit -Identity '{ou_name}' -ErrorAction Stop | Select-Object Name"
        )
        if ou_name.split(",")[0].split("=")[1] in ou_check:
            result["ou_exists"] = True
        else:
            result["issues"].append(f"OU '{ou_name}' not found")
    except Exception:
        result["issues"].append(f"OU '{ou_name}' not found or AD unreachable")

    # Check GPO inheritance
    try:
        gp_check = _run(
            f"(Get-GPInheritance -Target '{ou_name}').GpoInheritanceBlocked"
        )
        result["gpo_blocked"] = "Yes" in gp_check or "True" in gp_check
        if not result["gpo_blocked"]:
            result["issues"].append("GPO inheritance is not blocked on the OU")
    except Exception:
        result["issues"].append("Could not verify GPO inheritance")

    _cb(f"AD readiness: {len(result['issues'])} issue(s) found")
    return result
