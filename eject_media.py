import requests, urllib3
urllib3.disable_warnings()

servers = [
    ("ADV03", "192.168.10.6"),
    ("AVD04", "192.168.10.7"),
]
auth = ("root", "Tricolor00!")

for name, ip in servers:
    base = f"https://{ip}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
    # Check current state
    r = requests.get(base, auth=auth, verify=False)
    d = r.json()
    print(f"{name} ({ip}): Inserted={d.get('Inserted')}, Image={d.get('Image')}")
    
    if d.get("Inserted"):
        # Eject
        r2 = requests.post(f"{base}/Actions/VirtualMedia.EjectMedia", auth=auth, json={}, verify=False)
        print(f"  Eject: {r2.status_code} {r2.text[:100] if r2.text else 'OK'}")
    else:
        print(f"  Already ejected")
    
    # Verify
    r3 = requests.get(base, auth=auth, verify=False)
    d3 = r3.json()
    print(f"  After: Inserted={d3.get('Inserted')}")
