import requests, urllib3
urllib3.disable_warnings()
base = 'https://192.168.10.5'
auth = ('root', 'Tricolor00!')

r = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1/EthernetInterfaces', auth=auth, verify=False, timeout=15)
ifaces = r.json()
print('=== Ethernet Interfaces ===')
for m in ifaces.get('Members', []):
    uri = m['@odata.id']
    d = requests.get(f'{base}{uri}', auth=auth, verify=False, timeout=15).json()
    iid = d.get('Id', '?')
    name = d.get('Name', '?')
    mac = d.get('MACAddress', '?') or d.get('PermanentMACAddress', '?')
    speed = d.get('SpeedMbps', '?')
    status = d.get('Status', {}).get('State', '?')
    link = d.get('LinkStatus', '?')
    print(f"  {iid:35s} MAC={mac:20s} Speed={speed}  State={status}  Link={link}  Name={name}")
