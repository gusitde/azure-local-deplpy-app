"""Enable Broadcom NicMode on adv01 via iDRAC Redfish PATCH."""
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
    if r.status_code not in (200, 202, 204):
        print(f"  Response: {r.text[:500]}")
    return r

# First check the Settings endpoint for NIC.Integrated.1-1-1
# Dell uses the Settings sub-resource for pending changes
for func_id in ["NIC.Integrated.1-1-1", "NIC.Integrated.1-2-1"]:
    print(f"\n{'='*60}")
    print(f"Enabling NicMode on {func_id}")
    print(f"{'='*60}")
    
    # Check current NicMode
    func = get(f"/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{func_id}")
    current = func.get("Oem", {}).get("Dell", {}).get("DellNIC", {}).get("NicMode", "Unknown")
    print(f"  Current NicMode: {current}")
    
    if current == "Disabled":
        # Try Settings endpoint (pending change for next boot)
        settings_path = f"/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{func_id}/Settings"
        payload = {
            "Oem": {
                "Dell": {
                    "DellNIC": {
                        "NicMode": "Enabled"
                    }
                }
            }
        }
        print(f"  Trying Settings endpoint...")
        r = patch(settings_path, payload)
        
        if r.status_code >= 400:
            # Try direct PATCH on the function itself
            func_path = f"/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{func_id}"
            print(f"  Trying direct PATCH on function...")
            r = patch(func_path, payload)
        
        if r.status_code >= 400:
            # Try Dell Attributes approach
            attrs_path = f"/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellAttributes/{func_id}"
            payload2 = {
                "Attributes": {
                    "NicMode": "Enabled"
                }
            }
            print(f"  Trying Dell Attributes endpoint...")
            r = patch(attrs_path, payload2)
            if r.status_code in (200, 202):
                try:
                    resp = r.json()
                    print(f"  Response: {json.dumps(resp, indent=2)[:500]}")
                except:
                    pass

# Check if we need to schedule a job or reboot
print(f"\n{'='*60}")
print("Checking for pending config jobs...")
print(f"{'='*60}")
try:
    jobs = get("/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs?$expand=*($levels=1)")
    for j in jobs.get("Members", []):
        if j.get("JobState") in ("Scheduled", "New"):
            print(f"  Job: {j.get('Id')} - {j.get('Name')} - State: {j.get('JobState')}")
except Exception as e:
    print(f"  Error: {e}")
