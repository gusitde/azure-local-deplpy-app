"""
Download Dell DUP firmware files and push them to iDRAC via HttpPushUri.
"""
import requests
import json
import time
import os
import urllib3
urllib3.disable_warnings()

SERVERS = {
    "adv01": {"idrac": "192.168.10.4", "user": "root", "pass": "Tricolor00!"},
    "adv02": {"idrac": "192.168.10.5", "user": "root", "pass": "Tricolor00!"},
}

DUPS = {
    "broadcom_fw": {
        "name": "Broadcom NetXtreme-E NIC Firmware 23.31.1",
        "url": "https://dl.dell.com/FOLDER13684206M/1/Network_Firmware_5V215_WN64_23.31.1.EXE",
        "file": "Network_Firmware_5V215_WN64_23.31.1.EXE",
    },
    "idrac_fw": {
        "name": "iDRAC 7.00.00.183",
        "url": "https://dl.dell.com/FOLDER13740382M/1/iDRAC-with-Lifecycle-Controller_Firmware_VP556_WN64_7.00.00.183_A00.EXE", 
        "file": "iDRAC-with-Lifecycle-Controller_Firmware_VP556_WN64_7.00.00.183_A00.EXE",
    },
}

DUP_DIR = os.path.join(os.path.dirname(__file__), "dups")

def get_auth(server):
    return (SERVERS[server]["user"], SERVERS[server]["pass"])

def idrac_url(server, path):
    return f"https://{SERVERS[server]['idrac']}{path}"

def download_dup(dup_key):
    """Download DUP file from Dell CDN"""
    dup = DUPS[dup_key]
    os.makedirs(DUP_DIR, exist_ok=True)
    filepath = os.path.join(DUP_DIR, dup["file"])
    
    if os.path.exists(filepath) and os.path.getsize(filepath) > 1000000:
        print(f"  Already downloaded: {filepath} ({os.path.getsize(filepath)} bytes)")
        return filepath
    
    print(f"  Downloading {dup['name']} from Dell CDN...")
    print(f"  URL: {dup['url']}")
    r = requests.get(dup["url"], timeout=600, stream=True)
    r.raise_for_status()
    
    total = int(r.headers.get("Content-Length", 0))
    downloaded = 0
    with open(filepath, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024*1024):
            f.write(chunk)
            downloaded += len(chunk)
            if total > 0:
                pct = downloaded * 100 // total
                print(f"    {downloaded}/{total} bytes ({pct}%)", end="\r")
    
    print(f"\n  Downloaded: {filepath} ({os.path.getsize(filepath)} bytes)")
    return filepath

def push_firmware_multipart(server, filepath, dup_name):
    """Push firmware via MultipartHttpPushUri (iDRAC 9+)"""
    push_uri = "/redfish/v1/UpdateService/MultipartUpload"
    url = idrac_url(server, push_uri)
    
    print(f"\n>>> Multipart push to {server}: {dup_name}")
    print(f"    File: {filepath}")
    print(f"    URL: {url}")
    
    # Prepare multipart: UpdateParameters + UpdateFile
    update_params = json.dumps({
        "@Redfish.OperationApplyTime": "OnReset",
        "Targets": []
    })
    
    filesize = os.path.getsize(filepath)
    print(f"    File size: {filesize} bytes")
    
    with open(filepath, "rb") as f:
        files = {
            "UpdateParameters": (None, update_params, "application/json"),
            "UpdateFile": (os.path.basename(filepath), f, "application/octet-stream"),
        }
        
        r = requests.post(
            url,
            auth=get_auth(server),
            files=files,
            verify=False,
            timeout=600,
        )
    
    print(f"    Status: {r.status_code}")
    print(f"    Location: {r.headers.get('Location', 'N/A')}")
    
    try:
        body = r.json()
        print(f"    Response: {json.dumps(body, indent=2)[:800]}")
    except:
        print(f"    Body: {r.text[:500]}")
    
    loc = r.headers.get("Location", "")
    if loc:
        job_id = loc.split("/")[-1]
        return job_id
    
    # Also check if job ID is in body
    try:
        body = r.json()
        if "Id" in body:
            return body["Id"]
    except:
        pass
    
    return None

def push_firmware_http(server, filepath, dup_name):
    """Push firmware via HttpPushUri"""
    push_uri = "/redfish/v1/UpdateService/FirmwareInventory"
    url = idrac_url(server, push_uri)
    
    print(f"\n>>> HTTP Push to {server}: {dup_name}")
    print(f"    File: {filepath}")
    
    with open(filepath, "rb") as f:
        data = f.read()
    
    print(f"    File size: {len(data)} bytes")
    
    headers = {"Content-Type": "application/octet-stream"}
    r = requests.post(
        url,
        auth=get_auth(server),
        data=data,
        headers=headers,
        verify=False,
        timeout=600,
    )
    
    print(f"    Status: {r.status_code}")
    print(f"    Location: {r.headers.get('Location', 'N/A')}")
    
    try:
        body = r.json()
        print(f"    Response: {json.dumps(body, indent=2)[:800]}")
    except:
        print(f"    Body: {r.text[:500]}")
    
    loc = r.headers.get("Location", "")
    if loc:
        return loc.split("/")[-1]
    return None

def dell_install_action(server, filepath, dup_name):
    """Use Dell OEM Install action"""
    target = "/redfish/v1/UpdateService/Actions/Oem/DellUpdateService.Install"
    url = idrac_url(server, target)
    
    print(f"\n>>> Dell OEM Install on {server}: {dup_name}")
    
    # First upload the file, then use Install action
    # Actually, the Dell Install action takes a URI, not a file upload
    # Let me check what params it accepts
    
    # Check the action info
    svc_url = idrac_url(server, "/redfish/v1/UpdateService")
    r = requests.get(svc_url, auth=get_auth(server), verify=False, timeout=30)
    data = r.json()
    actions = data.get("Actions", {}).get("Oem", {})
    for k, v in actions.items():
        if "Install" in k:
            print(f"    Action: {k}")
            print(f"    Details: {json.dumps(v, indent=2)[:500]}")
    
    return None

def check_job(server, job_id):
    """Check a specific job"""
    url = idrac_url(server, f"/redfish/v1/Managers/iDRAC.Embedded.1/Jobs/{job_id}")
    r = requests.get(url, auth=get_auth(server), verify=False, timeout=30)
    if r.status_code == 200:
        return r.json()
    return {"error": r.status_code}

def monitor_job(server, job_id, timeout_mins=30):
    """Monitor a job until completion or scheduling"""
    print(f"\n--- Monitoring {server} job {job_id} ---")
    start = time.time()
    last_state = ""
    while time.time() - start < timeout_mins * 60:
        j = check_job(server, job_id)
        state = j.get("JobState", "Unknown")
        pct = j.get("PercentComplete", 0)
        msg = j.get("Message", "")
        
        if state != last_state:
            elapsed = int(time.time() - start)
            print(f"  [{elapsed:4d}s] State={state} Pct={pct}% Msg={msg}")
            last_state = state
        
        if state in ["Completed", "Failed", "CompletedWithErrors", "Scheduled"]:
            print(f"\n  RESULT: {state} - {msg}")
            return state
        
        time.sleep(10)
    
    print(f"  TIMEOUT after {timeout_mins} mins")
    return "Timeout"

def main():
    # Step 1: Download the Broadcom NIC firmware DUP
    print("=" * 70)
    print("STEP 1: DOWNLOAD BROADCOM NIC FIRMWARE DUP")
    print("=" * 70)
    
    brcm_path = download_dup("broadcom_fw")
    
    # Step 2: Push via Multipart Upload to adv01
    print("\n" + "=" * 70)
    print("STEP 2: PUSH BROADCOM FW TO ADV01 VIA MULTIPART")
    print("=" * 70)
    
    job_id = push_firmware_multipart("adv01", brcm_path, "Broadcom NIC FW 23.31.1")
    
    if not job_id:
        print("\nMultipart failed, trying HttpPushUri...")
        job_id = push_firmware_http("adv01", brcm_path, "Broadcom NIC FW 23.31.1")
    
    if job_id:
        result = monitor_job("adv01", job_id, timeout_mins=15)
        if result == "Scheduled":
            print("\n*** Firmware is SCHEDULED - will apply on next reboot ***")
    else:
        print("\nBoth upload methods failed. Checking Dell OEM action...")
        dell_install_action("adv01", brcm_path, "Broadcom NIC FW")

if __name__ == "__main__":
    main()
