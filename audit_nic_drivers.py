import subprocess, json

# Check Windows NIC driver versions on both nodes
nodes = [
    ("adv01", "192.168.1.30", "worldai\\gus-admin", "Tricolor00!@#$%^&*("),
    ("adv02", "192.168.1.105", "Administrator", "Tricolor00!@#$"),
]

for name, ip, user, pw in nodes:
    print(f"\n{'='*60}")
    print(f"  {name} ({ip}) - Windows NIC Drivers")
    print(f"{'='*60}")
    
    ps_cmd = f"""
$secPass = ConvertTo-SecureString '{pw}' -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential('{user}', $secPass)
Invoke-Command -ComputerName {ip} -Credential $cred -ScriptBlock {{
    Write-Host "=== Physical Net Adapters ==="
    Get-NetAdapter -Physical -IncludeHidden | ForEach-Object {{
        $drv = Get-NetAdapterAdvancedProperty -Name $_.Name -RegistryKeyword 'DriverVersion' -ErrorAction SilentlyContinue
        $drvInfo = Get-WindowsDriver -Online -Driver $_.DriverFileName -ErrorAction SilentlyContinue
        Write-Host ""
        Write-Host "Name: $($_.Name)"
        Write-Host "  Description: $($_.InterfaceDescription)"
        Write-Host "  Status: $($_.Status)"
        Write-Host "  DriverName: $($_.DriverName)"
        Write-Host "  DriverFileName: $($_.DriverFileName)"
        Write-Host "  DriverVersion: $($_.DriverVersion)"
        Write-Host "  DriverDate: $($_.DriverDate)"
        Write-Host "  DriverProvider: $($_.DriverProvider)"
        Write-Host "  PnpDeviceId: $($_.PnpDeviceId)"
    }}
    
    Write-Host "`n=== All Network PnP Devices (including hidden/disabled) ==="
    Get-PnpDevice -Class Net | Where-Object {{ $_.FriendlyName -like '*Mellanox*' -or $_.FriendlyName -like '*Broadcom*' -or $_.FriendlyName -like '*ConnectX*' -or $_.FriendlyName -like '*NetXtreme*' }} | ForEach-Object {{
        $drvDetail = Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Device_DriverVersion' -ErrorAction SilentlyContinue
        Write-Host ""
        Write-Host "FriendlyName: $($_.FriendlyName)"
        Write-Host "  Status: $($_.Status)"
        Write-Host "  InstanceId: $($_.InstanceId)"
        Write-Host "  DriverVersion: $($drvDetail.Data)"
    }}
}}
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        capture_output=True, text=True, timeout=60
    )
    print(result.stdout)
    if result.stderr:
        # Filter noise
        for line in result.stderr.split('\n'):
            if line.strip() and 'FullyQualifiedErrorId' not in line and 'CategoryInfo' not in line:
                print(f"  WARN: {line.strip()}")
