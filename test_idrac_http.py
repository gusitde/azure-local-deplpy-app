"""Test if iDRAC can reach our HTTP server."""
import requests
import urllib3
import paramiko
urllib3.disable_warnings()

AUTH = ("root", "Tricolor00!")

# Test 1: Try mounting ISO via HTTP with Redfish InsertMedia
ip = "192.168.10.6"
print("Test 1: InsertMedia with HTTP URL")
r = requests.post(
    f"https://{ip}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.InsertMedia",
    auth=AUTH, verify=False, timeout=30,
    json={"Image": "http://192.168.10.201:8089/AzureLocal24H2.26100.1742.LCM.12.2602.0.3018.x64.en-us.iso"}
)
print(f"  Status: {r.status_code}")
print(f"  Response: {r.text[:500]}")

# Test 2: Try racadm remoteimage with HTTP
print("\nTest 2: racadm remoteimage with HTTP")
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(ip, port=22, username="root", password="Tricolor00!", timeout=10)

cmds = [
    'racadm remoteimage -d',
    'racadm remoteimage -c -l "http://192.168.10.201:8089/AzureLocal24H2.26100.1742.LCM.12.2602.0.3018.x64.en-us.iso"',
    'racadm remoteimage -s',
]
for cmd in cmds:
    _, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    print(f"  CMD: {cmd.split('racadm ')[1][:60]}")
    print(f"  OUT: {out[:200]}")
    if err:
        print(f"  ERR: {err[:200]}")

# Test 3: racadm with NFS-style path
print("\nTest 3: racadm with NFS-style path")
_, stdout, _ = client.exec_command('racadm remoteimage -d', timeout=10)
stdout.read()
cmd = 'racadm remoteimage -c -l "192.168.10.201:/ald-iso/AzureLocal24H2.26100.1742.LCM.12.2602.0.3018.x64.en-us.iso"'
_, stdout, stderr = client.exec_command(cmd, timeout=30)
out = stdout.read().decode().strip()
print(f"  OUT: {out[:200]}")

# Test 4: Check what racadm considers valid
print("\nTest 4: racadm remoteimage help")
_, stdout, _ = client.exec_command('racadm help remoteimage', timeout=10)
out = stdout.read().decode().strip()
print(out[:800])

client.close()
