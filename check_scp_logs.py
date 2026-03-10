import requests, urllib3, json
urllib3.disable_warnings()
s = requests.Session()
s.auth = ('root', 'Tricolor00!')
s.verify = False
base = 'https://192.168.10.4'

# Get recent LC log entries
r = s.get(f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Lclog/Entries?$top=30')
j = r.json()
for entry in j.get('Members', []):
    msg = entry.get('Message', '')
    mid = entry.get('MessageId', '')
    created = entry.get('Created', '')
    # Show SCP-related or NIC-related entries
    if any(kw in msg.upper() for kw in ['SCP', 'IMPORT', 'PROFILE', 'NIC', 'CONFIG', 'UNABLE', 'FAIL', 'ERROR', 'APPLY']):
        print(f"{created} | {mid}")
        print(f"  {msg}")
        print()

print("--- Checking NIC attributes ---")
for fqdd in ['NIC.Integrated.1-1-1', 'NIC.Integrated.1-2-1']:
    r2 = s.get(f'{base}/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{fqdd}/Oem/Dell/DellNetworkAttributes/{fqdd}')
    if r2.ok:
        attrs = r2.json().get('Attributes', {})
        nicmode = attrs.get('NicMode', 'NOT FOUND')
        print(f"{fqdd}: NicMode={nicmode}")
    else:
        # Try alternative path
        r3 = s.get(f'{base}/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{fqdd}/Oem/Dell/DellNetworkAttributes/{fqdd}')
        if r3.ok:
            attrs = r3.json().get('Attributes', {})
            nicmode = attrs.get('NicMode', 'NOT FOUND')
            print(f"{fqdd}: NicMode={nicmode}")
        else:
            print(f"{fqdd}: Could not query ({r2.status_code})")
