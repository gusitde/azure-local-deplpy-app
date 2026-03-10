import requests, urllib3
urllib3.disable_warnings()
r = requests.get('https://192.168.10.5/redfish/v1/Systems/System.Embedded.1',
                 auth=('root', 'Tricolor00!'), verify=False, timeout=10)
data = r.json()
print(f"PowerState: {data['PowerState']}")
# Check virtual media
r2 = requests.get('https://192.168.10.5/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD',
                   auth=('root', 'Tricolor00!'), verify=False, timeout=10)
vm = r2.json()
print(f"VirtualMedia CD: Inserted={vm.get('Inserted')}, Image={vm.get('Image')}")
