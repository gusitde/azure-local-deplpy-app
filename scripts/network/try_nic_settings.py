"""
Try Settings endpoint for NIC device function, and if that fails,
explore disabling Broadcom on adv02 to match adv01's config.
"""
import requests
import json
import time
import urllib3
urllib3.disable_warnings()

ADV01_IP = "192.168.10.4"
ADV02_IP = "192.168.10.5"
USER = "root"
PASS = "Tricolor00!"
HEADERS = {"Content-Type": "application/json"}

def api(base_ip, method, path, data=None):
    url = f"https://{base_ip}{path}"
    kwargs = dict(auth=(USER, PASS), verify=False, timeout=30, headers=HEADERS)
    if data:
        kwargs["json"] = data
    r = getattr(requests, method)(url, **kwargs)
    return r

# ============================================================
# PART 1: Try Settings endpoint on adv01 Broadcom
# ============================================================
print("=" * 70)
print("PART 1: Try NetworkDeviceFunction Settings on adv01")
print("=" * 70)

settings_path = "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/NIC.Integrated.1-1-1/Settings"
r = api(ADV01_IP, "get", settings_path)
print(f"\n  GET {settings_path}")
print(f"  Status: {r.status_code}")
if r.ok:
    data = r.json()
    print(f"  Response keys: {list(data.keys())}")
    print(f"  DeviceEnabled: {data.get('DeviceEnabled')}")
    print(f"  NetDevFuncType: {data.get('NetDevFuncType')}")
    print(f"  Full response: {json.dumps(data, indent=2)[:1000]}")
    
    # Try to PATCH DeviceEnabled
    print(f"\n  Attempting PATCH DeviceEnabled=true...")
    r2 = api(ADV01_IP, "patch", settings_path, {"DeviceEnabled": True})
    print(f"  PATCH Status: {r2.status_code}")
    print(f"  Response: {r2.text[:500]}")
else:
    print(f"  Response: {r.text[:500]}")

# Also try the main function endpoint
func_path = "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/NIC.Integrated.1-1-1"
print(f"\n  Attempting PATCH on main function endpoint...")
r = api(ADV01_IP, "patch", func_path, {"DeviceEnabled": True})
print(f"  PATCH {func_path}")
print(f"  Status: {r.status_code}")
print(f"  Response: {r.text[:500]}")

# ============================================================
# PART 2: Check adv02 Broadcom NIC state in Windows
# ============================================================
print("\n" + "=" * 70)
print("PART 2: Check NIC state on both servers via WinRM")
print("=" * 70)

import subprocess

# adv01 - domain joined
ps_adv01 = '''
$s = New-PSSession -ComputerName 192.168.1.30 -Credential (New-Object PSCredential("worldai\\gus-admin", (ConvertTo-SecureString "Tricolor00!@#$%^&*(" -AsPlainText -Force)))
Invoke-Command -Session $s -ScriptBlock {
    Write-Host "=== adv01 Network Adapters ==="
    Get-NetAdapter | Select-Object Name, InterfaceDescription, Status, LinkSpeed, MacAddress | Format-Table -AutoSize
    Write-Host "`n=== adv01 All PnP Network Devices ==="
    Get-PnpDevice -Class Net -ErrorAction SilentlyContinue | Select-Object Status, Class, FriendlyName, InstanceId | Format-Table -AutoSize
    Write-Host "`n=== adv01 Hidden/Disabled Network PnP Devices ==="
    Get-PnpDevice -Class Net -Status Unknown -ErrorAction SilentlyContinue | Format-Table -AutoSize
    Get-PnpDevice -Class Net -Status Error -ErrorAction SilentlyContinue | Format-Table -AutoSize
}
Remove-PSSession $s
'''

print("\n--- adv01 NIC state ---")
result = subprocess.run(["powershell", "-Command", ps_adv01], capture_output=True, text=True, timeout=60)
print(result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout)
if result.stderr:
    print(f"  STDERR: {result.stderr[:500]}")

# adv02 - local admin
ps_adv02 = '''
$s = New-PSSession -ComputerName 192.168.1.105 -Credential (New-Object PSCredential("Administrator", (ConvertTo-SecureString "Tricolor00!@#$" -AsPlainText -Force)))
Invoke-Command -Session $s -ScriptBlock {
    Write-Host "=== adv02 Network Adapters ==="
    Get-NetAdapter | Select-Object Name, InterfaceDescription, Status, LinkSpeed, MacAddress | Format-Table -AutoSize
    Write-Host "`n=== adv02 All PnP Network Devices ==="
    Get-PnpDevice -Class Net | Select-Object Status, Class, FriendlyName, InstanceId | Format-Table -AutoSize
    Write-Host "`n=== adv02 Broadcom NICs Detail ==="
    Get-PnpDevice -Class Net | Where-Object { $_.FriendlyName -like "*Broadcom*" -or $_.FriendlyName -like "*BRCM*" -or $_.FriendlyName -like "*BCM*" } | ForEach-Object {
        Write-Host "$($_.FriendlyName) - Status: $($_.Status) - InstanceId: $($_.InstanceId)"
        $props = Get-PnpDeviceProperty -InstanceId $_.InstanceId -ErrorAction SilentlyContinue
        $problem = $props | Where-Object { $_.KeyName -eq "DEVPKEY_Device_ProblemCode" }
        if ($problem) { Write-Host "  ProblemCode: $($problem.Data)" }
    }
}
Remove-PSSession $s
'''

print("\n--- adv02 NIC state ---")
result = subprocess.run(["powershell", "-Command", ps_adv02], capture_output=True, text=True, timeout=60)
print(result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout)
if result.stderr:
    print(f"  STDERR: {result.stderr[:500]}")

# ============================================================
# PART 3: Check NIC.Integrated.1 firmware inventory on both
# ============================================================
print("\n" + "=" * 70)
print("PART 3: Firmware Inventory for NIC.Integrated.1 on both servers")
print("=" * 70)

for name, ip in [("adv01", ADV01_IP), ("adv02", ADV02_IP)]:
    r = api(ip, "get", "/redfish/v1/UpdateService/FirmwareInventory")
    if r.ok:
        members = r.json().get("Members", [])
        print(f"\n  {name} Firmware entries with NIC/Integrated/BRCM:")
        for m in members:
            fpath = m.get("@odata.id", "")
            if any(x in fpath.lower() for x in ["nic", "integrated", "brcm", "broadcom"]):
                r2 = api(ip, "get", fpath)
                if r2.ok:
                    fd = r2.json()
                    print(f"    {fd.get('Id')}: {fd.get('Name')} v{fd.get('Version')} - Updateable: {fd.get('Updateable')}")

# ============================================================
# PART 4: Try to set BIOS IntegratedNetwork1=Disabled on adv02
#         (Just show current value and what a PATCH would look like)
# ============================================================
print("\n" + "=" * 70)
print("PART 4: BIOS IntegratedNetwork1 setting on adv02")
print("=" * 70)

r = api(ADV02_IP, "get", "/redfish/v1/Systems/System.Embedded.1/Bios")
if r.ok:
    attrs = r.json().get("Attributes", {})
    print(f"  adv02 IntegratedNetwork1 = {attrs.get('IntegratedNetwork1')}")
    
# Get pending settings
r = api(ADV02_IP, "get", "/redfish/v1/Systems/System.Embedded.1/Bios/Settings")
if r.ok:
    data = r.json()
    pending = data.get("Attributes", {})
    if "IntegratedNetwork1" in pending:
        print(f"  adv02 Pending IntegratedNetwork1 = {pending.get('IntegratedNetwork1')}")
    print(f"  Settings endpoint available: YES")
    print(f"  Settings path: /redfish/v1/Systems/System.Embedded.1/Bios/Settings")

print("\n\n=== SUMMARY ===")
print("""
Options to resolve NIC count mismatch:

OPTION A: Fix Broadcom on adv01 (VERY DIFFICULT)
  - NIC firmware is degraded (0 attributes, no PCIDeviceID)
  - Can't update firmware via any Redfish method (RED097)
  - Can't change NicMode via any Redfish method
  - Would need physical intervention (BIOS setup menu, NIC replacement)
  - BIOS update might help but no BIOS DUP available

OPTION B: Disable Broadcom on adv02 via BIOS (RECOMMENDED)
  - PATCH IntegratedNetwork1=Disabled on adv02 BIOS
  - Reboot adv02
  - Both servers would show 2 Mellanox NICs only
  - Simple, reliable, reversible
  
OPTION C: Disable Broadcom NICs in Device Manager on adv02
  - Disable-PnpDevice on the Broadcom NICs  
  - May or may not satisfy validator depending on how it counts NICs
""")
