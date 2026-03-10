import requests, urllib3, json
urllib3.disable_warnings()
base = 'https://192.168.10.5'
auth = ('root', 'Tricolor00!')

r = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1/EthernetInterfaces', auth=auth, verify=False, timeout=15)
ifaces = r.json()
for m in ifaces.get('Members', []):
    uri = m['@odata.id']
    d = requests.get(f'{base}{uri}', auth=auth, verify=False, timeout=15).json()
    iid = d.get('Id', '?')
    print(f"\n=== {iid} ===")
    print(f"  Name: {d.get('Name')}")
    print(f"  MACAddress: {d.get('MACAddress')}")
    print(f"  PermanentMACAddress: {d.get('PermanentMACAddress')}")
    print(f"  SpeedMbps: {d.get('SpeedMbps')}")
    print(f"  LinkStatus: {d.get('LinkStatus')}")
    print(f"  Status: {d.get('Status')}")
    print(f"  FQDD: {d.get('FQDD', d.get('Id'))}")
    # Check for Oem/Dell data
    oem = d.get('Oem', {}).get('Dell', {})
    if oem:
        for k, v in oem.items():
            if isinstance(v, dict):
                print(f"  OEM.{k}: {json.dumps(v, indent=4)[:500]}")
            else:
                print(f"  OEM.{k}: {v}")

# Also check NetworkAdapters 
print("\n\n=== NetworkAdapters Collection ===")
r2 = requests.get(f'{base}/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters', auth=auth, verify=False, timeout=15)
if r2.ok:
    for m in r2.json().get('Members', []):
        uri = m['@odata.id']
        d = requests.get(f'{base}{uri}', auth=auth, verify=False, timeout=15).json()
        print(f"\n--- {d.get('Id')} ---")
        print(f"  Manufacturer: {d.get('Manufacturer')}")
        print(f"  Model: {d.get('Model')}")
        ports = d.get('Ports', {}).get('@odata.id', '')
        if ports:
            pr = requests.get(f'{base}{ports}', auth=auth, verify=False, timeout=15).json()
            for pm in pr.get('Members', []):
                pd = requests.get(f'{base}{pm["@odata.id"]}', auth=auth, verify=False, timeout=15).json()
                addrs = pd.get('Ethernet', {}).get('AssociatedMACAddresses', [])
                perma = pd.get('Ethernet', {}).get('PermanentMACAddress', '')
                print(f"    Port {pd.get('Id')}: PermanentMAC={perma}  AssociatedMACs={addrs}  LinkStatus={pd.get('LinkStatus')}")
