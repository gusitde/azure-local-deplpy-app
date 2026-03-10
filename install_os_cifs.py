"""Install Azure Local OS on servers .6 and .7 via iDRAC virtual media + CIFS."""
import requests
import urllib3
import time

urllib3.disable_warnings()

AUTH = ("root", "Tricolor00!")
# CIFS path: //local_ip/share_name/iso_filename
ISO_CIFS = "//192.168.10.201/ald-iso/AzureLocal24H2.26100.1742.LCM.12.2602.0.3018.x64.en-us.iso"
CIFS_USER = "gus@worldai.local"
CIFS_PASS = r"Tricolor00!@#$%^&*("

SERVERS = [
    {"idrac": "192.168.10.6", "name": "ADV03"},
    {"idrac": "192.168.10.7", "name": "AVD04"},
]


def rf(ip, path, method="GET", data=None):
    url = f"https://{ip}{path}"
    kw = dict(auth=AUTH, verify=False, timeout=30)
    if method == "GET":
        return requests.get(url, **kw)
    elif method == "POST":
        return requests.post(url, json=data, **kw)
    elif method == "PATCH":
        return requests.patch(url, json=data, **kw)


def install(ip, name):
    print(f"\n{'='*50}")
    print(f"  {name} ({ip})")
    print(f"{'='*50}")

    # 1. Check power
    r = rf(ip, "/redfish/v1/Systems/System.Embedded.1")
    ps = r.json().get("PowerState")
    print(f"  Power: {ps}")

    # 2. Power off if needed
    if ps != "Off":
        print(f"  Shutting down...")
        rf(ip, "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
           method="POST", data={"ResetType": "GracefulShutdown"})
        for i in range(30):
            time.sleep(5)
            r = rf(ip, "/redfish/v1/Systems/System.Embedded.1")
            if r.json().get("PowerState") == "Off":
                print(f"  Off after {(i+1)*5}s")
                break
        else:
            print(f"  Force off...")
            rf(ip, "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
               method="POST", data={"ResetType": "ForceOff"})
            time.sleep(10)

    # 3. Eject existing media
    print(f"  Ejecting media...")
    r = rf(ip, "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD")
    if r.json().get("Inserted"):
        rf(ip, "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.EjectMedia",
           method="POST", data={})
        time.sleep(3)

    # 4. Mount ISO via CIFS
    print(f"  Mounting ISO via CIFS: {ISO_CIFS}")
    r = rf(ip, "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD/Actions/VirtualMedia.InsertMedia",
           method="POST", data={
               "Image": ISO_CIFS,
               "UserName": CIFS_USER,
               "Password": CIFS_PASS
           })
    if r.status_code not in (200, 204):
        print(f"  FAILED: {r.status_code} - {r.text[:300]}")
        return False
    print(f"  ISO mounted OK")

    # Verify mount
    time.sleep(2)
    r = rf(ip, "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD")
    d = r.json()
    print(f"  Verify: Inserted={d.get('Inserted')}, Image={d.get('Image')}")

    # 5. Set boot from CD
    print(f"  Setting one-time boot to CD...")
    r = rf(ip, "/redfish/v1/Systems/System.Embedded.1",
           method="PATCH", data={"Boot": {"BootSourceOverrideTarget": "Cd", "BootSourceOverrideEnabled": "Once"}})
    if r.status_code not in (200, 204):
        print(f"  Boot override failed: {r.status_code} - {r.text[:200]}")

    # 6. Power on
    print(f"  Powering on...")
    r = rf(ip, "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
           method="POST", data={"ResetType": "On"})
    if r.status_code in (200, 204):
        print(f"  STARTED - {name} is booting from ISO!")
    else:
        print(f"  Power on: {r.status_code} - {r.text[:200]}")
        return False
    return True


if __name__ == "__main__":
    print("Starting OS installation on ADV03 and AVD04")
    print(f"ISO: {ISO_CIFS}")
    print(f"CIFS user: {CIFS_USER}")

    results = {}
    for srv in SERVERS:
        results[srv["name"]] = install(srv["idrac"], srv["name"])

    print(f"\n{'='*50}")
    print("SUMMARY:")
    for name, ok in results.items():
        status = "STARTED" if ok else "FAILED"
        print(f"  {name}: {status}")
    print(f"\nOS installation takes ~20-40 minutes.")
    print(f"{'='*50}")
