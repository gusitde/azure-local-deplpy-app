"""Try racadm approaches: NIC config, CIFS share, and set NicMode."""
import paramiko, time

IP = '192.168.10.4'

def ssh_cmd(cmd, timeout=60):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(IP, username='root', password='Tricolor00!', timeout=15)
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    ssh.close()
    return out.strip(), err.strip()

# 1. Check racadm NIC-related commands
print("=" * 60)
print("racadm: NIC configuration")
print("=" * 60)

# Check NIC attributes via racadm
for fqdd in ['NIC.Integrated.1-1-1', 'NIC.Integrated.1-2-1']:
    print(f"\n  --- {fqdd} ---")
    out, err = ssh_cmd(f'racadm get NIC.NICConfig.1')
    if out and not err:
        print(f"  NIC.NICConfig.1: {out[:300]}")
    else:
        print(f"  NIC.NICConfig.1 error: {err[:200]}")

# Try getting NicMode attribute
for group in ['NIC.DeviceLevelConfig', 'NIC.NICConfig', 'NIC.NICMode']:
    for idx in ['1', '2']:
        out, err = ssh_cmd(f'racadm get {group}.{idx}')
        if out and 'ERROR' not in out and 'not found' not in out.lower():
            print(f"\n  {group}.{idx}:")
            print(f"  {out[:300]}")

# 2. Check what attributes are available for integrated NIC
print("\n" + "=" * 60)
print("racadm: Get all NIC.Integrated attributes")
print("=" * 60)
for fqdd in ['NIC.Integrated.1-1-1', 'NIC.Integrated.1-2-1']:
    out, err = ssh_cmd(f'racadm get -t xml -f none nic.{fqdd}', timeout=30)
    if 'ERROR' not in out:
        print(f"  {fqdd}: {out[:500]}")
    else:
        print(f"  {fqdd}: {out[:200]}")

# 3. Try to set NicMode via racadm
print("\n" + "=" * 60)
print("racadm: Try setting NicMode")
print("=" * 60)
for attr in [
    'NIC.NICMode.1.NicMode',
    'NIC.DeviceLevelConfig.1.NicMode', 
    'nic.nicconfig.1.nicmode',
]:
    out, err = ssh_cmd(f'racadm get {attr}')
    print(f"  GET {attr}: {out[:200]} {err[:100]}")

# 4. List all NIC groups via racadm
print("\n" + "=" * 60)
print("racadm: List NIC groups")
print("=" * 60)
out, err = ssh_cmd('racadm get BIOS.IntegratedDevices')
print(f"  BIOS.IntegratedDevices:")
for line in out.split('\n'):
    if line.strip():
        print(f"    {line.rstrip()}")

# 5. Check IntegratedNetwork BIOS settings  
out, err = ssh_cmd('racadm get BIOS.IntegratedDevices.IntegratedNetwork1')
print(f"\n  BIOS.IntegratedDevices.IntegratedNetwork1: {out}")

# 6. Try to get full NIC info
print("\n" + "=" * 60)
print("racadm: Full NIC FQDD listing")
print("=" * 60)
out, err = ssh_cmd('racadm hwinventory NIC.Integrated.1-1-1', timeout=30)
print(f"  NIC.Integrated.1-1-1 HW Inventory:")
for line in out.split('\n'):
    print(f"    {line.rstrip()}")

# 7. Try SCP export for NIC settings
print("\n" + "=" * 60)
print("racadm: SCP export for NIC.Integrated.1-1-1")
print("=" * 60)
# Local file SCP
out, err = ssh_cmd('racadm get -f /tmp/nic_scp.xml -t xml -c NIC.Integrated.1-1-1', timeout=60)
print(f"  Export: {out[:300]}")
if not err and 'ERROR' not in out:
    out2, _ = ssh_cmd('cat /tmp/nic_scp.xml')
    print(f"  Content: {out2[:500]}")
