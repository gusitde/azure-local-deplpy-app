"""Pre-flight validation of Dell servers before Azure Local deployment.

Checks hardware, firmware, BIOS, network, and connectivity against
Microsoft Azure Local requirements gathered from:
    https://learn.microsoft.com/en-us/azure/azure-local/concepts/system-requirements-23h2

Validation results are returned as a structured report so the pipeline
can decide whether to proceed or abort.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from azure_local_deploy.idrac_client import IdracClient
from azure_local_deploy.configure_bios import AZURE_LOCAL_BIOS_DEFAULTS, compare_bios
from azure_local_deploy.utils import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class CheckResult:
    name: str
    severity: Severity
    message: str
    detail: str = ""


@dataclass
class ValidationReport:
    host: str
    checks: list[CheckResult] = field(default_factory=list)
    passed: int = 0
    warnings: int = 0
    failures: int = 0

    def add(self, result: CheckResult) -> None:
        self.checks.append(result)
        if result.severity == Severity.PASS:
            self.passed += 1
        elif result.severity == Severity.WARN:
            self.warnings += 1
        else:
            self.failures += 1

    @property
    def ok(self) -> bool:
        return self.failures == 0


# ---------------------------------------------------------------------------
# Individual checks  (each returns one or more CheckResult)
# ---------------------------------------------------------------------------

def _check_idrac_connectivity(idrac: IdracClient) -> CheckResult:
    """Verify we can reach the iDRAC Redfish endpoint."""
    try:
        system = idrac.get_system()
        model = system.get("Model", "Unknown")
        return CheckResult(
            name="iDRAC Connectivity",
            severity=Severity.PASS,
            message=f"Connected to {idrac.host} ({model})",
        )
    except Exception as exc:
        return CheckResult(
            name="iDRAC Connectivity",
            severity=Severity.FAIL,
            message=f"Cannot reach iDRAC at {idrac.host}",
            detail=str(exc),
        )


def _check_cpu(system: dict) -> list[CheckResult]:
    """Validate CPU meets Azure Local requirements (64-bit, VT-x/AMD-V capable)."""
    results: list[CheckResult] = []
    proc_summary = system.get("ProcessorSummary", {})
    cpu_count = proc_summary.get("Count", 0)
    cpu_model = proc_summary.get("Model", "Unknown")

    if cpu_count < 1:
        results.append(CheckResult(
            name="CPU Count",
            severity=Severity.FAIL,
            message="No processors detected",
        ))
    else:
        results.append(CheckResult(
            name="CPU Count",
            severity=Severity.PASS,
            message=f"{cpu_count} processor(s) detected: {cpu_model}",
        ))

    # Check for 64-bit (Intel or AMD EPYC)
    model_lower = cpu_model.lower()
    if any(kw in model_lower for kw in ("intel", "xeon", "epyc", "amd")):
        results.append(CheckResult(
            name="CPU Architecture",
            severity=Severity.PASS,
            message=f"64-bit processor detected: {cpu_model}",
        ))
    else:
        results.append(CheckResult(
            name="CPU Architecture",
            severity=Severity.WARN,
            message=f"Could not confirm 64-bit Intel/AMD processor: {cpu_model}",
        ))

    return results


def _check_memory(system: dict) -> CheckResult:
    """Check that the server has at least 32 GB RAM with ECC."""
    mem = system.get("MemorySummary", {})
    total_gb = mem.get("TotalSystemMemoryGiB", 0)
    status = mem.get("Status", {}).get("Health", "Unknown")

    if total_gb >= 32:
        return CheckResult(
            name="Memory",
            severity=Severity.PASS,
            message=f"{total_gb} GB RAM detected (≥32 GB required). Health: {status}",
        )
    else:
        return CheckResult(
            name="Memory",
            severity=Severity.FAIL,
            message=f"Only {total_gb} GB RAM detected (minimum 32 GB required)",
        )


def _check_boot_mode(bios_attrs: dict) -> CheckResult:
    """Verify UEFI boot mode."""
    mode = bios_attrs.get("BootMode", "Unknown")
    if mode.lower() in ("uefi", "uefi secure boot"):
        return CheckResult(
            name="Boot Mode",
            severity=Severity.PASS,
            message=f"Boot mode is {mode} (UEFI required)",
        )
    else:
        return CheckResult(
            name="Boot Mode",
            severity=Severity.FAIL,
            message=f"Boot mode is '{mode}' – must be UEFI",
        )


def _check_secure_boot(bios_attrs: dict) -> CheckResult:
    """Verify Secure Boot is enabled."""
    sb = bios_attrs.get("SecureBoot", "Unknown")
    if sb.lower() == "enabled":
        return CheckResult(
            name="Secure Boot",
            severity=Severity.PASS,
            message="Secure Boot is enabled",
        )
    else:
        return CheckResult(
            name="Secure Boot",
            severity=Severity.FAIL,
            message=f"Secure Boot is '{sb}' – must be Enabled",
        )


def _check_virtualisation(bios_attrs: dict) -> CheckResult:
    """Verify hardware virtualisation (VT-x/AMD-V)."""
    vt = bios_attrs.get("ProcVirtualization", "Unknown")
    if vt.lower() == "enabled":
        return CheckResult(
            name="Virtualisation (VT-x/AMD-V)",
            severity=Severity.PASS,
            message="Hardware virtualisation is enabled",
        )
    else:
        return CheckResult(
            name="Virtualisation (VT-x/AMD-V)",
            severity=Severity.FAIL,
            message=f"Hardware virtualisation is '{vt}' – must be Enabled",
        )


def _check_tpm(bios_attrs: dict) -> CheckResult:
    """Verify TPM 2.0 is present and enabled."""
    tpm = bios_attrs.get("TpmSecurity", "Unknown")
    if tpm.lower() in ("on", "onpbm", "enabled"):
        return CheckResult(
            name="TPM 2.0",
            severity=Severity.PASS,
            message=f"TPM security is '{tpm}' (enabled)",
        )
    else:
        return CheckResult(
            name="TPM 2.0",
            severity=Severity.FAIL,
            message=f"TPM security is '{tpm}' – must be enabled",
        )


def _check_sriov(bios_attrs: dict) -> CheckResult:
    """Verify SR-IOV Global Enable."""
    sr = bios_attrs.get("SriovGlobalEnable", "Unknown")
    if sr.lower() == "enabled":
        return CheckResult(
            name="SR-IOV",
            severity=Severity.PASS,
            message="SR-IOV Global Enable is on",
        )
    else:
        return CheckResult(
            name="SR-IOV",
            severity=Severity.WARN,
            message=f"SR-IOV is '{sr}' – recommended to be Enabled for Azure Local",
        )


def _check_storage(idrac: IdracClient) -> list[CheckResult]:
    """Check storage controllers and physical disks.

    Azure Local requires:
        - No RAID (pass-through / HBA mode)
        - At least 2 data drives per server (≥ 500 GB each)
        - Boot drive ≥ 200 GB
    """
    results: list[CheckResult] = []

    # Try to enumerate storage controllers
    try:
        storage = idrac.get("/Systems/System.Embedded.1/Storage")
        members = storage.get("Members", [])
        results.append(CheckResult(
            name="Storage Controllers",
            severity=Severity.PASS,
            message=f"{len(members)} storage controller(s) found",
        ))

        # Count physical disks across all controllers
        total_disks = 0
        data_disks = 0
        for member_ref in members:
            uri = member_ref.get("@odata.id", "").replace("/redfish/v1", "")
            if not uri:
                continue
            try:
                ctrl = idrac.get(uri)
                drives = ctrl.get("Drives", [])
                total_disks += len(drives)
                for drive_ref in drives:
                    drive_uri = drive_ref.get("@odata.id", "").replace("/redfish/v1", "")
                    if drive_uri:
                        try:
                            drv = idrac.get(drive_uri)
                            cap_gb = drv.get("CapacityBytes", 0) / (1024 ** 3)
                            if cap_gb >= 500:
                                data_disks += 1
                        except Exception:
                            pass
            except Exception:
                pass

        if total_disks >= 2:
            results.append(CheckResult(
                name="Physical Disks",
                severity=Severity.PASS,
                message=f"{total_disks} physical disk(s) found ({data_disks} ≥ 500 GB)",
            ))
        else:
            results.append(CheckResult(
                name="Physical Disks",
                severity=Severity.FAIL if total_disks == 0 else Severity.WARN,
                message=f"Only {total_disks} physical disk(s) found (≥2 recommended)",
            ))

    except Exception as exc:
        results.append(CheckResult(
            name="Storage Controllers",
            severity=Severity.WARN,
            message=f"Could not enumerate storage: {exc}",
        ))

    return results


def _check_network_adapters(idrac: IdracClient) -> list[CheckResult]:
    """Check that at least 2 network adapters are present."""
    results: list[CheckResult] = []
    try:
        adapters = idrac.get("/Systems/System.Embedded.1/NetworkInterfaces")
        members = adapters.get("Members", [])
        count = len(members)
        if count >= 2:
            results.append(CheckResult(
                name="Network Adapters",
                severity=Severity.PASS,
                message=f"{count} network adapter(s) found (≥2 required)",
            ))
        elif count == 1:
            results.append(CheckResult(
                name="Network Adapters",
                severity=Severity.WARN,
                message="Only 1 network adapter found (≥2 recommended)",
            ))
        else:
            results.append(CheckResult(
                name="Network Adapters",
                severity=Severity.FAIL,
                message="No network adapters found",
            ))
    except Exception as exc:
        results.append(CheckResult(
            name="Network Adapters",
            severity=Severity.WARN,
            message=f"Could not enumerate network adapters: {exc}",
        ))

    return results


def _check_host_ssh(host_ip: str, port: int = 22, timeout: int = 5) -> CheckResult:
    """Try TCP connect to the host OS SSH port."""
    try:
        sock = socket.create_connection((host_ip, port), timeout=timeout)
        sock.close()
        return CheckResult(
            name="Host SSH Connectivity",
            severity=Severity.PASS,
            message=f"SSH port {port} reachable on {host_ip}",
        )
    except Exception:
        return CheckResult(
            name="Host SSH Connectivity",
            severity=Severity.WARN,
            message=f"SSH port {port} not reachable on {host_ip} (OK if OS not yet installed)",
        )


def _check_bios_compliance(bios_attrs: dict) -> list[CheckResult]:
    """Compare all BIOS attributes against Azure Local recommended values."""
    mismatched, ok = compare_bios(bios_attrs, AZURE_LOCAL_BIOS_DEFAULTS)
    results: list[CheckResult] = []

    if mismatched:
        for attr, (cur, desired) in mismatched.items():
            results.append(CheckResult(
                name=f"BIOS: {attr}",
                severity=Severity.WARN,
                message=f"{attr} = '{cur}' (recommended: '{desired}')",
            ))
    else:
        results.append(CheckResult(
            name="BIOS Compliance",
            severity=Severity.PASS,
            message=f"All {len(ok)} checked BIOS attributes match Azure Local recommendations",
        ))

    return results


def _check_power_state(system: dict, host: str) -> CheckResult:
    """Report the current power state."""
    state = system.get("PowerState", "Unknown")
    return CheckResult(
        name="Power State",
        severity=Severity.PASS,
        message=f"Server {host} is currently {state}",
    )


# ---- Reserved IP range validation ----------------------------------------
# Azure Local/AKS reserves 10.96.0.0/12 (Services) and 10.244.0.0/16 (Pods).
# Deploying nodes or services with IPs in these ranges causes routing conflicts.

RESERVED_IP_RANGES = [
    ipaddress.IPv4Network("10.96.0.0/12"),
    ipaddress.IPv4Network("10.244.0.0/16"),
]


def _check_reserved_ip_ranges(ip_addresses: list[str]) -> list[CheckResult]:
    """Verify none of the provided IPs fall within Kubernetes reserved ranges.

    Azure Local uses an AKS workload engine that reserves:
        - 10.96.0.0/12  – Kubernetes service CIDR
        - 10.244.0.0/16 – Kubernetes pod CIDR

    Reference:
        https://learn.microsoft.com/en-us/azure/azure-local/plan/cloud-deployment-network-considerations
    """
    results: list[CheckResult] = []
    for addr in ip_addresses:
        try:
            ip = ipaddress.IPv4Address(addr.strip())
        except (ipaddress.AddressValueError, ValueError):
            results.append(CheckResult(
                name=f"Reserved IP: {addr}",
                severity=Severity.WARN,
                message=f"Could not parse IP address '{addr}'",
            ))
            continue

        conflict = False
        for net in RESERVED_IP_RANGES:
            if ip in net:
                results.append(CheckResult(
                    name=f"Reserved IP: {addr}",
                    severity=Severity.FAIL,
                    message=f"IP {addr} falls within Kubernetes reserved range {net}",
                    detail="Azure Local AKS reserves this range. Use a different subnet.",
                ))
                conflict = True
                break

        if not conflict:
            results.append(CheckResult(
                name=f"Reserved IP: {addr}",
                severity=Severity.PASS,
                message=f"IP {addr} is not in any Kubernetes reserved range",
            ))

    return results


# ---- DNS validation ------------------------------------------------------

def _check_dns_resolution(
    host: str, user: str, password: str,
    domain_fqdn: str = "", port: int = 22,
) -> list[CheckResult]:
    """Verify DNS resolution from the host for AD domain and internet endpoints.

    Checks:
        1. Resolve the AD domain FQDN (if provided).
        2. Resolve login.microsoftonline.com (AAD).
        3. Resolve management.azure.com (ARM).
    """
    from azure_local_deploy.remote import run_powershell

    results: list[CheckResult] = []
    targets = [
        ("login.microsoftonline.com", "Azure AD (Entra ID)"),
        ("management.azure.com", "Azure Resource Manager"),
    ]
    if domain_fqdn:
        targets.insert(0, (domain_fqdn, "Active Directory Domain"))

    for fqdn, label in targets:
        try:
            out = run_powershell(
                host, user, password,
                f"Resolve-DnsName -Name '{fqdn}' -Type A -ErrorAction Stop | "
                f"Select-Object -First 1 -ExpandProperty IPAddress",
                port=port,
            )
            ip = out.strip().splitlines()[0].strip() if out.strip() else "?"
            results.append(CheckResult(
                name=f"DNS: {label}",
                severity=Severity.PASS,
                message=f"Resolved {fqdn} → {ip}",
            ))
        except Exception as exc:
            results.append(CheckResult(
                name=f"DNS: {label}",
                severity=Severity.FAIL,
                message=f"Cannot resolve {fqdn}",
                detail=str(exc),
            ))

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_node(
    idrac: IdracClient,
    *,
    host_ip: str = "",
    ssh_port: int = 22,
    all_ips: list[str] | None = None,
    domain_fqdn: str = "",
    host_user: str = "",
    host_password: str = "",
    progress_callback=None,
) -> ValidationReport:
    """Run all pre-flight checks on a single Dell server.

    Parameters
    ----------
    idrac :
        Connected ``IdracClient``.
    host_ip :
        The expected OS IP address (for SSH reachability check).
    ssh_port :
        SSH port on the host OS.
    all_ips :
        All IP addresses to validate against Kubernetes reserved ranges.
    domain_fqdn :
        AD domain FQDN for DNS resolution check.
    host_user / host_password :
        Credentials for SSH-based checks (DNS, etc.).
    progress_callback :
        Optional callable for UI progress messages.

    Returns
    -------
    ValidationReport
    """
    _cb = progress_callback or (lambda msg: None)
    report = ValidationReport(host=idrac.host)

    # 1. iDRAC connectivity
    _cb(f"Validating iDRAC connectivity for {idrac.host}")
    report.add(_check_idrac_connectivity(idrac))
    if report.failures > 0:
        _cb(f"Validation FAILED for {idrac.host} – iDRAC unreachable")
        return report

    # 2. System info
    _cb(f"Checking hardware specs on {idrac.host}")
    system = idrac.get_system()
    report.add(_check_power_state(system, idrac.host))

    # 3. CPU
    for r in _check_cpu(system):
        report.add(r)

    # 4. Memory
    report.add(_check_memory(system))

    # 5. BIOS attributes
    _cb(f"Reading BIOS attributes on {idrac.host}")
    try:
        bios = idrac.get_bios_attributes()
        report.add(_check_boot_mode(bios))
        report.add(_check_secure_boot(bios))
        report.add(_check_virtualisation(bios))
        report.add(_check_tpm(bios))
        report.add(_check_sriov(bios))

        # Full compliance check
        _cb(f"Checking BIOS compliance on {idrac.host}")
        for r in _check_bios_compliance(bios):
            report.add(r)
    except Exception as exc:
        report.add(CheckResult(
            name="BIOS Attributes",
            severity=Severity.WARN,
            message=f"Could not read BIOS attributes: {exc}",
        ))

    # 6. Storage
    _cb(f"Checking storage on {idrac.host}")
    for r in _check_storage(idrac):
        report.add(r)

    # 7. Network adapters
    _cb(f"Checking network adapters on {idrac.host}")
    for r in _check_network_adapters(idrac):
        report.add(r)

    # 8. Host SSH (best-effort)
    if host_ip:
        _cb(f"Testing SSH connectivity to {host_ip}")
        report.add(_check_host_ssh(host_ip, port=ssh_port))

    # 9. Reserved IP range validation (Kubernetes CIDR clash detection)
    ip_list = all_ips or ([host_ip] if host_ip else [])
    if ip_list:
        _cb(f"Checking {len(ip_list)} IP(s) against Kubernetes reserved ranges")
        for r in _check_reserved_ip_ranges(ip_list):
            report.add(r)

    # 10. DNS resolution (requires SSH to the host)
    if host_ip and host_user and host_password:
        _cb(f"Checking DNS resolution from {host_ip}")
        for r in _check_dns_resolution(host_ip, host_user, host_password,
                                       domain_fqdn=domain_fqdn, port=ssh_port):
            report.add(r)

    # -- Summary ----------------------------------------------------------
    _print_report(report)
    _cb(
        f"Validation for {idrac.host}: "
        f"{report.passed} passed, {report.warnings} warnings, {report.failures} failures"
    )
    return report


def validate_all_nodes(
    servers: list[dict[str, Any]],
    *,
    progress_callback=None,
    abort_on_failure: bool = True,
) -> list[ValidationReport]:
    """Validate all nodes in the deployment config.

    Parameters
    ----------
    servers :
        List of server dicts from the YAML config.
    progress_callback :
        Optional callable for UI messages.
    abort_on_failure :
        If ``True``, raise after all checks if any node has failures.

    Returns
    -------
    list of ``ValidationReport``
    """
    _cb = progress_callback or (lambda msg: None)
    reports: list[ValidationReport] = []

    _cb(f"Starting pre-flight validation for {len(servers)} node(s)")
    for idx, srv in enumerate(servers, 1):
        _cb(f"Validating node {idx}/{len(servers)}: {srv.get('idrac_host', '?')}")
        try:
            with IdracClient(
                srv["idrac_host"],
                srv["idrac_user"],
                srv["idrac_password"],
            ) as idrac:
                report = validate_node(
                    idrac,
                    host_ip=srv.get("host_ip", ""),
                    ssh_port=int(srv.get("ssh_port", 22)),
                    progress_callback=progress_callback,
                )
                reports.append(report)
        except Exception as exc:
            report = ValidationReport(host=srv.get("idrac_host", "unknown"))
            report.add(CheckResult(
                name="Node Validation",
                severity=Severity.FAIL,
                message=f"Validation error: {exc}",
            ))
            reports.append(report)

    # Summary
    total_fail = sum(r.failures for r in reports)
    total_warn = sum(r.warnings for r in reports)
    total_pass = sum(r.passed for r in reports)

    summary = f"Pre-flight validation: {total_pass} passed, {total_warn} warnings, {total_fail} failures across {len(reports)} node(s)"
    log.info("[bold]%s[/]", summary)
    _cb(summary)

    if abort_on_failure and total_fail > 0:
        raise RuntimeError(
            f"Pre-flight validation failed: {total_fail} failure(s) across "
            f"{sum(1 for r in reports if not r.ok)} node(s). Fix issues and retry."
        )

    return reports


# ---------------------------------------------------------------------------
# Pretty-print
# ---------------------------------------------------------------------------

def _print_report(report: ValidationReport) -> None:
    """Log a human-readable validation report."""
    colour_map = {
        Severity.PASS: "green",
        Severity.WARN: "yellow",
        Severity.FAIL: "red",
    }
    log.info("\n[bold]──── Validation Report: %s ────[/]", report.host)
    for c in report.checks:
        colour = colour_map[c.severity]
        log.info("  [%s][%s][/%s] %s – %s", colour, c.severity.value, colour, c.name, c.message)
        if c.detail:
            log.info("        %s", c.detail)
    ok_label = "[green]PASS[/]" if report.ok else "[red]FAIL[/]"
    log.info("  Result: %s  (%d pass / %d warn / %d fail)", ok_label, report.passed, report.warnings, report.failures)
