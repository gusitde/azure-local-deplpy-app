import requests, urllib3
urllib3.disable_warnings()
base = 'https://192.168.10.5'
auth = ('root', 'Tricolor00!')
r = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1/Bios', auth=auth, verify=False, timeout=10)
a = r.json().get('Attributes', {})
print(f"Slot1: {a.get('Slot1')}")
print(f"Slot2: {a.get('Slot2')}")
print(f"Slot3: {a.get('Slot3')}")
r2 = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1', auth=auth, verify=False, timeout=10)
print(f"PowerState: {r2.json()['PowerState']}")
