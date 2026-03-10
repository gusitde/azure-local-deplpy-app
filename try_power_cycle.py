"""Try to reinitialize Broadcom NIC via inventory re-collect and AC power cycle."""
import requests, urllib3, json, paramiko, time
urllib3.disable_warnings()

IP = '192.168.10.4'
AUTH = ('root', 'Tricolor00!')

def ssh_cmd(cmd, timeout=60):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(IP, username='root', password='Tricolor00!', timeout=15)
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    ssh.close()
    return out.strip(), err.strip()

def wait_for_idrac(max_wait=300):
    """Wait for iDRAC to come back online."""
    start = time.time()
    while (time.time() - start) < max_wait:
        try:
            r = requests.get(f'https://{IP}/redfish/v1', auth=AUTH, verify=False, timeout=10)
            if r.ok:
                elapsed = int(time.time() - start)
                print(f"  iDRAC back online after {elapsed}s")
                return True
        except:
            pass
        time.sleep(5)
    return False

def check_broadcom():
    """Check if Broadcom NIC appears in firmware inventory."""
    try:
        r = requests.get(f'https://{IP}/redfish/v1/UpdateService/FirmwareInventory', auth=AUTH, verify=False, timeout=30)
        members = r.json().get('Members', [])
        for m in members:
            fpath = m.get('@odata.id', '')
            r2 = requests.get(f'https://{IP}{fpath}', auth=AUTH, verify=False, timeout=15)
            if r2.ok:
                d = r2.json()
                fname = d.get('Name', '').lower()
                fid = d.get('Id', '').lower()
                if 'broadcom' in fname or 'brcm' in fname or ('nic.integrated' in fid):
                    print(f"  FOUND: {d.get('Id')} - {d.get('Name')} v{d.get('Version')}")
                    return True
    except Exception as e:
        print(f"  Error checking: {e}")
    print("  Broadcom NOT in firmware inventory")
    return False

def check_nic_hw():
    """Check NIC.Integrated hwinventory via racadm."""
    try:
        out, _ = ssh_cmd('racadm hwinventory NIC.Integrated.1-1-1')
        for line in out.split('\n'):
            if any(k in line for k in ['PCI Device ID', 'NIC Mode', 'Family Version', 'ProductName', 'Permanent MAC']):
                print(f"  {line.strip()}")
    except Exception as e:
        print(f"  Error: {e}")

# Step 1: Force hardware inventory collection via LC
print("=" * 60)
print("Step 1: Force LC hardware inventory collection")
print("=" * 60)
out, err = ssh_cmd('racadm systemconfig getbackupscheduler')
print(f"  {out[:200]}")

# Try racadm CommandCollectInventory
out, err = ssh_cmd('racadm license --collectinventory')
print(f"  license collectinventory: {out[:200]}")

# Try via Redfish - refresh inventory
r = requests.post(
    f'https://{IP}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService/Actions/DellLCService.ExportHWInventory',
    auth=AUTH, verify=False, timeout=120,
    json={"ShareType": "Local"}
)
print(f"  ExportHWInventory: {r.status_code}")

# Step 2: Clear jobs and check
print("\n" + "=" * 60)
print("Step 2: Check current state")
print("=" * 60)
check_broadcom()
check_nic_hw()

# Step 3: Try racadm update with CIFS share using SMB to our machine
print("\n" + "=" * 60)
print("Step 3: Try racadm update with TFTP (setup simple test)")
print("=" * 60)

# Actually, let me try to set up a CIFS share and use that
# But first, let's try what protocols the update service supports
out, err = ssh_cmd('racadm help update')
# Look for protocol info
for line in out.split('\n'):
    if 'protocol' in line.lower() or 'share' in line.lower() or '-t' in line or 'type' in line.lower():
        print(f"  {line.rstrip()}")

# Step 4: Try full AC power cycle via iDRAC
print("\n" + "=" * 60)
print("Step 4: Attempting virtual AC power cycle")
print("=" * 60)
print("  This will: power off -> wait 30s -> power on")
print("  The server host OS will be shut down!")

# First, graceful shutdown
out, err = ssh_cmd('racadm serveraction graceshutdown')
print(f"  Graceful shutdown: {out}")

print("  Waiting 60s for OS to shut down...")
time.sleep(60)

# Verify power is off
out, err = ssh_cmd('racadm serveraction powerstatus')
print(f"  Power status: {out}")

# Now do a full power off (AC cycle simulation)
out, err = ssh_cmd('racadm serveraction powerdown')
print(f"  Power down: {out}")
time.sleep(10)

out, err = ssh_cmd('racadm serveraction powerstatus')
print(f"  Power status after powerdown: {out}")

# Wait 30 seconds (simulate AC drain)
print("  Waiting 30s for capacitor drain simulation...")
time.sleep(30)

# Power on
out, err = ssh_cmd('racadm serveraction powerup')
print(f"  Power up: {out}")

# Wait for system to boot
print("  Waiting for system to boot (180s)...")
time.sleep(180)

# Step 5: Check if Broadcom is now visible
print("\n" + "=" * 60)
print("Step 5: Check Broadcom NIC after power cycle")
print("=" * 60)
check_broadcom()
check_nic_hw()
