"""Try racadm via SSH and SimpleUpdate with target FQDD."""
import requests, urllib3, json, paramiko, time
urllib3.disable_warnings()

IP = '192.168.10.4'
AUTH = ('root', 'Tricolor00!')
HTTP_BASE = 'http://192.168.10.201:8089'
DUP_FILE = 'Network_Firmware_HVN2R_WN64_23.31.18.10_01.EXE'

def ssh_cmd(cmd, timeout=60):
    """Run racadm command via SSH."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(IP, username='root', password='Tricolor00!', timeout=15)
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    ssh.close()
    return out, err

# 1. Check racadm swinventory for Broadcom
print("=" * 60)
print("RACADM: Software Inventory (Broadcom entries)")
print("=" * 60)
out, err = ssh_cmd('racadm swinventory')
for line in out.split('\n'):
    if 'broadcom' in line.lower() or 'brcm' in line.lower() or 'NIC.Integrated' in line or 'ComponentType' in line or 'FQDD' in line or 'CurrentVersion' in line or 'ElementName' in line:
        print(f"  {line.rstrip()}")

# Print the full NIC.Integrated section
print("\n--- Full swinventory output (NIC.Integrated sections) ---")
lines = out.split('\n')
in_section = False
for i, line in enumerate(lines):
    if 'NIC.Integrated' in line:
        in_section = True
        # Print from a few lines before
        start = max(0, i-3)
        for j in range(start, i):
            print(f"  {lines[j].rstrip()}")
    if in_section:
        print(f"  {line.rstrip()}")
        if line.strip() == '' and i > 0 and lines[i-1].strip() == '':
            in_section = False

# 2. Check racadm hwinventory for NIC.Integrated entries
print("\n" + "=" * 60)
print("RACADM: Hardware Inventory (NIC.Integrated)")
print("=" * 60)
out, err = ssh_cmd('racadm hwinventory')
lines = out.split('\n')
in_section = False
for i, line in enumerate(lines):
    if 'NIC.Integrated' in line or ('Broadcom' in line or 'BRCM' in line):
        in_section = True
        start = max(0, i-2)
        for j in range(start, i):
            print(f"  {lines[j].rstrip()}")
    if in_section:
        print(f"  {line.rstrip()}")
        if line.strip() == '' and in_section and i > 0 and lines[i-1].strip() == '':
            in_section = False

# 3. Try racadm update with HTTP URI
print("\n" + "=" * 60)
print("RACADM: Trying firmware update via HTTP")
print("=" * 60)
dup_url = f"{HTTP_BASE}/{DUP_FILE}"
cmd = f'racadm update -f {DUP_FILE} -e {HTTP_BASE}/ -t HTTP -a FALSE'
print(f"  Command: {cmd}")
out, err = ssh_cmd(cmd, timeout=120)
print(f"  Output: {out[:500]}")
if err:
    print(f"  Error: {err[:300]}")

# 4. Try SimpleUpdate with explicit targets
print("\n" + "=" * 60)
print("Redfish: SimpleUpdate with explicit targets")
print("=" * 60)
for target in [
    ["/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1"],
    ["NIC.Integrated.1-1-1"],
    ["NIC.Integrated.1"],
]:
    payload = {
        "ImageURI": dup_url,
        "Targets": target,
        "@Redfish.OperationApplyTime": "Immediate"
    }
    r = requests.post(
        f'https://{IP}/redfish/v1/UpdateService/Actions/UpdateService.SimpleUpdate',
        auth=AUTH, verify=False, timeout=60,
        json=payload
    )
    print(f"\n  Targets={target}")
    print(f"  Status: {r.status_code}")
    try:
        data = r.json()
        if r.status_code in (200, 202):
            print(f"  SUCCESS: {r.headers.get('Location', '')}")
        else:
            for m in data.get("error", {}).get("@Message.ExtendedInfo", []):
                print(f"  ERROR: {m.get('Message', '')[:200]}")
    except:
        print(f"  Response: {r.text[:300]}")
