import requests, urllib3
urllib3.disable_warnings()
s = requests.Session()
s.auth = ('root', 'Tricolor00!')
s.verify = False

# Check ALL firmware on adv01 for Broadcom/Integrated
print("=== adv01 Broadcom firmware inventory ===")
r = s.get('https://192.168.10.4/redfish/v1/UpdateService/FirmwareInventory')
for m in r.json().get('Members', []):
    oid = m['@odata.id']
    if 'Integrated' in oid:
        r2 = s.get(f'https://192.168.10.4{oid}')
        if r2.ok:
            fw = r2.json()
            print(f"  {fw.get('Id')}: {fw.get('Name')} v{fw.get('Version')}")

# DellNIC FamilyVersion
print("\n=== adv01 Broadcom FamilyVersion ===")
for fqdd in ['NIC.Integrated.1-1-1', 'NIC.Integrated.1-2-1']:
    r3 = s.get(f'https://192.168.10.4/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{fqdd}/Oem/Dell/DellNIC/{fqdd}')
    if r3.ok:
        d = r3.json()
        print(f"  {fqdd}: FamilyVersion={d.get('FamilyVersion')} ProductName={d.get('ProductName')}")

print("\n=== adv02 Broadcom FamilyVersion ===")
s2 = requests.Session()
s2.auth = ('root', 'Tricolor00!')
s2.verify = False
for fqdd in ['NIC.Integrated.1-1-1', 'NIC.Integrated.1-2-1']:
    r4 = s2.get(f'https://192.168.10.5/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{fqdd}/Oem/Dell/DellNIC/{fqdd}')
    if r4.ok:
        d = r4.json()
        print(f"  {fqdd}: FamilyVersion={d.get('FamilyVersion')} ProductName={d.get('ProductName')}")
