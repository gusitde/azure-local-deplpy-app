"""Mount ISO via CIFS with domain credentials and boot."""
import requests, urllib3, time
urllib3.disable_warnings()

base = 'https://192.168.10.5'
auth = ('root', 'Tricolor00!')
local_ip = '192.168.10.201'
iso_filename = "AzureLocal24H2.26100.1742.LCM.12.2602.0.3018.x64.en-us.iso"
cifs_url = f"//{local_ip}/iso-share/{iso_filename}"

# Domain credentials
cifs_user = "gus-admin@worldai.local"
cifs_pass = "Tricolor00!@#$%^&*("

# 1. Eject existing
print("1. Ejecting existing virtual media...")
try:
    requests.post(f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.EjectMedia',
                  json={}, auth=auth, verify=False, timeout=15)
    print("   Ejected.")
except: pass
time.sleep(3)

# 2. Mount via CIFS
print(f"\n2. Mounting ISO via CIFS...")
print(f"   URL: {cifs_url}")
print(f"   User: {cifs_user}")
payload = {
    "Image": cifs_url,
    "Inserted": True,
    "WriteProtected": True,
    "UserName": cifs_user,
    "Password": cifs_pass,
}
r = requests.post(
    f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.InsertMedia',
    json=payload, auth=auth, verify=False, timeout=30)
print(f"   Response: {r.status_code}")
if r.status_code not in (200, 204):
    print(f"   Error: {r.text[:400]}")
    
    # Try with DOMAIN\user format
    print("\n   Retrying with worldai\\gus-admin format...")
    try:
        requests.post(f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.EjectMedia',
                      json={}, auth=auth, verify=False, timeout=15)
    except: pass
    time.sleep(2)
    payload["UserName"] = "worldai\\gus-admin"
    r = requests.post(
        f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.InsertMedia',
        json=payload, auth=auth, verify=False, timeout=30)
    print(f"   Response: {r.status_code}")
    if r.status_code not in (200, 204):
        print(f"   Error: {r.text[:400]}")

# 3. Verify
time.sleep(5)
print("\n3. Checking virtual media status...")
r = requests.get(f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD',
                 auth=auth, verify=False, timeout=10)
vm = r.json()
inserted = vm.get('Inserted', False)
image = vm.get('Image')
connected = vm.get('ConnectedVia')
print(f"   Inserted={inserted}, Image={image}, ConnectedVia={connected}")

if inserted:
    # 4. Set one-time boot
    print("\n4. Setting one-time boot to virtual CD...")
    r = requests.patch(f'{base}/redfish/v1/Systems/System.Embedded.1',
        json={"Boot": {"BootSourceOverrideTarget": "Cd", "BootSourceOverrideEnabled": "Once"}},
        auth=auth, verify=False, timeout=15)
    print(f"   Boot override: {r.status_code}")

    # 5. Restart
    print("\n5. Restarting server...")
    r = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1', auth=auth, verify=False, timeout=10)
    power = r.json()['PowerState']
    if power == 'On':
        r = requests.post(f'{base}/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset',
            json={"ResetType": "GracefulRestart"}, auth=auth, verify=False, timeout=15)
    else:
        r = requests.post(f'{base}/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset',
            json={"ResetType": "On"}, auth=auth, verify=False, timeout=15)
    print(f"   Power action: {r.status_code}")
    print("\n✅ Server booting from virtual CD!")
else:
    print("\n❌ ISO not mounted. Need to troubleshoot CIFS access.")
