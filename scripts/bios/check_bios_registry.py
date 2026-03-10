import requests, urllib3, json
urllib3.disable_warnings()
base = 'https://192.168.10.5'
auth = ('root', 'Tricolor00!')

# Check the Bios registry for TpmSecurity allowed values
print("Checking BIOS attribute registry for TpmSecurity...")
r = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1/Bios', auth=auth, verify=False, timeout=30)
bios = r.json()
attrs = bios.get('Attributes', {})

# Show TpmSecurity current value
print(f"TpmSecurity current: {attrs.get('TpmSecurity')}")

# Check the registry
print("\nChecking Attribute Registry...")
reg_url = bios.get('@Redfish.Settings', {}).get('SettingsObject', {}).get('@odata.id', '')
print(f"Settings URI: {reg_url}")

# Check registries
r2 = requests.get(f'{base}/redfish/v1/Registries', auth=auth, verify=False, timeout=30)
registries = r2.json()
for m in registries.get('Members', []):
    uri = m.get('@odata.id', '')
    if 'Bios' in uri or 'bios' in uri:
        print(f"\nFound BIOS registry: {uri}")
        r3 = requests.get(f'{base}{uri}', auth=auth, verify=False, timeout=30)
        reg = r3.json()
        loc = reg.get('Location', [])
        if loc:
            reg_file = loc[0].get('Uri', '')
            print(f"Registry file: {reg_file}")
            r4 = requests.get(f'{base}{reg_file}', auth=auth, verify=False, timeout=60)
            reg_data = r4.json()
            reg_attrs = reg_data.get('RegistryEntries', {}).get('Attributes', [])
            for a in reg_attrs:
                if a.get('AttributeName') == 'TpmSecurity':
                    print(f"\nTpmSecurity registry entry:")
                    print(json.dumps(a, indent=2))
                    break
            # Also check ProcCStates and RedundantOsBoot
            for name in ['ProcCStates', 'RedundantOsBoot']:
                for a in reg_attrs:
                    if a.get('AttributeName') == name:
                        print(f"\n{name} registry entry:")
                        print(json.dumps(a, indent=2))
                        break
