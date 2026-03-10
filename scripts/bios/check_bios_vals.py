import requests, urllib3, json
urllib3.disable_warnings()
base = 'https://192.168.10.5'
auth = ('root', 'Tricolor00!')

r = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1/Bios', auth=auth, verify=False, timeout=30)
attrs = r.json().get('Attributes', {})

# Check key attributes
for name in ['SysProfile', 'ProcCStates', 'RedundantOsBoot', 'TpmSecurity', 
             'SecureBoot', 'BootMode', 'ProcVirtualization', 'ProcVtd',
             'TpmActivation', 'Tpm2Hierarchy', 'WorkloadProfile']:
    print(f"{name}: {attrs.get(name, 'NOT PRESENT')}")

# Check registry for SysProfile and RedundantOsBoot parent
r2 = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1/Bios/BiosRegistry', auth=auth, verify=False, timeout=60)
reg_attrs = r2.json().get('RegistryEntries', {}).get('Attributes', [])
for a in reg_attrs:
    if a.get('AttributeName') in ['SysProfile', 'RedundantOsBoot', 'RedundantOsState']:
        print(f"\n{a['AttributeName']}:")
        print(f"  ReadOnly: {a.get('ReadOnly')}")
        print(f"  Values: {[v['ValueName'] for v in a.get('Value', [])]}")
        if a.get('Dependency'):
            print(f"  Dependency: {json.dumps(a['Dependency'], indent=4)}")
