"""Mount ISO and boot via racadm SSH on iDRAC."""
import paramiko
import time

IDRAC_USER = "root"
IDRAC_PASS = "Tricolor00!"
LOCAL_IP = "192.168.10.201"
ISO_PATH = "//192.168.10.201/ald-iso/AzureLocal24H2.26100.1742.LCM.12.2602.0.3018.x64.en-us.iso"
CIFS_USER = "gus@worldai.local"
CIFS_PASS = r"Tricolor00!@#$%^&*("

SERVERS = [
    {"idrac": "192.168.10.6", "name": "ADV03"},
    {"idrac": "192.168.10.7", "name": "AVD04"},
]


def ssh_cmd(ip, cmd, timeout=30):
    """Execute a racadm command via SSH."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(ip, port=22, username=IDRAC_USER, password=IDRAC_PASS, timeout=10)
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        return out, err
    finally:
        client.close()


def install_os(ip, name):
    print(f"\n{'='*60}")
    print(f"  {name} ({ip})")
    print(f"{'='*60}")

    # 1. Check power state
    out, _ = ssh_cmd(ip, "racadm serveraction powerstatus")
    print(f"  Power: {out}")
    
    # 2. Power off if needed
    if "ON" in out.upper():
        print(f"  Shutting down...")
        out, _ = ssh_cmd(ip, "racadm serveraction powerdown")
        print(f"  {out}")
        # Wait for power off
        for i in range(24):
            time.sleep(5)
            out, _ = ssh_cmd(ip, "racadm serveraction powerstatus")
            if "OFF" in out.upper():
                print(f"  Off after {(i+1)*5}s")
                break
        else:
            print(f"  Force off...")
            ssh_cmd(ip, "racadm serveraction powerdown -f")
            time.sleep(5)

    # 3. Disconnect any existing remote image
    print(f"  Disconnecting existing remote image...")
    out, _ = ssh_cmd(ip, "racadm remoteimage -d")
    print(f"  {out}")

    # 4. Connect remote image via CIFS
    print(f"  Mounting ISO via CIFS...")
    cmd = f'racadm remoteimage -c -l "{ISO_PATH}" -u "{CIFS_USER}" -p "{CIFS_PASS}"'
    out, err = ssh_cmd(ip, cmd)
    print(f"  Output: {out}")
    if err:
        print(f"  Error: {err}")
    
    # 5. Check remote image status
    out, _ = ssh_cmd(ip, "racadm remoteimage -s")
    print(f"  Status: {out}")

    # 6. Set one-time boot to virtual CD
    print(f"  Setting one-time boot to Virtual CD...")
    out, _ = ssh_cmd(ip, "racadm set BIOS.OneTimeBoot.OneTimeBootMode OneTimeBootSeq")
    print(f"  {out}")
    out, _ = ssh_cmd(ip, "racadm set BIOS.OneTimeBoot.OneTimeBootSeqDev Optical.iDRACVirtual.1-1")
    print(f"  {out}")
    
    # Alternative: use set iDRAC.VirtualMedia.BootOnce
    out, _ = ssh_cmd(ip, "racadm set iDRAC.VirtualMedia.BootOnce 1")
    print(f"  BootOnce: {out}")

    # 7. Create config job if needed
    out, _ = ssh_cmd(ip, "racadm jobqueue create BIOS.Setup.1-1")
    print(f"  Job: {out}")

    # 8. Power on
    print(f"  Powering on...")
    out, _ = ssh_cmd(ip, "racadm serveraction powerup")
    print(f"  {out}")
    
    print(f"  ==> {name} should now boot from ISO")
    return True


if __name__ == "__main__":
    print("Installing OS via racadm SSH")
    
    # First, let's just try the simpler Redfish approach again but try HTTP
    # Actually let's first check if racadm remoteimage supports HTTP
    for srv in SERVERS:
        ip = srv["idrac"]
        name = srv["name"]
        
        # Quick test: check remoteimage capabilities
        out, err = ssh_cmd(ip, "racadm remoteimage -s")
        print(f"\n{name} ({ip}) remote image status: {out}")
    
    print("\n\nProceeding with OS install on both servers...")
    for srv in SERVERS:
        install_os(srv["idrac"], srv["name"])
    
    print("\n" + "="*60)
    print("Both servers should now be booting from the ISO.")
    print("Installation takes ~20-40 minutes.")
    print("="*60)
