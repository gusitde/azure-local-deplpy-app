import requests, urllib3, json, time
urllib3.disable_warnings()
s = requests.Session()
s.auth = ('root', 'Tricolor00!')
s.verify = False
base = 'https://192.168.10.4'

# Try PATCH on DellNetworkAttributes Settings endpoint
for fqdd in ['NIC.Integrated.1-1-1', 'NIC.Integrated.1-2-1']:
    print(f"\n=== Setting NicMode=Enabled for {fqdd} ===")
    
    # Try the Settings endpoint
    settings_url = f'{base}/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{fqdd}/Oem/Dell/DellNetworkAttributes/{fqdd}/Settings'
    
    payload = {
        "Attributes": {
            "NicMode": "Enabled"
        }
    }
    
    # First check if the Settings endpoint exists
    r = s.get(settings_url)
    print(f"  GET Settings: {r.status_code}")
    if r.ok:
        print(f"  Current settings: {json.dumps(r.json().get('Attributes', {}), indent=2)[:500]}")
    
    # Try PATCH
    r2 = s.patch(settings_url, json=payload)
    print(f"  PATCH Settings: {r2.status_code}")
    if r2.status_code in [200, 202]:
        print(f"  Response: {r2.json()}")
    else:
        print(f"  Error: {r2.text[:500]}")
        
        # Try alternative path without Settings suffix  
        alt_url = f'{base}/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{fqdd}/Oem/Dell/DellNetworkAttributes/{fqdd}'
        r3 = s.patch(alt_url, json=payload)
        print(f"  PATCH Alt: {r3.status_code}")
        if r3.status_code in [200, 202]:
            print(f"  Response: {r3.json()}")
        else:
            print(f"  Alt Error: {r3.text[:500]}")

# Also try the Registries to find the correct path
print("\n=== Searching for NIC attribute registries ===")
r4 = s.get(f'{base}/redfish/v1/Registries')
if r4.ok:
    for m in r4.json().get('Members', []):
        oid = m.get('@odata.id', '')
        if 'NIC' in oid.upper() or 'Network' in oid:
            print(f"  Registry: {oid}")

# Try using Dell OEM job creation  
print("\n=== Trying SetAttribute via Dell OEM ===")
for fqdd in ['NIC.Integrated.1-1-1', 'NIC.Integrated.1-2-1']:
    # Try the Dell OEM SetAttributes action
    action_url = f'{base}/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{fqdd}/Oem/Dell/DellNetworkAttributes/{fqdd}/Settings'
    
    payload = {
        "@Redfish.SettingsApplyTime": {
            "@odata.type": "#Settings.v1_3_5.PreferredApplyTime",
            "ApplyTime": "OnReset"
        },
        "Attributes": {
            "NicMode": "Enabled"
        }
    }
    r5 = s.patch(action_url, json=payload)
    print(f"  {fqdd} PATCH with ApplyTime: {r5.status_code}")
    if r5.ok:
        print(f"  Response: {json.dumps(r5.json(), indent=2)[:500]}")
    else:
        print(f"  Error: {r5.text[:300]}")
