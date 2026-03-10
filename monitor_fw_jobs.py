"""
Monitor firmware update jobs and apply additional updates via iDRAC SimpleUpdate.
"""
import requests
import json
import time
import urllib3
urllib3.disable_warnings()

SERVERS = {
    "adv01": {"idrac": "192.168.10.4", "user": "root", "pass": "Tricolor00!"},
    "adv02": {"idrac": "192.168.10.5", "user": "root", "pass": "Tricolor00!"},
}

def get_auth(server):
    return (SERVERS[server]["user"], SERVERS[server]["pass"])

def idrac_url(server, path):
    return f"https://{SERVERS[server]['idrac']}{path}"

def list_jobs(server):
    """List all jobs"""
    url = idrac_url(server, "/redfish/v1/Managers/iDRAC.Embedded.1/Jobs?$expand=*($levels=1)")
    r = requests.get(url, auth=get_auth(server), verify=False, timeout=30)
    r.raise_for_status()
    jobs = r.json().get("Members", [])
    return jobs

def check_job(server, job_id):
    """Check a specific job"""
    url = idrac_url(server, f"/redfish/v1/Managers/iDRAC.Embedded.1/Jobs/{job_id}")
    r = requests.get(url, auth=get_auth(server), verify=False, timeout=30)
    if r.status_code == 200:
        return r.json()
    return {"error": r.status_code}

def simple_update(server, dup_url, dup_name):
    """Use SimpleUpdate to apply firmware from URL"""
    url = idrac_url(server, "/redfish/v1/UpdateService/Actions/UpdateService.SimpleUpdate")
    payload = {
        "ImageURI": dup_url,
        "@Redfish.OperationApplyTime": "OnReset"
    }
    print(f"\n>>> SimpleUpdate on {server}: {dup_name}")
    print(f"    URL: {dup_url}")
    r = requests.post(url, auth=get_auth(server), json=payload, verify=False, timeout=120)
    print(f"    Status: {r.status_code}")
    print(f"    Headers Location: {r.headers.get('Location', 'N/A')}")
    
    # Try to get response body
    try:
        body = r.json()
        print(f"    Body: {json.dumps(body, indent=2)[:500]}")
    except:
        print(f"    Body: (empty or non-JSON) {r.text[:200]}")
    
    # Get job ID from Location header
    loc = r.headers.get("Location", "")
    if loc:
        job_id = loc.split("/")[-1]
        print(f"    Job ID: {job_id}")
        return job_id
    
    return None

def monitor_job(server, job_id, timeout_mins=30):
    """Monitor a job until completion"""
    print(f"\n--- Monitoring {server} job {job_id} ---")
    start = time.time()
    last_msg = ""
    while time.time() - start < timeout_mins * 60:
        j = check_job(server, job_id)
        state = j.get("JobState", "Unknown")
        pct = j.get("PercentComplete", 0)
        msg = j.get("Message", "")
        
        if msg != last_msg:
            elapsed = int(time.time() - start)
            print(f"  [{elapsed:4d}s] State={state} Pct={pct}% Msg={msg}")
            last_msg = msg
        
        if state in ["Completed", "Failed", "CompletedWithErrors"]:
            print(f"\n  FINAL: {state} - {msg}")
            return state
        
        time.sleep(15)
    
    print(f"  TIMEOUT after {timeout_mins} mins")
    return "Timeout"

def main():
    # Step 1: Check recent jobs on adv01 (the SimpleUpdate we just submitted)
    print("=" * 70)
    print("RECENT JOBS ON ADV01")
    print("=" * 70)
    
    jobs = list_jobs("adv01")
    # Show most recent jobs
    recent = sorted(jobs, key=lambda j: j.get("StartTime", ""), reverse=True)[:10]
    for j in recent:
        print(f"  {j.get('Id'):20s} State={j.get('JobState'):20s} "
              f"Name={j.get('Name', '')[:40]} "
              f"Msg={j.get('Message', '')[:60]}")
    
    # Step 2: Find any Downloading/Scheduled/Running jobs
    active_jobs = [j for j in jobs if j.get("JobState") in 
                   ["Downloading", "Scheduled", "Running", "Waiting", "New"]]
    
    if active_jobs:
        print(f"\n{len(active_jobs)} active job(s) found!")
        for j in active_jobs:
            job_id = j["Id"]
            print(f"\nActive job: {job_id} - {j.get('Name', '')} - State: {j.get('JobState')}")
            state = monitor_job("adv01", job_id, timeout_mins=20)
            
            if state == "Scheduled":
                print(f"  Job {job_id} is scheduled - needs reboot to apply")
    else:
        print("\nNo active jobs. Checking if the Broadcom FW update was accepted...")
        
        # Look for recently completed or failed jobs
        for j in recent[:5]:
            state = j.get("JobState", "")
            name = j.get("Name", "")
            msg = j.get("Message", "")
            print(f"  Recent: {j['Id']} State={state} Name={name} Msg={msg[:80]}")

if __name__ == "__main__":
    main()
