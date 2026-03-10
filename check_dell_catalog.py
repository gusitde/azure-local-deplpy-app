import requests, urllib3, json, time
urllib3.disable_warnings()

s = requests.Session()
s.auth = ('root', 'Tricolor00!')
s.verify = False
base = 'https://192.168.10.4'

# Step 1: First check what updates are available from Dell's online catalog
print("=== Step 1: GetRepoBasedUpdateList from Dell catalog ===")
payload = {
    "IPAddress": "downloads.dell.com",
    "ShareType": "HTTPS",
    "CatalogFile": "catalog/Catalog.xml.gz",
    "ApplyUpdate": "False"  # Just list, don't apply yet
}

r = s.post(
    f'{base}/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellSoftwareInstallationService/Actions/DellSoftwareInstallationService.GetRepoBasedUpdateList',
    json=payload
)
print(f"Response: {r.status_code}")
if r.status_code == 202:
    task_uri = r.headers.get('Location', '')
    print(f"Task: {task_uri}")
    
    # Monitor the task
    for i in range(120):
        time.sleep(10)
        rt = s.get(f'{base}{task_uri}')
        if rt.ok:
            td = rt.json()
            state = td.get('TaskState', '')
            pct = td.get('PercentComplete', 0)
            msgs = td.get('Messages', [])
            msg_text = msgs[-1].get('Message', '') if msgs else ''
            
            elapsed = (i+1)*10
            print(f"  [{elapsed}s] {state} ({pct}%) - {msg_text[:100]}")
            
            if state in ['Completed', 'Exception', 'Killed']:
                # Print full result
                print(f"\nFull result:")
                print(json.dumps(td, indent=2)[:5000])
                
                # Look for BIOS update in the results
                if msgs:
                    for msg in msgs:
                        mtxt = msg.get('Message', '')
                        if 'BIOS' in mtxt or 'PackageList' in mtxt:
                            print(f"\nBIOS-related: {mtxt[:500]}")
                break
        else:
            print(f"  [{(i+1)*10}s] HTTP {rt.status_code}")
elif r.status_code == 200:
    print(json.dumps(r.json(), indent=2)[:3000])
else:
    print(f"Error: {r.text[:500]}")
