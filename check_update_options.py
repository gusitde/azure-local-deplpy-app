import requests, urllib3, json
urllib3.disable_warnings()

s = requests.Session()
s.auth = ('root', 'Tricolor00!')
s.verify = False
base = 'https://192.168.10.4'

# Check if iDRAC can do automatic update from Dell repository
print("=== Dell Repository Update Options ===")

# Check Dell OEM actions on UpdateService
r = s.get(f'{base}/redfish/v1/UpdateService')
if r.ok:
    us = r.json()
    oem = us.get('Oem', {}).get('Dell', {})
    for k, v in oem.items():
        if isinstance(v, dict) and '@odata.id' in v:
            print(f"  {k}: {v['@odata.id']}")
    actions = us.get('Actions', {})
    for k, v in actions.items():
        print(f"  Action: {k}")
        if isinstance(v, dict):
            for ak, av in v.items():
                print(f"    {ak}: {av}")

# Check Dell Software Installation Service
print("\n=== Dell Software Installation Service ===")
r2 = s.get(f'{base}/redfish/v1/Dell/Systems/System.Embedded.1/DellSoftwareInstallationService')
if r2.ok:
    svc = r2.json()
    actions = svc.get('Actions', {})
    for k, v in actions.items():
        print(f"  {k}:")
        if isinstance(v, dict):
            for ak, av in v.items():
                if not ak.startswith('@'):
                    print(f"    {ak}: {av}")

# Check existing firmware inventory for BIOS on adv02 (to see if we can reference the file)
print("\n=== adv02 BIOS firmware details ===")
s2 = requests.Session()
s2.auth = ('root', 'Tricolor00!')
s2.verify = False
r3 = s2.get('https://192.168.10.5/redfish/v1/UpdateService/FirmwareInventory/Current-159-2.24.0__BIOS.Setup.1-1')
if r3.ok:
    fw = r3.json()
    for k, v in fw.items():
        if not k.startswith('@') and not k.startswith('odata'):
            print(f"  {k}: {v}")

# Try Dell catalog URL for R640 BIOS
print("\n=== Trying Dell catalog ===")
# Dell catalog.xml.gz contains all update references
catalog_url = "https://downloads.dell.com/catalog/CatalogPC.cab"
# For servers it's usually: https://downloads.dell.com/catalog/Catalog.xml.gz
# Or the server specific: https://downloads.dell.com/catalog/Catalog.gz

# Try to use iDRAC SimpleUpdate with Dell's HTTP catalog
# First, let's try to use the Dell Repository Manager approach
# Check if we can get the Dell update catalog entry
print("\n=== Check Dell OEM Repository Update ===")
r4 = s.get(f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService')
if r4.ok:
    lcs = r4.json()
    actions = lcs.get('Actions', {})
    for k, v in actions.items():
        if 'Update' in k or 'Install' in k or 'Catalog' in k:
            print(f"  {k}: {v}")
