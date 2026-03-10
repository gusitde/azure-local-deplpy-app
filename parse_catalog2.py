"""
Parse catalog more thoroughly - extract all R640 entries with full details.
"""
import gzip
import xml.etree.ElementTree as ET
import os

catalog_path = os.path.join("dups", "Catalog.xml.gz")

with gzip.open(catalog_path, 'rb') as f:
    tree = ET.parse(f)
root = tree.getroot()

all_components = root.findall('.//SoftwareComponent')

# For each R640 entry, extract detailed info
print("=" * 90)
print("ALL R640 FIRMWARE ENTRIES FROM DELL CATALOG")
print("=" * 90)

r640_entries = []
for comp in all_components:
    comp_text = ET.tostring(comp, encoding='unicode')
    if 'R640' not in comp_text:
        continue
    
    path = comp.get('path', '')
    version = comp.get('vendorVersion', '')
    pkg_id = comp.get('packageID', '')
    release_date = comp.get('releaseDate', '')
    pkg_type = comp.get('packageType', '')
    
    # Get Name from child elements
    name_elem = comp.find('.//Name')
    name = ''
    if name_elem is not None:
        # Name might have Display child
        display = name_elem.find('Display')
        if display is not None:
            name = display.text or ''
        else:
            name = name_elem.text or ''
    
    # Get Category
    cat_elem = comp.find('.//Category')
    category = ''
    if cat_elem is not None:
        display = cat_elem.find('Display')
        if display is not None:
            category = display.text or ''
        else:
            category = cat_elem.text or ''
    
    # Get ComponentType
    comp_type = comp.find('.//ComponentType')
    comp_type_str = ''
    if comp_type is not None:
        display = comp_type.find('Display')
        if display is not None:
            comp_type_str = display.text or ''
        else:
            comp_type_str = comp_type.text or ''
    
    # Get supported systems
    systems = []
    for brand in comp.findall('.//SupportedSystems/Brand'):
        brand_name = brand.get('display', '')
        for model in brand.findall('Model'):
            model_name = model.get('display', '')
            systems.append(f"{brand_name} {model_name}")
    
    url = f"https://dl.dell.com/{path}" if path else ''
    
    r640_entries.append({
        'name': name,
        'version': version,
        'category': category,
        'comp_type': comp_type_str,
        'url': url,
        'path': path,
        'release_date': release_date,
        'pkg_id': pkg_id,
        'systems': systems,
    })

# Group by category
from collections import defaultdict
by_cat = defaultdict(list)
for e in r640_entries:
    cat = e['category'] or e['comp_type'] or 'Unknown'
    by_cat[cat].append(e)

for cat in sorted(by_cat.keys()):
    entries = by_cat[cat]
    print(f"\n{'='*70}")
    print(f"CATEGORY: {cat} ({len(entries)} entries)")
    print(f"{'='*70}")
    # Sort by release date descending
    entries.sort(key=lambda x: x['release_date'], reverse=True)
    for e in entries[:10]:  # Show top 10 per category
        print(f"  [{e['release_date']}] {e['name'][:70]}")
        print(f"    Version: {e['version']}  PkgID: {e['pkg_id']}")
        print(f"    URL: {e['url']}")
        print()

# Summary of key firmware
print("\n" + "=" * 90)
print("KEY FIRMWARE FOR R640 (BIOS, iDRAC, NIC, RAID)")
print("=" * 90)
keywords = ['bios', 'idrac', 'lifecycle', 'broadcom', 'mellanox', 'mlnx', 'nic', 'network', 'hba', 'raid', 'cpld', 'perc']
for e in sorted(r640_entries, key=lambda x: x['release_date'], reverse=True):
    text = f"{e['name']} {e['category']} {e['comp_type']}".lower()
    if any(kw in text for kw in keywords):
        print(f"  [{e['release_date']}] {e['name'][:80]}")
        print(f"    Version: {e['version']}  Category: {e['category']}")
        print(f"    URL: {e['url']}")
        print()
