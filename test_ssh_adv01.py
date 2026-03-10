import paramiko
import sys

host = '192.168.1.30'
user = 'worldai\\gus-admin'
password = 'Tricolor00!@#$%^&*('

print(f"Testing SSH to {host} as {user}...")
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    client.connect(host, port=22, username=user, password=password, timeout=15, banner_timeout=15, auth_timeout=15)
    _, stdout, stderr = client.exec_command('hostname', timeout=30)
    rc = stdout.channel.recv_exit_status()
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    print(f"SUCCESS! hostname={out}, rc={rc}")
    if err:
        print(f"stderr: {err}")
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
finally:
    client.close()
