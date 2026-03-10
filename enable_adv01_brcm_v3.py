"""Enable Broadcom NicMode on adv01 via Dell SCP Import - Fixed version."""
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

def post(path, payload):
    r = requests.post(f"{BASE}{path}", auth=AUTH, verify=False, timeout=30,
                      json=payload, headers={"Content-Type": "application/json"})
    return r

# First export current NIC config to see the correct attribute format
print("=" * 60)
print("Step 1: Export current NIC config to understand format")
print("=" * 60)

export_url = "/redfish/v1/Managers/iDRAC.Embedded.1/Actions/Oem/EID_674_Manager.ExportSystemConfiguration"
export_payload = {
    "ExportFormat": "XML",
    "ShareParameters": {
        "Target": "NIC"
    }
}

r = post(export_url, export_payload)
print(f"Export Status: {r.status_code}")

if r.status_code == 202:
    location = r.headers.get("Location", "")
    job_id = location.split("/")[-1] if location else None
    print(f"Job ID: {job_id}")
    
    # Wait for export to complete
    for i in range(30):
        time.sleep(5)
        job = get(f"/redfish/v1/TaskService/Tasks/{job_id}")
        state = job.get("TaskState", "Unknown")
        pct = job.get("PercentComplete", "?")
        print(f"  [{i*5}s] {state} ({pct}%)")
        
        if state in ("Completed", "CompletedWithErrors"):
            # Get the exported data
            messages = job.get("Messages", [])
            for msg in messages:
                msg_id = msg.get("MessageId", "")
                if "SystemConfiguration" in str(msg.get("Message", "")):
                    # The config is in the Message field
                    config = msg.get("Message", "")
                    # Find NicMode in the export
                    if "NicMode" in config:
                        # Extract relevant parts
                        import re
                        nic_parts = re.findall(r'<Component[^>]*NIC\.Integrated[^>]*>.*?</Component>', config, re.DOTALL)
                        for part in nic_parts[:2]:
                            # Show just NicMode attribute
                            mode_match = re.findall(r'<Attribute Name="NicMode">[^<]*</Attribute>', part)
                            fqdd_match = re.search(r'FQDD="([^"]*)"', part)
                            if fqdd_match and mode_match:
                                print(f"\n  {fqdd_match.group(1)}: {mode_match[0]}")
                    else:
                        print(f"\n  Config snippet (first 2000 chars):\n{config[:2000]}")
            break
        elif state == "Failed":
            msg = job.get("Messages", [{}])[0].get("Message", "Unknown error")
            print(f"  Failed: {msg}")
            break
else:
    print(f"  Response: {r.text[:500]}")

# Step 2: Now import with NicMode=Enabled
print("\n" + "=" * 60)
print("Step 2: Import SCP to enable NicMode")
print("=" * 60)

# Use proper XML with just the NIC components
scp_xml = """<SystemConfiguration>
<Component FQDD="NIC.Integrated.1-1-1">
<Attribute Name="NicMode">Enabled</Attribute>
</Component>
<Component FQDD="NIC.Integrated.1-2-1">
<Attribute Name="NicMode">Enabled</Attribute>
</Component>
</SystemConfiguration>"""

import_url = "/redfish/v1/Managers/iDRAC.Embedded.1/Actions/Oem/EID_674_Manager.ImportSystemConfiguration"

import_payload = {
    "ImportBuffer": scp_xml,
    "ShareParameters": {
        "Target": "NIC"
    },
    "ShutdownType": "NoReboot"
}

print(f"  Posting SCP Import (Target=NIC, NoReboot)...")
r = post(import_url, import_payload)
print(f"  Status: {r.status_code}")

if r.status_code == 202:
    location = r.headers.get("Location", "")
    job_id = location.split("/")[-1] if location else None
    print(f"  Job ID: {job_id}")
    
    for i in range(60):
        time.sleep(10)
        job = get(f"/redfish/v1/TaskService/Tasks/{job_id}")
        state = job.get("TaskState", "Unknown")
        pct = job.get("PercentComplete", "?")
        msg = ""
        messages = job.get("Messages", [])
        if messages:
            msg = messages[-1].get("Message", "")
        print(f"  [{i*10}s] {state} ({pct}%) - {msg[:100]}")
        
        if state in ("Completed", "CompletedWithErrors", "Failed"):
            # Print all messages
            for m in messages:
                print(f"    MSG: {m.get('MessageId', '')}: {m.get('Message', '')[:200]}")
            break
else:
    try:
        print(f"  Response: {r.json()}")
    except:
        print(f"  Response: {r.text[:500]}")
