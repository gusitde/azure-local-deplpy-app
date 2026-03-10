import requests, urllib3, json, time
urllib3.disable_warnings()

s = requests.Session()
s.auth = ('root', 'Tricolor00!')
s.verify = False
base = 'https://192.168.10.4'

# Check the exact schema for GetRepoBasedUpdateList
print("=== Checking action parameters ===")
r = s.get(f'{base}/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellSoftwareInstallationService')
if r.ok:
    d = r.json()
    actions = d.get('Actions', {})
    for k, v in actions.items():
        print(f"\n{k}:")
        print(json.dumps(v, indent=2))

# Also try InstallFromRepository with Dell HTTPS catalog
print("\n\n=== Trying InstallFromRepository ===")
# This requires proper parameter names
# Let's try to match what the schema expects
payload = {
    "IPAddress": "downloads.dell.com",
    "ShareType": "HTTPS",
    "ShareName": "catalog",
    "CatalogFile": "Catalog.xml.gz",
    "ApplyUpdate": "True",
    "RebootNeeded": "True"
}

r2 = s.post(
    f'{base}/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellSoftwareInstallationService/Actions/DellSoftwareInstallationService.InstallFromRepository',
    json=payload
)
print(f"InstallFromRepository: {r2.status_code}")
if r2.status_code in [200, 202]:
    print(json.dumps(r2.json(), indent=2)[:2000])
    if r2.status_code == 202:
        print(f"Task: {r2.headers.get('Location', '')}")
else:
    print(f"Error: {r2.text[:1000]}")

# Alternative: Try with just the URI
print("\n\n=== Trying InstallFromURI ===")
# Dell R640 BIOS 2.24.0 DUP
# The typical DUP path is like: https://downloads.dell.com/FOLDER0XXXXX/1/BIOS_XXXXX_WN64_2.24.0.EXE
# Let's try to find it via catalog first

# Let's also try the SimpleUpdate with a known Dell BIOS URL
# First, try to use racadm to get the update list
print("\n=== Using racadm for update check ===")
import paramiko
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.10.4', username='root', password='Tricolor00!', timeout=30)

# Try racadm update
cmds = [
    'racadm update -f catalog.xml.gz -e downloads.dell.com -t HTTPS -a FALSE',
    'racadm help update',
]

for cmd in cmds:
    print(f"\n> {cmd}")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
    out = stdout.read().decode()
    err = stderr.read().decode()
    if out:
        print(out.strip()[:2000])
    if err:
        print(f"ERR: {err.strip()[:500]}")

client.close()
