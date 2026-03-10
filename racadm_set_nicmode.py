import paramiko
import time

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.10.4', username='root', password='Tricolor00!', timeout=30)
print("Connected!")

commands = [
    # Try direct set with various FQDD formats
    'racadm set NIC.Integrated.1-1-1.NicMode Enabled',
    'racadm set NIC.Integrated.1-1-1#VndrConfigPage.NicMode Enabled',
    'racadm set NIC.Integrated.1-1-1#NICConfig.NicMode Enabled',
    
    # Try to list all system config for NIC.Integrated
    'racadm get -t xml -f /tmp/nic_export.xml -c NIC.Integrated.1-1-1',
    
    # Try system config import via racadm
    # First check if we can use systemconfig
    'racadm help systemconfig',
    
    # Try to use jobqueue to create a config job for integrated NIC
    'racadm jobqueue view',
    
    # Try to check pending values
    'racadm get -p NIC.Integrated.1-1-1',
    
    # Check if there's a way to reference integrated NIC config
    'racadm help set',
]

for cmd in commands:
    print(f"\n=== {cmd} ===")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode()
    err = stderr.read().decode()
    if out:
        lines = out.strip().split('\n')
        if len(lines) > 40:
            print('\n'.join(lines[:40]))
            print(f"... ({len(lines)-40} more lines)")
        else:
            print(out.strip())
    if err:
        print(f"STDERR: {err.strip()}")

client.close()
