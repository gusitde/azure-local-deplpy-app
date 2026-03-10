"""Deep dive into adv01 Broadcom NIC device functions and try Dell-specific attribute paths."""
import requests
import json
import urllib3
urllib3.disable_warnings()

BASE = "https://192.168.10.4"
AUTH = ("root", "Tricolor00!")

def get(path):
    r = requests.get(f"{BASE}{path}", auth=AUTH, verify=False, timeout=15)
    return r.json()

# Full dump of NIC device functions
for func_id in ["NIC.Integrated.1-1-1", "NIC.Integrated.1-2-1"]:
    print("=" * 60)
    print(f"Full dump: {func_id}")
    print("=" * 60)
    try:
        d = get(f"/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{func_id}")
        print(json.dumps(d, indent=2))
    except Exception as e:
        print(f"Error: {e}")

# Try Dell-specific attributes via System path
print("\n" + "=" * 60)
print("All Dell Attribute Registries")
print("=" * 60)
try:
    # List all Dell attribute groups
    r = get("/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellAttributes")
    for m in r.get("Members", []):
        oid = m["@odata.id"]
        if "NIC" in oid:
            print(f"  {oid}")
except Exception as e:
    print(f"  Error: {e}")

# Try to get all BIOS attributes related to NIC mode
print("\n" + "=" * 60)
print("All BIOS attrs with 'Integrated' or 'Nic' or 'Slot'")
print("=" * 60)
try:
    bios = get("/redfish/v1/Systems/System.Embedded.1/Bios")
    attrs = bios.get("Attributes", {})
    for key, val in sorted(attrs.items()):
        kl = key.lower()
        if "integrated" in kl or "slot" in kl:
            print(f"  {key} = {val}")
except Exception as e:
    print(f"  Error: {e}")

# Check port details  
print("\n" + "=" * 60)
print("Broadcom Port Details")
print("=" * 60)
for port_id in ["NIC.Integrated.1-1", "NIC.Integrated.1-2"]:
    try:
        p = get(f"/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkPorts/{port_id}")
        print(f"\n  Port: {port_id}")
        print(f"    LinkStatus: {p.get('LinkStatus')}")
        print(f"    AssociatedNetworkAddresses: {p.get('AssociatedNetworkAddresses')}")
        print(f"    CurrentLinkSpeedMbps: {p.get('CurrentLinkSpeedMbps')}")
        print(f"    ActiveLinkTechnology: {p.get('ActiveLinkTechnology')}")
        oem = p.get("Oem", {}).get("Dell", {})
        if oem:
            dell_port = oem.get("DellNetworkTransceiver", {})
            print(f"    Dell Transceiver: {json.dumps(dell_port, indent=6)}")
    except Exception as e:
        print(f"  Error for {port_id}: {e}")
