"""Remote command execution helpers (SSH / PowerShell over SSH).

SECURITY NOTE: SSH host-key verification uses AutoAddPolicy for first-connect
convenience in lab/deployment scenarios. In production, configure known_hosts
or switch to paramiko.RejectPolicy and pre-populate host keys.
"""

from __future__ import annotations

import paramiko

from azure_local_deploy.utils import get_logger, retry

log = get_logger(__name__)

# Allow override via environment variable for production
import os
_SSH_STRICT = os.environ.get("ALD_SSH_STRICT_HOST_KEYS", "").lower() == "true"


@retry(max_attempts=3, delay_seconds=5)
def run_powershell(
    host: str,
    user: str,
    password: str,
    script: str,
    *,
    port: int = 22,
    timeout: int = 120,
) -> str:
    """Execute a PowerShell snippet on a remote Windows/Azure Local host via SSH.

    Returns the combined stdout+stderr output as a string.
    Raises ``RuntimeError`` when the remote exit code is non-zero.
    """
    # Wrap the script so it always goes through powershell.exe
    command = f"powershell.exe -NoProfile -NonInteractive -Command \"{script}\""

    client = paramiko.SSHClient()
    if _SSH_STRICT:
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, port=port, username=user, password=password,
                       timeout=15, banner_timeout=15, auth_timeout=15)
        log.debug("Running on %s: %s", host, command[:120])
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()

        if exit_code != 0:
            log.error("Remote command failed (exit %d) on %s:\n  stdout: %s\n  stderr: %s",
                      exit_code, host, out, err)
            raise RuntimeError(f"Remote command failed (exit {exit_code}): {err or out}")

        if err:
            log.debug("stderr on %s: %s", host, err)
        return out
    finally:
        client.close()


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
