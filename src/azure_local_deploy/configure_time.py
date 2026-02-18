"""Configure NTP / time server on Azure Local hosts.

Critical for cluster-quorum and Azure Arc registration – all nodes must
agree on time within a tight tolerance.
"""

from __future__ import annotations

from azure_local_deploy.remote import run_powershell
from azure_local_deploy.utils import get_logger

log = get_logger(__name__)


def configure_time_server(
    host: str,
    user: str,
    password: str,
    ntp_servers: list[str],
    *,
    ssh_port: int = 22,
    timezone: str | None = None,
) -> None:
    """Set NTP peers and (optionally) timezone on a remote host.

    Parameters
    ----------
    host / user / password:
        SSH credentials for the Azure Local node.
    ntp_servers:
        One or more NTP server addresses (e.g. ``["time.windows.com", "pool.ntp.org"]``).
    timezone:
        Optional Windows timezone id (e.g. "UTC", "Eastern Standard Time").
    """
    log.info("[bold]== Stage: Time Server Configuration ==[/] on %s", host)

    if not ntp_servers:
        raise ValueError("At least one NTP server must be specified.")

    # 1. Set timezone (optional) -------------------------------------------
    if timezone:
        log.info("Setting timezone to [cyan]%s[/]", timezone)
        run_powershell(host, user, password,
                       f"Set-TimeZone -Id '{timezone}'",
                       port=ssh_port)

    # 2. Configure NTP peers -----------------------------------------------
    peers = " ".join(ntp_servers)
    # Use w32tm – the built-in Windows Time service
    cmds = [
        # Stop the service so config changes take effect
        "Stop-Service w32time -Force -ErrorAction SilentlyContinue",
        # Register the service (idempotent)
        "w32tm /register",
        # Set NTP peers
        f"w32tm /config /manualpeerlist:\"{peers}\" /syncfromflags:MANUAL /reliable:YES /update",
        # Restart
        "Start-Service w32time",
        # Force an immediate sync
        "w32tm /resync /force",
    ]
    combined = "; ".join(cmds)
    log.info("Configuring NTP peers: %s", peers)
    run_powershell(host, user, password, combined, port=ssh_port)

    # 3. Verify -------------------------------------------------------------
    _verify_time_sync(host, user, password, ssh_port)

    log.info("[bold green]Time server configuration complete[/] on %s", host)


def _verify_time_sync(host: str, user: str, password: str, port: int) -> None:
    """Query w32tm status and log the result."""
    result = run_powershell(host, user, password, "w32tm /query /status", port=port)
    log.info("Time sync status on %s:\n%s", host, result)

    # Quick sanity: look for 'Leap Indicator: 0' or 'Source:'
    if "Source:" in result:
        log.info("  ✔ Time source configured successfully")
    else:
        log.warning("  ✘ Could not verify time source on %s", host)
