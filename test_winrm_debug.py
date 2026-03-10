import os, sys, tempfile, subprocess
sys.path.insert(0, os.path.join(os.getcwd(), 'src'))

host = 'adv01.worldai.local'
user = 'worldai\\gus-admin'
password = 'Tricolor00!@#$%^&*('
script = 'hostname'

# Build the same wrapper as remote.py
wrapper = (
    "$ErrorActionPreference = 'Stop'\n"
    f"$pw = ConvertTo-SecureString -String @'\n{password}\n'@ -AsPlainText -Force\n"
    f"$cred = New-Object System.Management.Automation.PSCredential('{user}', $pw)\n"
    f"$result = Invoke-Command -ComputerName '{host}' -Credential $cred "
    f"-ScriptBlock {{ {script} }} -ErrorAction Stop\n"
    "$result | Out-String | Write-Output\n"
)

print("=== Generated wrapper ===")
print(wrapper)
print("=== END ===")

# Write to temp and run
with tempfile.NamedTemporaryFile(mode='w', suffix='.ps1', delete=False, encoding='utf-8') as tmp:
    tmp.write(wrapper)
    tmp_path = tmp.name

print(f"\nTemp file: {tmp_path}")

proc = subprocess.run(
    ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', tmp_path],
    capture_output=True, text=True, timeout=30,
)
print(f"\nReturn code: {proc.returncode}")
print(f"Stdout: {proc.stdout}")
print(f"Stderr: {proc.stderr}")

os.unlink(tmp_path)
