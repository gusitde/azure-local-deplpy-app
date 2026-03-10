"""Install Azure Local OS on servers .6 and .7 via iDRAC virtual media."""
import requests
import urllib3
import time
import socket
import subprocess

urllib3.disable_warnings()

IDRAC_USER = "root"
IDRAC_PASS = "Tricolor00!"
ISO_PATH = r"C:\Users\gus\.azure-local-deploy\AzureLocal24H2.26100.1742.LCM.12.2602.0.3018.x64.en-us.iso"
LOCAL_IP = "192.168.10.201"  # Our IP on the iDRAC VLAN
HTTP_PORT = 8089

SERVERS = [
    {"idrac": "192.168.10.6", "name": "ADV03"},
    {"idrac": "192.168.10.7", "name": "AVD04"},
]


def redfish(ip, path, method="GET", json_data=None):
    auth = (IDRAC_USER, IDRAC_PASS)
    url = f"https://{ip}{path}"
    kwargs = dict(auth=auth, verify=False, timeout=30)
    if method == "GET":
        return requests.get(url, **kwargs)
    elif method == "POST":
        return requests.post(url, json=json_data, **kwargs)
    elif method == "PATCH":
        return requests.patch(url, json=json_data, **kwargs)
    elif method == "DELETE":
        return requests.delete(url, **kwargs)


def setup_iso_share():
    """Set up CIFS share for ISO file."""
    import os
    iso_dir = os.path.dirname(ISO_PATH)
    iso_name = os.path.basename(ISO_PATH)
    
    # Use HTTP server instead - simpler
    iso_url = f"http://{LOCAL_IP}:{HTTP_PORT}/{iso_name}"
    print(f"ISO URL: {iso_url}")
    return iso_url


def eject_media(ip):
    """Eject any mounted virtual media."""
    print(f"  [{ip}] Ejecting virtual media...")
    r = redfish(ip, "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD")
    data = r.json()
    if data.get("Inserted"):
        r = redfish(ip, 
            "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.EjectMedia",
            method="POST", json_data={})
        if r.status_code in (200, 204):
            print(f"  [{ip}] Ejected OK")
        else:
            print(f"  [{ip}] Eject response: {r.status_code}")
    else:
        print(f"  [{ip}] No media mounted")


def insert_media(ip, iso_url):
    """Insert ISO via virtual media."""
    print(f"  [{ip}] Mounting ISO: {iso_url}")
    r = redfish(ip,
        "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.InsertMedia",
        method="POST",
        json_data={"Image": iso_url})
    if r.status_code in (200, 204):
        print(f"  [{ip}] ISO mounted OK")
    else:
        print(f"  [{ip}] Mount response: {r.status_code} - {r.text[:200]}")
        return False
    return True


def set_boot_cd(ip):
    """Set one-time boot from virtual CD."""
    print(f"  [{ip}] Setting one-time boot to CD...")
    r = redfish(ip, "/redfish/v1/Systems/System.Embedded.1",
        method="PATCH",
        json_data={
            "Boot": {
                "BootSourceOverrideTarget": "Cd",
                "BootSourceOverrideEnabled": "Once"
            }
        })
    if r.status_code in (200, 204):
        print(f"  [{ip}] Boot override set OK")
    else:
        print(f"  [{ip}] Boot override: {r.status_code} - {r.text[:200]}")


def power_off(ip):
    """Gracefully power off, force if needed."""
    r = redfish(ip, "/redfish/v1/Systems/System.Embedded.1")
    state = r.json().get("PowerState")
    if state == "Off":
        print(f"  [{ip}] Already powered off")
        return
    
    print(f"  [{ip}] Sending graceful shutdown...")
    redfish(ip, "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
        method="POST", json_data={"ResetType": "GracefulShutdown"})
    
    # Wait up to 120s
    for i in range(24):
        time.sleep(5)
        r = redfish(ip, "/redfish/v1/Systems/System.Embedded.1")
        if r.json().get("PowerState") == "Off":
            print(f"  [{ip}] Powered off after {(i+1)*5}s")
            return
    
    # Force off
    print(f"  [{ip}] Forcing power off...")
    redfish(ip, "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
        method="POST", json_data={"ResetType": "ForceOff"})
    time.sleep(5)
    print(f"  [{ip}] Force powered off")


def power_on(ip):
    """Power on the server."""
    print(f"  [{ip}] Powering on...")
    r = redfish(ip, "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
        method="POST", json_data={"ResetType": "On"})
    if r.status_code in (200, 204):
        print(f"  [{ip}] Power on OK")
    else:
        print(f"  [{ip}] Power on: {r.status_code} - {r.text[:200]}")


def install_os_on_server(ip, name, iso_url):
    """Full OS install sequence for one server."""
    print(f"\n{'='*60}")
    print(f"Installing OS on {name} ({ip})")
    print(f"{'='*60}")
    
    # 1. Power off
    power_off(ip)
    
    # 2. Eject any existing media
    eject_media(ip)
    
    # 3. Mount ISO
    if not insert_media(ip, iso_url):
        print(f"  [{ip}] FAILED to mount ISO - aborting")
        return False
    
    # 4. Set boot from CD
    set_boot_cd(ip)
    
    # 5. Power on
    power_on(ip)
    
    print(f"  [{ip}] OS installation started! Server is booting from ISO.")
    return True


if __name__ == "__main__":
    # Use CIFS share approach (same as adv02 install)
    import os
    iso_name = os.path.basename(ISO_PATH)
    
    # We'll use HTTP - start server in background first
    # The HTTP server should be started separately:
    #   cd dups && python -m http.server 8089 --bind 192.168.10.201
    # But ISO is in a different directory, so let's use CIFS
    
    # Actually, let's use the CIFS share approach
    # First check if SMB share exists
    print("Setting up ISO access...")
    iso_dir = os.path.dirname(ISO_PATH)
    
    # Create SMB share via PowerShell
    print("Checking/creating SMB share for ISO...")
    
    # Use direct HTTP from the ISO directory
    iso_url = f"http://{LOCAL_IP}:{HTTP_PORT}/{iso_name}"
    print(f"ISO will be served at: {iso_url}")
    print(f"\nIMPORTANT: Start HTTP server first in another terminal:")
    print(f'  cd "{iso_dir}" && python -m http.server {HTTP_PORT} --bind {LOCAL_IP}')
    print()
    
    # Check if HTTP server is running
    try:
        s = socket.create_connection((LOCAL_IP, HTTP_PORT), timeout=3)
        s.close()
        print("HTTP server is running!")
    except:
        print("WARNING: HTTP server is NOT running!")
        print("Starting it now would block, so please start it in another terminal.")
        print("Or we can use CIFS instead.")
        
        # Use CIFS approach
        cifs_user = "gus@worldai.local"
        cifs_pass = r"Tricolor00!@#$%^&*("
        
        share_name = "ald-iso"
        share_path = iso_dir
        
        print(f"\nUsing CIFS share approach instead...")
        # The CIFS URL format for iDRAC: //server/share/file
        iso_url = f"//{LOCAL_IP}/{share_name}/{iso_name}"
        print(f"CIFS ISO URL: {iso_url}")
        
        # We need to pass CIFS creds to iDRAC InsertMedia
        # Let's do this properly
        for srv in SERVERS:
            ip = srv["idrac"]
            name = srv["name"]
            print(f"\n{'='*60}")
            print(f"Installing OS on {name} ({ip})")
            print(f"{'='*60}")
            
            power_off(ip)
            eject_media(ip)
            
            print(f"  [{ip}] Mounting ISO via CIFS: {iso_url}")
            r = redfish(ip,
                "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.InsertMedia",
                method="POST",
                json_data={
                    "Image": iso_url,
                    "UserName": cifs_user,
                    "Password": cifs_pass
                })
            if r.status_code in (200, 204):
                print(f"  [{ip}] ISO mounted via CIFS OK")
            else:
                print(f"  [{ip}] CIFS mount: {r.status_code} - {r.text[:300]}")
                continue
            
            set_boot_cd(ip)
            power_on(ip)
            print(f"  [{ip}] OS installation started!")
        
        print("\n" + "="*60)
        print("Both servers are now booting from the Azure Local ISO!")
        print("Installation typically takes 20-40 minutes.")
        print("="*60)
        exit(0)
    
    # HTTP server is running - use HTTP approach
    for srv in SERVERS:
        success = install_os_on_server(srv["idrac"], srv["name"], iso_url)
        if not success:
            print(f"Failed to start OS install on {srv['name']}")
    
    print("\n" + "="*60)
    print("Both servers are now booting from the Azure Local ISO!")
    print("Installation typically takes 20-40 minutes.")
    print("="*60)
