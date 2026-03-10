$pw = @'
Tricolor00!@#$%^&*(
'@
$secPass = ConvertTo-SecureString $pw -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential('worldai\gus-admin', $secPass)

Write-Host "Querying adv01 network configuration..."
$output = Invoke-Command -ComputerName adv01.worldai.local -Credential $cred -ScriptBlock {
    Write-Output "=== HOSTNAME ==="
    hostname

    Write-Output "`n=== OS VERSION ==="
    (Get-CimInstance Win32_OperatingSystem).Caption

    Write-Output "`n=== PHYSICAL NETWORK ADAPTERS ==="
    Get-NetAdapter -Physical | Select-Object Name, InterfaceDescription, Status, MacAddress, LinkSpeed | Format-Table -AutoSize | Out-String

    Write-Output "`n=== ALL NETWORK ADAPTERS ==="
    Get-NetAdapter | Select-Object Name, InterfaceDescription, Status, MacAddress, LinkSpeed, VlanID | Format-Table -AutoSize | Out-String

    Write-Output "`n=== IP ADDRESSES (IPv4) ==="
    Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' } | Select-Object InterfaceAlias, IPAddress, PrefixLength | Format-Table -AutoSize | Out-String

    Write-Output "`n=== DEFAULT GATEWAY ==="
    Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object InterfaceAlias, NextHop, RouteMetric | Format-Table -AutoSize | Out-String

    Write-Output "`n=== DNS SERVERS ==="
    Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object { $_.ServerAddresses.Count -gt 0 } | Select-Object InterfaceAlias, ServerAddresses | Format-Table -AutoSize | Out-String

    Write-Output "`n=== VLAN IDs (Advanced Property) ==="
    Get-NetAdapterAdvancedProperty -DisplayName 'VLAN ID' -ErrorAction SilentlyContinue | Select-Object Name, DisplayValue | Format-Table -AutoSize | Out-String

    Write-Output "`n=== NET INTENT (HCI Network Intent) ==="
    try { Get-NetIntent -ErrorAction Stop | Format-List | Out-String } catch { Write-Output "Not available: $_" }

    Write-Output "`n=== VIRTUAL SWITCHES ==="
    Get-VMSwitch -ErrorAction SilentlyContinue | Select-Object Name, SwitchType, NetAdapterInterfaceDescription | Format-Table -AutoSize | Out-String

    Write-Output "`n=== HOST vNICs ==="
    Get-VMNetworkAdapter -ManagementOS -ErrorAction SilentlyContinue | Select-Object Name, SwitchName, MacAddress, IPAddresses, VlanSetting | Format-Table -AutoSize | Out-String

    Write-Output "`n=== STORAGE / SMB NICs ==="
    Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -like '*storage*' -or $_.InterfaceAlias -like '*SMB*' -or $_.InterfaceAlias -like '*RDMA*' } | Select-Object InterfaceAlias, IPAddress, PrefixLength | Format-Table -AutoSize | Out-String

    Write-Output "`n=== CLUSTER INFO ==="
    try {
        $cluster = Get-Cluster -ErrorAction Stop
        Write-Output "Cluster Name: $($cluster.Name)"
        Get-ClusterNode -ErrorAction Stop | Select-Object Name, State | Format-Table -AutoSize | Out-String
        Get-ClusterNetwork -ErrorAction Stop | Select-Object Name, Address, AddressMask, Role | Format-Table -AutoSize | Out-String
    } catch { Write-Output "Not available: $_" }

    Write-Output "`n=== NIC TEAMING ==="
    Get-NetLbfoTeam -ErrorAction SilentlyContinue | Format-List | Out-String

    Write-Output "`n=== SET (Switch Embedded Teaming) ==="
    Get-VMSwitch -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Output "Switch: $($_.Name)"
        Write-Output "  Team Members: $($_.NetAdapterInterfaceDescriptions -join ', ')"
    }
} -ErrorAction Stop

$output | Out-File -FilePath .\adv01_network_config.txt -Encoding UTF8
$output | Out-String | Write-Host
Write-Host "`nSCRIPT DONE - saved to adv01_network_config.txt"
