import requests, urllib3, json
urllib3.disable_warnings()
s = requests.Session()
s.auth = ('root', 'Tricolor00!')
s.verify = False
base = 'https://192.168.10.4'

# Get recent LC log entries (all of them, last 30)
print("=== Recent LC Log Entries ===")
r = s.get(f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Lclog/Entries?$top=30')
j = r.json()
for entry in j.get('Members', []):
    msg = entry.get('Message', '')
    mid = entry.get('MessageId', '')
    created = entry.get('Created', '')
    sev = entry.get('Severity', '')
    print(f"{created} [{sev}] {mid}")
    print(f"  {msg}")
    print()

# Check NIC attributes - find correct path first
print("\n=== Checking NIC Integrated Adapter ===")
r2 = s.get(f'{base}/redfish/v1/Systems/System.Embedded.1/NetworkAdapters')
if r2.ok:
    for m in r2.json().get('Members', []):
        print(f"  Adapter: {m.get('@odata.id')}")
else:
    print(f"  NetworkAdapters: {r2.status_code}")
    # Try chassis path
    r3 = s.get(f'{base}/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters')
    if r3.ok:
        for m in r3.json().get('Members', []):
            print(f"  Adapter (Chassis): {m.get('@odata.id')}")

# Check via iDRAC attributes  
print("\n=== Checking via iDRAC NIC Attributes (Dell OEM) ===")
for fqdd in ['NIC.Integrated.1-1-1', 'NIC.Integrated.1-2-1']:
    url = f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellAttributes/{fqdd}'
    r4 = s.get(url)
    if r4.ok:
        attrs = r4.json().get('Attributes', {})
        nicmode = attrs.get('NicMode', 'NOT FOUND')
        print(f"  {fqdd}: NicMode={nicmode}")
    else:
        print(f"  {fqdd}: {r4.status_code}")

# Also check the raw system network interface
print("\n=== System EthernetInterfaces ===")
r5 = s.get(f'{base}/redfish/v1/Systems/System.Embedded.1/EthernetInterfaces')
if r5.ok:
    for m in r5.json().get('Members', []):
        oid = m.get('@odata.id', '')
        if 'Integrated' in oid or 'NIC' in oid:
            r6 = s.get(f"{base}{oid}")
            if r6.ok:
                d = r6.json()
                print(f"  {d.get('Id')}: Status={d.get('Status',{}).get('State')} Speed={d.get('SpeedMbps')} MAC={d.get('MACAddress')}")
