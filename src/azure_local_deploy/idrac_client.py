"""Low-level iDRAC Redfish client wrapper.

Handles authentication, session management, and common Redfish calls
against a Dell iDRAC BMC endpoint.
"""

from __future__ import annotations

import time
from typing import Any

import requests
import urllib3

from azure_local_deploy.utils import get_logger, retry

# Suppress TLS warnings for self-signed iDRAC certs in lab environments.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REDFISH_BASE = "/redfish/v1"
POWER_STATES = {"On", "ForceOff", "GracefulShutdown", "ForceRestart", "GracefulRestart"}


class IdracClient:
    """Thin Redfish client for Dell iDRAC."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        verify_ssl: bool = False,
        timeout: int = 30,
    ) -> None:
        self.base_url = f"https://{host}"
        self.verify = verify_ssl
        self.timeout = timeout
        self._session = requests.Session()
        self._session.auth = (username, password)
        self._session.headers.update({"Content-Type": "application/json"})
        self._session.verify = self.verify
        self.host = host
        log.info("[bold green]iDRAC client[/] initialized for %s", host)

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.base_url}{REDFISH_BASE}{path}"

    @retry(max_attempts=3, delay_seconds=3)
    def get(self, path: str) -> dict[str, Any]:
        resp = self._session.get(self._url(path), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    @retry(max_attempts=3, delay_seconds=3)
    def post(self, path: str, payload: dict | None = None) -> requests.Response:
        resp = self._session.post(self._url(path), json=payload or {}, timeout=self.timeout)
        resp.raise_for_status()
        return resp

    @retry(max_attempts=3, delay_seconds=3)
    def patch(self, path: str, payload: dict) -> requests.Response:
        resp = self._session.patch(self._url(path), json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp

    @retry(max_attempts=3, delay_seconds=3)
    def delete(self, path: str) -> requests.Response:
        resp = self._session.delete(self._url(path), timeout=self.timeout)
        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------------
    # System queries
    # ------------------------------------------------------------------

    def get_system(self) -> dict[str, Any]:
        """Return the primary ComputerSystem resource."""
        return self.get("/Systems/System.Embedded.1")

    def get_power_state(self) -> str:
        return self.get_system()["PowerState"]

    def get_bios_attributes(self) -> dict[str, Any]:
        return self.get("/Systems/System.Embedded.1/Bios")["Attributes"]

    # ------------------------------------------------------------------
    # Power control
    # ------------------------------------------------------------------

    def set_power_state(self, state: str) -> None:
        """Issue a power action (On, ForceOff, GracefulRestart, …)."""
        if state not in POWER_STATES:
            raise ValueError(f"Invalid power state '{state}'. Choose from {POWER_STATES}")
        log.info("Setting power state to [bold]%s[/] on %s", state, self.host)
        self.post(
            "/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
            {"ResetType": state},
        )

    def ensure_powered_off(self, graceful_timeout: int = 120) -> None:
        """Gracefully shut down, falling back to force-off after timeout."""
        state = self.get_power_state()
        if state == "Off":
            log.info("Server %s is already powered off.", self.host)
            return
        log.info("Requesting graceful shutdown of %s …", self.host)
        self.set_power_state("GracefulShutdown")
        deadline = time.time() + graceful_timeout
        while time.time() < deadline:
            if self.get_power_state() == "Off":
                log.info("Server %s powered off gracefully.", self.host)
                return
            time.sleep(10)
        log.warning("Graceful shutdown timed-out – forcing power off on %s", self.host)
        self.set_power_state("ForceOff")
        time.sleep(5)

    # ------------------------------------------------------------------
    # Virtual media (ISO mount)
    # ------------------------------------------------------------------

    def list_virtual_media(self) -> list[dict]:
        """List available virtual-media slots."""
        collection = self.get("/Managers/iDRAC.Embedded.1/VirtualMedia")
        return collection.get("Members", [])

    def insert_virtual_media(self, iso_url: str, slot: str = "CD") -> None:
        """Mount an ISO via the virtual-media slot (default CD)."""
        path = f"/Managers/iDRAC.Embedded.1/VirtualMedia/{slot}/Actions/VirtualMedia.InsertMedia"
        log.info("Inserting virtual media [cyan]%s[/] into slot %s on %s", iso_url, slot, self.host)
        self.post(path, {"Image": iso_url, "Inserted": True, "WriteProtected": True})

    def eject_virtual_media(self, slot: str = "CD") -> None:
        path = f"/Managers/iDRAC.Embedded.1/VirtualMedia/{slot}/Actions/VirtualMedia.EjectMedia"
        log.info("Ejecting virtual media from slot %s on %s", slot, self.host)
        self.post(path)

    # ------------------------------------------------------------------
    # One-time boot override
    # ------------------------------------------------------------------

    def set_one_time_boot(self, target: str = "VCD-DVD") -> None:
        """Set next-boot to virtual CD/DVD so the server boots the mounted ISO."""
        log.info("Setting one-time boot to %s on %s", target, self.host)
        self.patch(
            "/Systems/System.Embedded.1",
            {
                "Boot": {
                    "BootSourceOverrideTarget": "Cd",
                    "BootSourceOverrideEnabled": "Once",
                }
            },
        )

    # ------------------------------------------------------------------
    # Job / task polling
    # ------------------------------------------------------------------

    def poll_task(self, task_uri: str, timeout: int = 1800, interval: int = 30) -> dict:
        """Poll a Redfish task until completion or timeout."""
        log.info("Polling task %s (timeout=%ds) …", task_uri, timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = self.get(task_uri)
            state = task.get("TaskState", "Unknown")
            pct = task.get("PercentComplete", "?")
            log.info("  Task %s – state=%s  progress=%s%%", task_uri, state, pct)
            if state in ("Completed", "CompletedOK"):
                return task
            if state in ("Exception", "Killed"):
                raise RuntimeError(f"Task {task_uri} failed: {task}")
            time.sleep(interval)
        raise TimeoutError(f"Task {task_uri} did not complete within {timeout}s")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "IdracClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
