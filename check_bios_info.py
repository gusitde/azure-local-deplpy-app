import requests, urllib3, json
urllib3.disable_warnings()

# Get BIOS firmware details from both nodes
for name, ip in [('adv01', '192.168.10.4'), ('adv02', '192.168.10.5')]:
    print(f"\n=== {name} ({ip}) ===")
    s = requests.Session()
    s.auth = ('root', 'Tricolor00!')
    s.verify = False
    
    # Get BIOS version info
    r = s.get(f'https://{ip}/redfish/v1/Systems/System.Embedded.1')
    if r.ok:
        d = r.json()
        print(f"  BiosVersion: {d.get('BiosVersion')}")
        print(f"  Model: {d.get('Model')}")
        print(f"  SKU: {d.get('SKU')}")
        print(f"  ServiceTag: {d.get('SKU')}")
    
    # Get firmware inventory for BIOS
    r2 = s.get(f'https://{ip}/redfish/v1/UpdateService/FirmwareInventory')
    if r2.ok:
        for m in r2.json().get('Members', []):
            oid = m.get('@odata.id', '')
            if 'BIOS' in oid.upper():
                r3 = s.get(f'https://{ip}{oid}')
                if r3.ok:
                    fw = r3.json()
                    print(f"  BIOS Firmware:")
                    print(f"    Id: {fw.get('Id')}")
                    print(f"    Name: {fw.get('Name')}")
                    print(f"    Version: {fw.get('Version')}")
                    print(f"    Updateable: {fw.get('Updateable')}")
                    print(f"    ReleaseDate: {fw.get('ReleaseDate')}")
    
    # Get iDRAC firmware version too
    r4 = s.get(f'https://{ip}/redfish/v1/Managers/iDRAC.Embedded.1')
    if r4.ok:
        d = r4.json()
        print(f"  iDRAC Version: {d.get('FirmwareVersion')}")
    
    # Check DellSoftwareInstallationService for update methods
    r5 = s.get(f'https://{ip}/redfish/v1/UpdateService')
    if r5.ok:
        us = r5.json()
        print(f"  UpdateService:")
        print(f"    HttpPushUri: {us.get('HttpPushUri')}")
        acts = us.get('Actions', {})
        for k, v in acts.items():
            if 'SimpleUpdate' in k:
                print(f"    SimpleUpdate target: {v.get('target')}")
                allowable = v.get('TransferProtocol@Redfish.AllowableValues', [])
                print(f"    TransferProtocols: {allowable}")
