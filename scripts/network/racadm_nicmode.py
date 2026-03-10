import paramiko
import time

# Connect to iDRAC via SSH
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

print("Connecting to iDRAC 192.168.10.4 via SSH...")
client.connect('192.168.10.4', username='root', password='Tricolor00!', timeout=30)
print("Connected!")

# Check current NicMode for integrated NICs
commands = [
    'racadm get NIC.NICConfig.1.NicMode',
    'racadm get NIC.NICConfig.2.NicMode',
    # Try to list all NIC groups
    'racadm get NIC.NICConfig',
    # Check integrated NIC specific FQDDs
    'racadm get NIC.Integrated.1-1-1',
]

for cmd in commands:
    print(f"\n=== {cmd} ===")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode()
    err = stderr.read().decode()
    if out:
        print(out.strip())
    if err:
        print(f"STDERR: {err.strip()}")

# Try setting NicMode
print("\n=== Setting NicMode=Enabled ===")
set_cmds = [
    'racadm set NIC.NICConfig.1.NicMode Enabled',
    'racadm set NIC.NICConfig.2.NicMode Enabled',
]

for cmd in set_cmds:
    print(f"\n> {cmd}")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode()
    err = stderr.read().decode()
    if out:
        print(out.strip())
    if err:
        print(f"STDERR: {err.strip()}")

client.close()
print("\nDone.")
