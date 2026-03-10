"""Dry-run the fixed configure_bios logic to see what would be patched."""
import requests, urllib3, json
urllib3.disable_warnings()
base = 'https://192.168.10.5'
auth = ('root', 'Tricolor00!')

# Current BIOS
r = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1/Bios', auth=auth, verify=False, timeout=30)
current = r.json().get('Attributes', {})

# Registry
r2 = requests.get(f'{base}/redfish/v1/Systems/System.Embedded.1/Bios/BiosRegistry', auth=auth, verify=False, timeout=60)
registry = {e["AttributeName"]: e for e in r2.json().get("RegistryEntries", {}).get("Attributes", []) if "AttributeName" in e}

# Desired (from fixed AZURE_LOCAL_BIOS_DEFAULTS)
desired = {
    "ProcVirtualization": "Enabled",
    "ProcX2Apic": "Enabled",
    "ProcVtd": "Enabled",
    "SriovGlobalEnable": "Enabled",
    "SecureBoot": "Enabled",
    "BootMode": "Uefi",
    "TpmSecurity": "On",
    "TpmActivation": "Enabled",
    "Tpm2Hierarchy": "Enabled",
    "TpmPpiBypassProvision": "Enabled",
    "MemOpMode": "OptimizerMode",
    "NodeInterleave": "Disabled",
    "SysProfile": "PerfPerWattOptimizedDapc",
    "ProcCStates": "Disabled",
    "WorkloadProfile": "NotAvailable",
    "LogicalProc": "Enabled",
    "EmbSata": "AhciMode",
    "RedundantOsBoot": "Enabled",
}

# Filter
filtered = {}
skipped = []
for attr, val in desired.items():
    entry = registry.get(attr)
    if entry is None:
        skipped.append(f"{attr}: not in registry (not on this platform)")
        continue
    if entry.get("ReadOnly", False):
        skipped.append(f"{attr}: read-only")
        continue
    if entry.get("Type") == "Enumeration":
        valid = {v["ValueName"] for v in entry.get("Value", [])}
        if valid and val not in valid:
            skipped.append(f"{attr}: desired '{val}' not in {sorted(valid)}")
            continue
    filtered[attr] = val

print("=== SKIPPED (read-only or invalid) ===")
for s in skipped:
    print(f"  {s}")

# Compare against current
print("\n=== ALREADY OK ===")
mismatched = {}
for attr, val in filtered.items():
    cur = current.get(attr)
    if cur is None:
        print(f"  {attr}: NOT PRESENT on server")
    elif str(cur) == val:
        print(f"  {attr}: {val} ✓")
    else:
        mismatched[attr] = (cur, val)

print(f"\n=== NEED CHANGING ({len(mismatched)}) ===")
for attr, (cur, des) in mismatched.items():
    print(f"  {attr}: {cur} → {des}")

if not mismatched:
    print("\n✅ No BIOS changes needed — pipeline Stage 5 will pass cleanly!")
