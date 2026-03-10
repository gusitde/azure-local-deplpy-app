"""Check adv01 Broadcom NIC status and try to push firmware."""
import requests, urllib3, json, time
urllib3.disable_warnings()

IP = '192.168.10.4'
AUTH = ('root', 'Tricolor00!')
HTTP_BASE = 'http://192.168.10.201:8089'

# 1. Check full firmware inventory
print("=" * 60)
print("adv01 - Full Firmware Inventory")
print("=" * 60)
r = requests.get(f'https://{IP}/redfish/v1/UpdateService/FirmwareInventory', auth=AUTH, verify=False, timeout=30)
members = r.json().get('Members', [])
has_broadcom = False
for m in members:
    fpath = m.get('@odata.id', '')
    r2 = requests.get(f'https://{IP}{fpath}', auth=AUTH, verify=False, timeout=15)
    if r2.ok:
        d = r2.json()
        fid = d.get('Id', '?')
        fname = d.get('Name', '?')
        fver = d.get('Version', '?')
        print(f"  {fid[:55]:56s} {fname[:40]:41s} v{fver}")
        if 'broadcom' in fname.lower() or 'brcm' in fname.lower() or 'integrated.1' in fid.lower():
            has_broadcom = True

# 2. Check network adapters
print("\n" + "=" * 60)
print("adv01 - Network Adapters")
print("=" * 60)
r = requests.get(f'https://{IP}/redfish/v1/Systems/System.Embedded.1/NetworkAdapters', auth=AUTH, verify=False, timeout=30)
if r.ok:
    for m in r.json().get('Members', []):
        apath = m.get('@odata.id', '')
        r2 = requests.get(f'https://{IP}{apath}', auth=AUTH, verify=False, timeout=15)
        if r2.ok:
            ad = r2.json()
            print(f"  {ad.get('Id')} - {ad.get('Model')} - Status: {ad.get('Status')}")

# 3. Try pushing Broadcom firmware regardless
print("\n" + "=" * 60)
print(f"Broadcom in inventory: {has_broadcom}")
print("Attempting SimpleUpdate for Broadcom NIC FW on adv01...")
print("=" * 60)

dup_url = f"{HTTP_BASE}/Network_Firmware_HVN2R_WN64_23.31.18.10_01.EXE"
payload = {
    "ImageURI": dup_url,
    "@Redfish.OperationApplyTime": "Immediate"
}
r = requests.post(
    f'https://{IP}/redfish/v1/UpdateService/Actions/UpdateService.SimpleUpdate',
    auth=AUTH, verify=False, timeout=60,
    headers={"Content-Type": "application/json"},
    json=payload
)
print(f"  SimpleUpdate response: {r.status_code}")
if r.status_code in (200, 202):
    location = r.headers.get("Location", "")
    job_id = location.split("/")[-1] if location else "?"
    print(f"  Job ID: {job_id}")
    print(f"  Location: {location}")
    
    # Monitor the job
    print("\n  Monitoring job...")
    job_url = f"https://{IP}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs/{job_id}"
    start = time.time()
    last_msg = ""
    while (time.time() - start) < 600:
        try:
            r2 = requests.get(job_url, auth=AUTH, verify=False, timeout=30)
            if r2.ok:
                jd = r2.json()
                state = jd.get("JobState", "?")
                pct = jd.get("PercentComplete", 0)
                msg = jd.get("Message", "")
                if msg != last_msg:
                    elapsed = int(time.time() - start)
                    print(f"  [{elapsed:3d}s] {state} {pct}% - {msg}")
                    last_msg = msg
                if state in ("Completed", "CompletedWithErrors"):
                    print("  SUCCESS!")
                    break
                elif state == "Failed":
                    print(f"  FAILED: {msg}")
                    break
        except Exception as e:
            print(f"  Connection error: {e}")
        time.sleep(10)
else:
    try:
        err = r.json()
        for m in err.get("error", {}).get("@Message.ExtendedInfo", []):
            print(f"  ERROR: {m.get('Message', '')}")
    except:
        print(f"  Response: {r.text[:500]}")

# 4. Also try MultipartUpload with the DUP file
if r.status_code not in (200, 202):
    print("\n  SimpleUpdate failed. Trying MultipartUpload...")
    dup_path = r"C:\Users\gus\Documents\GitHub\azure-local-deplpy-app\dups\Network_Firmware_HVN2R_WN64_23.31.18.10_01.EXE"
    
    with open(dup_path, 'rb') as f:
        files = {
            'UpdateParameters': (None, json.dumps({"Targets": [], "@Redfish.OperationApplyTime": "Immediate"}), 'application/json'),
            'UpdateFile': ('Network_Firmware_HVN2R_WN64_23.31.18.10_01.EXE', f, 'application/octet-stream')
        }
        r3 = requests.post(
            f'https://{IP}/redfish/v1/UpdateService/MultipartUpload',
            auth=AUTH, verify=False, timeout=300,
            files=files
        )
    print(f"  MultipartUpload response: {r3.status_code}")
    try:
        data = r3.json()
        if r3.status_code in (200, 202):
            print(f"  Job: {data}")
        else:
            for m in data.get("error", {}).get("@Message.ExtendedInfo", []):
                print(f"  ERROR: {m.get('Message', '')}")
    except:
        print(f"  Response: {r3.text[:500]}")
