"""Monitor racadm job progress."""
import paramiko, time

IP = '192.168.10.4'
JOB_ID = 'JID_731643292644'

def ssh_cmd(cmd, timeout=60):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(IP, username='root', password='Tricolor00!', timeout=15)
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    ssh.close()
    return out

start = time.time()
last_pct = -1
while (time.time() - start) < 900:
    out = ssh_cmd(f'racadm jobqueue view -i {JOB_ID}')
    elapsed = int(time.time() - start)
    
    # Parse output
    status = ""
    percent = ""
    message = ""
    for line in out.split('\n'):
        line = line.strip()
        if line.startswith('Status'):
            status = line.split('=', 1)[-1].strip() if '=' in line else line
        elif line.startswith('Percent Complete'):
            percent = line.split('=', 1)[-1].strip() if '=' in line else line
        elif line.startswith('Message'):
            message = line.split('=', 1)[-1].strip() if '=' in line else line
    
    pct_val = int(percent.replace('[', '').replace(']', '').strip()) if percent else 0
    if pct_val != last_pct or elapsed % 30 == 0:
        print(f"[{elapsed:4d}s] Status={status} Progress={percent} Msg={message}")
        last_pct = pct_val
    
    if 'Completed' in status or 'Failed' in status or 'Error' in status:
        print(f"\nFinal status: {status}")
        print(f"Full output:\n{out}")
        break
    
    time.sleep(10)
else:
    print("Timeout waiting for job completion")
    out = ssh_cmd(f'racadm jobqueue view -i {JOB_ID}')
    print(f"Last output:\n{out}")
