"""Enable Broadcom NicMode on adv01 via Dell SCP Import."""
import requests
import json
import time
import urllib3
urllib3.disable_warnings()

BASE = "https://192.168.10.4"
AUTH = ("root", "Tricolor00!")

def get(path):
    r = requests.get(f"{BASE}{path}", auth=AUTH, verify=False, timeout=15)
    return r.json()

def post(path, payload, headers=None):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    r = requests.post(f"{BASE}{path}", auth=AUTH, verify=False, timeout=30,
                      json=payload, headers=h)
    return r

# First, let's try the Systems-level Settings endpoint (from @Redfish.Settings)
print("=" * 60)
print("Trying Systems-level Settings for NIC functions")
print("=" * 60)

for func_id in ["NIC.Integrated.1-1-1", "NIC.Integrated.1-2-1"]:
    settings_path = f"/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{func_id}/Settings"
    print(f"\n  GET {settings_path}")
    try:
        d = get(settings_path)
        if "error" not in d:
            print(f"  Keys: {list(d.keys())}")
            oem = d.get("Oem", {})
            print(f"  Oem: {json.dumps(oem, indent=4)[:500]}")
        else:
            print(f"  Error response")
    except Exception as e:
        print(f"  Error: {e}")

# Use SCP Import to set NicMode
print("\n" + "=" * 60)
print("Using ImportSystemConfiguration to enable NicMode")
print("=" * 60)

scp_xml = """<SystemConfiguration>
  <Component FQDD="NIC.Integrated.1-1-1">
    <Attribute Name="NicMode">Enabled</Attribute>
  </Component>
  <Component FQDD="NIC.Integrated.1-2-1">
    <Attribute Name="NicMode">Enabled</Attribute>
  </Component>
</SystemConfiguration>"""

import_url = "/redfish/v1/Managers/iDRAC.Embedded.1/Actions/Oem/EID_674_Manager.ImportSystemConfiguration"

payload = {
    "ImportBuffer": scp_xml,
    "ShareParameters": {
        "Target": "ALL"
    },
    "ShutdownType": "Graceful"
}

print(f"  Posting SCP Import...")
r = post(import_url, payload)
print(f"  Status: {r.status_code}")

if r.status_code == 202:
    # Job created - track it
    location = r.headers.get("Location", "")
    print(f"  Job Location: {location}")
    
    # Extract job ID
    job_id = location.split("/")[-1] if location else None
    if not job_id:
        try:
            resp = r.json()
            job_id = resp.get("Id")
            print(f"  Job ID from body: {job_id}")
        except:
            pass
    
    if job_id:
        print(f"\n  Monitoring job {job_id}...")
        for i in range(60):
            time.sleep(10)
            try:
                job = get(f"/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs/{job_id}")
                state = job.get("JobState", "Unknown")
                pct = job.get("PercentComplete", "?")
                msg = job.get("Message", "")
                print(f"  [{i*10}s] {state} ({pct}%) - {msg}")
                if state in ("Completed", "CompletedWithErrors", "Failed"):
                    break
            except Exception as e:
                print(f"  [{i*10}s] Error checking job: {e}")
else:
    try:
        resp = r.json()
        print(f"  Response: {json.dumps(resp, indent=2)[:1000]}")
    except:
        print(f"  Response text: {r.text[:500]}")
