"""
Try multiple approaches to enable Broadcom NicMode on adv01.
The Broadcom is NIC.Integrated.1 (rNDC), both ports show LinkStatus=Up.
"""
import requests
import json
import time
import urllib3
urllib3.disable_warnings()

IDRAC_IP = "192.168.10.4"
IDRAC_USER = "root"
IDRAC_PASS = "Tricolor00!"
BASE = f"https://{IDRAC_IP}"
AUTH = (IDRAC_USER, IDRAC_PASS)
HEADERS = {"Content-Type": "application/json"}

def get(path):
    r = requests.get(f"{BASE}{path}", auth=AUTH, verify=False, timeout=30)
    return r

def patch(path, data):
    r = requests.patch(f"{BASE}{path}", auth=AUTH, verify=False, timeout=30,
                       headers=HEADERS, json=data)
    return r

def post(path, data=None):
    r = requests.post(f"{BASE}{path}", auth=AUTH, verify=False, timeout=30,
                      headers=HEADERS, json=data or {})
    return r

def delete(path):
    r = requests.delete(f"{BASE}{path}", auth=AUTH, verify=False, timeout=30)
    return r

# ============================================================
# 1. Check current NIC attributes for NIC.Integrated.1
# ============================================================
print("=" * 70)
print("APPROACH 0: Check NIC attribute endpoints")
print("=" * 70)

# Try different attribute endpoints
endpoints = [
    "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Integrated.1",
    "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Integrated.1",
    "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions",
    "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkPorts",
]

for ep in endpoints:
    r = get(ep)
    print(f"\n  GET {ep}")
    print(f"  Status: {r.status_code}")
    if r.ok:
        data = r.json()
        if "Members" in data:
            print(f"  Members: {[m.get('@odata.id', m) for m in data['Members']]}")
        elif "Model" in data:
            print(f"  Model: {data.get('Model')}")
            print(f"  Status: {data.get('Status')}")
            if "Oem" in data:
                dell = data["Oem"].get("Dell", {})
                if "DellNIC" in dell:
                    print(f"  DellNIC: {json.dumps(dell['DellNIC'], indent=4)}")
                if "DellNICCapabilities" in dell:
                    print(f"  DellNICCapabilities: {json.dumps(dell['DellNICCapabilities'], indent=4)}")

# ============================================================
# 2. Check DellNICAttributes 
# ============================================================
print("\n" + "=" * 70)
print("APPROACH 1: Check DellNICAttributes for integrated NIC")
print("=" * 70)

# Try both NIC.Integrated.1-1-1 and NIC.Integrated.1-2-1
for nic_id in ["NIC.Integrated.1-1-1", "NIC.Integrated.1-2-1"]:
    path = f"/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{nic_id}/Oem/Dell/DellNetworkAttributes/{nic_id}"
    r = get(path)
    print(f"\n  GET {path}")
    print(f"  Status: {r.status_code}")
    if r.ok:
        data = r.json()
        attrs = data.get("Attributes", {})
        print(f"  Number of attributes: {len(attrs)}")
        # Print NicMode and key attributes
        for key in sorted(attrs.keys()):
            if any(k in key.lower() for k in ["nicmode", "mode", "pci", "enable", "disable", "partition"]):
                print(f"    {key} = {attrs[key]}")
    else:
        # Try with shorter path
        for alt in [
            f"/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellAttributes/{nic_id}",
            f"/redfish/v1/Registries/NetworkAttributesRegistry_{nic_id}",
        ]:
            r2 = get(alt)
            print(f"  ALT GET {alt} -> {r2.status_code}")
            if r2.ok:
                data2 = r2.json()
                if "Attributes" in data2:
                    attrs = data2["Attributes"]
                    print(f"    Number of attributes: {len(attrs)}")
                    for key in sorted(attrs.keys()):
                        if any(k in key.lower() for k in ["nicmode", "mode", "pci"]):
                            print(f"      {key} = {attrs[key]}")

# ============================================================
# 3. Try Lifecycle Controller ClearPending + SetAttribute
# ============================================================
print("\n" + "=" * 70)
print("APPROACH 2: Lifecycle Controller NIC attribute via Dell.NICService")
print("=" * 70)

# Check for Dell NIC service actions
nic_service_paths = [
    "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellNICService",
    "/redfish/v1/Dell/Systems/System.Embedded.1/DellNICService",
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellNICService",
]

for path in nic_service_paths:
    r = get(path)
    print(f"\n  GET {path} -> {r.status_code}")
    if r.ok:
        data = r.json()
        actions = data.get("Actions", {})
        print(f"  Actions: {json.dumps(actions, indent=4)}")

# ============================================================
# 4. Try Lifecycle Controller job-based approach
# ============================================================
print("\n" + "=" * 70)
print("APPROACH 3: Check for pending/scheduled jobs and LC status")
print("=" * 70)

# Check LC status
r = get("/redfish/v1/Dell/Managers/iDRAC.Embedded.1/DellLCService")
print(f"\n  DellLCService: {r.status_code}")
if r.ok:
    data = r.json()
    actions = data.get("Actions", {})
    for action_name, action_data in actions.items():
        if "target" in action_data:
            print(f"  Action: {action_name} -> {action_data['target']}")

# Check job queue
r = get("/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs?$top=5")
if not r.ok:
    r = get("/redfish/v1/Managers/iDRAC.Embedded.1/Jobs?$top=5")
print(f"\n  Jobs: {r.status_code}")
if r.ok:
    data = r.json()
    members = data.get("Members", [])
    print(f"  Total jobs: {data.get('Members@odata.count', len(members))}")
    for job in members[:5]:
        jid = job.get("Id", "?")
        jname = job.get("Name", "?")
        jstate = job.get("JobState", "?")
        jmsg = job.get("Message", "?")
        print(f"    {jid}: {jname} - {jstate} - {jmsg}")

# ============================================================
# 5. Try iDRAC Manager Attributes for NIC configuration
# ============================================================
print("\n" + "=" * 70)
print("APPROACH 4: iDRAC Manager attributes for NIC")
print("=" * 70)

r = get("/redfish/v1/Managers/iDRAC.Embedded.1/Attributes")
if r.ok:
    data = r.json()
    attrs = data.get("Attributes", {})
    nic_attrs = {k: v for k, v in attrs.items() if any(x in k.lower() for x in ["nic", "network", "lom"])}
    print(f"  NIC-related iDRAC attributes ({len(nic_attrs)}):")
    for k, v in sorted(nic_attrs.items()):
        print(f"    {k} = {v}")

# ============================================================
# 6. Try to set NicMode via BIOS attribute patch 
# ============================================================
print("\n" + "=" * 70)
print("APPROACH 5: Check BIOS EmbNic settings")
print("=" * 70)

r = get("/redfish/v1/Systems/System.Embedded.1/Bios")
if r.ok:
    data = r.json()
    attrs = data.get("Attributes", {})
    emb_attrs = {k: v for k, v in attrs.items() if any(x in k.lower() for x in ["emb", "integrated", "nic", "network", "lom"])}
    print(f"  BIOS NIC-related attributes ({len(emb_attrs)}):")
    for k, v in sorted(emb_attrs.items()):
        print(f"    {k} = {v}")

# ============================================================
# 7. Try Network Device Function details
# ============================================================
print("\n" + "=" * 70)
print("APPROACH 6: Network Device Functions for Integrated NIC")
print("=" * 70)

r = get("/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions")
if r.ok:
    data = r.json()
    for member in data.get("Members", []):
        func_path = member.get("@odata.id", "")
        r2 = get(func_path)
        print(f"\n  Function: {func_path}")
        if r2.ok:
            func_data = r2.json()
            print(f"    Name: {func_data.get('Name')}")
            print(f"    Status: {func_data.get('Status')}")
            print(f"    DeviceEnabled: {func_data.get('DeviceEnabled')}")
            print(f"    NetDevFuncType: {func_data.get('NetDevFuncType')}")
            eth = func_data.get("Ethernet", {})
            if eth:
                print(f"    MacAddress: {eth.get('PermanentMACAddress')}")
            
            # Check OEM data
            oem = func_data.get("Oem", {}).get("Dell", {})
            if "DellNIC" in oem:
                dell_nic = oem["DellNIC"]
                print(f"    DellNIC.BusNumber: {dell_nic.get('BusNumber')}")
                print(f"    DellNIC.DeviceNumber: {dell_nic.get('DeviceNumber')}")
                print(f"    DellNIC.PCIDeviceID: {dell_nic.get('PCIDeviceID')}")
                print(f"    DellNIC.PermanentMACAddress: {dell_nic.get('PermanentMACAddress')}")
            if "DellNICCapabilities" in oem:
                caps = oem["DellNICCapabilities"]
                print(f"    DellNICCapabilities.NicMode: {caps.get('NicMode')}")
                print(f"    DellNICCapabilities.NicPartitioning: {caps.get('NicPartitioning')}")
            
            # Check settings link
            settings = func_data.get("@Redfish.Settings", {})
            if settings:
                print(f"    Settings URI: {settings.get('SettingsObject', {}).get('@odata.id')}")

# ============================================================
# 8. Try Dell OEM NIC attribute endpoint with PATCH
# ============================================================
print("\n" + "=" * 70)
print("APPROACH 7: Try PATCH NicMode via Dell OEM endpoint")
print("=" * 70)

# First try to find the correct attributes endpoint
for nic_id in ["NIC.Integrated.1-1-1"]:
    # Standard Redfish Settings approach
    settings_path = f"/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{nic_id}/Oem/Dell/DellNetworkAttributes/{nic_id}/Settings"
    r = get(settings_path)
    print(f"\n  GET {settings_path} -> {r.status_code}")
    if r.ok:
        data = r.json()
        print(f"  Current settings: {json.dumps(data.get('Attributes', {}), indent=4)[:500]}")
        
        # Try to PATCH NicMode
        print(f"\n  Attempting PATCH NicMode=Enabled...")
        payload = {
            "Attributes": {
                "NicMode": "Enabled"
            }
        }
        r2 = patch(settings_path, payload)
        print(f"  PATCH Status: {r2.status_code}")
        if r2.text:
            print(f"  Response: {r2.text[:500]}")
    else:
        print(f"  Response: {r.text[:300]}")

    # Also try the direct attributes endpoint
    direct_path = f"/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{nic_id}/Oem/Dell/DellNetworkAttributes/{nic_id}"
    r = get(direct_path)
    print(f"\n  GET {direct_path} -> {r.status_code}")
    if r.ok:
        data = r.json()
        attrs = data.get("Attributes", {})
        print(f"  Attributes count: {len(attrs)}")
        if "NicMode" in attrs:
            print(f"  NicMode = {attrs['NicMode']}")
        # Check if Settings link exists
        settings_ref = data.get("@Redfish.Settings", {})
        if settings_ref:
            print(f"  Settings: {settings_ref}")

# ============================================================
# 9. Compare with adv02 - how does NicMode look there?
# ============================================================
print("\n" + "=" * 70)
print("COMPARISON: Check NIC attributes on adv02")
print("=" * 70)

ADV02_BASE = "https://192.168.10.5"
ADV02_AUTH = ("root", "Tricolor00!")

for nic_id in ["NIC.Integrated.1-1-1"]:
    path = f"/redfish/v1/Systems/System.Embedded.1/NetworkAdapters/NIC.Integrated.1/NetworkDeviceFunctions/{nic_id}/Oem/Dell/DellNetworkAttributes/{nic_id}"
    r = requests.get(f"{ADV02_BASE}{path}", auth=ADV02_AUTH, verify=False, timeout=30)
    print(f"\n  adv02 GET {path} -> {r.status_code}")
    if r.ok:
        data = r.json()
        attrs = data.get("Attributes", {})
        print(f"  Attributes count: {len(attrs)}")
        for key in sorted(attrs.keys()):
            if any(k in key.lower() for k in ["nicmode", "mode", "partition", "pci", "enable"]):
                print(f"    {key} = {attrs[key]}")
    
    # Also check settings
    settings_path = path + "/Settings"
    r = requests.get(f"{ADV02_BASE}{settings_path}", auth=ADV02_AUTH, verify=False, timeout=30)
    print(f"  adv02 GET {settings_path} -> {r.status_code}")

# Check adv02 network adapter
r = requests.get(f"{ADV02_BASE}/redfish/v1/Systems/System.Embedded.1/NetworkAdapters", auth=ADV02_AUTH, verify=False, timeout=30)
if r.ok:
    data = r.json()
    print(f"\n  adv02 Network Adapters:")
    for m in data.get("Members", []):
        adapter_path = m.get("@odata.id", "")
        r2 = requests.get(f"{ADV02_BASE}{adapter_path}", auth=ADV02_AUTH, verify=False, timeout=30)
        if r2.ok:
            ad = r2.json()
            print(f"    {ad.get('Id')}: {ad.get('Model')} - Status: {ad.get('Status')}")

print("\n\nDone!")
