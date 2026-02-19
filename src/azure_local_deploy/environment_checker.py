"""Microsoft Azure Local Environment Checker integration.

Remotely installs the ``AzStackHci.EnvironmentChecker`` PowerShell module on
each node via SSH, runs all five validators, collects structured results, and
then **uninstalls** the module (as required by Microsoft before deployment).

Validators
----------
1. **Connectivity** – Internet / firewall / proxy reachability to Azure endpoints.
2. **Hardware** – CPU, RAM, storage, NIC vs. Azure Local system requirements.
3. **Active Directory** – OU preparation, domain-join readiness.
4. **Network** – IP range conflicts, DNS resolution, VLAN availability.
5. **Arc Integration** – Azure Arc onboarding prerequisites.

Reference
---------
https://learn.microsoft.com/en-us/azure/azure-local/manage/use-environment-checker?view=azloc-2601
"""

from __future__ import annotations

import json
import textwrap
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from azure_local_deploy.remote import run_powershell
from azure_local_deploy.utils import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

VALIDATOR_CMDLETS: dict[str, str] = {
    "Connectivity": "Invoke-AzStackHciConnectivityValidation",
    "Hardware": "Invoke-AzStackHciHardwareValidation",
    "Active Directory": "Invoke-AzStackHciActiveDirectoryValidation",
    "Network": "Invoke-AzStackHciNetworkValidation",
    "Arc Integration": "Invoke-AzStackHciArcIntegrationValidation",
}


@dataclass
class ValidatorResult:
    """Result from a single Environment Checker validator."""
    name: str                        # e.g. "Connectivity"
    status: str = "Unknown"          # Overall: Succeeded | Failed | Skipped
    critical: int = 0
    warning: int = 0
    informational: int = 0
    passed: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)
    raw_output: str = ""
    error: str = ""


@dataclass
class EnvironmentCheckReport:
    """Aggregated report across all validators for one node."""
    host: str
    validators: list[ValidatorResult] = field(default_factory=list)
    install_time_seconds: float = 0.0
    run_time_seconds: float = 0.0
    overall_status: str = "Unknown"   # Passed | Failed | Partial | Error

    @property
    def ok(self) -> bool:
        return self.overall_status in ("Passed", "Partial")

    @property
    def critical_count(self) -> int:
        return sum(v.critical for v in self.validators)

    @property
    def warning_count(self) -> int:
        return sum(v.warning for v in self.validators)


# ---------------------------------------------------------------------------
# PowerShell script fragments
# ---------------------------------------------------------------------------

_INSTALL_SCRIPT = textwrap.dedent(r"""
    $ErrorActionPreference = 'Stop'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    # Ensure NuGet provider is present
    if (-not (Get-PackageProvider -Name NuGet -ListAvailable -ErrorAction SilentlyContinue)) {
        Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force | Out-Null
    }

    # Trust PSGallery
    Set-PSRepository -Name PSGallery -InstallationPolicy Trusted

    # Ensure PowerShellGet is current
    Install-Module PowerShellGet -AllowClobber -Force -ErrorAction SilentlyContinue | Out-Null

    # Install Environment Checker
    Install-Module -Name AzStackHci.EnvironmentChecker -Force -AllowClobber | Out-Null

    # Verify
    $mod = Get-Module -Name AzStackHci.EnvironmentChecker -ListAvailable | Select-Object -First 1
    if ($mod) {
        Write-Output "INSTALLED:$($mod.Version)"
    } else {
        Write-Error 'AzStackHci.EnvironmentChecker module installation failed'
    }
""").strip()

_UNINSTALL_SCRIPT = textwrap.dedent(r"""
    $ErrorActionPreference = 'SilentlyContinue'
    Remove-Module AzStackHci.EnvironmentChecker -Force -ErrorAction SilentlyContinue
    Get-Module AzStackHci.EnvironmentChecker -ListAvailable |
        Where-Object { $_.Path -like "*$($_.Version)*" } |
        Uninstall-Module -Force -ErrorAction SilentlyContinue
    Write-Output 'UNINSTALLED'
""").strip()


def _build_validator_script(cmdlet: str) -> str:
    """Build a PowerShell snippet that runs a validator and returns JSON."""
    # Import the module, run the validator with -PassThru, convert to JSON.
    # We wrap in try/catch so individual validator failures don't kill the
    # whole session.
    return textwrap.dedent(rf"""
        $ErrorActionPreference = 'Stop'
        try {{
            Import-Module AzStackHci.EnvironmentChecker -Force
            $results = {cmdlet} -PassThru 2>&1
            # PassThru returns objects; serialise to JSON for parsing
            $results | ConvertTo-Json -Depth 10 -Compress
        }} catch {{
            # Return an error object as JSON so the caller can parse it
            @{{ Error = $_.Exception.Message }} | ConvertTo-Json -Compress
        }}
    """).strip()


# ---------------------------------------------------------------------------
# Install / uninstall helpers
# ---------------------------------------------------------------------------

def install_environment_checker(
    host: str,
    user: str,
    password: str,
    *,
    port: int = 22,
    timeout: int = 300,
    progress_callback: Callable[[str], None] | None = None,
) -> str:
    """Install AzStackHci.EnvironmentChecker on a remote node.

    Returns the installed module version string.
    """
    _cb = progress_callback or (lambda msg: None)
    _cb(f"Installing AzStackHci.EnvironmentChecker on {host} …")
    log.info("Installing Environment Checker on %s", host)

    out = run_powershell(host, user, password, _INSTALL_SCRIPT, port=port, timeout=timeout)

    # Parse version from "INSTALLED:<version>"
    for line in out.splitlines():
        if line.startswith("INSTALLED:"):
            version = line.split(":", 1)[1].strip()
            log.info("Environment Checker %s installed on %s", version, host)
            _cb(f"Environment Checker v{version} installed on {host}")
            return version

    raise RuntimeError(f"Failed to install Environment Checker on {host}: {out}")


def uninstall_environment_checker(
    host: str,
    user: str,
    password: str,
    *,
    port: int = 22,
    timeout: int = 120,
    progress_callback: Callable[[str], None] | None = None,
) -> None:
    """Uninstall AzStackHci.EnvironmentChecker from a remote node.

    Microsoft requires the module be removed before deployment to avoid
    conflicts with the integrated checker that ships with Azure Local.
    """
    _cb = progress_callback or (lambda msg: None)
    _cb(f"Uninstalling Environment Checker from {host} …")
    log.info("Uninstalling Environment Checker from %s", host)

    try:
        out = run_powershell(host, user, password, _UNINSTALL_SCRIPT, port=port, timeout=timeout)
        if "UNINSTALLED" in out:
            log.info("Environment Checker removed from %s", host)
            _cb(f"Environment Checker removed from {host}")
        else:
            log.warning("Unexpected uninstall output on %s: %s", host, out)
    except Exception as exc:
        # Non-fatal: log and continue
        log.warning("Could not uninstall Environment Checker on %s: %s", host, exc)
        _cb(f"Warning: could not uninstall Environment Checker on {host}: {exc}")


# ---------------------------------------------------------------------------
# Run validators
# ---------------------------------------------------------------------------

def _parse_validator_output(name: str, raw: str) -> ValidatorResult:
    """Parse the JSON output from a validator cmdlet into a ValidatorResult."""
    result = ValidatorResult(name=name, raw_output=raw)

    if not raw.strip():
        result.status = "Skipped"
        result.error = "No output returned"
        return result

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Output wasn't valid JSON — might be plain text from an error
        result.status = "Error"
        result.error = raw[:500]
        return result

    # Handle error wrapper from our try/catch
    if isinstance(data, dict) and "Error" in data:
        result.status = "Error"
        result.error = data["Error"]
        return result

    # data could be a single object or a list of test results
    items = data if isinstance(data, list) else [data]
    result.details = items

    for item in items:
        severity = str(item.get("Severity", item.get("severity", ""))).lower()
        status = str(item.get("Status", item.get("status", ""))).lower()

        if severity == "critical" or status == "failed":
            result.critical += 1
        elif severity == "warning":
            result.warning += 1
        elif severity in ("informational", "hidden"):
            result.informational += 1
        else:
            result.passed += 1

    result.status = "Failed" if result.critical > 0 else "Succeeded"
    return result


def run_validator(
    host: str,
    user: str,
    password: str,
    validator_name: str,
    *,
    port: int = 22,
    timeout: int = 600,
    progress_callback: Callable[[str], None] | None = None,
) -> ValidatorResult:
    """Run a single Environment Checker validator on a remote node."""
    _cb = progress_callback or (lambda msg: None)

    cmdlet = VALIDATOR_CMDLETS.get(validator_name)
    if not cmdlet:
        return ValidatorResult(
            name=validator_name, status="Error",
            error=f"Unknown validator: {validator_name}",
        )

    _cb(f"Running {validator_name} validator on {host} …")
    log.info("Running %s validator on %s", validator_name, host)

    script = _build_validator_script(cmdlet)
    try:
        raw = run_powershell(host, user, password, script, port=port, timeout=timeout)
        result = _parse_validator_output(validator_name, raw)
    except Exception as exc:
        log.error("%s validator failed on %s: %s", validator_name, host, exc)
        result = ValidatorResult(name=validator_name, status="Error", error=str(exc))

    _cb(
        f"{validator_name} on {host}: {result.status} "
        f"({result.passed} pass, {result.critical} critical, "
        f"{result.warning} warn)"
    )
    return result


# ---------------------------------------------------------------------------
# Full node check
# ---------------------------------------------------------------------------

def run_environment_checker(
    host: str,
    user: str,
    password: str,
    *,
    port: int = 22,
    validators: list[str] | None = None,
    install_timeout: int = 300,
    validator_timeout: int = 600,
    auto_uninstall: bool = True,
    progress_callback: Callable[[str], None] | None = None,
) -> EnvironmentCheckReport:
    """Install the Environment Checker, run all validators, and uninstall.

    Parameters
    ----------
    host, user, password, port :
        SSH connection details for the target node.
    validators :
        Optional list of validator names to run.  Defaults to all five.
    install_timeout :
        Seconds to wait for module installation.
    validator_timeout :
        Seconds per validator execution.
    auto_uninstall :
        If ``True`` (default), uninstall the module after all checks.
        Microsoft requires it to be removed before deployment.
    progress_callback :
        Optional callable for UI progress messages.

    Returns
    -------
    EnvironmentCheckReport
    """
    _cb = progress_callback or (lambda msg: None)
    report = EnvironmentCheckReport(host=host)
    active_validators = validators or list(VALIDATOR_CMDLETS.keys())

    # 1. Install
    t0 = time.time()
    try:
        install_environment_checker(
            host, user, password,
            port=port, timeout=install_timeout, progress_callback=_cb,
        )
    except Exception as exc:
        log.error("Environment Checker install failed on %s: %s", host, exc)
        _cb(f"Environment Checker install failed on {host}: {exc}")
        report.overall_status = "Error"
        report.install_time_seconds = time.time() - t0
        return report
    report.install_time_seconds = time.time() - t0

    # 2. Run each validator
    t1 = time.time()
    for name in active_validators:
        result = run_validator(
            host, user, password, name,
            port=port, timeout=validator_timeout, progress_callback=_cb,
        )
        report.validators.append(result)
    report.run_time_seconds = time.time() - t1

    # 3. Determine overall status
    total_critical = report.critical_count
    errors = sum(1 for v in report.validators if v.status == "Error")

    if total_critical > 0:
        report.overall_status = "Failed"
    elif errors > 0:
        report.overall_status = "Partial"
    else:
        report.overall_status = "Passed"

    # 4. Uninstall (always, to prevent conflicts with deployment)
    if auto_uninstall:
        uninstall_environment_checker(
            host, user, password,
            port=port, progress_callback=_cb,
        )

    return report


# ---------------------------------------------------------------------------
# Multi-node convenience
# ---------------------------------------------------------------------------

def run_environment_checker_all_nodes(
    servers: list[dict[str, Any]],
    *,
    validators: list[str] | None = None,
    install_timeout: int = 300,
    validator_timeout: int = 600,
    auto_uninstall: bool = True,
    abort_on_failure: bool = True,
    progress_callback: Callable[[str], None] | None = None,
) -> list[EnvironmentCheckReport]:
    """Run the full Environment Checker on every node.

    Parameters
    ----------
    servers :
        List of server dicts from the YAML config (need ``host_ip``,
        ``host_user``, ``host_password``, optionally ``ssh_port``).
    abort_on_failure :
        If ``True``, raise after all nodes if any has critical issues.
    Other parameters :
        Forwarded to :func:`run_environment_checker`.

    Returns
    -------
    list of EnvironmentCheckReport
    """
    _cb = progress_callback or (lambda msg: None)
    reports: list[EnvironmentCheckReport] = []

    _cb(f"Running Azure Local Environment Checker on {len(servers)} node(s)")
    log.info(
        "[bold]Running Environment Checker on %d node(s)[/]",
        len(servers),
    )

    for idx, srv in enumerate(servers, 1):
        host = srv.get("host_ip", "")
        user = srv.get("host_user", "Administrator")
        password = srv.get("host_password", srv.get("idrac_password", ""))
        port = int(srv.get("ssh_port", 22))

        if not host:
            log.warning("Server #%d has no host_ip – skipping Environment Checker", idx)
            _cb(f"Skipping node {idx}: no host_ip configured")
            continue

        _cb(f"Environment Checker – node {idx}/{len(servers)}: {host}")
        report = run_environment_checker(
            host, user, password,
            port=port,
            validators=validators,
            install_timeout=install_timeout,
            validator_timeout=validator_timeout,
            auto_uninstall=auto_uninstall,
            progress_callback=_cb,
        )
        reports.append(report)

    # Summary
    print_environment_report_summary(reports, progress_callback=_cb)

    if abort_on_failure:
        failed = [r for r in reports if r.overall_status == "Failed"]
        if failed:
            hosts = ", ".join(r.host for r in failed)
            raise RuntimeError(
                f"Environment Checker found critical issues on: {hosts}. "
                f"Fix the reported problems and retry."
            )

    return reports


# ---------------------------------------------------------------------------
# Pretty-print
# ---------------------------------------------------------------------------

def print_environment_report(report: EnvironmentCheckReport) -> None:
    """Log a detailed report for a single node."""
    log.info(
        "\n[bold]──── Environment Checker: %s (%s) ────[/]",
        report.host, report.overall_status,
    )

    for v in report.validators:
        colour = {"Succeeded": "green", "Failed": "red"}.get(v.status, "yellow")
        log.info(
            "  [%s][%s][/%s] %s – %d pass, %d critical, %d warn",
            colour, v.status, colour,
            v.name, v.passed, v.critical, v.warning,
        )
        if v.error:
            log.info("        Error: %s", v.error[:200])

        # Show critical/warning details
        for d in v.details:
            sev = str(d.get("Severity", d.get("severity", ""))).lower()
            if sev in ("critical", "warning"):
                title = d.get("Title", d.get("Name", d.get("name", "?")))
                desc = d.get("Description", d.get("description", ""))
                remediation = d.get("Remediation", d.get("remediation", ""))
                log.info("        [%s] %s", sev.upper(), title)
                if desc:
                    log.info("          %s", desc[:200])
                if remediation:
                    log.info("          Remediation: %s", remediation[:200])


def print_environment_report_summary(
    reports: list[EnvironmentCheckReport],
    *,
    progress_callback: Callable[[str], None] | None = None,
) -> None:
    """Print a summary across all nodes."""
    _cb = progress_callback or (lambda msg: None)

    log.info("\n[bold]═══ Environment Checker Summary ═══[/]")
    total_critical = 0
    total_warning = 0

    for r in reports:
        print_environment_report(r)
        total_critical += r.critical_count
        total_warning += r.warning_count

    ok = all(r.ok for r in reports)
    status_str = "[green]PASSED[/]" if ok else "[red]FAILED[/]"
    log.info(
        "\n  Overall: %s  (%d nodes, %d critical, %d warnings)",
        status_str, len(reports), total_critical, total_warning,
    )

    summary = (
        f"Environment Checker: {'PASSED' if ok else 'FAILED'} – "
        f"{len(reports)} nodes, {total_critical} critical, {total_warning} warnings"
    )
    _cb(summary)
