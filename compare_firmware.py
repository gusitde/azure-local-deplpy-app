"""Compare firmware inventory between adv01 (iDRAC .4) and adv02 (iDRAC .5)."""
import requests
import json
import urllib3
urllib3.disable_warnings()

AUTH = ("root", "Tricolor00!")

def get_firmware(base):
    r = requests.get(f"{base}/redfish/v1/UpdateService/FirmwareInventory?$expand=*($levels=1)",
                     auth=AUTH, verify=False, timeout=30)
    data = r.json()
    inventory = {}
    for m in data.get("Members", []):
        name = m.get("Name", "Unknown")
        version = m.get("Version", "N/A")
        comp_id = m.get("Id", "")
        inventory[comp_id] = {"Name": name, "Version": version}
    return inventory

print("Fetching adv01 firmware (192.168.10.4)...")
adv01_fw = get_firmware("https://192.168.10.4")

print("Fetching adv02 firmware (192.168.10.5)...")
adv02_fw = get_firmware("https://192.168.10.5")

# Build comparison by component name
adv01_by_name = {}
for cid, info in adv01_fw.items():
    key = info["Name"]
    if key not in adv01_by_name:
        adv01_by_name[key] = []
    adv01_by_name[key].append(info["Version"])

adv02_by_name = {}
for cid, info in adv02_fw.items():
    key = info["Name"]
    if key not in adv02_by_name:
        adv02_by_name[key] = []
    adv02_by_name[key].append(info["Version"])

all_names = sorted(set(list(adv01_by_name.keys()) + list(adv02_by_name.keys())))

print(f"\n{'Component':<55} {'adv01':<20} {'adv02':<20} {'Match'}")
print("=" * 105)

mismatches = []
for name in all_names:
    v1 = ", ".join(adv01_by_name.get(name, ["N/A"]))
    v2 = ", ".join(adv02_by_name.get(name, ["N/A"]))
    match = "✓" if v1 == v2 else "✗ MISMATCH"
    if v1 != v2:
        mismatches.append((name, v1, v2))
    print(f"{name:<55} {v1:<20} {v2:<20} {match}")

if mismatches:
    print(f"\n\n{'='*60}")
    print(f"  {len(mismatches)} MISMATCHES FOUND")
    print(f"{'='*60}")
    for name, v1, v2 in mismatches:
        print(f"  {name}")
        print(f"    adv01: {v1}")
        print(f"    adv02: {v2}")
else:
    print("\n  All firmware versions match!")
