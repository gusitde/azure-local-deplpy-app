import requests, urllib3, json
urllib3.disable_warnings()
s = requests.Session()
s.auth = ('root', 'Tricolor00!')
s.verify = False
base = 'https://192.168.10.4'

# Check the NetworkDeviceFunctions for the integrated NIC
print("=== NIC.Integrated.1 NetworkDeviceFunctions ===")
r = s.get(f'{base}/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions')
if r.ok:
    for m in r.json().get('Members', []):
        oid = m.get('@odata.id', '')
        print(f"  Function: {oid}")
        r2 = s.get(f'{base}{oid}')
        if r2.ok:
            d = r2.json()
            print(f"    NetDevFuncType: {d.get('NetDevFuncType')}")
            print(f"    DeviceEnabled: {d.get('DeviceEnabled')}")
            # Check Oem Dell attributes
            oem = d.get('Oem', {}).get('Dell', {})
            dell_attrs = oem.get('DellNetworkAttributes', {})
            if dell_attrs:
                attrs_link = dell_attrs.get('@odata.id', '')
                if attrs_link:
                    r3 = s.get(f'{base}{attrs_link}')
                    if r3.ok:
                        attrs = r3.json().get('Attributes', {})
                        print(f"    NicMode: {attrs.get('NicMode', 'N/A')}")
                    else:
                        print(f"    Attrs link {r3.status_code}")
            # Check DellNIC
            dell_nic = oem.get('DellNIC', {})
            if dell_nic:
                nic_link = dell_nic.get('@odata.id', '')
                if nic_link:
                    r4 = s.get(f'{base}{nic_link}')
                    if r4.ok:
                        nd = r4.json()
                        print(f"    NicMode: {nd.get('NicMode', 'N/A')}")
                        print(f"    DeviceName: {nd.get('DeviceName', 'N/A')}")
                        print(f"    PCIDeviceID: {nd.get('PCIDeviceID', 'N/A')}")
            print()

# Also try getting pending attributes
print("\n=== Pending NIC Attributes ===")
for fqdd in ['NIC.Integrated.1-1-1', 'NIC.Integrated.1-2-1']:
    # Try settings path
    url = f'{base}/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{fqdd}/Oem/Dell/DellNetworkAttributes/{fqdd}'
    r5 = s.get(url) 
    if r5.ok:
        attrs = r5.json().get('Attributes', {})
        print(f"{fqdd}: NicMode={attrs.get('NicMode', 'N/A')}")
    else:
        print(f"{fqdd}: {r5.status_code} at settings path")

# Check SCP export to see current NicMode
print("\n=== SCP Export (NIC target) ===")
payload = {
    "ExportFormat": "JSON",
    "ShareParameters": {
        "Target": "NIC"
    }
}
r6 = s.post(f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/Actions/Oem/EID_674_Manager.ExportSystemConfiguration', json=payload)
if r6.status_code == 202:
    task_uri = r6.headers.get('Location', '')
    print(f"Export job created: {task_uri}")
    import time
    for i in range(30):
        time.sleep(5)
        rj = s.get(f'{base}{task_uri}')
        if rj.ok:
            jd = rj.json()
            state = jd.get('TaskState', '')
            print(f"  [{i*5}s] State={state}")
            if state == 'Completed':
                # Get the output
                resp = jd.get('Oem', {}).get('Dell', {}).get('ServerConfigProfile', {})
                if not resp:
                    # Try Messages
                    msgs = jd.get('Messages', [])
                    for msg in msgs:
                        if 'ServerConfigProfile' in str(msg):
                            resp = msg
                # Look for NicMode in the config
                config_str = json.dumps(resp)
                if 'NicMode' in config_str:
                    # Parse and find NicMode entries
                    components = resp.get('Components', [])
                    for comp in components:
                        fqdd = comp.get('FQDD', '')
                        if 'Integrated' in fqdd:
                            attrs = comp.get('Attributes', [])
                            for attr in attrs:
                                if attr.get('Name') == 'NicMode':
                                    print(f"  {fqdd}: NicMode={attr.get('Value')}")
                else:
                    print(f"  NicMode not found in export. Keys: {list(resp.keys()) if isinstance(resp, dict) else type(resp)}")
                break
        else:
            print(f"  [{i*5}s] HTTP {rj.status_code}")
else:
    print(f"Export failed: {r6.status_code} {r6.text[:500]}")
