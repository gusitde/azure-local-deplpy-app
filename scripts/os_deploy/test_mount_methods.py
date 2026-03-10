"""Test all mount methods after firewall fix."""
import requests
import urllib3
import paramiko
import socket
import time

urllib3.disable_warnings()
AUTH = ("root", "Tricolor00!")
IP = "192.168.10.6"

# Test 1: Can iDRAC reach us at all? Try HTTP InsertMedia
print("=" * 50)
print("Test 1: HTTP InsertMedia")
r = requests.post(
    f"https://{IP}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.InsertMedia",
    auth=AUTH, verify=False, timeout=30,
    json={"Image": "http://192.168.10.201:8089/AzureLocal24H2.26100.1742.LCM.12.2602.0.3018.x64.en-us.iso"}
)
print(f"  Status: {r.status_code}")
err_msg = r.json().get("error", {}).get("@Message.ExtendedInfo", [{}])[0].get("MessageId", "")
print(f"  MessageId: {err_msg}")

# Test 2: CIFS with different cred formats
print("\n" + "=" * 50)
cifs_tests = [
    {"desc": "No credentials", "Image": "//192.168.10.201/ald-iso/AzureLocal24H2.26100.1742.LCM.12.2602.0.3018.x64.en-us.iso"},
    {"desc": "MACHINE\\user", "Image": "//192.168.10.201/ald-iso/AzureLocal24H2.26100.1742.LCM.12.2602.0.3018.x64.en-us.iso",
     "UserName": "WORLDAI\\gus-admin", "Password": r"Tricolor00!@#$%^&*("},
    {"desc": "UPN format", "Image": "//192.168.10.201/ald-iso/AzureLocal24H2.26100.1742.LCM.12.2602.0.3018.x64.en-us.iso",
     "UserName": "gus-admin@worldai.local", "Password": r"Tricolor00!@#$%^&*("},
    {"desc": "local user gus", "Image": "//192.168.10.201/ald-iso/AzureLocal24H2.26100.1742.LCM.12.2602.0.3018.x64.en-us.iso",
     "UserName": "gus", "Password": r"Tricolor00!@#$%^&*("},
]

for test in cifs_tests:
    desc = test.pop("desc")
    print(f"\nTest CIFS: {desc}")
    # Eject first
    requests.post(
        f"https://{IP}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.EjectMedia",
        auth=AUTH, verify=False, timeout=10, json={})
    time.sleep(1)
    
    r = requests.post(
        f"https://{IP}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.InsertMedia",
        auth=AUTH, verify=False, timeout=30, json=test)
    print(f"  Status: {r.status_code}")
    if r.status_code in (200, 204):
        print(f"  SUCCESS!")
        break
    else:
        info = r.json().get("error", {}).get("@Message.ExtendedInfo", [{}])[0]
        print(f"  {info.get('MessageId', '')}: {info.get('Message', '')[:150]}")

# Test 3: Try using Redfish SimpleUpdate to test HTTP connectivity from iDRAC
print("\n" + "=" * 50)
print("Test 3: SimpleUpdate to test HTTP connectivity from iDRAC")
# Just use a HEAD-like test - point to a small test file
r = requests.post(
    f"https://{IP}/redfish/v1/UpdateService/Actions/UpdateService.SimpleUpdate",
    auth=AUTH, verify=False, timeout=30,
    json={"ImageURI": "http://192.168.10.201:8089/Catalog.xml.gz", "TransferProtocol": "HTTP"}
)
print(f"  Status: {r.status_code}")
# This should at least create a job (even if it fails later), showing HTTP connectivity
if r.status_code in (200, 202):
    location = r.headers.get("Location", "")
    print(f"  Job created: {location}")
    print("  This proves iDRAC CAN reach our HTTP server!")
else:
    info = r.json().get("error", {}).get("@Message.ExtendedInfo", [{}])[0]
    print(f"  {info.get('MessageId', '')}: {info.get('Message', '')[:200]}")
