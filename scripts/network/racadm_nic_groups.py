import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.10.4', username='root', password='Tricolor00!', timeout=30)
print("Connected!")

commands = [
    # Check DeviceLevelConfig - this might have NicMode
    'racadm get NIC.DeviceLevelConfig',
    'racadm get NIC.DeviceLevelConfig.1',
    'racadm get NIC.DeviceLevelConfig.2',
    'racadm get NIC.DeviceLevelConfig.3',
    'racadm get NIC.DeviceLevelConfig.4',
    # Check VndrConfigPage
    'racadm get NIC.VndrConfigPage',
    'racadm get NIC.VndrConfigPage.1',
    # Check FrmwImgMenu  
    'racadm get NIC.FrmwImgMenu',
    'racadm get NIC.FrmwImgMenu.1',
    # Try setting via DeviceLevelConfig
    # First let's see what attributes DeviceLevelConfig has
]

for cmd in commands:
    print(f"\n=== {cmd} ===")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode()
    err = stderr.read().decode()
    if out:
        lines = out.strip().split('\n')
        if len(lines) > 30:
            print('\n'.join(lines[:30]))
            print(f"... ({len(lines)-30} more lines)")
        else:
            print(out.strip())
    if err:
        print(f"STDERR: {err.strip()}")

client.close()
