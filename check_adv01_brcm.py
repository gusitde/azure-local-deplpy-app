"""Check adv01 iDRAC NIC attributes for Broadcom integrated NICs."""
import requests
import json
import urllib3
urllib3.disable_warnings()

BASE = "https://192.168.10.4"
AUTH = ("root", "Tricolor00!")

def get(path):
    r = requests.get(f"{BASE}{path}", auth=AUTH, verify=False, timeout=15)
    return r.json()

# Check NIC.Integrated.1 device-level attributes
print("=" * 60)
print("NIC.Integrated.1 (Broadcom) - Full Details")
print("=" * 60)

# Get the full adapter info
adapter = get("/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1")
print(json.dumps(adapter, indent=2)[:3000])

# Check Dell OEM NIC attributes
print("\n" + "=" * 60)
print("Dell OEM NIC Attributes for Integrated NIC")
print("=" * 60)

# Try to get NIC attributes via Dell Attributes endpoint
try:
    attrs = get("/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions")
    print("\nNetworkDeviceFunctions:")
    for m in attrs.get("Members", []):
        oid = m["@odata.id"]
        print(f"\n  Function: {oid}")
        func = get(oid)
        print(f"    Name: {func.get('Name')}")
        print(f"    NetDevFuncType: {func.get('NetDevFuncType')}")
        print(f"    DeviceEnabled: {func.get('DeviceEnabled')}")
        print(f"    Ethernet MAC: {func.get('Ethernet', {}).get('MACAddress', 'N/A')}")
        # Check Dell OEM attributes
        oem = func.get("Oem", {}).get("Dell", {})
        if oem:
            dell_attrs = oem.get("DellNetworkAttributes", {})
            if dell_attrs:
                print(f"    Dell OEM Attributes endpoint: {dell_attrs.get('@odata.id', 'N/A')}")
except Exception as e:
    print(f"  Error: {e}")

# Check Dell iDRAC NIC Configuration
print("\n" + "=" * 60)
print("Dell iDRAC Attributes (Integrated NIC)")
print("=" * 60)

try:
    # Try Dell-specific attribute endpoints
    for nic_id in ["NIC.Integrated.1-1-1", "NIC.Integrated.1-2-1"]:
        print(f"\n  Checking {nic_id}...")
        try:
            r = get(f"/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellAttributes/{nic_id}")
            attrs = r.get("Attributes", {})
            for k, v in sorted(attrs.items()):
                # Show relevant attributes
                if any(x in k.lower() for x in ["boot", "enable", "mode", "wake", "device", "nic"]):
                    print(f"    {k} = {v}")
        except Exception as e2:
            print(f"    Error: {e2}")
except Exception as e:
    print(f"  Error: {e}")

# Also compare with NIC.Slot.2
print("\n" + "=" * 60)
print("NIC.Slot.2 (Mellanox) - For comparison")
print("=" * 60)
try:
    for nic_id in ["NIC.Slot.2-1-1", "NIC.Slot.2-2-1"]:
        print(f"\n  Checking {nic_id}...")
        try:
            r = get(f"/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellAttributes/{nic_id}")
            attrs = r.get("Attributes", {})
            for k, v in sorted(attrs.items()):
                if any(x in k.lower() for x in ["boot", "enable", "mode", "wake", "device", "nic"]):
                    print(f"    {k} = {v}")
        except Exception as e2:
            print(f"    Error: {e2}")
except Exception as e:
    print(f"  Error: {e}")
