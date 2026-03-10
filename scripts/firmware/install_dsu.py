"""
Install Dell System Update (DSU) on adv01 and use it to detect/apply firmware updates.
Also try on adv02.
"""
import subprocess
import time

ADV01_IP = "192.168.1.30"
ADV01_USER = "worldai\\gus-admin"
ADV01_PASS = 'Tricolor00!@#$%^&*('

ADV02_IP = "192.168.1.105"  
ADV02_USER = "Administrator"
ADV02_PASS = 'Tricolor00!@#$'

def run_winrm(ip, user, pwd, script, timeout=300):
    """Run PowerShell on remote server via WinRM"""
    # Escape for PowerShell
    ps_cmd = f'''$secPass = ConvertTo-SecureString '{pwd}' -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential('{user}', $secPass)
$sess = New-PSSession -ComputerName {ip} -Credential $cred -ErrorAction Stop
try {{
    Invoke-Command -Session $sess -ScriptBlock {{ {script} }} -ErrorAction Stop
}} finally {{
    Remove-PSSession $sess -ErrorAction SilentlyContinue
}}'''
    
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        capture_output=True, text=True, timeout=timeout
    )
    return result.stdout, result.stderr, result.returncode

def main():
    # Step 1: Check if DSU is already installed on adv01
    print("=" * 70)
    print("STEP 1: CHECK DSU STATUS ON ADV01")
    print("=" * 70)
    
    script = '''
    # Check if DSU is installed
    $dsu = Get-Command dsu -ErrorAction SilentlyContinue
    if ($dsu) {
        Write-Host "DSU found at: $($dsu.Source)"
        & dsu --version 2>&1
    } else {
        # Check common install paths
        $paths = @(
            "C:\\Program Files\\Dell\\DELL System Update\\DSU.exe",
            "C:\\Program Files (x86)\\Dell\\DELL System Update\\DSU.exe"
        )
        $found = $false
        foreach ($p in $paths) {
            if (Test-Path $p) {
                Write-Host "DSU found at: $p"
                & $p --version 2>&1
                $found = $true
                break
            }
        }
        if (-not $found) {
            Write-Host "DSU NOT INSTALLED"
        }
    }
    
    # Also check internet connectivity
    Write-Host "`n--- Internet connectivity ---"
    try {
        $r = Invoke-WebRequest -Uri "https://dl.dell.com" -Method Head -TimeoutSec 10 -UseBasicParsing
        Write-Host "dl.dell.com reachable: $($r.StatusCode)"
    } catch {
        Write-Host "dl.dell.com: $($_.Exception.Message)"
    }
    
    # Check current BIOS version
    Write-Host "`n--- BIOS Version ---"
    Get-WmiObject Win32_BIOS | Select-Object SMBIOSBIOSVersion, ReleaseDate | Format-List
    '''
    
    print("\nRunning on adv01...")
    out, err, rc = run_winrm(ADV01_IP, ADV01_USER, ADV01_PASS, script)
    print(out)
    if err:
        print(f"STDERR: {err[:500]}")
    
    print("\n" + "=" * 70)
    print("STEP 2: CHECK DSU STATUS ON ADV02")
    print("=" * 70)
    
    print("\nRunning on adv02...")
    out2, err2, rc2 = run_winrm(ADV02_IP, ADV02_USER, ADV02_PASS, script)
    print(out2)
    if err2:
        print(f"STDERR: {err2[:500]}")
    
    # Step 3: Download and install DSU if not found 
    print("\n" + "=" * 70)
    print("STEP 3: INSTALL DSU ON ADV01 (if needed)")
    print("=" * 70)
    
    install_script = '''
    $dsuPath = "C:\\Program Files\\Dell\\DELL System Update\\DSU.exe"
    if (Test-Path $dsuPath) {
        Write-Host "DSU already installed"
    } else {
        Write-Host "Downloading DSU..."
        $dsuUrl = "https://dl.dell.com/FOLDER14217017M/1/Systems-Management_Application_RXKJ5_WN64_2.2.0.1_A00.EXE"
        $downloadPath = "C:\\Temp\\DSU_Install.exe"
        New-Item -ItemType Directory -Force -Path "C:\\Temp" | Out-Null
        
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $dsuUrl -OutFile $downloadPath -UseBasicParsing
        Write-Host "Downloaded: $(Get-Item $downloadPath | Select-Object -ExpandProperty Length) bytes"
        
        Write-Host "Installing DSU silently..."
        $proc = Start-Process $downloadPath -ArgumentList "/s" -Wait -PassThru
        Write-Host "Install exit code: $($proc.ExitCode)"
        
        if (Test-Path $dsuPath) {
            Write-Host "DSU installed successfully!"
        } else {
            Write-Host "DSU install may have failed, checking..."
            Get-ChildItem "C:\\Program Files\\Dell\\" -Recurse -Filter "DSU*" -ErrorAction SilentlyContinue
        }
    }
    '''
    
    print("\nInstalling DSU on adv01...")
    out3, err3, rc3 = run_winrm(ADV01_IP, ADV01_USER, ADV01_PASS, install_script, timeout=600)
    print(out3)
    if err3:
        print(f"STDERR: {err3[:500]}")

    # Step 4: Run DSU preview (detect available updates only)
    print("\n" + "=" * 70)
    print("STEP 4: DSU PREVIEW (detect available updates)")
    print("=" * 70)
    
    preview_script = '''
    $dsuPath = "C:\\Program Files\\Dell\\DELL System Update\\DSU.exe"
    if (-not (Test-Path $dsuPath)) {
        Write-Host "DSU not found!"
        return
    }
    
    Write-Host "Running DSU preview (detecting available updates)..."
    # --preview flag shows what would be updated without applying
    $result = & $dsuPath --preview --non-interactive 2>&1
    $result | ForEach-Object { Write-Host $_ }
    Write-Host "`nDSU preview exit code: $LASTEXITCODE"
    '''
    
    print("\nRunning DSU preview on adv01...")
    out4, err4, rc4 = run_winrm(ADV01_IP, ADV01_USER, ADV01_PASS, preview_script, timeout=600)
    print(out4)
    if err4:
        print(f"STDERR: {err4[:500]}")

if __name__ == "__main__":
    main()
