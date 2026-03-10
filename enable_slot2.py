"""Enable Slot2 (Mellanox ConnectX-4 Lx) in BIOS and reboot."""
import requests, urllib3, time
urllib3.disable_warnings()

base = 'https://192.168.10.5'
auth = ('root', 'Tricolor00!')

# 1. Clear job queue
print("1. Clearing job queue...")
r = requests.post(
    f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellJobService/Actions/DellJobService.DeleteJobQueue',
    json={"JobID": "JID_CLEARALL"}, auth=auth, verify=False, timeout=30)
print(f"   Clear: {r.status_code}")
time.sleep(5)

# 2. Patch BIOS - enable Slot2
print("\n2. Enabling Slot2 (Mellanox ConnectX-4 Lx)...")
r = requests.patch(
    f'{base}/redfish/v1/Systems/System.Embedded.1/Bios/Settings',
    json={"Attributes": {"Slot2": "Enabled"}},
    auth=auth, verify=False, timeout=30)
print(f"   Patch: {r.status_code}")
if r.status_code != 200:
    print(f"   Body: {r.text[:300]}")

# 3. Create BIOS config job
print("\n3. Creating BIOS config job...")
r = requests.post(
    f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs',
    json={"TargetSettingsURI": "/redfish/v1/Systems/System.Embedded.1/Bios/Settings"},
    auth=auth, verify=False, timeout=30)
print(f"   Job: {r.status_code}")
job_uri = r.headers.get('Location', '')
print(f"   URI: {job_uri}")

# 4. Reboot to apply
print("\n4. Rebooting to apply BIOS change...")
r = requests.post(
    f'{base}/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset',
    json={"ResetType": "GracefulRestart"}, auth=auth, verify=False, timeout=15)
print(f"   Reboot: {r.status_code}")

# 5. Poll job
if job_uri:
    job_path = job_uri
    if '/redfish/v1' in job_path:
        job_path = job_path.split('/redfish/v1')[1]
    print(f"\n5. Polling job {job_path}...")
    for i in range(30):
        time.sleep(30)
        try:
            rj = requests.get(f'{base}/redfish/v1{job_path}', auth=auth, verify=False, timeout=15)
            j = rj.json()
            state = j.get('JobState', j.get('TaskState', '?'))
            pct = j.get('PercentComplete', '?')
            print(f"   [{(i+1)*30}s] State={state} Progress={pct}%")
            if state == 'Completed':
                print("\n✅ Slot2 (Mellanox) enabled! Server coming back up.")
                break
            if state == 'Failed':
                print(f"\n❌ Job failed: {j.get('Message')}")
                break
        except:
            print(f"   [{(i+1)*30}s] iDRAC not responding (rebooting)...")
