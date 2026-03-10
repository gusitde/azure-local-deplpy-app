import paramiko
import time

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.10.4', username='root', password='Tricolor00!', timeout=30)
print("Connected! Monitoring update job JID_731568598679...")

for i in range(60):
    stdin, stdout, stderr = client.exec_command('racadm jobqueue view -i JID_731568598679', timeout=30)
    out = stdout.read().decode().strip()
    
    # Extract key fields
    status = ''
    pct = ''
    msg = ''
    for line in out.split('\n'):
        if 'Status=' in line:
            status = line.split('=')[1].strip() if '=' in line else line
        elif 'Percent Complete=' in line:
            pct = line.split('=')[1].strip() if '=' in line else line
        elif 'Message=' in line:
            msg = line.split('=', 1)[1].strip() if '=' in line else line
    
    elapsed = i * 10
    print(f"[{elapsed}s] Status={status} Pct={pct} Msg={msg[:120]}")
    
    if 'Completed' in status or 'Failed' in status:
        print(f"\n=== Full job output ===")
        print(out)
        break
    
    time.sleep(10)

# After catalog check, list any pending update jobs
print("\n=== All jobs ===")
stdin, stdout, stderr = client.exec_command('racadm jobqueue view', timeout=30)
out = stdout.read().decode().strip()
# Show last 50 lines
lines = out.split('\n')
for line in lines[-50:]:
    print(line)

client.close()
