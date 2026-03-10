"""Check BIOS attributes related to integrated NIC and try to enable it."""
import requests, urllib3, json
urllib3.disable_warnings()

IP = '192.168.10.4'
AUTH = ('root', 'Tricolor00!')

# 1. Get ALL BIOS attributes and filter for NIC-related ones
print("=" * 60)
print("BIOS Attributes related to Integrated NIC")
print("=" * 60)
r = requests.get(f'https://{IP}/redfish/v1/Systems/System.Embedded.1/Bios', auth=AUTH, verify=False, timeout=30)
bios = r.json()
attrs = bios.get('Attributes', {})

nic_keywords = ['nic', 'network', 'integrated', 'iov', 'sriov', 'device', 'slot', 'pci', 'broadcom', 'brcm']
for k, v in sorted(attrs.items()):
    if any(kw in k.lower() for kw in nic_keywords):
        print(f"  {k}: {v}")

# 2. Check BIOS pending attributes
print("\n" + "=" * 60)
print("Pending BIOS Settings")
print("=" * 60)
r2 = requests.get(f'https://{IP}/redfish/v1/Systems/System.Embedded.1/Bios/Settings', auth=AUTH, verify=False, timeout=30)
if r2.ok:
    pending = r2.json().get('Attributes', {})
    if pending:
        for k, v in sorted(pending.items()):
            print(f"  {k}: {v}")
    else:
        print("  No pending changes")

# 3. Check what adv02 has for comparison
print("\n" + "=" * 60)
print("adv02 BIOS Attributes (NIC-related)")
print("=" * 60)
IP2 = '192.168.10.5'
r3 = requests.get(f'https://{IP2}/redfish/v1/Systems/System.Embedded.1/Bios', auth=AUTH, verify=False, timeout=30)
attrs2 = r3.json().get('Attributes', {})
for k, v in sorted(attrs2.items()):
    if any(kw in k.lower() for kw in nic_keywords):
        print(f"  {k}: {v}")

# 4. Show differences
print("\n" + "=" * 60)
print("Differences in NIC-related BIOS attributes")
print("=" * 60)
all_keys = set()
for k in attrs:
    if any(kw in k.lower() for kw in nic_keywords):
        all_keys.add(k)
for k in attrs2:
    if any(kw in k.lower() for kw in nic_keywords):
        all_keys.add(k)

for k in sorted(all_keys):
    v1 = attrs.get(k, 'N/A')
    v2 = attrs2.get(k, 'N/A')
    if v1 != v2:
        print(f"  {k}: adv01={v1} | adv02={v2}")
