"""Debug virtual media mount issues on new iDRACs."""
import requests
import urllib3
urllib3.disable_warnings()

AUTH = ("root", "Tricolor00!")

for ip in ["192.168.10.6", "192.168.10.7"]:
    print(f"\n=== {ip} ===")
    
    # Check VirtualMedia capabilities
    r = requests.get(f"https://{ip}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD", auth=AUTH, verify=False, timeout=10)
    d = r.json()
    print(f"MediaTypes: {d.get('MediaTypes')}")
    print(f"ConnectedVia: {d.get('ConnectedVia')}")
    print(f"TransferMethod: {d.get('TransferMethod')}")
    print(f"TransferProtocolType: {d.get('TransferProtocolType')}")
    
    # Check actions
    actions = d.get("Actions", {})
    for a_name, a_val in actions.items():
        if "InsertMedia" in a_name:
            print(f"InsertMedia action: {a_val}")
            # Check allowed values
            for k, v in a_val.items():
                if "AllowableValues" in k or "Parameter" in k:
                    print(f"  {k}: {v}")
    
    # Check OEM virtual media info
    oem = d.get("Oem", {}).get("Dell", {})
    if oem:
        print(f"Dell OEM: {list(oem.keys())}")
    
    # Also check VirtualMedia collection
    r2 = requests.get(f"https://{ip}/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia", auth=AUTH, verify=False, timeout=10)
    d2 = r2.json()
    members = d2.get("Members", [])
    print(f"VirtualMedia members: {[m['@odata.id'].split('/')[-1] for m in members]}")
    
    # Check if there's a RemovableMedia or something
    for m in members:
        mid = m["@odata.id"].split("/")[-1]
        if mid != "CD":
            rm = requests.get(f"https://{ip}{m['@odata.id']}", auth=AUTH, verify=False, timeout=10)
            md = rm.json()
            print(f"  {mid}: MediaTypes={md.get('MediaTypes')}")

# Also check iDRAC network connectivity
print("\n\n=== iDRAC Network Attributes ===")
for ip in ["192.168.10.6"]:
    # Check if iDRAC can resolve/reach our IP
    r = requests.get(f"https://{ip}/redfish/v1/Managers/iDRAC.Embedded.1/Attributes", auth=AUTH, verify=False, timeout=10)
    d = r.json()
    attrs = d.get("Attributes", {})
    # Get NIC attributes
    for k, v in attrs.items():
        if any(x in k.lower() for x in ["ipv4", "gateway", "dns", "vlan"]):
            if v:
                print(f"  {k}: {v}")
