"""Explore Dell OEM update endpoints and try force install."""
import requests, urllib3, json
urllib3.disable_warnings()

IP = '192.168.10.4'
AUTH = ('root', 'Tricolor00!')
HTTP_BASE = 'http://192.168.10.201:8089'

def jget(path):
    r = requests.get(f'https://{IP}{path}', auth=AUTH, verify=False, timeout=30)
    return r.json() if r.ok else None

# 1. Check UpdateService details
print("=" * 60)
print("UpdateService Full Details")
print("=" * 60)
us = jget('/redfish/v1/UpdateService')
if us:
    for k in ['Actions', 'HttpPushUri', 'FirmwareInventory', 'SoftwareInventory']:
        if k in us:
            print(f"\n  {k}:")
            print(f"    {json.dumps(us[k], indent=4)[:600]}")

# 2. Check Dell Software Inventory
print("\n" + "=" * 60)
print("Software Inventory (looking for Broadcom)")
print("=" * 60)
si = jget('/redfish/v1/UpdateService/SoftwareInventory')
if si:
    for m in si.get('Members', []):
        spath = m.get('@odata.id', '')
        sd = jget(spath)
        if sd:
            sname = sd.get('Name', '')
            sid = sd.get('Id', '')
            sver = sd.get('Version', '')
            if 'broadcom' in sname.lower() or 'brcm' in sname.lower() or 'integrated' in sid.lower() or '14e4' in str(sd).lower():
                print(f"  MATCH: {sid} - {sname} v{sver}")
                print(f"    Full: {json.dumps(sd, indent=4)[:400]}")
            # Also print all for overview
            print(f"  {sid[:55]:56s} {sname[:40]:41s} v{sver}")

# 3. Check Dell Lifecycle Controller endpoints
print("\n" + "=" * 60)
print("Dell Lifecycle Controller Service")
print("=" * 60)
for ep in [
    '/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService',
    '/redfish/v1/Dell/Managers/iDRAC.Embedded.1/DellLCService',
]:
    d = jget(ep)
    if d:
        actions = d.get('Actions', {})
        print(f"  Endpoint: {ep}")
        for k, v in actions.items():
            if '#' in k:
                print(f"    {k}: target={v.get('target','')}")

# 4. Check Dell Software Installation Service
print("\n" + "=" * 60)
print("Dell Software Installation Service")
print("=" * 60)
for ep in [
    '/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellSoftwareInstallationService',
    '/redfish/v1/Dell/Managers/iDRAC.Embedded.1/DellSoftwareInstallationService',
]:
    d = jget(ep)
    if d:
        actions = d.get('Actions', {})
        print(f"  Endpoint: {ep}")
        for k, v in actions.items():
            if '#' in k:
                print(f"    {k}: target={v.get('target','')}")
                if 'AllowableValues' in str(v):
                    print(f"      {json.dumps(v, indent=4)[:300]}")

# 5. Try DellUpdateService.Install with correct payload
print("\n" + "=" * 60)
print("Trying DellUpdateService.Install with correct format")
print("=" * 60)
dup_url = f"{HTTP_BASE}/Network_Firmware_HVN2R_WN64_23.31.18.10_01.EXE"

# Format 1: Simple URI string
payload1 = {"URIPath": dup_url}
r = requests.post(
    f'https://{IP}/redfish/v1/UpdateService/Actions/Oem/DellUpdateService.Install',
    auth=AUTH, verify=False, timeout=60,
    json=payload1
)
print(f"  Format 1 (URIPath): {r.status_code}")
try:
    for m in r.json().get("error", {}).get("@Message.ExtendedInfo", []):
        print(f"    {m.get('Message', '')[:200]}")
except: pass

# Format 2: URI list
payload2 = {"SoftwareIdentityURIs": [dup_url]}
r = requests.post(
    f'https://{IP}/redfish/v1/UpdateService/Actions/Oem/DellUpdateService.Install',
    auth=AUTH, verify=False, timeout=60,
    json=payload2
)
print(f"  Format 2 (SoftwareIdentityURIs): {r.status_code}")
try:
    for m in r.json().get("error", {}).get("@Message.ExtendedInfo", []):
        print(f"    {m.get('Message', '')[:200]}")
except: pass
