"""Try multiple methods to mount ISO on iDRAC and boot."""
import requests, urllib3, time, socket
urllib3.disable_warnings()

base = 'https://192.168.10.5'
auth = ('root', 'Tricolor00!')
local_ip = '192.168.10.201'
iso_filename = "AzureLocal24H2.26100.1742.LCM.12.2602.0.3018.x64.en-us.iso"

# Eject first
print("Ejecting existing virtual media...")
try:
    requests.post(f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.EjectMedia',
                  json={}, auth=auth, verify=False, timeout=15)
except: pass
time.sleep(2)

# Method 1: CIFS with Windows credentials (special chars in password)
print("\n=== Method 1: CIFS with gus account ===")
cifs_url = f"//{local_ip}/iso-share/{iso_filename}"
pw = 'Tricolor00!@#$%^&*('
payload = {"Image": cifs_url, "Inserted": True, "WriteProtected": True,
           "UserName": "gus", "Password": pw}
r = requests.post(
    f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.InsertMedia',
    json=payload, auth=auth, verify=False, timeout=30)
print(f"  Status: {r.status_code}")
if r.status_code in (200, 204):
    print("  ✅ CIFS mount successful!")
else:
    print(f"  Failed: {r.text[:200]}")

    # Method 2: Try with MACHINE\\gus format
    print("\n=== Method 2: CIFS with HOSTNAME\\gus ===")
    import platform
    hostname = platform.node()
    payload["UserName"] = f"{hostname}\\gus"
    try:
        requests.post(f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.EjectMedia',
                      json={}, auth=auth, verify=False, timeout=15)
    except: pass
    time.sleep(2)
    r = requests.post(
        f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.InsertMedia',
        json=payload, auth=auth, verify=False, timeout=30)
    print(f"  Status: {r.status_code}")
    if r.status_code in (200, 204):
        print("  ✅ CIFS mount successful!")
    else:
        print(f"  Failed: {r.text[:200]}")

        # Method 3: Try HTTP approach with proper handling
        print("\n=== Method 3: HTTP with proper server ===")
        import threading
        from http.server import HTTPServer
        from functools import partial
        from RangeHTTPServer import RangeRequestHandler  # type: ignore
        
        # If RangeHTTPServer not available, use basic handler
        try:
            handler = partial(RangeRequestHandler, directory=r"C:\Users\gus\.azure-local-deploy")
        except:
            from http.server import SimpleHTTPRequestHandler
            handler = partial(SimpleHTTPRequestHandler, directory=r"C:\Users\gus\.azure-local-deploy")
        
        httpd = HTTPServer((local_ip, 8080), handler)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        http_url = f"http://{local_ip}:8080/{iso_filename}"
        print(f"  HTTP URL: {http_url}")
        
        try:
            requests.post(f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.EjectMedia',
                          json={}, auth=auth, verify=False, timeout=15)
        except: pass
        time.sleep(2)
        
        r = requests.post(
            f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.InsertMedia',
            json={"Image": http_url, "Inserted": True, "WriteProtected": True},
            auth=auth, verify=False, timeout=30)
        print(f"  Status: {r.status_code}")
        if r.status_code in (200, 204):
            print("  ✅ HTTP mount successful!")
        else:
            print(f"  Failed: {r.text[:200]}")
            httpd.shutdown()

# Verify
time.sleep(3)
r = requests.get(f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD',
                 auth=auth, verify=False, timeout=10)
vm = r.json()
print(f"\nVirtual Media CD: Inserted={vm.get('Inserted')}, Image={vm.get('Image')}, ConnectedVia={vm.get('ConnectedVia')}")

if vm.get('Inserted'):
    print("\n✅ ISO mounted! Setting boot override and restarting...")
    # Set one-time boot
    r = requests.patch(f'{base}/redfish/v1/Systems/System.Embedded.1',
        json={"Boot": {"BootSourceOverrideTarget": "Cd", "BootSourceOverrideEnabled": "Once"}},
        auth=auth, verify=False, timeout=15)
    print(f"  Boot override: {r.status_code}")
    
    # Restart
    r = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1', auth=auth, verify=False, timeout=10)
    power = r.json()['PowerState']
    if power == 'On':
        r = requests.post(f'{base}/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset',
            json={"ResetType": "GracefulRestart"}, auth=auth, verify=False, timeout=15)
        print(f"  Restart: {r.status_code}")
    else:
        r = requests.post(f'{base}/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset',
            json={"ResetType": "On"}, auth=auth, verify=False, timeout=15)
        print(f"  Power On: {r.status_code}")
    print("\n🚀 Server booting from virtual CD!")
else:
    print("\n❌ ISO not mounted. Check iDRAC virtual console for diagnostics.")
