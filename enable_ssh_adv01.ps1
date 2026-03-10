$pw = @'
Tricolor00!@#$%^&*(
'@
$secPass = ConvertTo-SecureString $pw -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential('worldai\gus-admin', $secPass)

Write-Host "Enabling OpenSSH Server on adv01..."
$result = Invoke-Command -ComputerName adv01.worldai.local -Credential $cred -ScriptBlock {
    # Check if OpenSSH Server is already installed
    $sshd = Get-WindowsCapability -Online | Where-Object { $_.Name -like 'OpenSSH.Server*' }
    Write-Output "Current SSH Server status: $($sshd.State)"
    
    if ($sshd.State -ne 'Installed') {
        Write-Output "Installing OpenSSH Server..."
        Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
        Write-Output "OpenSSH Server installed."
    }
    
    # Start and enable the service
    Start-Service sshd -ErrorAction SilentlyContinue
    Set-Service -Name sshd -StartupType Automatic
    
    # Configure firewall rule
    $rule = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
    if (-not $rule) {
        New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
        Write-Output "Firewall rule created."
    } else {
        Write-Output "Firewall rule already exists."
    }

    # Set PowerShell as default shell for SSH
    New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force | Out-Null
    Write-Output "Default SSH shell set to PowerShell."
    
    # Verify
    $svc = Get-Service sshd
    Write-Output "sshd service status: $($svc.Status)"
    Write-Output "sshd startup type: $($svc.StartType)"
} -ErrorAction Stop

$result | ForEach-Object { Write-Host $_ }
Write-Host "`nDONE"
