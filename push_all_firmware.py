"""
Push firmware updates to both servers via iDRAC SimpleUpdate.
Uses local HTTP server at http://192.168.10.201:8089/
"""
import requests
import json
import time
import urllib3
urllib3.disable_warnings()

HTTP_BASE = "http://192.168.10.201:8089"
USER = "root"
PASS = "Tricolor00!"
HEADERS = {"Content-Type": "application/json"}

SERVERS = {
    "adv01": "192.168.10.4",
    "adv02": "192.168.10.5",
}

# Firmware DUPs to apply - order matters: iDRAC first, then BIOS, then NIC
FIRMWARE = [
    {
        "name": "iDRAC 7.00.00.183",
        "file": "iDRAC-with-Lifecycle-Controller_Firmware_VP556_WN64_7.00.00.183_A00.EXE",
        "targets": ["adv01", "adv02"],  # Both need update (7.00.00.181 -> .183)
    },
    {
        "name": "BIOS 2.25.0",
        "file": "BIOS_9M80P_WN64_2.25.0_01.EXE",
        "targets": ["adv01", "adv02"],  # adv01=2.23.0, adv02=2.24.0 -> 2.25.0
    },
    {
        "name": "Broadcom NIC FW 23.31.18.10",
        "file": "Network_Firmware_HVN2R_WN64_23.31.18.10_01.EXE",
        "targets": ["adv02"],  # Only adv02 has Broadcom in inventory; adv01 will fail (RED097)
    },
]

def simple_update(idrac_ip, dup_url, server_name):
    """Submit a SimpleUpdate job to iDRAC."""
    url = f"https://{idrac_ip}/redfish/v1/UpdateService/Actions/UpdateService.SimpleUpdate"
    payload = {
        "ImageURI": dup_url,
        "@Redfish.OperationApplyTime": "Immediate"
    }
    r = requests.post(url, auth=(USER, PASS), verify=False, timeout=60,
                      headers=HEADERS, json=payload)
    print(f"    SimpleUpdate -> {r.status_code}")
    
    if r.status_code in (200, 202):
        # Extract job ID from Location header
        job_id = None
        location = r.headers.get("Location", "")
        if location:
            job_id = location.split("/")[-1]
        else:
            # Try from response body
            try:
                data = r.json()
                job_id = data.get("Id", "")
            except:
                pass
        print(f"    Job ID: {job_id}")
        return job_id
    else:
        try:
            err = r.json()
            msgs = err.get("error", {}).get("@Message.ExtendedInfo", [])
            for m in msgs:
                print(f"    ERROR: {m.get('Message', '')}")
        except:
            print(f"    Response: {r.text[:300]}")
        return None

def monitor_job(idrac_ip, job_id, server_name, timeout_min=30):
    """Monitor a firmware update job until completion."""
    url = f"https://{idrac_ip}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs/{job_id}"
    start = time.time()
    last_pct = -1
    
    while (time.time() - start) < (timeout_min * 60):
        try:
            r = requests.get(url, auth=(USER, PASS), verify=False, timeout=30)
            if r.ok:
                data = r.json()
                state = data.get("JobState", "Unknown")
                pct = data.get("PercentComplete", 0)
                msg = data.get("Message", "")
                
                if pct != last_pct:
                    elapsed = int(time.time() - start)
                    print(f"    [{server_name}] {state} {pct}% - {msg} ({elapsed}s)")
                    last_pct = pct
                
                if state in ("Completed", "CompletedWithErrors"):
                    return state
                elif state in ("Failed", "CompletedWithErrors"):
                    print(f"    FAILED: {msg}")
                    return state
            else:
                # Job URL might be different
                alt_url = f"https://{idrac_ip}/redfish/v1/TaskService/Tasks/{job_id}"
                r2 = requests.get(alt_url, auth=(USER, PASS), verify=False, timeout=30)
                if r2.ok:
                    data = r2.json()
                    state = data.get("TaskState", "Unknown")
                    pct = data.get("PercentComplete", 0)
                    if pct != last_pct:
                        print(f"    [{server_name}] {state} {pct}%")
                        last_pct = pct
                    if state in ("Completed", "Exception"):
                        return "Completed" if state == "Completed" else "Failed"
        except Exception as e:
            print(f"    [{server_name}] Connection error (iDRAC may be rebooting): {e}")
        
        time.sleep(15)
    
    print(f"    [{server_name}] TIMEOUT after {timeout_min} min")
    return "Timeout"

def check_pending_jobs(idrac_ip, server_name):
    """Check for any pending/running jobs that might block updates."""
    url = f"https://{idrac_ip}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs?$expand=*($levels=1)"
    r = requests.get(url, auth=(USER, PASS), verify=False, timeout=30)
    if r.ok:
        data = r.json()
        members = data.get("Members", [])
        active = [j for j in members if j.get("JobState") in ("Scheduled", "Running", "Downloading", "Waiting")]
        if active:
            print(f"  [{server_name}] Active jobs found:")
            for j in active:
                print(f"    {j.get('Id')}: {j.get('Name')} - {j.get('JobState')} - {j.get('Message')}")
            return True
    return False

# ============================================================
# MAIN
# ============================================================
print("=" * 70)
print("FIRMWARE UPDATE - BOTH SERVERS VIA LOCAL HTTP")
print(f"HTTP Server: {HTTP_BASE}")
print("=" * 70)

# First verify HTTP server is accessible from here
try:
    r = requests.get(f"{HTTP_BASE}/", timeout=5)
    print(f"HTTP server check: {r.status_code} OK")
except Exception as e:
    print(f"WARNING: HTTP server not reachable: {e}")
    print("Make sure the HTTP server is running!")

# Check for pending jobs on both servers
print("\nChecking for pending jobs...")
for name, ip in SERVERS.items():
    check_pending_jobs(ip, name)

# Process each firmware update
for fw in FIRMWARE:
    print(f"\n{'='*70}")
    print(f"UPDATING: {fw['name']}")
    print(f"DUP: {fw['file']}")
    print(f"Targets: {', '.join(fw['targets'])}")
    print(f"{'='*70}")
    
    dup_url = f"{HTTP_BASE}/{fw['file']}"
    
    # Submit to all targets
    jobs = {}
    for target in fw["targets"]:
        ip = SERVERS[target]
        print(f"\n  Submitting to {target} ({ip})...")
        job_id = simple_update(ip, dup_url, target)
        if job_id:
            jobs[target] = job_id
    
    if not jobs:
        print("  No jobs created, skipping monitoring.")
        continue
    
    # Monitor all jobs
    print(f"\n  Monitoring {len(jobs)} job(s)...")
    results = {}
    for target, job_id in jobs.items():
        ip = SERVERS[target]
        result = monitor_job(ip, job_id, target, timeout_min=30)
        results[target] = result
    
    # Summary
    print(f"\n  Results for {fw['name']}:")
    for target, result in results.items():
        status = "OK" if result == "Completed" else "FAILED"
        print(f"    {target}: {result} [{status}]")
    
    # If this was iDRAC update, wait extra for iDRAC to come back
    if "iDRAC" in fw["name"]:
        print("\n  Waiting 120s for iDRAC to fully restart...")
        time.sleep(120)
        for name, ip in SERVERS.items():
            try:
                r = requests.get(f"https://{ip}/redfish/v1/Managers/iDRAC.Embedded.1",
                               auth=(USER, PASS), verify=False, timeout=30)
                if r.ok:
                    ver = r.json().get("FirmwareVersion", "?")
                    print(f"    {name} iDRAC version: {ver}")
            except:
                print(f"    {name} iDRAC not responding yet")

print("\n" + "=" * 70)
print("FIRMWARE UPDATE COMPLETE")
print("=" * 70)

# Final firmware check
print("\nVerifying current firmware versions...")
for name, ip in SERVERS.items():
    print(f"\n  {name} ({ip}):")
    try:
        # BIOS
        r = requests.get(f"https://{ip}/redfish/v1/Systems/System.Embedded.1",
                        auth=(USER, PASS), verify=False, timeout=30)
        if r.ok:
            print(f"    BIOS: {r.json().get('BiosVersion', '?')}")
        
        # iDRAC
        r = requests.get(f"https://{ip}/redfish/v1/Managers/iDRAC.Embedded.1",
                        auth=(USER, PASS), verify=False, timeout=30)
        if r.ok:
            print(f"    iDRAC: {r.json().get('FirmwareVersion', '?')}")
    except Exception as e:
        print(f"    Error: {e}")
