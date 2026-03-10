"""Query adv01 network config via SSH."""
from azure_local_deploy.remote import run_powershell

host = "192.168.1.30"
user = "Administrator"
pwd = "Tricolor00!@#$"

def ps(script):
    return run_powershell(host, user, pwd, script, port=22)

print("Querying adv01 network config...")
print()

# 1. Network adapters
result = ps("Get-NetAdapter | Where-Object Status -eq Up | Select-Object Name, InterfaceDescription, MacAddress, Status, LinkSpeed | Format-Table -AutoSize")
print("=== Network Adapters ===")
print(result)
print()

# 2. IP addresses
result2 = ps("Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' } | Select-Object InterfaceAlias, IPAddress, PrefixLength | Format-Table -AutoSize")
print("=== IP Addresses ===")
print(result2)
print()

# 3. Default gateway
result3 = ps("Get-NetRoute -DestinationPrefix 0.0.0.0/0 -ErrorAction SilentlyContinue | Select-Object InterfaceAlias, NextHop | Format-Table -AutoSize")
print("=== Default Gateway ===")
print(result3)
print()

# 4. DNS
result4 = ps("Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object ServerAddresses | Select-Object InterfaceAlias, ServerAddresses | Format-Table -AutoSize")
print("=== DNS Servers ===")
print(result4)
print()

# 5. VLANs
result5 = ps("Get-NetAdapterAdvancedProperty -DisplayName 'VLAN ID' -ErrorAction SilentlyContinue | Select-Object Name, DisplayValue | Format-Table -AutoSize")
print("=== VLANs ===")
print(result5)
print()

# 6. Hostname and OS version
result6 = ps("$env:COMPUTERNAME; (Get-CimInstance Win32_OperatingSystem).Version; (Get-CimInstance Win32_OperatingSystem).Caption")
print("=== Hostname & OS ===")
print(result6)
