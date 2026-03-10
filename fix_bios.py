import requests, urllib3
urllib3.disable_warnings()
base = 'https://192.168.10.5'
auth = ('root', 'Tricolor00!')

# Clear the job queue first
print("Clearing iDRAC job queue...")
r = requests.post(
    f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellJobService/Actions/DellJobService.DeleteJobQueue',
    json={"JobID": "JID_CLEARALL"},
    auth=auth, verify=False, timeout=30
)
print(f"Clear jobs response: {r.status_code} {r.text[:200]}")

# Now patch BIOS settings
print("\nPatching BIOS settings...")
attrs = {
    "TpmSecurity": "OnPbm",
    "ProcCStates": "Disabled",
    "RedundantOsBoot": "Enabled"
}
r2 = requests.patch(
    f'{base}/redfish/v1/Systems/System.Embedded.1/Bios/Settings',
    json={"Attributes": attrs},
    auth=auth, verify=False, timeout=30
)
print(f"Patch response: {r2.status_code}")
if r2.status_code != 200:
    print(f"  Body: {r2.text[:500]}")
else:
    print("  BIOS settings queued successfully")

# Create config job
print("\nCreating BIOS config job...")
r3 = requests.post(
    f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs',
    json={"TargetSettingsURI": "/redfish/v1/Systems/System.Embedded.1/Bios/Settings"},
    auth=auth, verify=False, timeout=30
)
print(f"Job create response: {r3.status_code}")
job_uri = r3.headers.get('Location', '')
print(f"  Job URI: {job_uri}")

if r3.status_code in (200, 201, 202):
    # Reboot to apply
    print("\nRebooting server to apply BIOS changes...")
    r4 = requests.post(
        f'{base}/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset',
        json={"ResetType": "GracefulRestart"},
        auth=auth, verify=False, timeout=15
    )
    print(f"Reboot response: {r4.status_code}")
    
    import time
    # Poll the job
    if job_uri:
        job_path = job_uri.replace('/redfish/v1', '')
        print(f"\nPolling job {job_path}...")
        for i in range(40):
            time.sleep(30)
            rj = requests.get(f'{base}/redfish/v1{job_path}', auth=auth, verify=False, timeout=15)
            j = rj.json()
            state = j.get('JobState', '?')
            pct = j.get('PercentComplete', '?')
            print(f"  [{i*30}s] State={state} Progress={pct}%")
            if state == 'Completed':
                print("BIOS job completed!")
                break
            if state == 'Failed':
                print(f"BIOS job failed: {j.get('Message')}")
                break
