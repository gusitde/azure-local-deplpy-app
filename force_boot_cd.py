"""Force boot from virtual CD: power off, verify ISO mounted, set boot, power on."""
import requests, urllib3, time
urllib3.disable_warnings()

base = 'https://192.168.10.5'
auth = ('root', 'Tricolor00!')

# 1. Check current state
print("1. Current state...")
r = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1', auth=auth, verify=False, timeout=10)
sys_data = r.json()
print(f"   PowerState: {sys_data['PowerState']}")
boot = sys_data.get('Boot', {})
print(f"   BootSourceOverrideTarget: {boot.get('BootSourceOverrideTarget')}")
print(f"   BootSourceOverrideEnabled: {boot.get('BootSourceOverrideEnabled')}")
print(f"   Allowed targets: {boot.get('BootSourceOverrideTarget@Redfish.AllowableValues', [])}")

# 2. Check virtual media
r = requests.get(f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD',
                 auth=auth, verify=False, timeout=10)
vm = r.json()
print(f"\n2. Virtual Media CD:")
print(f"   Inserted: {vm.get('Inserted')}")
print(f"   Image: {vm.get('Image')}")
print(f"   ConnectedVia: {vm.get('ConnectedVia')}")

# If not mounted, remount
if not vm.get('Inserted'):
    print("\n   Re-mounting ISO...")
    local_ip = '192.168.10.201'
    iso_filename = "AzureLocal24H2.26100.1742.LCM.12.2602.0.3018.x64.en-us.iso"
    cifs_url = f"//{local_ip}/iso-share/{iso_filename}"
    payload = {
        "Image": cifs_url, "Inserted": True, "WriteProtected": True,
        "UserName": "gus@worldai.local", "Password": "Tricolor00!@#$%^&*("
    }
    r = requests.post(
        f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.InsertMedia',
        json=payload, auth=auth, verify=False, timeout=30)
    print(f"   Mount: {r.status_code}")
    time.sleep(3)

# 3. Force power off
print("\n3. Forcing power off...")
r = requests.post(f'{base}/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset',
    json={"ResetType": "ForceOff"}, auth=auth, verify=False, timeout=15)
print(f"   ForceOff: {r.status_code}")

# Wait for power off
for i in range(12):
    time.sleep(5)
    r = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1', auth=auth, verify=False, timeout=10)
    state = r.json()['PowerState']
    if state == 'Off':
        print(f"   Server is OFF after {(i+1)*5}s")
        break
    print(f"   Still {state}...")

# 4. Set boot override WHILE off
print("\n4. Setting boot override to Cd...")
r = requests.patch(f'{base}/redfish/v1/Systems/System.Embedded.1',
    json={"Boot": {"BootSourceOverrideTarget": "Cd", "BootSourceOverrideEnabled": "Once"}},
    auth=auth, verify=False, timeout=15)
print(f"   Boot override: {r.status_code}")
if r.status_code != 200:
    print(f"   Body: {r.text[:300]}")

# Verify boot override took
time.sleep(2)
r = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1', auth=auth, verify=False, timeout=10)
boot2 = r.json().get('Boot', {})
print(f"   Verified - Target: {boot2.get('BootSourceOverrideTarget')}, Enabled: {boot2.get('BootSourceOverrideEnabled')}")

# 5. Also try Dell OEM SetNextOneTimeBoot if available
print("\n5. Trying Dell OEM import system config for CD boot...")
# Use BootSeq approach via import config
import_payload = {
    "ShareParameters": {
        "Target": "ALL"
    },
    "ImportBuffer": '<SystemConfiguration><Component FQDD="iDRAC.Embedded.1"><Attribute Name="ServerBoot.FirstBootDevice">VCD-DVD</Attribute></Component></SystemConfiguration>'
}
try:
    r = requests.post(
        f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/Actions/Oem/EID_674_Manager.ImportSystemConfiguration',
        json=import_payload, auth=auth, verify=False, timeout=30)
    print(f"   Import config: {r.status_code}")
    if r.status_code not in (200, 202):
        print(f"   (non-critical) {r.text[:200]}")
except Exception as e:
    print(f"   (skipped) {e}")

time.sleep(3)

# 6. Power on
print("\n6. Powering on...")
r = requests.post(f'{base}/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset',
    json={"ResetType": "On"}, auth=auth, verify=False, timeout=15)
print(f"   Power On: {r.status_code}")

print("\n🚀 Server powering on with virtual CD boot override set.")
print("   Check iDRAC virtual console to verify it boots from the ISO installer.")
