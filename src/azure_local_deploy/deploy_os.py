"""Deploy Azure Local OS image to a Dell server through iDRAC virtual media.

Steps:
    1. Power off the server (graceful → force).
    2. Mount the Azure Local ISO via Redfish virtual media.
       - If *iso_url* is a local file path, a temporary HTTP server is started
         so the iDRAC can pull the ISO over the network.
    3. Set one-time boot to virtual CD.
    4. Power on – the server boots from the ISO.
    5. Wait for the OS installation to complete (poll iDRAC for power-off
       or for the host to become reachable over SSH/WinRM).
    6. Eject virtual media.
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import paramiko

from azure_local_deploy.idrac_client import IdracClient
from azure_local_deploy.utils import get_logger, retry

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Local-file HTTP server helper
# ---------------------------------------------------------------------------

def _get_local_ip_for_idrac(idrac_host: str) -> str:
    """Return the local IP that can reach *idrac_host*."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((idrac_host, 443))
        return s.getsockname()[0]
    finally:
        s.close()


class _QuietHandler(SimpleHTTPRequestHandler):
    """HTTP handler that suppresses per-request log spam."""

    def log_message(self, fmt, *args):
        log.debug("HTTP: " + fmt, *args)


class _IsoServer:
    """Temporary HTTP server that serves a single ISO file.

    Starts in a daemon thread and is stopped via ``stop()``.
    """

    def __init__(self, iso_path: Path, bind_ip: str, port: int = 0) -> None:
        directory = str(iso_path.parent)
        handler = partial(_QuietHandler, directory=directory)
        self._httpd = HTTPServer((bind_ip, port), handler)
        self.port = self._httpd.server_address[1]
        self.bind_ip = bind_ip
        self.filename = iso_path.name
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://{self.bind_ip}:{self.port}/{self.filename}"

    def start(self) -> str:
        self._thread.start()
        log.info("ISO HTTP server started at %s", self.url)
        return self.url

    def stop(self) -> None:
        self._httpd.shutdown()
        self._thread.join(timeout=5)
        log.info("ISO HTTP server stopped.")


def _resolve_iso_url(
    iso_url: str,
    idrac_host: str,
    cifs_user: str = "",
    cifs_password: str = "",
) -> tuple[str, dict | None, _IsoServer | None]:
    """Resolve *iso_url* to something the iDRAC can consume.

    For local file paths the preferred strategy is a CIFS share (much more
    reliable on Dell iDRAC than HTTP streaming).  A temporary HTTP server is
    used as fallback only if no CIFS credentials are provided.

    Returns ``(effective_url, cifs_creds_or_None, http_server_or_None)``.
    """
    path = Path(iso_url)
    if not path.is_file():
        return iso_url, None, None  # already a network URL

    local_ip = _get_local_ip_for_idrac(idrac_host)

    # --- Preferred: CIFS share -------------------------------------------
    if cifs_user:
        share_name = _ensure_smb_share(path.parent)
        cifs_url = f"//{local_ip}/{share_name}/{path.name}"
        creds = {"UserName": cifs_user, "Password": cifs_password}
        log.info("Local ISO detected – will mount via CIFS: %s", cifs_url)
        return cifs_url, creds, None

    # --- Fallback: HTTP server -------------------------------------------
    srv = _IsoServer(path, bind_ip=local_ip)
    url = srv.start()
    log.info("Local ISO detected – serving via HTTP: %s", url)
    return url, None, srv


def _ensure_smb_share(directory: Path, share_name: str = "ald-iso") -> str:
    """Create a temporary SMB share for *directory* if it doesn't exist."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"if (!(Get-SmbShare -Name '{share_name}' -EA SilentlyContinue)) "
             f"{{ New-SmbShare -Name '{share_name}' -Path '{directory}' "
             f"-FullAccess 'Everyone' -Description 'ALD temp ISO share' | Out-Null; "
             f"'CREATED' }} else {{ 'EXISTS' }}"],
            capture_output=True, text=True, timeout=30,
        )
        log.info("SMB share '%s' → %s (%s)", share_name, directory,
                 result.stdout.strip())
    except Exception as exc:
        log.warning("Could not create SMB share: %s – assuming it exists", exc)
    return share_name

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def deploy_os_image(
    idrac: IdracClient,
    iso_url: str,
    host_ip: str,
    host_user: str = "Administrator",
    host_password: str = "",
    install_timeout: int = 3600,
    ssh_port: int = 22,
    cifs_user: str = "",
    cifs_password: str = "",
) -> None:
    """Full OS-deployment workflow for a single server.

    Parameters
    ----------
    idrac:
        Authenticated iDRAC Redfish client.
    iso_url:
        HTTP(S)/NFS/CIFS URL *or local file path* of the Azure Local ISO.
    host_ip:
        Management IP that the new OS will obtain (used to verify completion).
    host_user / host_password:
        Credentials for SSH to the freshly installed OS.
    install_timeout:
        Maximum seconds to wait for the installation to finish.
    ssh_port:
        SSH port on the target host.
    cifs_user / cifs_password:
        Credentials for CIFS share access when *iso_url* is a local path.
    """

    log.info("[bold]== Stage: OS Image Deployment ==[/]")

    # Resolve ISO URL – prefer CIFS share, fallback to HTTP server
    effective_url, cifs_creds, iso_server = _resolve_iso_url(
        iso_url, idrac.host, cifs_user=cifs_user, cifs_password=cifs_password,
    )

    try:
        # 1. Power off -----------------------------------------------------
        idrac.ensure_powered_off()

        # 2. Mount ISO -----------------------------------------------------
        log.info("Mounting Azure Local ISO: %s", effective_url)
        try:
            idrac.eject_virtual_media()  # clean up any previous mount
        except Exception:
            pass  # slot may already be empty
        idrac.insert_virtual_media(effective_url, cifs_creds=cifs_creds)

        # 3. Set boot override ---------------------------------------------
        idrac.set_one_time_boot()

        # 4. Power on ------------------------------------------------------
        log.info("Powering on server to boot from ISO …")
        idrac.set_power_state("On")

        # 5. Wait for installation to finish -------------------------------
        log.info("Waiting up to %ds for OS installation to complete …", install_timeout)
        _wait_for_os_ready(host_ip, host_user, host_password, install_timeout, ssh_port)

        # 6. Eject virtual media -------------------------------------------
        try:
            idrac.eject_virtual_media()
        except Exception:
            log.warning("Could not eject virtual media – continuing anyway.")

    finally:
        # Always stop the temp HTTP server if we started one
        if iso_server is not None:
            iso_server.stop()

    log.info("[bold green]OS image deployment complete[/] for %s", host_ip)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _wait_for_os_ready(
    host: str,
    user: str,
    password: str,
    timeout: int,
    port: int,
) -> None:
    """Poll the host via SSH or WinRM until it responds, or raise on timeout."""
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        if _check_ssh(host, user, password, port):
            log.info("Host %s is reachable via SSH after %d attempts.", host, attempt)
            return
        if _check_winrm(host):
            log.info("Host %s is reachable via WinRM after %d attempts.", host, attempt)
            return
        log.info("  Attempt %d – host %s not ready yet (SSH+WinRM) …", attempt, host)
        time.sleep(30)
    raise TimeoutError(f"Host {host} did not become reachable within {timeout}s")


def _check_winrm(host: str, port: int = 5985) -> bool:
    """Return True if WinRM port is open and responding."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


def _check_ssh(host: str, user: str, password: str, port: int = 22) -> bool:
    """Return True if we can open an SSH session and run a trivial command."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, port=port, username=user, password=password, timeout=10)
        _, stdout, _ = client.exec_command("hostname", timeout=10)
        stdout.read()
        return True
    except Exception:
        return False
    finally:
        client.close()
