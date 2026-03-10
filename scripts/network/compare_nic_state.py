import paramiko
import requests, urllib3, json
urllib3.disable_warnings()

# Check adv02 iDRAC for comparison
print("=== Checking adv02 iDRAC (192.168.10.5) ===")
s = requests.Session()
s.auth = ('root', 'Tricolor00!')
s.verify = False

# Check DellNIC for integrated
for fqdd in ['NIC.Integrated.1-1-1', 'NIC.Integrated.1-2-1']:
    url = f'https://192.168.10.5/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{fqdd}/Oem/Dell/DellNIC/{fqdd}'
    r = s.get(url)
    if r.ok:
        d = r.json()
        print(f"\n{fqdd}:")
        print(f"  NicMode: {d.get('NicMode')}")
        print(f"  PCIDeviceID: {d.get('PCIDeviceID')}")
        print(f"  PCIVendorID: {d.get('PCIVendorID')}")
        print(f"  ProductName: {d.get('ProductName')}")
        print(f"  VendorName: {d.get('VendorName')}")
    else:
        print(f"\n{fqdd}: {r.status_code}")

# Check racadm on adv02 for NIC config
print("\n\n=== adv02 racadm NIC config ===")
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.10.5', username='root', password='Tricolor00!', timeout=30)

commands = [
    'racadm get NIC.NICConfig',
    'racadm hwinventory NIC.Integrated.1-1-1',
]

for cmd in commands:
    print(f"\n> {cmd}")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode()
    err = stderr.read().decode()
    if out:
        lines = out.strip().split('\n')
        # Show only relevant lines for hwinventory
        for line in lines:
            if any(k in line.lower() for k in ['nicmode', 'nic mode', 'pcidevice', 'productname', 'key=', 'device description']):
                print(f"  {line.strip()}")
            elif len(lines) < 10:
                print(f"  {line.strip()}")
    if err:
        print(f"  ERR: {err.strip()}")

client.close()

# Also check if adv02 has firmware reset option
print("\n\n=== Checking adv01 iDRAC for NIC firmware reset options ===")
s2 = requests.Session()
s2.auth = ('root', 'Tricolor00!')
s2.verify = False

# Check NetworkAdapter actions
r2 = s2.get('https://192.168.10.4/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1')
if r2.ok:
    d = r2.json()
    print(f"Adapter: {d.get('Manufacturer')} {d.get('Model')}")
    actions = d.get('Actions', {})
    if actions:
        print(f"Actions: {json.dumps(actions, indent=2)}")
    oem_actions = d.get('Oem', {}).get('Dell', {})
    if oem_actions:
        for k, v in oem_actions.items():
            if 'Action' in k or 'action' in str(v):
                print(f"OEM: {k} = {v}")
