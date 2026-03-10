"""Try every possible way to set NicMode=Enabled on adv01 Broadcom NIC."""
import paramiko, requests, urllib3, json, time
urllib3.disable_warnings()

IP = '192.168.10.4'
AUTH = ('root', 'Tricolor00!')

def ssh_cmd(cmd, timeout=60):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(IP, username='root', password='Tricolor00!', timeout=15)
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    ssh.close()
    return out.strip(), err.strip()

# 1. Try all possible racadm set commands for NicMode
print("=" * 60)
print("racadm: Try setting NicMode via various FQDDs")
print("=" * 60)
set_cmds = [
    'racadm set NIC.Integrated.1-1-1.NicMode Enabled',
    'racadm set NIC.NICMode.1.NicMode Enabled',
    'racadm set nic:NIC.Integrated.1-1-1#NICMode.NicMode Enabled',
    'racadm set NIC.Integrated.1-1-1#NICMode.NicMode Enabled',
    'racadm set nic.DeviceLevelConfig.NIC.Integrated.1-1-1.NicMode Enabled',
    'racadm set NIC.NICConfig.NIC.Integrated.1-1-1.NicMode Enabled',
]
for cmd in set_cmds:
    out, err = ssh_cmd(cmd)
    result = out or err
    print(f"  {cmd}")
    print(f"    -> {result[:200]}")
    if 'successfully' in result.lower():
        print("    *** SUCCESS! ***")
        break

# 2. Try racadm set with different attribute groups for the integrated NIC
print("\n" + "=" * 60)
print("racadm: List groups for NIC.Integrated.1-1-1")
print("=" * 60)
out, _ = ssh_cmd('racadm get -f NIC.Integrated.1-1-1')
print(f"  {out[:500]}")

# Try racadm getconfig
out, _ = ssh_cmd('racadm getconfig -g cfgNIC -o NIC.Integrated.1-1-1')
print(f"\n  getconfig: {out[:300]}")

# 3. Try SCP (Server Configuration Profile) import to set NicMode
print("\n" + "=" * 60)
print("Redfish: SCP Import to set NicMode=Enabled")
print("=" * 60)

scp_xml = """<SystemConfiguration>
  <Component FQDD="NIC.Integrated.1-1-1">
    <Attribute Name="NicMode">Enabled</Attribute>
  </Component>
  <Component FQDD="NIC.Integrated.1-2-1">
    <Attribute Name="NicMode">Enabled</Attribute>
  </Component>
</SystemConfiguration>"""

# Try SCP import via Redfish
payload = {
    "ImportBuffer": scp_xml,
    "ShareParameters": {
        "Target": "NIC"
    }
}

for ep in [
    '/redfish/v1/Managers/iDRAC.Embedded.1/Actions/Oem/EID_674_Manager.ImportSystemConfiguration',
    '/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService/Actions/DellLCService.ImportSystemConfiguration',
]:
    r = requests.post(
        f'https://{IP}{ep}',
        auth=AUTH, verify=False, timeout=60,
        json=payload
    )
    print(f"\n  SCP Import via {ep.split('/')[-1]}")
    print(f"  Status: {r.status_code}")
    if r.status_code in (200, 202):
        location = r.headers.get('Location', '')
        print(f"  Location: {location}")
        # Monitor job
        if location:
            job_id = location.split('/')[-1]
            print(f"  Job ID: {job_id}")
            start = time.time()
            while (time.time() - start) < 300:
                try:
                    r2 = requests.get(f'https://{IP}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs/{job_id}', auth=AUTH, verify=False, timeout=30)
                    if r2.ok:
                        jd = r2.json()
                        state = jd.get('JobState', '')
                        msg = jd.get('Message', '')
                        pct = jd.get('PercentComplete', 0)
                        elapsed = int(time.time() - start)
                        print(f"  [{elapsed:3d}s] {state} {pct}% - {msg}")
                        if state in ('Completed', 'CompletedWithErrors', 'Failed'):
                            break
                except:
                    pass
                time.sleep(5)
        break
    else:
        try:
            for m in r.json().get("error", {}).get("@Message.ExtendedInfo", []):
                print(f"  ERROR: {m.get('Message', '')[:300]}")
        except:
            print(f"  Response: {r.text[:300]}")

# 4. Try SCP import with the Lifecycle Controller endpoint  
print("\n" + "=" * 60)
print("Redfish: SCP Import with ShutdownType and TimeToWait")
print("=" * 60)

payload2 = {
    "ImportBuffer": scp_xml,
    "ShutdownType": "Forced",
    "TimeToWait": 300,
    "ShareParameters": {
        "Target": "NIC"
    }
}

r = requests.post(
    f'https://{IP}/redfish/v1/Managers/iDRAC.Embedded.1/Actions/Oem/EID_674_Manager.ImportSystemConfiguration',
    auth=AUTH, verify=False, timeout=60,
    json=payload2
)
print(f"  Status: {r.status_code}")
if r.status_code in (200, 202):
    location = r.headers.get('Location', '')
    print(f"  Location: {location}")
    if location:
        job_id = location.split('/')[-1]
        print(f"  Job ID: {job_id}")
        start = time.time()
        while (time.time() - start) < 300:
            try:
                r2 = requests.get(f'https://{IP}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs/{job_id}', auth=AUTH, verify=False, timeout=30)
                if r2.ok:
                    jd = r2.json()
                    state = jd.get('JobState', '')
                    msg = jd.get('Message', '')
                    pct = jd.get('PercentComplete', 0)
                    elapsed = int(time.time() - start)
                    print(f"  [{elapsed:3d}s] {state} {pct}% - {msg}")
                    if state in ('Completed', 'CompletedWithErrors', 'Failed'):
                        break
            except:
                pass
            time.sleep(5)
else:
    try:
        for m in r.json().get("error", {}).get("@Message.ExtendedInfo", []):
            print(f"  ERROR: {m.get('Message', '')[:300]}")
    except:
        print(f"  Response: {r.text[:300]}")
