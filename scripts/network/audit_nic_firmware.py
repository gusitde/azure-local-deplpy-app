import requests, urllib3, json
urllib3.disable_warnings()

results = {}

for name, ip in [('adv01', '192.168.10.4'), ('adv02', '192.168.10.5')]:
    print(f"\n{'='*60}")
    print(f"  {name} ({ip}) - NIC Firmware Inventory")
    print(f"{'='*60}")
    s = requests.Session()
    s.auth = ('root', 'Tricolor00!')
    s.verify = False
    
    # Get full firmware inventory
    r = s.get(f'https://{ip}/redfish/v1/UpdateService/FirmwareInventory')
    if not r.ok:
        print(f"  ERROR: {r.status_code}")
        continue
    
    for m in r.json().get('Members', []):
        oid = m.get('@odata.id', '')
        # Only NIC-related firmware
        if 'NIC' not in oid and 'Network' not in oid:
            continue
        r2 = s.get(f'https://{ip}{oid}')
        if r2.ok:
            fw = r2.json()
            fid = fw.get('Id', '')
            fname = fw.get('Name', '')
            fver = fw.get('Version', '')
            if 'Current' in fid or 'Installed' in fid:
                print(f"\n  {fid}")
                print(f"    Name: {fname}")
                print(f"    Version: {fver}")
                # Get Dell-specific info
                dell = fw.get('Oem', {}).get('Dell', {}).get('DellSoftwareInventory', {})
                if dell:
                    print(f"    ComponentID: {dell.get('ComponentID')}")
                    print(f"    ComponentType: {dell.get('ComponentType')}")
                    print(f"    DeviceID: {dell.get('DeviceID')}")
                    print(f"    SubDeviceID: {dell.get('SubDeviceID')}")
                    print(f"    VendorID: {dell.get('VendorID')}")
