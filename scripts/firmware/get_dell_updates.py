import requests, urllib3, json, time
urllib3.disable_warnings()

s = requests.Session()
s.auth = ('root', 'Tricolor00!')
s.verify = False
base = 'https://192.168.10.4'

# Step 1: Get repo-based update list from Dell catalog
print("=== Getting available updates from Dell online catalog ===")
payload = {
    "IPAddress": "downloads.dell.com",
    "ShareType": "HTTPS",
    "CatalogFile": "catalog/Catalog.xml.gz"
}

r = s.post(
    f'{base}/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellSoftwareInstallationService/Actions/DellSoftwareInstallationService.GetRepoBasedUpdateList',
    json=payload
)
print(f"Response: {r.status_code}")

if r.status_code == 202:
    task_uri = r.headers.get('Location', '')
    print(f"Task: {task_uri}")
    
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
            # Truncate very long messages
            display = msg_text[:150] if len(msg_text) > 150 else msg_text
            print(f"  [{elapsed}s] {state} ({pct}%) - {display}")
            
            if state in ['Completed', 'Exception', 'Killed']:
                # Parse the PackageList from the message
                for msg in msgs:
                    mtxt = msg.get('Message', '')
                    if 'PackageList' in mtxt or len(mtxt) > 500:
                        # Try to extract XML/JSON update list
                        print(f"\n=== Full update list message (first 3000 chars) ===")
                        print(mtxt[:3000])
                break
        else:
            print(f"  [{(i+1)*10}s] HTTP {rt.status_code}")
elif r.status_code == 200:
    d = r.json()
    print(json.dumps(d, indent=2)[:3000])
else:
    print(f"Error: {r.text[:1000]}")
