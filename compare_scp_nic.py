"""Export SCP for NIC from both servers to compare, and try alternative approaches."""
import requests, urllib3, json, time
urllib3.disable_warnings()

IP1 = '192.168.10.4'  # adv01
IP2 = '192.168.10.5'  # adv02
AUTH = ('root', 'Tricolor00!')

def export_scp(ip, target="NIC", label=""):
    """Export SCP via Redfish."""
    payload = {
        "ExportFormat": "XML",
        "ShareParameters": {
            "Target": target
        },
        "ExportUse": "Default"
    }
    r = requests.post(
        f'https://{ip}/redfish/v1/Managers/iDRAC.Embedded.1/Actions/Oem/EID_674_Manager.ExportSystemConfiguration',
        auth=AUTH, verify=False, timeout=60,
        json=payload
    )
    if r.status_code == 202:
        location = r.headers.get('Location', '')
        job_id = location.split('/')[-1] if location else ''
        print(f"  {label} Export job: {job_id}")
        
        start = time.time()
        while (time.time() - start) < 120:
            r2 = requests.get(f'https://{ip}{location}', auth=AUTH, verify=False, timeout=30)
            if r2.ok:
                try:
                    jd = r2.json()
                except:
                    time.sleep(3)
                    continue
                state = jd.get('TaskState', jd.get('JobState', ''))
                msg = jd.get('Messages', [{}])
                
                if state == 'Completed' or 'Completed' in str(jd):
                    # Get the SCP content
                    for m in jd.get('Messages', []):
                        if 'Message' in m and '<?xml' in m.get('Message', ''):
                            return m['Message']
                    # Try Oem
                    oem = jd.get('Oem', {}).get('Dell', {})
                    if oem:
                        return json.dumps(oem, indent=2)[:2000]
                    return json.dumps(jd, indent=2)[:3000]
                elif 'Failed' in state or 'Exception' in state:
                    print(f"    Failed: {json.dumps(jd, indent=2)[:500]}")
                    return None
            time.sleep(3)
    else:
        print(f"  {label} Export failed: {r.status_code}")
        try:
            print(f"  {json.dumps(r.json(), indent=2)[:300]}")
        except:
            pass
    return None

# 1. Export NIC SCP from adv01
print("=" * 60)
print("adv01 NIC SCP Export")
print("=" * 60)
scp1 = export_scp(IP1, "NIC", "adv01")
if scp1:
    # Find NIC.Integrated sections
    if '<?xml' in scp1:
        # Parse and show NIC.Integrated parts
        import re
        integrated_matches = re.findall(r'(<Component[^>]*NIC\.Integrated[^>]*>.*?</Component>)', scp1, re.DOTALL)
        if integrated_matches:
            for m in integrated_matches:
                print(f"\n{m[:800]}")
        else:
            # Show all
            print(scp1[:2000])
    else:
        print(scp1[:2000])

# 2. Export NIC SCP from adv02
print("\n" + "=" * 60)
print("adv02 NIC SCP Export")
print("=" * 60)
scp2 = export_scp(IP2, "NIC", "adv02")
if scp2:
    if '<?xml' in scp2:
        import re
        integrated_matches = re.findall(r'(<Component[^>]*NIC\.Integrated[^>]*>.*?</Component>)', scp2, re.DOTALL)
        if integrated_matches:
            for m in integrated_matches:
                print(f"\n{m[:800]}")
        else:
            print(scp2[:2000])
    else:
        print(scp2[:2000])

# 3. Check the failed SCP import job details
print("\n" + "=" * 60)
print("Failed SCP Import Job Details")
print("=" * 60)
for jid in ['JID_731645863528']:
    r = requests.get(f'https://{IP1}/redfish/v1/TaskService/Tasks/{jid}', auth=AUTH, verify=False, timeout=30)
    if r.ok:
        jd = r.json()
        print(f"  TaskState: {jd.get('TaskState')}")
        for msg in jd.get('Messages', []):
            print(f"  Message: {msg.get('Message', '')[:300]}")
            if msg.get('MessageArgs'):
                print(f"  Args: {msg.get('MessageArgs')}")

# 4. Check LC log for NIC-related entries
print("\n" + "=" * 60)
print("Life Cycle Log (recent NIC entries)")  
print("=" * 60)
r = requests.get(
    f'https://{IP1}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService/Actions/DellLCService.GetRSStatus',
    auth=AUTH, verify=False, timeout=30
)
# Actually get LC logs
r = requests.get(
    f'https://{IP1}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Lclog/Entries?$top=30',
    auth=AUTH, verify=False, timeout=30
)
if r.ok:
    entries = r.json().get('Members', [])
    for e in entries:
        msg = e.get('Message', '')
        if 'NIC' in msg or 'network' in msg.lower() or 'firmware' in msg.lower() or 'SCP' in msg or 'config' in msg.lower():
            print(f"  [{e.get('Created','')}] {msg[:200]}")
