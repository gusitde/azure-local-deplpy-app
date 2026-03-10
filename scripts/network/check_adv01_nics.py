"""Check adv01 iDRAC for all NICs including disabled Broadcom ports."""
import requests
import json
import urllib3
urllib3.disable_warnings()

BASE = "https://192.168.10.4"
AUTH = ("root", "Tricolor00!")

def get(path):
    r = requests.get(f"{BASE}{path}", auth=AUTH, verify=False, timeout=15)
    return r.json()

# Get network adapters
print("=" * 60)
print("ADV01 iDRAC Network Adapter Inventory")
print("=" * 60)

adapters = get("/redfish/v1/Systems/System.Embedded.1/NetworkAdapters")
for m in adapters.get("Members", []):
    oid = m["@odata.id"]
    d = get(oid)
    name = d.get("Name", "?")
    mfr = d.get("Manufacturer", "?")
    model = d.get("Model", "?")
    print(f"\nAdapter: {oid}")
    print(f"  Name: {name}")
    print(f"  Manufacturer: {mfr}")
    print(f"  Model: {model}")

    # Get ports
    ports_link = None
    if "NetworkPorts" in d:
        ports_link = d["NetworkPorts"].get("@odata.id")
    elif "Ports" in d:
        ports_link = d["Ports"].get("@odata.id")

    if ports_link:
        ports = get(ports_link)
        for pm in ports.get("Members", []):
            port_oid = pm["@odata.id"]
            pd = get(port_oid)
            port_name = pd.get("Name", "?")
            port_id = pd.get("Id", "?")
            link_status = pd.get("LinkStatus", "?")
            speed = pd.get("CurrentLinkSpeedMbps", "?")
            print(f"    Port: {port_name} (Id={port_id}) LinkStatus={link_status} Speed={speed}Mbps")

# Also check BIOS NIC settings
print("\n" + "=" * 60)
print("BIOS Attributes (NIC-related)")
print("=" * 60)

try:
    bios = get("/redfish/v1/Systems/System.Embedded.1/Bios")
    attrs = bios.get("Attributes", {})
    for key, val in sorted(attrs.items()):
        if any(x in key.lower() for x in ["nic", "slot", "integrated", "pxe", "network", "iscsi"]):
            print(f"  {key} = {val}")
except Exception as e:
    print(f"  Error reading BIOS: {e}")
