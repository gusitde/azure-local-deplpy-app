"""Deploy Azure Local OS image to a Dell server through iDRAC virtual media.

Steps:
    1. Power off the server (graceful → force).
    2. Mount the Azure Local ISO via Redfish virtual media.
    3. Set one-time boot to virtual CD.
    4. Power on – the server boots from the ISO.
    5. Wait for the OS installation to complete (poll iDRAC for power-off
       or for the host to become reachable over SSH/WinRM).
    6. Eject virtual media.
"""

from __future__ import annotations

import time

import paramiko

from azure_local_deploy.idrac_client import IdracClient
from azure_local_deploy.utils import get_logger, retry

log = get_logger(__name__)

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
) -> None:
    """Full OS-deployment workflow for a single server.

    Parameters
    ----------
    idrac:
        Authenticated iDRAC Redfish client.
    iso_url:
        HTTP(S)/NFS/CIFS URL of the Azure Local ISO accessible to the iDRAC.
    host_ip:
        Management IP that the new OS will obtain (used to verify completion).
    host_user / host_password:
        Credentials for SSH to the freshly installed OS.
    install_timeout:
        Maximum seconds to wait for the installation to finish.
    ssh_port:
        SSH port on the target host.
    """

    log.info("[bold]== Stage: OS Image Deployment ==[/]")

    # 1. Power off ---------------------------------------------------------
    idrac.ensure_powered_off()

    # 2. Mount ISO ---------------------------------------------------------
    log.info("Mounting Azure Local ISO: %s", iso_url)
    try:
        idrac.eject_virtual_media()  # clean up any previous mount
    except Exception:
        pass  # slot may already be empty
    idrac.insert_virtual_media(iso_url)

    # 3. Set boot override -------------------------------------------------
    idrac.set_one_time_boot()

    # 4. Power on ----------------------------------------------------------
    log.info("Powering on server to boot from ISO …")
    idrac.set_power_state("On")

    # 5. Wait for installation to finish -----------------------------------
    log.info("Waiting up to %ds for OS installation to complete …", install_timeout)
    _wait_for_os_ready(host_ip, host_user, host_password, install_timeout, ssh_port)

    # 6. Eject virtual media -----------------------------------------------
    try:
        idrac.eject_virtual_media()
    except Exception:
        log.warning("Could not eject virtual media – continuing anyway.")

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
    """Poll the host via SSH until it responds, or raise on timeout."""
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        if _check_ssh(host, user, password, port):
            log.info("Host %s is reachable via SSH after %d attempts.", host, attempt)
            return
        log.info("  SSH attempt %d – host %s not ready yet …", attempt, host)
        time.sleep(30)
    raise TimeoutError(f"Host {host} did not become reachable within {timeout}s")


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
