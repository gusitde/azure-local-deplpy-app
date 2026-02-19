"""Configure proxy settings on Azure Local nodes.

Azure Local supports non-authenticated proxies only. The proxy configuration
must be identical across three OS components:
    1. WinInet
    2. WinHTTP
    3. Environment variables (HTTP_PROXY / HTTPS_PROXY / NO_PROXY)

Proxy must be configured BEFORE Arc registration. The orchestrator
automatically carries the proxy config to Arc Resource Bridge and AKS.

Reference:
    https://learn.microsoft.com/en-us/azure/azure-local/manage/configure-proxy-settings-23h2
    https://learn.microsoft.com/en-us/azure/azure-local/plan/cloud-deployment-network-considerations
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
class ProxyConfig:
    """Proxy configuration for Azure Local nodes."""
    http_proxy: str = ""            # e.g. "http://proxy.corp.com:8080"
    https_proxy: str = ""           # e.g. "http://proxy.corp.com:8080"
    no_proxy: list[str] = field(default_factory=list)  # Bypass list
    # Common bypasses added automatically
    auto_bypass: bool = True        # Add localhost, node IPs, etc.


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def configure_proxy(
    host: str,
    user: str,
    password: str,
    proxy: ProxyConfig,
    *,
    ssh_port: int = 22,
    node_ips: list[str] | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Configure proxy settings on a remote Azure Local node.

    Applies the same proxy configuration to WinInet, WinHTTP, and
    environment variables to ensure consistency.

    Parameters
    ----------
    host / user / password:
        SSH credentials for the target node.
    proxy:
        Proxy configuration.
    ssh_port:
        SSH port on the host.
    node_ips:
        List of other node IPs to add to bypass list.
    progress_callback:
        Optional callable for progress messages.

    Returns
    -------
    dict with keys ``wininet``, ``winhttp``, ``env_vars`` indicating success.
    """
    _cb = progress_callback or (lambda msg: None)

    if not proxy.http_proxy and not proxy.https_proxy:
        log.info("No proxy configured – skipping proxy setup for %s", host)
        _cb(f"No proxy configured for {host} – skipping")
        return {"wininet": True, "winhttp": True, "env_vars": True}

    log.info("[bold]== Configure Proxy ==[/] on %s", host)
    _cb(f"Configuring proxy on {host}")

    # Build bypass list
    bypass = list(proxy.no_proxy)
    if proxy.auto_bypass:
        bypass.extend(["localhost", "127.0.0.1", "*.local"])
        if node_ips:
            bypass.extend(node_ips)
    bypass_str = ";".join(sorted(set(bypass)))

    proxy_url = proxy.https_proxy or proxy.http_proxy
    result: dict[str, Any] = {}

    # 1. WinInet (Internet Explorer / system proxy)
    _cb(f"Setting WinInet proxy on {host} …")
    wininet_ok = _set_wininet_proxy(host, user, password, ssh_port, proxy_url, bypass_str)
    result["wininet"] = wininet_ok

    # 2. WinHTTP
    _cb(f"Setting WinHTTP proxy on {host} …")
    winhttp_ok = _set_winhttp_proxy(host, user, password, ssh_port, proxy_url, bypass_str)
    result["winhttp"] = winhttp_ok

    # 3. Environment variables
    _cb(f"Setting environment variables on {host} …")
    env_ok = _set_env_proxy(host, user, password, ssh_port, proxy, bypass_str)
    result["env_vars"] = env_ok

    all_ok = all(result.values())
    if all_ok:
        log.info("[bold green]Proxy configuration complete on %s[/]", host)
        _cb(f"Proxy configured on {host} ✔")
    else:
        log.warning("Some proxy settings failed on %s: %s", host, result)
        _cb(f"Proxy partially configured on {host}: {result}")

    return result


def check_proxy_consistency(
    host: str,
    user: str,
    password: str,
    *,
    ssh_port: int = 22,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Check that proxy settings are consistent across WinInet, WinHTTP, and env vars.

    Returns
    -------
    dict with ``consistent`` (bool) and per-component proxy strings.
    """
    _cb = progress_callback or (lambda msg: None)
    _cb(f"Checking proxy consistency on {host} …")

    result: dict[str, Any] = {"consistent": False}

    # WinInet
    try:
        wininet = run_powershell(
            host, user, password,
            "(Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings').ProxyServer",
            port=ssh_port,
        ).strip()
        result["wininet_proxy"] = wininet
    except Exception:
        result["wininet_proxy"] = ""

    # WinHTTP
    try:
        winhttp = run_powershell(
            host, user, password,
            "netsh winhttp show proxy",
            port=ssh_port,
        ).strip()
        result["winhttp_output"] = winhttp
    except Exception:
        result["winhttp_output"] = ""

    # Environment variables
    try:
        env_http = run_powershell(
            host, user, password,
            "[Environment]::GetEnvironmentVariable('HTTP_PROXY', 'Machine')",
            port=ssh_port,
        ).strip()
        env_https = run_powershell(
            host, user, password,
            "[Environment]::GetEnvironmentVariable('HTTPS_PROXY', 'Machine')",
            port=ssh_port,
        ).strip()
        result["env_http_proxy"] = env_http
        result["env_https_proxy"] = env_https
    except Exception:
        result["env_http_proxy"] = ""
        result["env_https_proxy"] = ""

    # Check consistency
    proxies = {result.get("wininet_proxy", ""), result.get("env_https_proxy", "")}
    proxies.discard("")
    result["consistent"] = len(proxies) <= 1

    _cb(f"Proxy consistency on {host}: {'OK' if result['consistent'] else 'MISMATCH'}")
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _set_wininet_proxy(
    host: str, user: str, password: str, port: int,
    proxy_url: str, bypass: str,
) -> bool:
    """Set the WinInet (Internet Explorer) proxy via registry."""
    cmd = (
        f"Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings' "
        f"-Name 'ProxyEnable' -Value 1 -Type DWord -Force; "
        f"Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings' "
        f"-Name 'ProxyServer' -Value '{proxy_url}' -Type String -Force; "
        f"Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings' "
        f"-Name 'ProxyOverride' -Value '{bypass}' -Type String -Force"
    )
    try:
        run_powershell(host, user, password, cmd, port=port)
        log.info("  ✔ WinInet proxy set to %s", proxy_url)
        return True
    except Exception as exc:
        log.warning("  ✘ WinInet proxy failed: %s", exc)
        return False


def _set_winhttp_proxy(
    host: str, user: str, password: str, port: int,
    proxy_url: str, bypass: str,
) -> bool:
    """Set the WinHTTP proxy via netsh."""
    cmd = f"netsh winhttp set proxy proxy-server='{proxy_url}' bypass-list='{bypass}'"
    try:
        run_powershell(host, user, password, cmd, port=port)
        log.info("  ✔ WinHTTP proxy set to %s", proxy_url)
        return True
    except Exception as exc:
        log.warning("  ✘ WinHTTP proxy failed: %s", exc)
        return False


def _set_env_proxy(
    host: str, user: str, password: str, port: int,
    proxy: ProxyConfig, bypass: str,
) -> bool:
    """Set machine-level environment variables for proxy."""
    env_cmds = []
    if proxy.http_proxy:
        env_cmds.append(
            f"[Environment]::SetEnvironmentVariable('HTTP_PROXY', '{proxy.http_proxy}', 'Machine')"
        )
    if proxy.https_proxy:
        env_cmds.append(
            f"[Environment]::SetEnvironmentVariable('HTTPS_PROXY', '{proxy.https_proxy}', 'Machine')"
        )
    if bypass:
        env_cmds.append(
            f"[Environment]::SetEnvironmentVariable('NO_PROXY', '{bypass}', 'Machine')"
        )
    cmd = "; ".join(env_cmds)
    try:
        run_powershell(host, user, password, cmd, port=port)
        log.info("  ✔ Environment proxy variables set")
        return True
    except Exception as exc:
        log.warning("  ✘ Environment proxy variables failed: %s", exc)
        return False
