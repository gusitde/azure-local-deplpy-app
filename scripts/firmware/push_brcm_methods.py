"""Try multiple methods to push Broadcom FW to adv01."""
import requests, urllib3, json, time, os
urllib3.disable_warnings()

IP = '192.168.10.4'
AUTH = ('root', 'Tricolor00!')
DUP_FILE = r"C:\Users\gus\Documents\GitHub\azure-local-deplpy-app\dups\Network_Firmware_HVN2R_WN64_23.31.18.10_01.EXE"
DUP_SMALL = r"C:\Users\gus\Documents\GitHub\azure-local-deplpy-app\dups\Network_Firmware_5V215_WN64_23.31.1.EXE"

def try_multipart(dup_path, label):
    print(f"\n{'='*60}")
    print(f"Method 1: MultipartUpload - {label}")
    print(f"{'='*60}")
    with open(dup_path, 'rb') as f:
        files = {
            'UpdateParameters': (None, json.dumps({"Targets": [], "@Redfish.OperationApplyTime": "Immediate"}), 'application/json'),
            'UpdateFile': (os.path.basename(dup_path), f, 'application/octet-stream')
        }
        r = requests.post(
            f'https://{IP}/redfish/v1/UpdateService/MultipartUpload',
            auth=AUTH, verify=False, timeout=300,
            files=files
        )
    print(f"  Status: {r.status_code}")
    try:
        data = r.json()
        if r.status_code in (200, 202):
            print(f"  Success! {json.dumps(data, indent=2)[:500]}")
            return True
        else:
            for m in data.get("error", {}).get("@Message.ExtendedInfo", []):
                print(f"  ERROR: {m.get('Message', '')}")
                print(f"  Resolution: {m.get('Resolution', '')}")
    except:
        print(f"  Response: {r.text[:500]}")
    return False

def try_oem_install(dup_url, label):
    print(f"\n{'='*60}")
    print(f"Method 2: OEM DellUpdateService.Install - {label}")
    print(f"{'='*60}")
    
    # First check if the OEM action exists
    r = requests.get(f'https://{IP}/redfish/v1/UpdateService', auth=AUTH, verify=False, timeout=30)
    actions = r.json().get('Actions', {})
    oem_actions = actions.get('Oem', {})
    print(f"  Available OEM actions: {list(oem_actions.keys())}")
    
    if 'DellUpdateService.Install' in str(oem_actions) or '#DellUpdateService.Install' in str(oem_actions):
        install_target = None
        for k, v in oem_actions.items():
            if 'Install' in k:
                install_target = v.get('target', '')
                break
        
        if install_target:
            print(f"  Target: {install_target}")
            payload = [
                {"URI": dup_url}
            ]
            r2 = requests.post(
                f'https://{IP}{install_target}',
                auth=AUTH, verify=False, timeout=120,
                headers={"Content-Type": "application/json"},
                json=payload
            )
            print(f"  Status: {r2.status_code}")
            try:
                print(f"  Response: {json.dumps(r2.json(), indent=2)[:500]}")
            except:
                print(f"  Response: {r2.text[:500]}")
            return r2.status_code in (200, 202)
    else:
        print("  OEM Install action not found")
    return False

def try_http_push(dup_path, label):
    print(f"\n{'='*60}")
    print(f"Method 3: HttpPushUri Upload - {label}")
    print(f"{'='*60}")
    
    # Get HttpPushUri
    r = requests.get(f'https://{IP}/redfish/v1/UpdateService', auth=AUTH, verify=False, timeout=30)
    push_uri = r.json().get('HttpPushUri', '')
    print(f"  HttpPushUri: {push_uri}")
    
    if push_uri:
        with open(dup_path, 'rb') as f:
            dup_data = f.read()
        
        # Try with different content types
        for ct in ['application/octet-stream', 'multipart/form-data']:
            print(f"  Trying Content-Type: {ct}")
            headers = {'Content-Type': ct}
            r2 = requests.post(
                f'https://{IP}{push_uri}',
                auth=AUTH, verify=False, timeout=300,
                headers=headers,
                data=dup_data
            )
            print(f"    Status: {r2.status_code}")
            try:
                resp = r2.json()
                if r2.status_code in (200, 202):
                    print(f"    Success! {json.dumps(resp, indent=2)[:500]}")
                    return True
                else:
                    for m in resp.get("error", {}).get("@Message.ExtendedInfo", []):
                        print(f"    ERROR: {m.get('Message', '')}")
            except:
                print(f"    Response: {r2.text[:300]}")
    return False

# Also check NIC attributes endpoint
print("=" * 60)
print("Checking NIC.Integrated.1 attributes availability")
print("=" * 60)
for ep in [
    '/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Integrated.1',
    '/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions',
    '/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1',
    '/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions',
]:
    r = requests.get(f'https://{IP}{ep}', auth=AUTH, verify=False, timeout=15)
    if r.ok:
        d = r.json()
        print(f"  {ep}")
        if 'Members' in d:
            for m in d['Members']:
                print(f"    -> {m.get('@odata.id')}")
        else:
            print(f"    {json.dumps({k:v for k,v in d.items() if k in ('Id','Model','Status','Controllers','@odata.id')}, indent=4)}")

# Check DellNetworkAttributes 
for nic_id in ['NIC.Integrated.1-1-1', 'NIC.Integrated.1-2-1']:
    ep = f'/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{nic_id}/Oem/Dell/DellNetworkAttributes/{nic_id}'
    r = requests.get(f'https://{IP}{ep}', auth=AUTH, verify=False, timeout=15)
    print(f"\n  DellNetworkAttributes/{nic_id}: {r.status_code}")
    if r.ok:
        d = r.json()
        attrs = d.get('Attributes', {})
        print(f"    Attribute count: {len(attrs)}")
        for k in ['NicMode', 'FirmwareVersion', 'PCIDeviceID', 'ChipMdl']:
            if k in attrs:
                print(f"    {k}: {attrs[k]}")

# Try the approaches
HTTP_BASE = 'http://192.168.10.201:8089'

# Method 1: MultipartUpload with big DUP
try_multipart(DUP_FILE, "23.31.18.10")

# Method 1b: MultipartUpload with smaller DUP  
if os.path.exists(DUP_SMALL):
    try_multipart(DUP_SMALL, "23.31.1")

# Method 2: OEM Install
try_oem_install(f"{HTTP_BASE}/Network_Firmware_HVN2R_WN64_23.31.18.10_01.EXE", "23.31.18.10")

# Method 3: HttpPushUri
try_http_push(DUP_FILE, "23.31.18.10")
