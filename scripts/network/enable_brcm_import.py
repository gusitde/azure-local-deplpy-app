"""Enable Broadcom NicMode on adv01 - direct SCP import."""
import requests
import json
import time
import urllib3
urllib3.disable_warnings()

BASE = "https://192.168.10.4"
AUTH = ("root", "Tricolor00!")

def get(path):
    r = requests.get(f"{BASE}{path}", auth=AUTH, verify=False, timeout=15)
    return r

def post(path, payload):
    r = requests.post(f"{BASE}{path}", auth=AUTH, verify=False, timeout=30,
                      json=payload, headers={"Content-Type": "application/json"})
    return r

# Import SCP to enable NicMode on both Broadcom integrated NICs
scp_xml = '<SystemConfiguration><Component FQDD="NIC.Integrated.1-1-1"><Attribute Name="NicMode">Enabled</Attribute></Component><Component FQDD="NIC.Integrated.1-2-1"><Attribute Name="NicMode">Enabled</Attribute></Component></SystemConfiguration>'

import_url = "/redfish/v1/Managers/iDRAC.Embedded.1/Actions/Oem/EID_674_Manager.ImportSystemConfiguration"

import_payload = {
    "ImportBuffer": scp_xml,
    "ShareParameters": {
        "Target": "NIC"
    },
    "ShutdownType": "NoReboot"
}

print("Posting SCP Import (Target=NIC, NoReboot)...")
r = post(import_url, import_payload)
print(f"Status: {r.status_code}")
print(f"Headers Location: {r.headers.get('Location', 'N/A')}")

if r.status_code == 202:
    location = r.headers.get("Location", "")
    job_id = location.split("/")[-1] if location else None
    print(f"Job ID: {job_id}")
    
    # Use Dell Jobs endpoint instead of TaskService
    for i in range(60):
        time.sleep(5)
        try:
            r2 = get(f"/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs/{job_id}")
            if r2.status_code == 200:
                job = r2.json()
            else:
                # Try TaskService
                r2 = get(f"/redfish/v1/TaskService/Tasks/{job_id}")
                if r2.status_code == 200 and r2.text.strip():
                    job = r2.json()
                else:
                    r2 = get(f"{location}")
                    job = r2.json() if r2.status_code == 200 else {}
            
            state = job.get("JobState", job.get("TaskState", "Unknown"))
            pct = job.get("PercentComplete", "?")
            msg = job.get("Message", "")
            print(f"  [{i*5}s] {state} ({pct}%) - {msg[:150]}")
            
            if state in ("Completed", "CompletedWithErrors", "Failed", "Scheduled"):
                if state == "Scheduled":
                    print("  --> Config scheduled for next reboot")
                break
        except Exception as e:
            print(f"  [{i*5}s] Error: {e}")
else:
    try:
        print(f"Response: {json.dumps(r.json(), indent=2)[:1000]}")
    except:
        print(f"Response: {r.text[:500]}")
