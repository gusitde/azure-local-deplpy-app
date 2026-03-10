"""Monitor adv01 SCP job and wait for reboot to complete."""
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

job_id = "JID_731552590547"

print(f"Monitoring SCP job {job_id} on adv01 iDRAC...")
print("Waiting for reboot cycle to complete...")

last_state = ""
for i in range(120):
    try:
        job = get(f"/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs/{job_id}")
        state = job.get("JobState", "Unknown")
        pct = job.get("PercentComplete", "?")
        msg = job.get("Message", "")[:120]
        
        status_line = f"[{i*10}s] {state} ({pct}%) - {msg}"
        if state != last_state:
            print(status_line)
            last_state = state
        elif i % 6 == 0:  # Print every 60s even if unchanged
            print(status_line)
        
        if state in ("Completed", "CompletedWithErrors"):
            print(f"\n  SUCCESS! NicMode change applied.")
            break
        elif state == "Failed":
            print(f"\n  FAILED: {msg}")
            break
    except Exception as e:
        if i % 6 == 0:
            print(f"  [{i*10}s] iDRAC query error (server rebooting): {str(e)[:80]}")
    
    time.sleep(10)

# Check system power state
print("\nChecking system power state...")
try:
    sys = get("/redfish/v1/Systems/System.Embedded.1")
    print(f"  PowerState: {sys.get('PowerState')}")
    print(f"  HostName: {sys.get('HostName')}")
except Exception as e:
    print(f"  Error: {e}")
