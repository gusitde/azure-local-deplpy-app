"""Mount ISO via CIFS share and boot from virtual CD on iDRAC."""
import requests, urllib3, time, socket
urllib3.disable_warnings()

base = 'https://192.168.10.5'
auth = ('root', 'Tricolor00!')

# Get local IP that reaches iDRAC
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.connect(('192.168.10.5', 443))
local_ip = s.getsockname()[0]
s.close()
print(f"Local IP for iDRAC: {local_ip}")

iso_filename = "AzureLocal24H2.26100.1742.LCM.12.2602.0.3018.x64.en-us.iso"
cifs_url = f"//{local_ip}/iso-share/{iso_filename}"
print(f"CIFS URL: {cifs_url}")

# 1. Eject any existing virtual media
print("\n1. Ejecting existing virtual media...")
for slot in ['CD', 'RemovableDisk']:
    try:
        r = requests.post(
            f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/{slot}/Actions/VirtualMedia.EjectMedia',
            json={}, auth=auth, verify=False, timeout=15)
        print(f"   Eject {slot}: {r.status_code}")
    except:
        pass

time.sleep(3)

# 2. Insert ISO via CIFS
print("\n2. Mounting ISO via CIFS...")
payload = {
    "Image": cifs_url,
    "Inserted": True,
    "WriteProtected": True,
    "UserName": "gus",             # Windows account
    "Password": "Tricolor00!"      # Windows password
}
r = requests.post(
    f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.InsertMedia',
    json=payload, auth=auth, verify=False, timeout=30)
print(f"   Insert response: {r.status_code}")
if r.status_code not in (200, 204):
    print(f"   Body: {r.text[:500]}")
    # Try alternative: OEM attach
    print("\n   Trying Dell OEM ConnectRFS method...")
    oem_payload = {
        "ShareParameters": {
            "Target": "ALL",
            "IPAddress": local_ip,
            "ShareName": "iso-share",
            "ShareType": "CIFS",
            "FileName": iso_filename,
            "UserName": "gus",
            "Password": "Tricolor00!"
        }
    }
    r2 = requests.post(
        f'{base}/redfish/v1/Dell/Systems/System.Embedded.1/DellOSDeploymentService/Actions/DellOSDeploymentService.ConnectNetworkISOImage',
        json=oem_payload, auth=auth, verify=False, timeout=30)
    print(f"   OEM Connect: {r2.status_code}")
    if r2.status_code not in (200, 202):
        print(f"   Body: {r2.text[:500]}")

# 3. Verify mount
print("\n3. Checking virtual media status...")
time.sleep(5)
r = requests.get(f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD',
                 auth=auth, verify=False, timeout=10)
vm = r.json()
print(f"   CD: Inserted={vm.get('Inserted')}, Image={vm.get('Image')}, "
      f"ConnectedVia={vm.get('ConnectedVia')}")

# 4. Set one-time boot to virtual CD
print("\n4. Setting one-time boot to virtual CD...")
r = requests.patch(
    f'{base}/redfish/v1/Systems/System.Embedded.1',
    json={"Boot": {"BootSourceOverrideTarget": "Cd", "BootSourceOverrideEnabled": "Once"}},
    auth=auth, verify=False, timeout=15)
print(f"   Boot override: {r.status_code}")

# 5. Get current power state
r = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1', auth=auth, verify=False, timeout=10)
power = r.json()['PowerState']
print(f"\n5. Current power state: {power}")

# 6. Power cycle 
if power == 'On':
    print("   Doing GracefulRestart...")
    r = requests.post(
        f'{base}/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset',
        json={"ResetType": "GracefulRestart"}, auth=auth, verify=False, timeout=15)
    print(f"   Restart: {r.status_code}")
else:
    print("   Powering On...")
    r = requests.post(
        f'{base}/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset',
        json={"ResetType": "On"}, auth=auth, verify=False, timeout=15)
    print(f"   Power On: {r.status_code}")

print("\n✅ Server is rebooting with virtual CD mounted.")
print("   The Azure Local installer should start automatically.")
print("   Monitor via iDRAC console or wait for host 192.168.1.32 to come online.")
