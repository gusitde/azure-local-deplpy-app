import requests, urllib3
urllib3.disable_warnings()
auth = ('root', 'Tricolor00!')

for ip in ['192.168.10.5', '192.168.10.6', '192.168.10.7']:
    print(f'=== Server at {ip} ===')
    try:
        r = requests.get(f'https://{ip}/redfish/v1/Systems/System.Embedded.1', auth=auth, verify=False, timeout=10)
        d = r.json()
        print(f'  Model: {d.get("Model")}')
        print(f'  ServiceTag: {d.get("SKU")}')
        print(f'  BIOS: {d.get("BiosVersion")}')
        print(f'  PowerState: {d.get("PowerState")}')
        mem = d.get('MemorySummary', {})
        print(f'  RAM: {mem.get("TotalSystemMemoryGiB")} GiB')
        cpu = d.get('ProcessorSummary', {})
        print(f'  CPU: {cpu.get("Count")}x {cpu.get("Model")}')
        print(f'  HostName: {d.get("HostName")}')
        
        # Get iDRAC info
        r2 = requests.get(f'https://{ip}/redfish/v1/Managers/iDRAC.Embedded.1', auth=auth, verify=False, timeout=10)
        d2 = r2.json()
        print(f'  iDRAC FW: {d2.get("FirmwareVersion")}')
        
        # Get storage
        r3 = requests.get(f'https://{ip}/redfish/v1/Systems/System.Embedded.1/Storage', auth=auth, verify=False, timeout=10)
        d3 = r3.json()
        storage_members = d3.get('Members', [])
        print(f'  Storage Controllers: {len(storage_members)}')
        for sm in storage_members:
            sid = sm.get('@odata.id', '').split('/')[-1]
            print(f'    - {sid}')
        
        # Get NICs
        r4 = requests.get(f'https://{ip}/redfish/v1/Systems/System.Embedded.1/NetworkAdapters', auth=auth, verify=False, timeout=10)
        d4 = r4.json()
        nic_members = d4.get('Members', [])
        print(f'  Network Adapters: {len(nic_members)}')
        for nm in nic_members:
            nid = nm.get('@odata.id', '').split('/')[-1]
            try:
                rn = requests.get(f'https://{ip}{nm["@odata.id"]}', auth=auth, verify=False, timeout=10)
                nd = rn.json()
                mfg = nd.get('Manufacturer', '')
                model = nd.get('Model', '')
                print(f'    - {nid}: {mfg} {model}')
            except:
                print(f'    - {nid}')
                
    except Exception as e:
        print(f'  ERROR: {e}')
    print()
