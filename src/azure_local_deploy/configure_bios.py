"""Configure Dell server BIOS settings for Azure Local via iDRAC Redfish.

Azure Local requires specific BIOS settings:
    - Intel VT / AMD-V  (hardware virtualisation) **enabled**
    - Intel VT-d / AMD IOMMU **enabled**
    - SR-IOV Global Enable **enabled**
    - Secure Boot **enabled**
    - Boot Mode **UEFI**
    - TPM 2.0 **present & enabled**
    - Memory Operating Mode = Optimizer
    - Node Interleaving = Disabled
    - System Profile = Performance / Performance Per Watt (DAPC)
    - Processor C-States = Disabled (optional, improves latency)
    - Logical Processor (Hyper-Threading) = Enabled

This module sets attributes via the Redfish BIOS ``Settings`` endpoint,
which takes effect on the next reboot.  After patching, it optionally
triggers a reboot to apply.

Reference:
    https://learn.microsoft.com/en-us/azure/azure-local/concepts/system-requirements-23h2
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from azure_local_deploy.idrac_client import IdracClient
from azure_local_deploy.utils import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Default BIOS attribute targets for Azure Local on Dell PowerEdge
# ---------------------------------------------------------------------------

# Keys are Dell BIOS attribute names as reported by Redfish;
# some names differ between 14G/15G/16G – the mapping below covers
# the most common Dell PowerEdge nomenclature.

AZURE_LOCAL_BIOS_DEFAULTS: dict[str, str] = {
    # Virtualisation
    "ProcVirtualization": "Enabled",
    "ProcX2Apic": "Enabled",

    # VT-d / IOMMU
    "ProcVtd": "Enabled",

    # SR-IOV
    "SriovGlobalEnable": "Enabled",

    # Secure Boot
    "SecureBoot": "Enabled",

    # Boot mode
    "BootMode": "Uefi",

    # TPM
    "TpmSecurity": "On",                 # On (R640: only On/Off; newer: OnPbm)
    "TpmActivation": "Enabled",
    "Tpm2Hierarchy": "Enabled",
    "TpmPpiBypassProvision": "Enabled",   # avoid physical-presence prompts

    # Memory
    "MemOpMode": "OptimizerMode",
    "NodeInterleave": "Disabled",

    # Performance
    "SysProfile": "PerfPerWattOptimizedDapc",
    "ProcCStates": "Disabled",
    "WorkloadProfile": "NotAvailable",

    # Hyper-Threading
    "LogicalProc": "Enabled",

    # Serial communication – required for Emergency Management Service (EMS)
    "EmbSata": "AhciMode",

    # Power redundancy
    "RedundantOsBoot": "Enabled",
}


@dataclass
class BiosProfile:
    """A named collection of BIOS attribute overrides."""
    name: str = "AzureLocal"
    description: str = "Optimised for Microsoft Azure Local"
    attributes: dict[str, str] = field(default_factory=lambda: dict(AZURE_LOCAL_BIOS_DEFAULTS))


# ---------------------------------------------------------------------------
# Read / compare
# ---------------------------------------------------------------------------

def get_current_bios(idrac: IdracClient) -> dict[str, Any]:
    """Return current BIOS attributes from the server."""
    return idrac.get_bios_attributes()


def _get_bios_registry(idrac: IdracClient) -> dict[str, dict[str, Any]]:
    """Fetch the BIOS attribute registry and index by attribute name.

    Returns a dict mapping attribute name to its registry entry (including
    ``ReadOnly``, ``Value`` list, etc.).
    """
    try:
        reg_data = idrac.get("/Systems/System.Embedded.1/Bios/BiosRegistry")
        entries = reg_data.get("RegistryEntries", {}).get("Attributes", [])
        return {e["AttributeName"]: e for e in entries if "AttributeName" in e}
    except Exception as exc:
        log.warning("Could not fetch BIOS attribute registry: %s", exc)
        return {}


def _filter_writable_attrs(
    desired: dict[str, str],
    registry: dict[str, dict[str, Any]],
) -> tuple[dict[str, str], list[str]]:
    """Remove attributes that are read-only or have invalid desired values.

    Returns (filtered_desired, skipped_reasons) where *skipped_reasons* is a
    list of human-readable messages for each attribute dropped.
    """
    if not registry:
        return desired, []  # no registry → optimistic attempt

    filtered: dict[str, str] = {}
    skipped: list[str] = []

    for attr, val in desired.items():
        entry = registry.get(attr)
        if entry is None:
            # Attribute not in registry – skip silently (not on this platform)
            continue

        # Check read-only
        if entry.get("ReadOnly", False):
            skipped.append(f"{attr}: read-only (controlled by another setting)")
            continue

        # Check valid values for Enumeration type
        if entry.get("Type") == "Enumeration":
            valid = {v["ValueName"] for v in entry.get("Value", [])}
            if valid and val not in valid:
                skipped.append(
                    f"{attr}: desired '{val}' not in valid values {sorted(valid)}"
                )
                continue

        filtered[attr] = val

    return filtered, skipped


def compare_bios(
    current: dict[str, Any],
    desired: dict[str, str],
) -> tuple[dict[str, tuple[Any, str]], dict[str, str]]:
    """Compare *current* BIOS attributes against *desired*.

    Returns
    -------
    mismatched : dict
        ``{attr: (current_value, desired_value)}`` for attributes that differ.
    already_ok : dict
        ``{attr: value}`` for attributes already at the desired value.
    """
    mismatched: dict[str, tuple[Any, str]] = {}
    already_ok: dict[str, str] = {}

    for attr, desired_val in desired.items():
        current_val = current.get(attr)
        if current_val is None:
            # Attribute not present – might not exist on this platform
            log.debug("BIOS attribute %s not found on %s", attr, "server")
            continue
        if str(current_val) == desired_val:
            already_ok[attr] = desired_val
        else:
            mismatched[attr] = (current_val, desired_val)

    return mismatched, already_ok


# ---------------------------------------------------------------------------
# Apply BIOS settings
# ---------------------------------------------------------------------------

def _patch_bios_pending(idrac: IdracClient, attributes: dict[str, str]) -> str | None:
    """PATCH the BIOS pending-settings resource.

    Returns the task URI if one is returned, or ``None``.
    """
    payload = {"Attributes": attributes}
    log.info("Patching %d BIOS attributes on %s", len(attributes), idrac.host)
    for attr, val in attributes.items():
        log.info("  %-40s → %s", attr, val)

    resp = idrac.patch("/Systems/System.Embedded.1/Bios/Settings", payload)
    task_uri = resp.headers.get("Location", "")
    if task_uri and task_uri.startswith("/redfish/v1"):
        task_uri = task_uri.replace("/redfish/v1", "")
    return task_uri or None


def _create_bios_config_job(idrac: IdracClient) -> str:
    """Create a ``BIOSConfiguration`` scheduled job via the Dell Job Service.

    This schedules the pending BIOS attributes to be applied on next reboot.
    """
    payload = {
        "TargetSettingsURI": "/redfish/v1/Systems/System.Embedded.1/Bios/Settings",
    }
    resp = idrac.post(
        "/Managers/iDRAC.Embedded.1/Jobs",
        payload,
    )
    task_uri = resp.headers.get("Location", "")
    if task_uri and task_uri.startswith("/redfish/v1"):
        task_uri = task_uri.replace("/redfish/v1", "")
    log.info("BIOS config job created: %s", task_uri)
    return task_uri


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def configure_bios(
    idrac: IdracClient,
    profile: BiosProfile | None = None,
    custom_attributes: dict[str, str] | None = None,
    apply_reboot: bool = True,
    task_timeout: int = 1200,
    progress_callback=None,
) -> dict[str, Any]:
    """Set BIOS attributes on a Dell server for Azure Local.

    Parameters
    ----------
    idrac :
        Connected ``IdracClient``.
    profile :
        A ``BiosProfile`` to use.  Defaults to ``AZURE_LOCAL_BIOS_DEFAULTS``.
    custom_attributes :
        Extra or overriding attributes merged on top of the profile.
    apply_reboot :
        If ``True``, reboot after patching so settings take effect.
    task_timeout :
        Maximum seconds to wait for BIOS config job completion.
    progress_callback :
        Optional callable for progress messages.

    Returns
    -------
    dict with keys ``host``, ``changed``, ``unchanged``, ``applied``.
    """
    _cb = progress_callback or (lambda msg: None)
    prof = profile or BiosProfile()
    desired = dict(prof.attributes)
    if custom_attributes:
        desired.update(custom_attributes)

    result: dict[str, Any] = {
        "host": idrac.host,
        "profile": prof.name,
        "changed": {},
        "unchanged": {},
        "applied": False,
    }

    # -- Read current BIOS ------------------------------------------------
    _cb(f"Reading current BIOS settings from {idrac.host}")
    current = get_current_bios(idrac)

    # -- Fetch registry & filter out read-only / invalid attrs -----------
    _cb(f"Checking BIOS attribute registry on {idrac.host}")
    registry = _get_bios_registry(idrac)
    desired, skipped = _filter_writable_attrs(desired, registry)
    for msg in skipped:
        log.warning("Skipping BIOS attribute: %s", msg)
    if skipped:
        _cb(f"Skipped {len(skipped)} read-only/invalid BIOS attribute(s)")

    # -- Compare -----------------------------------------------------------
    mismatched, already_ok = compare_bios(current, desired)
    result["unchanged"] = already_ok
    result["changed"] = {k: {"from": v[0], "to": v[1]} for k, v in mismatched.items()}
    result["skipped"] = skipped

    if not mismatched:
        log.info("[green]All %d BIOS attributes already match on %s[/]", len(already_ok), idrac.host)
        _cb(f"BIOS already configured correctly on {idrac.host}")
        return result

    log.info("[yellow]%d BIOS attributes need changing on %s[/]", len(mismatched), idrac.host)
    _cb(f"{len(mismatched)} BIOS attribute(s) to change on {idrac.host}")

    # -- Clear any stale iDRAC jobs before patching ----------------------
    _cb(f"Clearing iDRAC job queue on {idrac.host}")
    _clear_job_queue(idrac)

    # -- Patch pending settings -------------------------------------------
    attrs_to_set = {k: v[1] for k, v in mismatched.items()}
    _cb(f"Patching BIOS pending settings on {idrac.host}")
    _patch_bios_pending(idrac, attrs_to_set)

    # -- Create BIOS config job -------------------------------------------
    _cb(f"Creating BIOS configuration job on {idrac.host}")
    try:
        job_uri = _create_bios_config_job(idrac)
    except Exception as exc:
        log.warning("Could not create BIOS config job (may be auto-created): %s", exc)
        job_uri = ""

    # -- Reboot to apply ---------------------------------------------------
    if apply_reboot:
        _cb(f"Rebooting {idrac.host} to apply BIOS changes")
        log.info("Rebooting %s to apply BIOS settings …", idrac.host)
        try:
            idrac.ensure_powered_off()
            time.sleep(5)
            idrac.set_power_state("On")
        except Exception as exc:
            log.warning("Power cycle issue: %s — trying ForceRestart", exc)
            idrac.set_power_state("ForceRestart")

        # Wait for the BIOS config job to complete
        if job_uri:
            _cb("Waiting for BIOS configuration job to complete …")
            try:
                idrac.poll_task(job_uri, timeout=task_timeout, interval=30)
            except Exception as exc:
                log.warning("BIOS config job poll ended: %s", exc)

        # Give the server time to reboot and stabilise
        _cb(f"Waiting for {idrac.host} to boot after BIOS change")
        _wait_for_host_ready(idrac, timeout=600)
        result["applied"] = True
    else:
        _cb("BIOS changes staged – will apply on next reboot")
        log.info("BIOS changes staged on %s – pending next reboot", idrac.host)

    _cb(f"BIOS configuration complete for {idrac.host}")
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_job_queue(idrac: IdracClient) -> None:
    """Clear the iDRAC job queue to avoid stale-job conflicts."""
    try:
        idrac.post(
            "/Managers/iDRAC.Embedded.1/Oem/Dell/DellJobService/Actions/DellJobService.DeleteJobQueue",
            {"JobID": "JID_CLEARALL"},
        )
        log.info("iDRAC job queue cleared on %s", idrac.host)
        time.sleep(5)  # Give iDRAC a moment to settle
    except Exception as exc:
        log.warning("Could not clear iDRAC job queue: %s", exc)


def _wait_for_host_ready(idrac: IdracClient, timeout: int = 600) -> None:
    """Wait until the server reports PowerState == On after a reboot."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            state = idrac.get_power_state()
            if state == "On":
                log.info("Server %s is powered on and ready.", idrac.host)
                return
        except Exception:
            pass
        time.sleep(15)
    raise TimeoutError(f"Server {idrac.host} did not come back within {timeout}s")
