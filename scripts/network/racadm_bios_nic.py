import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.10.4', username='root', password='Tricolor00!', timeout=30)
print("Connected!")

commands = [
    # Check BIOS integrated devices settings
    'racadm get BIOS.IntegratedDevices',
    # Check specifically for InternalSDCard, Integrated NIC settings
    'racadm get BIOS.IntegratedDevices.IntegratedNetwork1',
    # Check network related BIOS settings
    'racadm get BIOS.MiscSettings',
    # Hardware inventory for integrated NICs
    'racadm hwinventory NIC.Integrated.1-1-1',
    # Check system inventory for group listing
    'racadm get NIC',
    # Try different FQDD format
    'racadm get NIC.NICConfig.3.NicMode',
    'racadm get NIC.NICConfig.4.NicMode',
    # Try nicconfig group names
    'racadm getconfig -g cfgNIC -i 1',
    'racadm getconfig -g cfgNIC -i 3',
]

for cmd in commands:
    print(f"\n=== {cmd} ===")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode()
    err = stderr.read().decode()
    if out:
        # Trim long output
        lines = out.strip().split('\n')
        if len(lines) > 50:
            print('\n'.join(lines[:50]))
            print(f"... ({len(lines)-50} more lines)")
        else:
            print(out.strip())
    if err:
        print(f"STDERR: {err.strip()}")

client.close()
