"""Check firmware versions and job status on both nodes."""
import requests, urllib3
urllib3.disable_warnings()
USER='root'; PASS='Tricolor00!'
servers = {'adv01': '192.168.10.4', 'adv02': '192.168.10.5'}

for name, ip in servers.items():
    sep = '=' * 60
    print(f'\n{sep}')
    print(f'  {name} ({ip})')
    print(sep)
    try:
        # BIOS
        r = requests.get(f'https://{ip}/redfish/v1/Systems/System.Embedded.1', auth=(USER,PASS), verify=False, timeout=30)
        if r.ok:
            print(f'  BIOS:       {r.json().get("BiosVersion","?")}')

        # iDRAC
        r = requests.get(f'https://{ip}/redfish/v1/Managers/iDRAC.Embedded.1', auth=(USER,PASS), verify=False, timeout=30)
        if r.ok:
            print(f'  iDRAC:      {r.json().get("FirmwareVersion","?")}')

        # Firmware inventory - key components
        r = requests.get(f'https://{ip}/redfish/v1/UpdateService/FirmwareInventory', auth=(USER,PASS), verify=False, timeout=30)
        if r.ok:
            members = r.json().get('Members', [])
            for m in members:
                fpath = m.get('@odata.id', '')
                fid = fpath.split('/')[-1]
                if any(x in fid.lower() for x in ['nic.', 'cpld', 'nonraid', 'bios', 'idrac']):
                    r2 = requests.get(f'https://{ip}{fpath}', auth=(USER,PASS), verify=False, timeout=15)
                    if r2.ok:
                        d = r2.json()
                        prefix = 'Current' if 'Current' in fid else 'Installed'
                        cname = d.get("Name", "?")[:45]
                        ver = d.get("Version", "?")
                        print(f'  {prefix:10s} {cname:46s} v{ver}')

        # Check pending/active jobs
        r = requests.get(f'https://{ip}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs?$expand=*($levels=1)',
                         auth=(USER,PASS), verify=False, timeout=30)
        if r.ok:
            jobs = r.json().get('Members', [])
            active = [j for j in jobs if j.get('JobState') in ('Scheduled','Running','Downloading','Waiting','New')]
            if active:
                print(f'\n  ** {len(active)} ACTIVE/PENDING JOBS:')
                for j in active:
                    jid = j.get("Id", "?")
                    jname = j.get("Name", "?")[:40]
                    jstate = j.get("JobState", "?")
                    jpct = j.get("PercentComplete", 0)
                    jmsg = j.get("Message", "")[:60]
                    print(f'     {jid}: {jname} - {jstate} {jpct}% - {jmsg}')
            else:
                print(f'\n  No active/pending jobs')

            # Also show recently completed/failed
            recent = [j for j in jobs if j.get('JobState') in ('Completed','Failed','CompletedWithErrors')]
            recent.sort(key=lambda x: x.get('EndTime', ''), reverse=True)
            if recent[:5]:
                print(f'  Recent completed jobs:')
                for j in recent[:5]:
                    jid = j.get("Id", "?")
                    jname = j.get("Name", "?")[:40]
                    jstate = j.get("JobState", "?")
                    jmsg = j.get("Message", "")[:60]
                    print(f'     {jid}: {jname} - {jstate} - {jmsg}')

    except Exception as e:
        print(f'  ERROR: {e}')
