import xml.etree.ElementTree as ET
import os

catalog_path = os.path.join(os.environ['TEMP'], 'Catalog.xml')
print(f"Parsing {catalog_path} ({os.path.getsize(catalog_path)} bytes)...")

# Parse iteratively to handle large file
tree = ET.parse(catalog_path)
root = tree.getroot()

# Strip namespaces
for elem in root.iter():
    if '}' in elem.tag:
        elem.tag = elem.tag.split('}')[1]
    for k in list(elem.attrib.keys()):
        if '}' in k:
            elem.attrib[k.split('}')[1]] = elem.attrib.pop(k)

# Current firmware versions on adv01/adv02 for comparison
current_fw = {
    'BIOS': {'adv01': '2.23.0', 'adv02': '2.24.0', 'compID': '159'},
    'iDRAC': {'adv01': '7.00.00.181', 'adv02': '7.00.00.181', 'compID': '25227'},
    'Mellanox NIC FW': {'adv01': '14.32.21.02', 'adv02': '14.32.21.02', 'compID': '104480'},
    'Broadcom NIC FW': {'adv01': '23.21.13.39', 'adv02': '23.31.18.10', 'compID': '105008'},
    'Dell HBA330': {'adv01': '16.17.01.00', 'adv02': '16.17.01.00', 'compID': '104298'},
}

# Collect ALL R640 packages
r640_packages = []

for pkg in root.iter('SoftwareComponent'):
    path = pkg.get('path', '')
    version = pkg.get('vendorVersion', '') or pkg.get('dellVersion', '')
    release = pkg.get('releaseDate', '')
    size = pkg.get('size', '')
    pkg_type = pkg.get('packageType', '')
    
    # Get display name
    cat_name = ''
    for name_elem in pkg.iter('Name'):
        for disp in name_elem.iter('Display'):
            if disp.get('lang', '') == 'en':
                cat_name = disp.text or ''
                break
        if not cat_name:
            for disp in name_elem.iter('Display'):
                cat_name = disp.text or ''
                break
        break
    
    # Get category
    category = ''
    for cat_elem in pkg.iter('Category'):
        for disp in cat_elem.iter('Display'):
            if disp.get('lang', '') == 'en':
                category = disp.text or ''
                break
        if not category:
            for disp in cat_elem.iter('Display'):
                category = disp.text or ''
                break
        break
    
    # Check if supports R640
    supports_r640 = False
    for sys_elem in pkg.iter('SupportedSystems'):
        for brand in sys_elem.iter('Brand'):
            for model in brand.iter('Model'):
                display = model.get('Display', '')
                if 'R640' in display.upper():
                    supports_r640 = True
                    break
            if supports_r640:
                break
    
    if not supports_r640:
        continue
    
    # Get component IDs
    comp_ids = []
    for dev in pkg.iter('Device'):
        cid = dev.get('componentID', '')
        if cid:
            comp_ids.append(cid)
    
    r640_packages.append({
        'name': cat_name,
        'category': category,
        'version': version,
        'path': path,
        'release': release,
        'size': size,
        'pkg_type': pkg_type,
        'comp_ids': comp_ids,
    })

print(f"\nFound {len(r640_packages)} total packages for R640")

# Group by category and show latest per component
from collections import defaultdict
by_category = defaultdict(list)
for p in r640_packages:
    by_category[p['category']].append(p)

# Sort each category by release date desc
for cat in sorted(by_category.keys()):
    pkgs = sorted(by_category[cat], key=lambda x: x['release'], reverse=True)
    
    # Deduplicate by name (keep latest)
    seen_names = set()
    unique_pkgs = []
    for p in pkgs:
        # Create a simplified name key
        name_key = p['name'].split(' -')[0].strip() if ' -' in p['name'] else p['name']
        if name_key not in seen_names:
            seen_names.add(name_key)
            unique_pkgs.append(p)
    
    print(f"\n{'='*70}")
    print(f"  Category: {cat}")
    print(f"{'='*70}")
    
    for p in unique_pkgs[:10]:  # Show top 10 per category
        # Check if we have current version info
        match_info = ''
        for fw_name, fw_data in current_fw.items():
            if fw_data['compID'] in p['comp_ids']:
                adv01_ver = fw_data['adv01']
                adv02_ver = fw_data['adv02']
                match_info = f" [adv01={adv01_ver}, adv02={adv02_ver}]"
                if p['version'] > adv01_ver or p['version'] > adv02_ver:
                    match_info += " ** UPDATE AVAILABLE **"
                break
        
        size_mb = int(p['size']) / 1024 / 1024 if p['size'] else 0
        print(f"\n  {p['name']}")
        print(f"    Version: {p['version']}  Released: {p['release']}  Size: {size_mb:.1f}MB{match_info}")
        print(f"    URL: https://downloads.dell.com/{p['path']}")
        print(f"    CompIDs: {','.join(p['comp_ids'][:5])}")
