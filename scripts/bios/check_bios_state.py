import requests, urllib3, json
urllib3.disable_warnings()
base = 'https://192.168.10.5'
auth = ('root', 'Tricolor00!')

# Check current BIOS attributes
r = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1/Bios', auth=auth, verify=False, timeout=15)
attrs = r.json().get('Attributes', {})

targets = ['SriovGlobalEnable', 'TpmSecurity', 'TpmPpiBypassProvision', 'ProcCStates', 'RedundantOsBoot']
print("=== Current BIOS Attributes ===")
for t in targets:
    print(f"  {t}: {attrs.get(t, 'NOT FOUND')}")

# Check pending settings
print("\n=== Pending BIOS Settings ===")
r2 = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1/Bios/Settings', auth=auth, verify=False, timeout=15)
pending = r2.json().get('Attributes', {})
if pending:
    for t in targets:
        if t in pending:
            print(f"  {t}: {pending[t]} (pending)")
else:
    print("  No pending changes")

# List active jobs
print("\n=== Active Jobs ===")
r3 = requests.get(f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs?$expand=*($levels=1)', auth=auth, verify=False, timeout=15)
jobs = r3.json().get('Members', [])
for j in jobs:
    jid = j.get('Id', '?')
    js = j.get('JobState', '?')
    jt = j.get('JobType', '?')
    msg = j.get('Message', '')
    if js not in ('Completed',):
        print(f"  {jid}: State={js} Type={jt} Msg={msg}")
print(f"  Total jobs: {len(jobs)}")
