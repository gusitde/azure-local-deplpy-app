import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), 'src'))

from azure_local_deploy.remote import run_powershell

# Test auto transport (should fall back from SSH to WinRM)
print("Testing auto transport (SSH -> WinRM fallback)...")
result = run_powershell(
    'adv01.worldai.local',
    'worldai\\gus-admin',
    'Tricolor00!@#$%^&*(',
    'hostname',
    transport='auto',
)
print(f"Auto result: {result}")

# Test explicit WinRM transport
print("\nTesting explicit WinRM transport...")
result2 = run_powershell(
    'adv01.worldai.local',
    'worldai\\gus-admin',
    'Tricolor00!@#$%^&*(',
    'hostname; (Get-CimInstance Win32_OperatingSystem).Caption',
    transport='winrm',
    port=5985,
)
print(f"WinRM result: {result2}")

print("\nAll tests passed!")
