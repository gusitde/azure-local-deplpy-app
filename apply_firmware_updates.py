"""
Apply firmware updates to Dell PowerEdge R640 servers via iDRAC Redfish API.
Priority updates:
1. Broadcom NIC firmware on adv01 (23.21.13.39 -> latest)
2. BIOS on adv01 (2.23.0 -> 2.24.0)
3. iDRAC on both (7.00.00.181 -> 7.00.00.183)
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

# Dell DUP download URLs (from Dell support site for R640)
DUPS = {
    "broadcom_fw": {
        "name": "Broadcom NetXtreme-E NIC Firmware 23.31.1",
        "url": "https://dl.dell.com/FOLDER13684206M/1/Network_Firmware_5V215_WN64_23.31.1.EXE",
    },
    "idrac_fw": {
        "name": "iDRAC 7.00.00.183",
        "url": "https://dl.dell.com/FOLDER13740382M/1/iDRAC-with-Lifecycle-Controller_Firmware_VP556_WN64_7.00.00.183_A00.EXE",
    },
}

def get_auth(server):
    return (SERVERS[server]["user"], SERVERS[server]["pass"])

def idrac_url(server, path):
    return f"https://{SERVERS[server]['idrac']}{path}"

def get_firmware_inventory(server):
    """Get current firmware inventory"""
    url = idrac_url(server, "/redfish/v1/UpdateService/FirmwareInventory")
    r = requests.get(url, auth=get_auth(server), verify=False, timeout=30)
    r.raise_for_status()
    members = r.json().get("Members", [])
    
    results = {}
    for m in members:
        uri = m["@odata.id"]
        detail = requests.get(idrac_url(server, uri), auth=get_auth(server), verify=False, timeout=30)
        if detail.status_code == 200:
            d = detail.json()
            name = d.get("Name", "")
            version = d.get("Version", "")
            comp_id = d.get("Id", "")
            if any(kw in name.lower() for kw in ["bios", "broadcom", "mellanox", "idrac", "hba", "perc", "cpld"]):
                results[comp_id] = {"Name": name, "Version": version, "Id": comp_id}
    return results

def check_update_service(server):
    """Check iDRAC update service capabilities"""
    url = idrac_url(server, "/redfish/v1/UpdateService")
    r = requests.get(url, auth=get_auth(server), verify=False, timeout=30)
    r.raise_for_status()
    data = r.json()
    print(f"\n=== {server} UpdateService ===")
    print(f"  ServiceEnabled: {data.get('ServiceEnabled')}")
    print(f"  HttpPushUri: {data.get('HttpPushUri')}")
    print(f"  MultipartHttpPushUri: {data.get('MultipartHttpPushUri')}")
    
    # Check for SimpleUpdate action
    actions = data.get("Actions", {})
    simple = actions.get("#UpdateService.SimpleUpdate", {})
    if simple:
        print(f"  SimpleUpdate target: {simple.get('target')}")
        allowed = simple.get("TransferProtocol@Redfish.AllowableValues", [])
        print(f"  Allowed protocols: {allowed}")
    
    # Check for InstallFromRepository
    oem = actions.get("Oem", {})
    for k, v in oem.items():
        if "InstallFromRepository" in k or "Install" in k:
            print(f"  OEM action: {k} -> {v.get('target', v)}")
    
    return data

def simple_update(server, dup_url, dup_name):
    """Use SimpleUpdate to apply firmware from URL"""
    url = idrac_url(server, "/redfish/v1/UpdateService/Actions/UpdateService.SimpleUpdate")
    payload = {
        "ImageURI": dup_url,
        "@Redfish.OperationApplyTime": "OnReset"
    }
    print(f"\n>>> SimpleUpdate on {server}: {dup_name}")
    print(f"    URL: {dup_url}")
    r = requests.post(url, auth=get_auth(server), json=payload, verify=False, timeout=60)
    print(f"    Status: {r.status_code}")
    if r.status_code in [200, 202]:
        data = r.json()
        job_id = None
        # Check for job ID in response
        if "Id" in data:
            job_id = data["Id"]
        elif "@odata.id" in data:
            job_id = data["@odata.id"].split("/")[-1]
        # Also check Location header
        loc = r.headers.get("Location", "")
        if loc and not job_id:
            job_id = loc.split("/")[-1]
        print(f"    Job ID: {job_id}")
        return job_id
    else:
        print(f"    Error: {r.text[:500]}")
        return None

def http_push_update(server, dup_url, dup_name):
    """Download DUP then push via HttpPushUri"""
    print(f"\n>>> Downloading {dup_name}...")
    r = requests.get(dup_url, timeout=300, stream=True)
    r.raise_for_status()
    dup_data = r.content
    print(f"    Downloaded {len(dup_data)} bytes")
    
    push_url = idrac_url(server, "/redfish/v1/UpdateService/FirmwareInventory")
    # Try HttpPushUri
    push_url = idrac_url(server, "/redfish/v1/UpdateService")
    svc = requests.get(push_url, auth=get_auth(server), verify=False, timeout=30).json()
    http_push = svc.get("HttpPushUri")
    
    if http_push:
        print(f"    Pushing to {http_push}...")
        headers = {"Content-Type": "application/octet-stream"}
        r = requests.post(
            idrac_url(server, http_push),
            auth=get_auth(server),
            data=dup_data,
            headers=headers,
            verify=False,
            timeout=300
        )
        print(f"    Status: {r.status_code}")
        if r.status_code in [200, 201, 202]:
            print(f"    Response: {r.text[:500]}")
            return True
        else:
            print(f"    Error: {r.text[:500]}")
    return False

def install_from_repository(server):
    """Use iDRAC's InstallFromRepository to auto-update from Dell CDN"""
    # First check available actions
    url = idrac_url(server, "/redfish/v1/UpdateService")
    r = requests.get(url, auth=get_auth(server), verify=False, timeout=30)
    data = r.json()
    
    actions = data.get("Actions", {})
    oem = actions.get("Oem", {})
    
    install_target = None
    for k, v in oem.items():
        if "InstallFromRepository" in k:
            install_target = v.get("target")
            break
    
    if not install_target:
        print(f"  InstallFromRepository not available on {server}")
        return None
    
    print(f"\n>>> InstallFromRepository on {server}")
    print(f"    Target: {install_target}")
    
    # Try with default Dell repo (downloads.dell.com)
    payload = {
        "IPAddress": "downloads.dell.com",
        "ShareType": "HTTPS",
        "ShareName": "/catalog",
        "CatalogFile": "Catalog.xml.gz",
        "ApplyUpdate": "True",
        "RebootNeeded": "False"  # Don't auto-reboot
    }
    
    r = requests.post(
        idrac_url(server, install_target),
        auth=get_auth(server),
        json=payload,
        verify=False,
        timeout=60
    )
    print(f"    Status: {r.status_code}")
    print(f"    Response: {r.text[:1000]}")
    
    if r.status_code in [200, 202]:
        loc = r.headers.get("Location", "")
        if loc:
            job_id = loc.split("/")[-1]
            print(f"    Job ID: {job_id}")
            return job_id
    return None

def check_job(server, job_id):
    """Check job status"""
    url = idrac_url(server, f"/redfish/v1/Managers/iDRAC.Embedded.1/Jobs/{job_id}")
    r = requests.get(url, auth=get_auth(server), verify=False, timeout=30)
    if r.status_code == 200:
        data = r.json()
        return {
            "Id": data.get("Id"),
            "Name": data.get("Name"),
            "JobState": data.get("JobState"),
            "Message": data.get("Message"),
            "PercentComplete": data.get("PercentComplete"),
        }
    return {"error": r.status_code, "text": r.text[:200]}

def main():
    # Step 1: Show current firmware
    print("=" * 70)
    print("CURRENT FIRMWARE INVENTORY (key components)")
    print("=" * 70)
    
    for server in ["adv01", "adv02"]:
        print(f"\n--- {server} ---")
        try:
            inv = get_firmware_inventory(server)
            for comp_id, info in sorted(inv.items()):
                print(f"  {info['Name']:50s} {info['Version']}")
        except Exception as e:
            print(f"  Error: {e}")
    
    # Step 2: Check update service capabilities
    print("\n" + "=" * 70)
    print("UPDATE SERVICE CAPABILITIES")
    print("=" * 70)
    
    for server in ["adv01", "adv02"]:
        try:
            check_update_service(server)
        except Exception as e:
            print(f"  {server} error: {e}")
    
    # Step 3: Try SimpleUpdate for Broadcom firmware on adv01
    print("\n" + "=" * 70)
    print("APPLYING BROADCOM NIC FIRMWARE ON ADV01")
    print("=" * 70)
    
    job_id = simple_update("adv01", DUPS["broadcom_fw"]["url"], DUPS["broadcom_fw"]["name"])
    if job_id:
        print(f"\nMonitoring job {job_id}...")
        for i in range(30):
            time.sleep(10)
            status = check_job("adv01", job_id)
            print(f"  [{i*10}s] {status}")
            state = status.get("JobState", "")
            if state in ["Completed", "Failed", "CompletedWithErrors"]:
                break

if __name__ == "__main__":
    main()
