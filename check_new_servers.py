import requests, urllib3
urllib3.disable_warnings()
auth = ('root', 'Tricolor00!')

for ip in ['192.168.10.6', '192.168.10.7']:
    print(f'=== {ip} ===')
    r = requests.get(f'https://{ip}/redfish/v1/Systems/System.Embedded.1', auth=auth, verify=False, timeout=10)
    d = r.json()
    ps = d.get('PowerState')
    print(f'  PowerState: {ps}')
    boot = d.get('Boot', {})
    print(f'  BootTarget: {boot.get("BootSourceOverrideTarget")}')
    
    # Virtual media
    r2 = requests.get(f'https://{ip}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD', auth=auth, verify=False, timeout=10)
    d2 = r2.json()
    ins = d2.get('Inserted')
    img = d2.get('Image')
    print(f'  VirtualMedia: Inserted={ins}, Image={img}')
    
    # NonRAID drives (data disks)
    r3 = requests.get(f'https://{ip}/redfish/v1/Systems/System.Embedded.1/Storage/NonRAID.Integrated.1-1', auth=auth, verify=False, timeout=10)
    d3 = r3.json()
    drives = d3.get('Drives', [])
    print(f'  NonRAID Drives: {len(drives)}')
    for drv in drives[:6]:
        did = drv.get('@odata.id','').split('/')[-1]
        rd = requests.get(f'https://{ip}{drv["@odata.id"]}', auth=auth, verify=False, timeout=10)
        dd = rd.json()
        cap = dd.get('CapacityBytes', 0)
        cap_gb = round(cap / (1024**3), 1) if cap else 0
        model = dd.get('Model', '')
        media = dd.get('MediaType', '')
        print(f'    {did}: {model} {cap_gb}GB {media}')
    
    # BOSS drives (OS boot)
    r4 = requests.get(f'https://{ip}/redfish/v1/Systems/System.Embedded.1/Storage/AHCI.Slot.3-1', auth=auth, verify=False, timeout=10)
    d4 = r4.json()
    boss_drives = d4.get('Drives', [])
    print(f'  BOSS Drives: {len(boss_drives)}')
    for drv in boss_drives[:2]:
        did = drv.get('@odata.id','').split('/')[-1]
        rd = requests.get(f'https://{ip}{drv["@odata.id"]}', auth=auth, verify=False, timeout=10)
        dd = rd.json()
        cap = dd.get('CapacityBytes', 0)
        cap_gb = round(cap / (1024**3), 1) if cap else 0
        model = dd.get('Model', '')
        print(f'    {did}: {model} {cap_gb}GB')
    
    # Check if OS might be running - try WinRM
    import socket
    for port in [5985, 22]:
        try:
            s = socket.create_connection((ip.replace('192.168.10', '192.168.1'), port), timeout=3)
            s.close()
            print(f'  WinRM/SSH port {port}: OPEN')
        except:
            pass
    print()
