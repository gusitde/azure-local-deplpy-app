"""Remote command execution helpers (SSH / WinRM / PowerShell).

Supports two transports:
  - **SSH** (paramiko) – default, port 22
  - **WinRM** (pywinrm) – fallback, port 5985/5986

When ``transport="auto"`` (the default), SSH is attempted first; if the TCP
connection times out or is refused the function transparently falls back to
WinRM on port 5985.

SECURITY NOTE: SSH host-key verification uses AutoAddPolicy for first-connect
convenience in lab/deployment scenarios. In production, configure known_hosts
or switch to paramiko.RejectPolicy and pre-populate host keys.
"""

from __future__ import annotations

import os
import socket
from typing import Literal

import paramiko

from azure_local_deploy.utils import get_logger, retry

log = get_logger(__name__)

# Allow override via environment variable for production
_SSH_STRICT = os.environ.get("ALD_SSH_STRICT_HOST_KEYS", "").lower() == "true"

Transport = Literal["auto", "ssh", "winrm"]


# ── WinRM helper (lazy-imported) ─────────────────────────────────────────────

def _run_winrm(
    host: str,
    user: str,
    password: str,
    script: str,
    *,
    port: int = 5985,
    timeout: int = 120,
) -> str:
    """Execute a PowerShell script on a remote host via WinRM.

    Uses native PowerShell ``Invoke-Command`` (subprocess) for Kerberos/NTLM
    authentication.  Falls back to *pywinrm* if native PS is unavailable.

    Returns stdout. Raises ``RuntimeError`` on non-zero exit code.
    """
    import subprocess
    import tempfile

    log.debug("WinRM (native PS) running on %s:%d: %s", host, port, script[:120])

    # Build a wrapper script that creates PSCredential and invokes remotely
    wrapper = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$pw = ConvertTo-SecureString -String @'\n{password}\n'@ -AsPlainText -Force\n"
        f"$cred = New-Object System.Management.Automation.PSCredential('{user}', $pw)\n"
        f"$result = Invoke-Command -ComputerName '{host}' -Credential $cred "
        f"-ScriptBlock {{ {script} }} -ErrorAction Stop\n"
        "$result | Out-String | Write-Output\n"
    )

    # Write wrapper to a temp file to avoid quoting nightmares
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ps1", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(wrapper)
        tmp_path = tmp.name

    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-File", tmp_path,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 30,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    out = proc.stdout.strip()
    err = proc.stderr.strip()

    if proc.returncode != 0:
        log.error(
            "WinRM command failed (exit %d) on %s:\n  stdout: %s\n  stderr: %s",
            proc.returncode, host, out, err,
        )
        raise RuntimeError(
            f"Remote command failed (exit {proc.returncode}): {err or out}"
        )

    if err:
        log.debug("WinRM stderr on %s: %s", host, err)
    return out


# ── SSH helper ───────────────────────────────────────────────────────────────

def _run_ssh(
    host: str,
    user: str,
    password: str,
    script: str,
    *,
    port: int = 22,
    timeout: int = 120,
) -> str:
    """Execute a PowerShell snippet via SSH (paramiko).

    Returns stdout. Raises ``RuntimeError`` on non-zero exit code.
    """
    command = f'powershell.exe -NoProfile -NonInteractive -Command "{script}"'

    client = paramiko.SSHClient()
    if _SSH_STRICT:
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            host, port=port, username=user, password=password,
            timeout=15, banner_timeout=15, auth_timeout=15,
        )
        log.debug("SSH running on %s: %s", host, command[:120])
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()

        if exit_code != 0:
            log.error(
                "SSH command failed (exit %d) on %s:\n  stdout: %s\n  stderr: %s",
                exit_code, host, out, err,
            )
            raise RuntimeError(
                f"Remote command failed (exit {exit_code}): {err or out}"
            )

        if err:
            log.debug("SSH stderr on %s: %s", host, err)
        return out
    finally:
        client.close()


# ── Public API ───────────────────────────────────────────────────────────────

@retry(max_attempts=3, delay_seconds=5)
def run_powershell(
    host: str,
    user: str,
    password: str,
    script: str,
    *,
    port: int = 22,
    timeout: int = 120,
    transport: Transport = "auto",
) -> str:
    """Execute a PowerShell snippet on a remote Windows/Azure Local host.

    Parameters
    ----------
    transport : ``"auto"`` | ``"ssh"`` | ``"winrm"``
        * ``"ssh"``  – use SSH only (port 22 by default).
        * ``"winrm"`` – use WinRM only (port 5985/5986).
        * ``"auto"``  – try SSH first; on TCP timeout/refused fall back to
          WinRM on port 5985.  If *port* is explicitly set to 5985 or 5986 the
          function goes straight to WinRM.

    Returns the stdout output as a string.
    Raises ``RuntimeError`` when the remote exit code is non-zero.
    """
    # Determine effective transport
    if transport == "winrm" or port in (5985, 5986):
        return _run_winrm(host, user, password, script, port=port, timeout=timeout)

    if transport == "ssh":
        return _run_ssh(host, user, password, script, port=port, timeout=timeout)

    # transport == "auto": try SSH, fall back to WinRM
    try:
        return _run_ssh(host, user, password, script, port=port, timeout=timeout)
    except (
        TimeoutError,
        socket.timeout,
        OSError,
        paramiko.ssh_exception.NoValidConnectionsError,
    ) as ssh_err:
        log.warning(
            "SSH to %s:%d failed (%s) – falling back to WinRM on port 5985",
            host, port, ssh_err,
        )
        return _run_winrm(host, user, password, script, port=5985, timeout=timeout)


def run_powershell_script_file(
    host: str,
    user: str,
    password: str,
    local_script_path: str,
    *,
    port: int = 22,
    timeout: int = 300,
) -> str:
    """Upload a local .ps1 file and execute it on the remote host."""
    import os

    remote_path = f"C:\\Temp\\{os.path.basename(local_script_path)}"

    client = paramiko.SSHClient()
    if _SSH_STRICT:
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, port=port, username=user, password=password,
                       timeout=15, banner_timeout=15, auth_timeout=15)

        # Ensure remote temp dir exists
        client.exec_command("powershell.exe -Command \"New-Item -ItemType Directory -Force -Path C:\\Temp\"")

        sftp = client.open_sftp()
        sftp.put(local_script_path, remote_path)
        sftp.close()

        command = f"powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"{remote_path}\""
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()

        if exit_code != 0:
            raise RuntimeError(f"Script failed (exit {exit_code}): {err or out}")
        return out
    finally:
        client.close()
