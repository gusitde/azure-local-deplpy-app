"""Dell firmware update via iDRAC Redfish API.

Uses the Dell Lifecycle Controller ``SimpleUpdate`` action to apply firmware
updates from a user-supplied Dell Update Package (DUP) repository or
individual DUP files (HTTP/HTTPS/CIFS/NFS).

Flow:
    1. Query current firmware inventory.
    2. For each component (BIOS, iDRAC, NIC, RAID, Disk, …) – trigger
       ``SimpleUpdate`` with the DUP URL from the configuration.
    3. Poll the resulting task until completion.
    4. Reboot the server when required (some DUPs apply on next boot).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from azure_local_deploy.idrac_client import IdracClient
from azure_local_deploy.utils import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FirmwareTarget:
    """Represents a single firmware component to update."""
    component: str                  # e.g. "BIOS", "iDRAC", "NIC", "RAID", "Disk"
    dup_url: str                    # HTTP/HTTPS/NFS/CIFS path to .exe DUP
    target_version: str = ""        # optional: expected version after update
    install_option: str = "Now"    # Now | NowAndReboot | NextReboot


# ---------------------------------------------------------------------------
# Firmware inventory
# ---------------------------------------------------------------------------

def get_firmware_inventory(idrac: IdracClient) -> list[dict[str, Any]]:
    """Return the full firmware inventory from the Redfish ``UpdateService``.

    Each entry contains at minimum:
        - Name, Version, Id, Updateable, SoftwareId
    """
    collection = idrac.get("/UpdateService/FirmwareInventory")
    members = collection.get("Members", [])
    inventory: list[dict[str, Any]] = []

    for member_ref in members:
        uri = member_ref.get("@odata.id", "")
        if not uri:
            continue
        try:
            entry = idrac.get(uri.replace("/redfish/v1", ""))
            inventory.append({
                "Id": entry.get("Id", ""),
                "Name": entry.get("Name", ""),
                "Version": entry.get("Version", ""),
                "Updateable": entry.get("Updateable", False),
                "SoftwareId": entry.get("SoftwareId", ""),
                "ComponentId": entry.get("Oem", {}).get("Dell", {}).get("DellSoftwareInventory", {}).get("ComponentID", ""),
            })
        except Exception as exc:
            log.warning("Could not read firmware entry %s: %s", uri, exc)

    return inventory


def log_firmware_inventory(idrac: IdracClient) -> list[dict[str, Any]]:
    """Fetch and pretty-print the firmware inventory; return it."""
    inv = get_firmware_inventory(idrac)
    log.info("[bold cyan]Firmware inventory for %s[/] (%d components)", idrac.host, len(inv))
    for item in inv:
        tag = "[green]Updateable[/]" if item["Updateable"] else "[dim]Locked[/]"
        log.info("  %-40s  v%-20s  %s", item["Name"][:40], item["Version"], tag)
    return inv


# ---------------------------------------------------------------------------
# Simple Update action
# ---------------------------------------------------------------------------

def _trigger_simple_update(
    idrac: IdracClient,
    dup_url: str,
    install_option: str = "Now",
) -> str:
    """Trigger the Redfish ``SimpleUpdate`` action and return the task URI.

    ``install_option`` maps to the Dell-specific ``InstallUpon`` value:
        - ``Now``              – install immediately (host must be off for BIOS)
        - ``NowAndReboot``     – install and auto-reboot
        - ``NextReboot``       – stage; install on next reboot
    """
    payload: dict[str, Any] = {
        "ImageURI": dup_url,
        "@Redfish.OperationApplyTime": "Immediate",
    }
    # Dell OEM extension for install scheduling
    oem_install_map = {
        "Now": "Immediate",
        "NowAndReboot": "OnReset",
        "NextReboot": "OnReset",
    }
    payload["@Redfish.OperationApplyTime"] = oem_install_map.get(install_option, "Immediate")

    log.info("Triggering SimpleUpdate  image=%s  applyTime=%s  on %s",
             dup_url, payload["@Redfish.OperationApplyTime"], idrac.host)

    resp = idrac.post("/UpdateService/Actions/UpdateService.SimpleUpdate", payload)

    # The task location is returned in the ``Location`` header
    task_uri = resp.headers.get("Location", "")
    if not task_uri:
        # Some iDRAC versions return the task in the body
        body = resp.json() if resp.content else {}
        task_uri = body.get("@odata.id", "")
    if task_uri and task_uri.startswith("/redfish/v1"):
        task_uri = task_uri.replace("/redfish/v1", "")

    log.info("Firmware update task: %s", task_uri)
    return task_uri


# ---------------------------------------------------------------------------
# Dell Repository Update (catalog-based)
# ---------------------------------------------------------------------------

def _trigger_repository_update(
    idrac: IdracClient,
    catalog_url: str,
    apply_reboot: bool = True,
) -> str:
    """Trigger a Dell Repository Update using the ``DellLCService.InstallFromRepository`` action.

    This uses the Dell OEM extension to pull from a Dell Update Repository
    (e.g., a local Dell Repository Manager mirror or downloads.dell.com).
    """
    payload: dict[str, Any] = {
        "IPAddress": catalog_url,
        "ShareType": "HTTPS" if catalog_url.startswith("https") else "HTTP",
        "ApplyUpdate": "True",
        "RebootNeeded": "TRUE" if apply_reboot else "FALSE",
        "CatalogFile": "Catalog.xml",
    }
    # Try the OEM action
    action_uri = "/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService/Actions/DellLCService.InstallFromRepository"
    log.info("Triggering repository-based firmware update from %s on %s", catalog_url, idrac.host)

    resp = idrac.post(action_uri, payload)

    task_uri = resp.headers.get("Location", "")
    if task_uri and task_uri.startswith("/redfish/v1"):
        task_uri = task_uri.replace("/redfish/v1", "")
    log.info("Repository update task: %s", task_uri)
    return task_uri


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def update_firmware(
    idrac: IdracClient,
    targets: list[FirmwareTarget] | None = None,
    catalog_url: str = "",
    apply_reboot: bool = True,
    task_timeout: int = 3600,
    progress_callback=None,
) -> dict[str, Any]:
    """Update firmware on a Dell server via iDRAC Redfish.

    Strategy
    --------
    * If *catalog_url* is provided, use the Dell Repository update (catalog-based)
      which will automatically update all components with available updates.
    * Otherwise iterate over *targets* and apply each DUP individually.

    Returns a summary dict:
        ``{"host": ..., "updated": [...], "skipped": [...], "failed": [...]}``
    """
    _cb = progress_callback or (lambda msg: None)
    summary: dict[str, Any] = {
        "host": idrac.host,
        "updated": [],
        "skipped": [],
        "failed": [],
    }

    # -- Show current inventory -------------------------------------------
    _cb(f"Fetching firmware inventory for {idrac.host}")
    log_firmware_inventory(idrac)

    # -- Catalog-based update ---------------------------------------------
    if catalog_url:
        _cb(f"Starting repository-based firmware update from {catalog_url}")
        try:
            task_uri = _trigger_repository_update(idrac, catalog_url, apply_reboot=apply_reboot)
            if task_uri:
                _cb("Polling firmware repository update task …")
                result = idrac.poll_task(task_uri, timeout=task_timeout, interval=60)
                summary["updated"].append({
                    "component": "Repository (all components)",
                    "task": result.get("TaskState", "Unknown"),
                })
            else:
                summary["updated"].append({
                    "component": "Repository (all components)",
                    "task": "Submitted (no task URI returned)",
                })
        except Exception as exc:
            log.error("Repository firmware update failed on %s: %s", idrac.host, exc)
            summary["failed"].append({"component": "Repository", "error": str(exc)})

        _cb(f"Firmware update complete for {idrac.host}")
        return summary

    # -- Individual DUP updates -------------------------------------------
    if not targets:
        log.info("No firmware targets specified and no catalog URL – skipping firmware update on %s", idrac.host)
        _cb("No firmware updates configured – skipping")
        return summary

    for target in targets:
        _cb(f"Updating {target.component} firmware on {idrac.host}")
        log.info("[bold]Updating %s[/] → %s", target.component, target.dup_url)

        try:
            task_uri = _trigger_simple_update(idrac, target.dup_url, target.install_option)
            if task_uri:
                result = idrac.poll_task(task_uri, timeout=task_timeout, interval=30)
                summary["updated"].append({
                    "component": target.component,
                    "version": target.target_version,
                    "task_state": result.get("TaskState", "Unknown"),
                })
            else:
                summary["updated"].append({
                    "component": target.component,
                    "version": target.target_version,
                    "task_state": "Submitted",
                })
        except Exception as exc:
            log.error("Firmware update for %s failed on %s: %s", target.component, idrac.host, exc)
            summary["failed"].append({"component": target.component, "error": str(exc)})

    # -- Reboot if needed --------------------------------------------------
    if apply_reboot and summary["updated"]:
        _cb(f"Rebooting {idrac.host} to apply staged firmware updates")
        log.info("Rebooting %s to finalise firmware updates …", idrac.host)
        try:
            idrac.set_power_state("ForceRestart")
            # Wait a bit for the reboot to start
            time.sleep(30)
            # Wait for iDRAC to come back (it may reset during iDRAC FW update)
            _wait_for_idrac(idrac, timeout=600)
        except Exception as exc:
            log.warning("Post-firmware reboot issue on %s: %s", idrac.host, exc)

    _cb(f"Firmware update complete for {idrac.host}")
    return summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait_for_idrac(idrac: IdracClient, timeout: int = 600) -> None:
    """Wait until the iDRAC Redfish endpoint responds after a reboot."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            idrac.get_system()
            log.info("iDRAC %s is back online.", idrac.host)
            return
        except Exception:
            time.sleep(15)
    raise TimeoutError(f"iDRAC {idrac.host} did not respond within {timeout}s after reboot")
