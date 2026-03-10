import requests, urllib3, json
urllib3.disable_warnings()
s = requests.Session()
s.auth = ('root', 'Tricolor00!')
s.verify = False
base = 'https://192.168.10.4'

# Check the NetworkAttributesRegistry to find valid attributes
print("=== NIC.Integrated.1-1-1 Registry ===")
r = s.get(f'{base}/redfish/v1/Registries/NetworkAttributesRegistry_NIC.Integrated.1-1-1')
if r.ok:
    d = r.json()
    # Get the registry location
    locs = d.get('Location', [])
    for loc in locs:
        uri = loc.get('Uri', '')
        if uri:
            print(f"  Registry URI: {uri}")
            r2 = s.get(f'{base}{uri}')
            if r2.ok:
                reg = r2.json()
                attrs = reg.get('RegistryEntries', {}).get('Attributes', [])
                print(f"  Total attributes: {len(attrs)}")
                # Find NicMode
                for attr in attrs:
                    aname = attr.get('AttributeName', '')
                    if 'nic' in aname.lower() or 'mode' in aname.lower():
                        print(f"  Attr: {aname} = ReadOnly:{attr.get('ReadOnly')} Type:{attr.get('Type')} CurrentValue:{attr.get('CurrentValue')} DefaultValue:{attr.get('DefaultValue')}")
                        if attr.get('Value'):
                            print(f"    Values: {attr.get('Value')}")

# Also check what the network device function looks like for the integrated NIC
print("\n=== Full NetworkDeviceFunction details for NIC.Integrated.1-1-1 ===")
r3 = s.get(f'{base}/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/NIC.Integrated.1-1-1')
if r3.ok:
    d = r3.json()
    # Print Dell OEM section
    oem = d.get('Oem', {}).get('Dell', {})
    print(f"  OEM Keys: {list(oem.keys())}")
    for key, val in oem.items():
        if isinstance(val, dict):
            oid = val.get('@odata.id', '')
            if oid:
                print(f"\n  {key}: {oid}")
                r4 = s.get(f'{base}{oid}')
                if r4.ok:
                    vd = r4.json()
                    for k, v in vd.items():
                        if not k.startswith('@') and not k.startswith('odata'):
                            print(f"    {k}: {v}")

# Check SCP export properly - look at the raw task output
print("\n=== SCP Export (raw, NIC only) ===")
payload = {
    "ExportFormat": "JSON",
    "ShareParameters": {
        "Target": "NIC"
    }
}
r5 = s.post(f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/Actions/Oem/EID_674_Manager.ExportSystemConfiguration', json=payload)
if r5.status_code == 202:
    task_uri = r5.headers.get('Location', '')
    import time
    for i in range(30):
        time.sleep(3)
        rj = s.get(f'{base}{task_uri}')
        if rj.ok:
            jd = rj.json()
            state = jd.get('TaskState', '')
            if state == 'Completed':
                # Get full response
                print(json.dumps(jd, indent=2)[:3000])
                break
