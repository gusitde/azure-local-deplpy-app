"""
Firmware Update via Local HTTP Server + iDRAC SimpleUpdate.

Strategy:
1. Start HTTP server on 192.168.10.201:8089 serving dups/ folder
2. Use iDRAC SimpleUpdate to pull DUPs from local HTTP server
3. Update order: iDRAC first, then BIOS (both need reboot)
4. Broadcom NIC FW on adv02 only (adv01 has no Broadcom in inventory)

Local machine: 192.168.10.201 (iDRAC network)
adv01 iDRAC: 192.168.10.4
adv02 iDRAC: 192.168.10.5
"""
import requests
import json
import time
import sys
import urllib3
urllib3.disable_warnings()

HTTP_SERVER = "http://192.168.10.201:8089"
IDRAC_USER = "root"
IDRAC_PASS = "Tricolor00!"
HEADERS = {"Content-Type": "application/json"}

SERVERS = {
    "adv01": "192.168.10.4",
    "adv02": "192.168.10.5",
}

# DUP files relative to HTTP server root
DUPS = {
    "iDRAC": "iDRAC-with-Lifecycle-Controller_Firmware_VP556_WN64_7.00.00.183_A00.EXE",
    "BIOS": "BIOS_9M80P_WN64_2.25.0_01.EXE",
    "Broadcom_FW": "Network_Firmware_HVN2R_WN64_23.31.18.10_01.EXE",
}


def api(idrac_ip, method, path, data=None, timeout=60):
    url = f"https://{idrac_ip}{path}"
    kwargs = dict(auth=(IDRAC_USER, IDRAC_PASS), verify=False, timeout=timeout, headers=HEADERS)
    if data:
        kwargs["json"] = data
    r = getattr(requests, method)(url, **kwargs)
    return r


def simple_update(idrac_ip, dup_filename, server_name):
    """Trigger SimpleUpdate on an iDRAC using HTTP URI from local server."""
    uri = f"{HTTP_SERVER}/{dup_filename}"
    print(f"\n  [{server_name}] SimpleUpdate: {dup_filename}")
    print(f"  URI: {uri}")
    
    payload = {
        "ImageURI": uri,
        "@Redfish.OperationApplyTime": "Immediate"
    }
    
    r = api(idrac_ip, "post",
            "/redfish/v1/UpdateService/Actions/UpdateService.SimpleUpdate",
            payload)
    
    print(f"  Status: {r.status_code}")
    
    if r.status_code in (200, 202):
        # Extract job ID from Location header or response
        job_id = None
        if "Location" in r.headers:
            job_id = r.headers["Location"].split("/")[-1]
        else:
            try:
                body = r.json()
                job_id = body.get("Id") or body.get("JobId")
                if not job_id and "Members" in body:
                    pass
            except:
                pass
        
        if job_id:
            print(f"  Job ID: {job_id}")
        else:
            print(f"  Response headers: {dict(r.headers)}")
            try:
                print(f"  Response body: {r.json()}")
            except:
                print(f"  Response body: {r.text[:500]}")
        return job_id
    else:
        try:
            error = r.json()
            msgs = error.get("error", {}).get("@Message.ExtendedInfo", [])
            for msg in msgs:
                print(f"  ERROR: {msg.get('Message', msg)}")
        except:
            print(f"  Response: {r.text[:500]}")
        return None


def monitor_job(idrac_ip, job_id, server_name, timeout_mins=30):
    """Monitor a firmware update job until completion."""
    print(f"\n  [{server_name}] Monitoring job {job_id}...")
    start = time.time()
    last_pct = -1
    
    while time.time() - start < timeout_mins * 60:
        try:
            r = api(idrac_ip, "get", f"/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs/{job_id}")
            if not r.ok:
                # Try TaskService
                r = api(idrac_ip, "get", f"/redfish/v1/TaskService/Tasks/{job_id}")
            
            if r.ok:
                data = r.json()
                state = data.get("JobState", data.get("TaskState", "Unknown"))
                pct = data.get("PercentComplete", 0)
                msg = data.get("Message", "")
                
                if pct != last_pct:
                    elapsed = int(time.time() - start)
                    print(f"    [{elapsed:3d}s] {state} {pct}% - {msg}")
                    last_pct = pct
                
                if state in ("Completed", "CompletedWithErrors"):
                    print(f"  [{server_name}] Job {job_id}: {state}")
                    return state
                elif state in ("Failed", "CompletedWithErrors"):
                    print(f"  [{server_name}] Job {job_id}: FAILED - {msg}")
                    return state
                elif "fail" in state.lower():
                    print(f"  [{server_name}] Job {job_id}: FAILED - {msg}")
                    return state
            else:
                print(f"    Job query returned {r.status_code}")
        except Exception as e:
            print(f"    Error querying job: {e}")
        
        time.sleep(15)
    
    print(f"  [{server_name}] Job {job_id}: TIMED OUT after {timeout_mins} minutes")
    return "Timeout"


def check_http_server():
    """Verify the HTTP server is reachable from this machine."""
    print("Checking HTTP server availability...")
    try:
        r = requests.get(f"{HTTP_SERVER}/", timeout=5)
        print(f"  HTTP server: {r.status_code}")
        if r.ok:
            # Try to access a DUP file
            r2 = requests.head(f"{HTTP_SERVER}/{DUPS['BIOS']}", timeout=5)
            print(f"  BIOS DUP accessible: {r2.status_code} ({r2.headers.get('Content-Length', '?')} bytes)")
            return True
        return False
    except Exception as e:
        print(f"  HTTP server not reachable: {e}")
        print(f"  Please start the HTTP server first:")
        print(f"    cd dups && python -m http.server 8089 --bind 192.168.10.201")
        return False


def get_pending_jobs(idrac_ip, server_name):
    """Check for any pending/running jobs that might block updates."""
    r = api(idrac_ip, "get", "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs?$expand=*($levels=1)")
    if r.ok:
        data = r.json()
        members = data.get("Members", [])
        active = [j for j in members if j.get("JobState") in ("Scheduled", "Running", "Downloading", "New")]
        if active:
            print(f"  [{server_name}] Active jobs found:")
            for j in active:
                print(f"    {j.get('Id')}: {j.get('Name')} - {j.get('JobState')} - {j.get('Message')}")
        return active
    return []


def delete_all_jobs(idrac_ip, server_name):
    """Delete all jobs from the queue (required before some updates)."""
    print(f"  [{server_name}] Clearing job queue...")
    r = api(idrac_ip, "post",
            "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellJobService/Actions/DellJobService.DeleteJobQueue",
            {"JobID": "JID_CLEARALL"})
    print(f"  Clear job queue: {r.status_code}")
    if not r.ok:
        try:
            print(f"  {r.json().get('error', {}).get('@Message.ExtendedInfo', [{}])[0].get('Message', r.text[:200])}")
        except:
            pass
    time.sleep(5)


def main():
    # Step 0: Check HTTP server
    if not check_http_server():
        print("\n*** HTTP server not running. Start it in another terminal: ***")
        print(f"    cd dups && python -m http.server 8089 --bind 192.168.10.201")
        sys.exit(1)
    
    # Determine what to update
    if len(sys.argv) > 1:
        component = sys.argv[1].lower()
        servers_to_update = sys.argv[2:] if len(sys.argv) > 2 else list(SERVERS.keys())
    else:
        component = "all"
        servers_to_update = list(SERVERS.keys())
    
    print(f"\nComponent: {component}")
    print(f"Servers: {servers_to_update}")
    
    # Step 1: Clear job queues
    for name in servers_to_update:
        ip = SERVERS[name]
        pending = get_pending_jobs(ip, name)
        if pending:
            delete_all_jobs(ip, name)
    
    results = {}
    
    # Step 2: Update iDRAC firmware (both servers)
    if component in ("all", "idrac"):
        print("\n" + "=" * 70)
        print("UPDATING iDRAC FIRMWARE (7.00.00.181 -> 7.00.00.183)")
        print("=" * 70)
        for name in servers_to_update:
            ip = SERVERS[name]
            job_id = simple_update(ip, DUPS["iDRAC"], name)
            if job_id:
                results[f"{name}_idrac"] = {"job_id": job_id, "ip": ip, "name": name}
        
        # Monitor iDRAC jobs
        for key, info in results.items():
            if "idrac" in key:
                state = monitor_job(info["ip"], info["job_id"], info["name"], timeout_mins=30)
                results[key]["state"] = state
        
        print("\niDRAC update results:")
        for key, info in results.items():
            if "idrac" in key:
                print(f"  {info['name']}: {info.get('state', 'unknown')}")
        
        if any("idrac" in k and results[k].get("state") == "Completed" for k in results):
            print("\n  iDRAC will reboot automatically. Waiting 3 minutes for iDRAC to come back...")
            time.sleep(180)
    
    # Step 3: Update BIOS (both servers) 
    if component in ("all", "bios"):
        print("\n" + "=" * 70)
        print("UPDATING BIOS (adv01: 2.23.0, adv02: 2.24.0 -> 2.25.0)")
        print("=" * 70)
        for name in servers_to_update:
            ip = SERVERS[name]
            job_id = simple_update(ip, DUPS["BIOS"], name)
            if job_id:
                results[f"{name}_bios"] = {"job_id": job_id, "ip": ip, "name": name}
        
        # Monitor BIOS jobs - these typically schedule and require host reboot
        for key, info in list(results.items()):
            if "bios" in key:
                state = monitor_job(info["ip"], info["job_id"], info["name"], timeout_mins=20)
                results[key]["state"] = state
        
        print("\nBIOS update results:")
        for key, info in results.items():
            if "bios" in key:
                print(f"  {info['name']}: {info.get('state', 'unknown')}")
    
    # Step 4: Update Broadcom NIC FW (adv02 only - adv01 has no Broadcom in inventory)
    if component in ("all", "broadcom", "nic"):
        print("\n" + "=" * 70)
        print("UPDATING BROADCOM NIC FIRMWARE (adv02 only: 23.21.14.14 -> 23.31.18.10)")
        print("=" * 70)
        nic_servers = [s for s in servers_to_update if s == "adv02"]
        if not nic_servers:
            # Also try adv01 in case BIOS update fixed the issue
            print("  Note: adv01 Broadcom not in inventory. Trying anyway after BIOS update...")
            nic_servers = servers_to_update
        
        for name in nic_servers:
            ip = SERVERS[name]
            job_id = simple_update(ip, DUPS["Broadcom_FW"], name)
            if job_id:
                results[f"{name}_nic"] = {"job_id": job_id, "ip": ip, "name": name}
        
        for key, info in list(results.items()):
            if "nic" in key:
                state = monitor_job(info["ip"], info["job_id"], info["name"], timeout_mins=20)
                results[key]["state"] = state
    
    # Summary
    print("\n" + "=" * 70)
    print("FIRMWARE UPDATE SUMMARY")
    print("=" * 70)
    for key, info in sorted(results.items()):
        print(f"  {key}: Job {info['job_id']} -> {info.get('state', 'unknown')}")
    
    print("\nNote: BIOS updates require a host reboot to take effect.")
    print("If BIOS jobs show 'Scheduled', reboot the servers to apply.")


if __name__ == "__main__":
    main()
