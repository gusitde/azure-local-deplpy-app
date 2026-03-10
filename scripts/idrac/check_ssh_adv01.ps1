$pw = @'
Tricolor00!@#$%^&*(
'@
$secPass = ConvertTo-SecureString $pw -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential('worldai\gus-admin', $secPass)

$result = Invoke-Command -ComputerName adv01.worldai.local -Credential $cred -ScriptBlock {
    Write-Output "=== SSHD Service ==="
    Get-Service sshd | Select-Object Status, StartType | Format-Table | Out-String

    Write-Output "=== Listening on port 22 ==="
    netstat -an | Select-String ':22 ' | Out-String

    Write-Output "=== Firewall rules for SSH ==="
    Get-NetFirewallRule -Direction Inbound | Where-Object { $_.DisplayName -like '*SSH*' -or $_.DisplayName -like '*OpenSSH*' } | Select-Object Name, DisplayName, Enabled, Action | Format-Table -AutoSize | Out-String

    Write-Output "=== sshd_config ==="
    if (Test-Path 'C:\ProgramData\ssh\sshd_config') {
        Get-Content 'C:\ProgramData\ssh\sshd_config' | Where-Object { $_ -notmatch '^\s*#' -and $_ -match '\S' } | Out-String
    } else {
        Write-Output "sshd_config not found"
    }
} -ErrorAction Stop

$result | ForEach-Object { Write-Host $_ }
Write-Host "DONE"
