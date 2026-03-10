"""
Install drivers and firmware on adv02 (192.168.1.105)
DUPs to install:
  1. Chipset_Driver_4DDMJ  (Intel chipset)
  2. Network_Driver_G6M58  (Mellanox driver)  
  3. Network_Firmware_XGP2X (Mellanox firmware)
  4. Network_Driver_T5K6M   (Broadcom - already installed, needs reboot)
"""
import subprocess, os, sys, time

SERVER = "192.168.1.105"
CRED_USER = "Administrator"
CRED_PASS = 'Tricolor00!@#$'
DUP_DIR = r"C:\Users\gus\Documents\GitHub\azure-local-deplpy-app\dups"
REMOTE_DIR = r"C:\Temp"

DUPS_TO_INSTALL = [
    ("Chipset_Driver_4DDMJ_WN64_10.1.19913.8607_A00_01.EXE", "Intel Chipset"),
    ("Network_Firmware_XGP2X_WN64_14.32.20.04.EXE", "Mellanox Firmware"),
    ("Network_Driver_G6M58_WN64_24.04.03_01.EXE", "Mellanox Driver"),
]

def run_ps(cmd, timeout=300):
    """Run a PowerShell command and return output."""
    full = f'powershell -NoProfile -Command "{cmd}"'
    r = subprocess.run(full, capture_output=True, text=True, timeout=timeout, shell=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def remote_cmd(script_block, timeout=300):
    """Run a command on the remote server via Invoke-Command."""
    ps = (
        f"$pw = ConvertTo-SecureString '{CRED_PASS}' -AsPlainText -Force; "
        f"$c = New-Object PSCredential('{CRED_USER}', $pw); "
        f"Invoke-Command -ComputerName {SERVER} -Credential $c -ScriptBlock {{ {script_block} }}"
    )
    return run_ps(ps, timeout)

def copy_dup(filename):
    """Copy a DUP to the remote server via SMB admin share."""
    src = os.path.join(DUP_DIR, filename)
    # Use UNC path with admin share
    dst = f"\\\\{SERVER}\\C$\\Temp\\{filename}"
    if not os.path.exists(src):
        print(f"  ERROR: {src} not found locally!")
        return False
    
    # Use PowerShell to copy with credentials
    ps = (
        f"$pw = ConvertTo-SecureString '{CRED_PASS}' -AsPlainText -Force; "
        f"$c = New-Object PSCredential('{CRED_USER}', $pw); "
        f"New-PSDrive -Name Z -PSProvider FileSystem -Root '\\\\{SERVER}\\C$' -Credential $c -ErrorAction Stop | Out-Null; "
        f"Copy-Item '{src}' 'Z:\\Temp\\{filename}' -Force; "
        f"Remove-PSDrive Z; "
        f"Write-Host 'COPY_OK'"
    )
    out, err, rc = run_ps(ps, 120)
    if "COPY_OK" in out:
        return True
    print(f"  Copy error: {err or out}")
    return False

def install_dup(filename, label):
    """Install a DUP on the remote server silently."""
    remote_path = f"{REMOTE_DIR}\\{filename}"
    # Dell DUPs use /s /f for silent forced install
    script = (
        f"Write-Host 'Installing {label}...'; "
        f"$p = Start-Process -FilePath '{remote_path}' -ArgumentList '/s','/f' -Wait -PassThru -NoNewWindow; "
        f"Write-Host ('EXIT_CODE=' + $p.ExitCode)"
    )
    out, err, rc = remote_cmd(script, timeout=600)
    return out, err

def main():
    print("=" * 60)
    print("ADV02 Driver Installation")
    print("=" * 60)
    
    # Step 0: Verify connectivity
    print("\n[0] Verifying connectivity to adv02...")
    out, err, rc = remote_cmd("Write-Host $env:COMPUTERNAME")
    if "ADV02" not in out.upper():
        print(f"  FAIL: Cannot reach adv02. Got: {out} {err}")
        sys.exit(1)
    print(f"  Connected to {out.strip()}")
    
    # Step 1: Copy DUPs
    print("\n[1] Copying DUPs to adv02...")
    for filename, label in DUPS_TO_INSTALL:
        print(f"  Copying {label} ({filename})...")
        if copy_dup(filename):
            print(f"    OK")
        else:
            print(f"    FAILED - will try to continue")
    
    # Verify copies
    out, err, rc = remote_cmd("Get-ChildItem C:\\Temp\\*.EXE | Select-Object Name, @{N='MB';E={[math]::Round($_.Length/1MB,1)}} | Format-Table -AutoSize")
    print(f"\n  Files on adv02 C:\\Temp:")
    print(f"  {out}")
    
    # Step 2: Install each DUP
    print("\n[2] Installing DUPs...")
    results = {}
    for filename, label in DUPS_TO_INSTALL:
        print(f"\n  --- {label} ---")
        out, err = install_dup(filename, label)
        print(f"  Output: {out}")
        if err:
            print(f"  Stderr: {err}")
        
        # Parse exit code
        if "EXIT_CODE=" in out:
            code = out.split("EXIT_CODE=")[-1].strip()
            results[label] = code
            code_int = int(code) if code.isdigit() or (code.startswith('-') and code[1:].isdigit()) else code
            if code == "0":
                print(f"  SUCCESS (exit 0)")
            elif code == "2":
                print(f"  SUCCESS - REBOOT REQUIRED (exit 2)")
            elif code == "3":
                print(f"  SUCCESS - SOFT REBOOT REQUIRED (exit 3)")
            elif code == "5":
                print(f"  ALREADY UP TO DATE (exit 5)")
            else:
                print(f"  WARNING: exit code {code}")
        else:
            results[label] = "UNKNOWN"
            print(f"  Could not determine exit code")
    
    # Step 3: Post-install check
    print("\n[3] Post-install NIC status...")
    out, err, rc = remote_cmd(
        "Get-NetAdapter | Select-Object Name, InterfaceDescription, Status, LinkSpeed, DriverVersion | Format-Table -AutoSize"
    )
    print(f"  {out}")
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for label, code in results.items():
        status = {
            "0": "SUCCESS", "2": "REBOOT NEEDED", "3": "SOFT REBOOT NEEDED",
            "5": "ALREADY CURRENT"
        }.get(str(code), f"CODE {code}")
        print(f"  {label}: {status}")
    
    needs_reboot = any(c in ("0", "2", "3") for c in results.values())
    print(f"\n  Broadcom driver (installed earlier): NEEDS REBOOT")
    if needs_reboot:
        print(f"\n  >>> REBOOT RECOMMENDED to activate all driver updates <<<")
        resp = input("  Reboot adv02 now? (y/n): ").strip().lower()
        if resp == 'y':
            print("  Rebooting adv02...")
            remote_cmd("Restart-Computer -Force")
            print("  Reboot initiated. Wait ~3-5 minutes for server to come back.")
        else:
            print("  Skipping reboot. Remember to reboot before deployment.")

if __name__ == "__main__":
    main()
