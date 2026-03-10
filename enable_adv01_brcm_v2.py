"""Enable Broadcom NicMode on adv01 via Dell OEM Managers attributes."""
import requests
import json
import urllib3
urllib3.disable_warnings()

BASE = "https://192.168.10.4"
AUTH = ("root", "Tricolor00!")

def get(path):
    r = requests.get(f"{BASE}{path}", auth=AUTH, verify=False, timeout=15)
    return r.json()

def patch(path, payload):
    r = requests.patch(f"{BASE}{path}", auth=AUTH, verify=False, timeout=30,
                       json=payload, headers={"Content-Type": "application/json"})
    print(f"  PATCH {path}: {r.status_code}")
    try:
        resp = r.json()
        print(f"  Response: {json.dumps(resp, indent=2)[:800]}")
    except:
        print(f"  Response text: {r.text[:500]}")
    return r

def post(path, payload):
    r = requests.post(f"{BASE}{path}", auth=AUTH, verify=False, timeout=30,
                      json=payload, headers={"Content-Type": "application/json"})
    print(f"  POST {path}: {r.status_code}")
    try:
        resp = r.json()
        print(f"  Response: {json.dumps(resp, indent=2)[:800]}")
    except:
        print(f"  Response text: {r.text[:500]}")
    return r

# First, discover the correct Dell Attributes paths
print("=" * 60)
print("Discovering Dell Manager Attributes paths")
print("=" * 60)

try:
    r = get("/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellAttributes")
    for m in r.get("Members", []):
        print(f"  {m['@odata.id']}")
except Exception as e:
    print(f"  Not found at Managers path: {e}")

# Try System-level attributes
print("\nTrying System-level Dell Attributes...")
try:
    r = get("/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellAttributes")
    for m in r.get("Members", []):
        print(f"  {m['@odata.id']}")
except Exception as e:
    print(f"  Not found: {e}")

# Try to list Registries to find NIC attribute schema
print("\nTrying Registries...")
try:
    r = get("/redfish/v1/Registries")
    for m in r.get("Members", []):
        oid = m["@odata.id"]
        if "NIC" in oid or "Network" in oid:
            print(f"  {oid}")
except Exception as e:
    print(f"  Error: {e}")

# The Dell way: Use the Settings sub-resource with the correct property name
# NicMode is a Dell NIC attribute, set via the NetworkDeviceFunction Settings
print("\n" + "=" * 60)
print("Trying Settings with NetDevFuncType approach")
print("=" * 60)

for func_id in ["NIC.Integrated.1-1-1", "NIC.Integrated.1-2-1"]:
    settings_path = f"/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{func_id}/Settings"
    
    # First GET the settings to see what's patchable
    print(f"\n  GET {settings_path}")
    try:
        d = get(settings_path)
        print(f"  Patchable keys: {list(d.keys())[:20]}")
        if "Oem" in d:
            print(f"  Oem section exists: {list(d.get('Oem', {}).keys())}")
    except Exception as e:
        print(f"  Error: {e}")

# Try the Dell LC CreateConfigJob approach
# First set the attribute, then create a config job
print("\n" + "=" * 60)
print("Trying Dell LC SetAttribute + CreateConfigJob")
print("=" * 60)

# Method: Use Dell OEM Actions on the Manager
try:
    # List available actions
    mgr = get("/redfish/v1/Managers/iDRAC.Embedded.1")
    actions = mgr.get("Actions", {})
    oem_actions = actions.get("Oem", {})
    for k, v in oem_actions.items():
        if "Attribute" in k or "Config" in k or "Import" in k or "Job" in k:
            print(f"  Action: {k} -> {v.get('target', 'N/A')}")
except Exception as e:
    print(f"  Error: {e}")

# Try the Dell Manager Attributes approach (racadm equivalent)
print("\n" + "=" * 60)
print("Trying Managers/iDRAC.Embedded.1 Attributes")
print("=" * 60)
try:
    # Get iDRAC Manager attributes
    mgr_attrs = get("/redfish/v1/Managers/iDRAC.Embedded.1/Attributes")
    attrs = mgr_attrs.get("Attributes", {})
    nic_attrs = {k: v for k, v in attrs.items() if "NIC" in k.upper() or "Integrated" in k}
    for k, v in sorted(nic_attrs.items())[:20]:
        print(f"  {k} = {v}")
except Exception as e:
    print(f"  Error: {e}")
