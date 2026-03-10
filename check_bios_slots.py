"""
Check BIOS/iDRAC PCI slot configuration and NIC settings on adv01.
The Broadcom NIC is in Slot 2 (NIC.Slot.2-1). Maybe the slot is disabled in BIOS.
"""
import requests
import json
import urllib3
urllib3.disable_warnings()

ADV01 = {"idrac": "192.168.10.4", "user": "root", "pass": "Tricolor00!"}
ADV02 = {"idrac": "192.168.10.5", "user": "root", "pass": "Tricolor00!"}

def get(server, path):
    url = f"https://{server['idrac']}{path}"
    r = requests.get(url, auth=(server["user"], server["pass"]), verify=False, timeout=30)
    return r

def main():
    # Check BIOS attributes related to slots and NICs
    print("=" * 70)
    print("ADV01 - BIOS ATTRIBUTES (Slot/NIC related)")
    print("=" * 70)
    
    r = get(ADV01, "/redfish/v1/Systems/System.Embedded.1/Bios")
    if r.status_code == 200:
        bios = r.json()
        attrs = bios.get("Attributes", {})
        
        # Filter for slot, NIC, PCI, integrated related settings
        keywords = ["slot", "nic", "pci", "integrat", "embed", "network", "lan", "boot"]
        relevant = {}
        for k, v in sorted(attrs.items()):
            if any(kw in k.lower() for kw in keywords):
                relevant[k] = v
        
        for k, v in sorted(relevant.items()):
            print(f"  {k:50s} = {v}")
        
        print(f"\n  Total BIOS attributes: {len(attrs)}")
        print(f"  Matching attributes: {len(relevant)}")
    else:
        print(f"  Error: {r.status_code}")
    
    # Check same on adv02 for comparison
    print("\n" + "=" * 70)
    print("ADV02 - BIOS ATTRIBUTES (Slot/NIC related)")
    print("=" * 70)
    
    r2 = get(ADV02, "/redfish/v1/Systems/System.Embedded.1/Bios")
    if r2.status_code == 200:
        bios2 = r2.json()
        attrs2 = bios2.get("Attributes", {})
        
        relevant2 = {}
        for k, v in sorted(attrs2.items()):
            if any(kw in k.lower() for kw in keywords):
                relevant2[k] = v
        
        for k, v in sorted(relevant2.items()):
            print(f"  {k:50s} = {v}")
    
    # Compare differences
    print("\n" + "=" * 70)
    print("DIFFERENCES BETWEEN ADV01 AND ADV02 BIOS SETTINGS (Slot/NIC)")
    print("=" * 70)
    
    if r.status_code == 200 and r2.status_code == 200:
        attrs1 = r.json().get("Attributes", {})
        attrs2 = r2.json().get("Attributes", {})
        
        # Show all differences 
        all_keys = set(list(attrs1.keys()) + list(attrs2.keys()))
        diffs = {}
        for k in sorted(all_keys):
            v1 = attrs1.get(k, "N/A")
            v2 = attrs2.get(k, "N/A")
            if v1 != v2:
                diffs[k] = (v1, v2)
        
        print(f"\n  Total different attributes: {len(diffs)}")
        for k, (v1, v2) in sorted(diffs.items()):
            print(f"  {k:50s} adv01={v1!s:30s} adv02={v2}")
    
    # Check NetworkAdapters on both
    print("\n" + "=" * 70)
    print("NETWORK ADAPTERS - ADV01")
    print("=" * 70)
    
    r = get(ADV01, "/redfish/v1/Systems/System.Embedded.1/NetworkAdapters")
    if r.status_code == 200:
        members = r.json().get("Members", [])
        for m in members:
            uri = m["@odata.id"]
            d = get(ADV01, uri)
            if d.status_code == 200:
                data = d.json()
                print(f"\n  {data.get('Id')}: {data.get('Manufacturer')} {data.get('Model')}")
                print(f"    Status: {data.get('Status', {})}")
                
                # Check NetworkPorts
                ports_uri = data.get("NetworkPorts", {}).get("@odata.id")
                if ports_uri:
                    pr = get(ADV01, ports_uri)
                    if pr.status_code == 200:
                        ports = pr.json().get("Members", [])
                        for p in ports:
                            port_r = get(ADV01, p["@odata.id"])
                            if port_r.status_code == 200:
                                pd = port_r.json()
                                print(f"    Port {pd.get('Id')}: LinkStatus={pd.get('LinkStatus')} "
                                      f"Status={pd.get('Status', {})}")

if __name__ == "__main__":
    main()
