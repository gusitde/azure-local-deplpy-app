"""Find and enable disabled NIC BIOS attributes."""
import requests, urllib3, json
urllib3.disable_warnings()

base = 'https://192.168.10.5'
auth = ('root', 'Tricolor00!')

# Get all BIOS attributes
r = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1/Bios', auth=auth, verify=False, timeout=30)
attrs = r.json().get('Attributes', {})

# Find all NIC-related attributes
print("=== NIC-related BIOS attributes ===")
nic_attrs = {}
for k, v in sorted(attrs.items()):
    if any(x in k.lower() for x in ['nic', 'network', 'pxe', 'iscsi', 'slot', 'integrated']):
        nic_attrs[k] = v
        marker = " ❌ DISABLED" if v in ('Disabled', 'DisabledOs') else ""
        print(f"  {k}: {v}{marker}")

# Also check the registry for these to find which are writable
print("\n=== Checking registry for disabled NIC attrs ===")
r2 = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1/Bios/BiosRegistry', auth=auth, verify=False, timeout=60)
registry = {e["AttributeName"]: e for e in r2.json().get("RegistryEntries", {}).get("Attributes", []) if "AttributeName" in e}

disabled_nics = {k: v for k, v in nic_attrs.items() if v in ('Disabled', 'DisabledOs')}
for k, v in disabled_nics.items():
    entry = registry.get(k, {})
    ro = entry.get("ReadOnly", "?")
    vals = [x["ValueName"] for x in entry.get("Value", [])]
    print(f"  {k}: current={v}, ReadOnly={ro}, valid={vals}")
