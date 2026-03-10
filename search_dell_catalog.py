import xml.etree.ElementTree as ET
import os

catalog_path = os.path.join(os.environ['TEMP'], 'Catalog.xml')
print(f"Parsing {catalog_path}...")

tree = ET.parse(catalog_path)
root = tree.getroot()

# Dell catalog namespace
ns = {'': root.tag.split('}')[0] + '}' if '}' in root.tag else ''}
# Remove namespace for easier access
for elem in root.iter():
    if '}' in elem.tag:
        elem.tag = elem.tag.split('}')[1]

# Find R640 system ID
# Dell R640 system IDs are typically like "087C" or similar
# Let's search for R640 in the Brand/Model sections
r640_system_ids = set()
for sys_elem in root.iter('SupportedSystems'):
    for brand in sys_elem.iter('Brand'):
        for model in brand.iter('Model'):
            model_name = model.get('Display', '') + model.text if model.text else model.get('Display', '')
            if 'R640' in model_name.upper() or 'R640' in str(model.get('systemID', '')):
                sid = model.get('systemID', '')
                if sid:
                    r640_system_ids.add(sid)

print(f"R640 system IDs found: {r640_system_ids}")

# Search for BIOS, Broadcom NIC (105008), and Mellanox NIC (104480) packages for R640
targets = {
    'BIOS': {'componentID': '159', 'found': []},
    'Broadcom NIC': {'componentID': '105008', 'found': []},
    'Mellanox NIC': {'componentID': '104480', 'found': []},
}

for pkg in root.iter('SoftwareComponent'):
    comp_id = pkg.get('componentType', '')
    pkg_id = pkg.get('packageID', '')
    path = pkg.get('path', '')
    
    # Check if this package supports R640
    supports_r640 = False
    for sys_elem in pkg.iter('SupportedSystems'):
        for brand in sys_elem.iter('Brand'):
            for model in brand.iter('Model'):
                sid = model.get('systemID', '')
                display = model.get('Display', '')
                if 'R640' in display.upper() or sid in r640_system_ids:
                    supports_r640 = True
                    break
    
    if not supports_r640:
        continue
    
    # Check component type and ID
    cat_name = ''
    for name_elem in pkg.iter('Name'):
        for disp in name_elem.iter('Display'):
            cat_name = disp.text or ''
            break
        break
    
    comp_id_val = pkg.get('componentType', '')
    
    # Check if it matches our targets
    for target_name, target_info in targets.items():
        # Match by component ID in the package
        for dev in pkg.iter('Device'):
            did = dev.get('componentID', '')
            if did == target_info['componentID']:
                version = pkg.get('vendorVersion', '') or pkg.get('dellVersion', '')
                target_info['found'].append({
                    'path': path,
                    'version': version,
                    'name': cat_name,
                    'packageID': pkg_id,
                    'releaseDate': pkg.get('releaseDate', ''),
                    'size': pkg.get('size', ''),
                })
                break

# Print results - latest version only
for target_name, target_info in targets.items():
    print(f"\n{'='*60}")
    print(f"  {target_name} (ComponentID: {target_info['componentID']})")
    print(f"{'='*60}")
    
    if not target_info['found']:
        print("  No packages found!")
        continue
    
    # Sort by version descending
    entries = sorted(target_info['found'], key=lambda x: x['releaseDate'], reverse=True)
    
    # Show top 3
    for e in entries[:3]:
        print(f"\n  Version: {e['version']}")
        print(f"  Name: {e['name']}")
        print(f"  Path: https://downloads.dell.com/{e['path']}")
        print(f"  Release: {e['releaseDate']}")
        print(f"  Size: {e['size']}")
        print(f"  PackageID: {e['packageID']}")
