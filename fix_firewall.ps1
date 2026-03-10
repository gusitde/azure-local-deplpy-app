# Run as admin to fix firewall
New-NetFirewallRule -DisplayName "ALD-SMB-iDRAC" -Direction Inbound -Protocol TCP -LocalPort 445 -Action Allow -Profile Any -Description "Allow SMB for iDRAC ISO sharing"
Write-Host "SMB rule created"

# Also ensure HTTP port is explicitly allowed
# (ALD-ISO-Server exists but let's verify)
$existing = Get-NetFirewallRule -DisplayName "ALD-ISO-Server" -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "ALD-ISO-Server rule already exists"
} else {
    New-NetFirewallRule -DisplayName "ALD-ISO-Server" -Direction Inbound -Protocol TCP -LocalPort 8089 -Action Allow -Profile Any
    Write-Host "HTTP rule created"
}

# List the rules
Get-NetFirewallRule -DisplayName "ALD-*" | Select-Object DisplayName, Enabled, Direction, Action | Format-Table -AutoSize

# Also set the Ethernet adapter (iDRAC VLAN) to Private profile to enable file sharing
Set-NetConnectionProfile -InterfaceAlias "Ethernet" -NetworkCategory Private -ErrorAction SilentlyContinue
Write-Host "Network profile changed to Private"

Get-NetConnectionProfile | Format-Table InterfaceAlias, NetworkCategory -AutoSize
