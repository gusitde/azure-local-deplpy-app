import requests, urllib3, json, time, io, gzip
urllib3.disable_warnings()

# Step 1: Download the correct catalog - Dell enterprise servers use 
# downloads.dell.com/catalog/Catalog.xml.gz but it seems we got the PC one
# Let's try the correct path that iDRAC uses

# Actually, iDRAC looks at: https://downloads.dell.com/catalog/Catalog.xml.gz
# The issue is our download might have been the wrong file
# Let me check what we got

import os
import xml.etree.ElementTree as ET

# Check if the existing catalog has any Dell server info
catalog_path = os.path.join(os.environ['TEMP'], 'Catalog.xml')
tree = ET.parse(catalog_path)
root = tree.getroot()

# Strip namespaces
for elem in root.iter():
    if '}' in elem.tag:
        elem.tag = elem.tag.split('}')[1]

identifier = root.get('identifier', '')
release_id = root.get('releaseID', '')
print(f"Current catalog: identifier={identifier}, releaseID={release_id}")
print(f"This appears to be the PC catalog (CatalogPC)")

# Let's download directly from Dell - the "real" enterprise catalog
# Dell Repository Manager uses: https://downloads.dell.com/catalog/Catalog.xml.gz
# But the iDRAC Lifecycle Controller uses: downloads.dell.com with just "catalog/Catalog.xml.gz"
# which IS the correct path

# Alternative approach: Use Dell's TechCenter API to look up drivers by service tag
print("\n=== Checking Dell TechCenter API ===")
for stag in ['2BC9243']:  # adv01 service tag
    # Dell's public API
    headers = {'Accept': 'application/json'}
    url = f'https://apigtwb2c.us.dell.com/PROD/sbil/eapi/v5/WS-DL-Service/list/getdriversbytag'
    params = {'servicetag': stag, 'oscode': 'WS24H2'}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=30)
        print(f"  API Status: {r.status_code}")
        if r.ok:
            data = r.json()
            print(f"  Response type: {type(data)}")
            if isinstance(data, dict):
                print(f"  Keys: {list(data.keys())[:10]}")
            elif isinstance(data, list):
                print(f"  Count: {len(data)}")
    except Exception as e:
        print(f"  Error: {e}")

# Another approach: try Dell's public downloads search
print("\n=== Trying Dell downloads search ===")
# These are known Dell DUP package IDs for R640
# Let me search for them by pattern
known_r640_urls = {
    'BIOS': [
        # Dell BIOS update format is typically: BIOS_XXXXX_WN64_version.EXE
        'https://downloads.dell.com/FOLDER12345678M/1/BIOS_V5GM8_WN64_2.24.0.EXE',
    ],
}

# Better approach: Use the actual DSU catalog for PowerEdge
# The R640 SUU catalog is at:
dsu_catalog = 'https://downloads.dell.com/catalog/Catalog.xml.gz'
print(f"\nDownloading enterprise catalog from {dsu_catalog}...")

r = requests.get(dsu_catalog, timeout=120)
print(f"Downloaded: {len(r.content)} bytes")

# Decompress
import io, gzip
data = gzip.decompress(r.content)
print(f"Decompressed: {len(data)} bytes")

# Save and parse
ent_path = os.path.join(os.environ['TEMP'], 'EntCatalog.xml')
with open(ent_path, 'wb') as f:
    f.write(data)

tree2 = ET.parse(ent_path)
root2 = tree2.getroot()
for elem in root2.iter():
    if '}' in elem.tag:
        elem.tag = elem.tag.split('}')[1]

ident2 = root2.get('identifier', '')
rel2 = root2.get('releaseID', '')
print(f"Enterprise catalog: identifier={ident2}, releaseID={rel2}")

# Count packages
pkg_count = sum(1 for _ in root2.iter('SoftwareComponent'))
print(f"Total packages: {pkg_count}")

# Find R640
models = set()
for pkg in root2.iter('SoftwareComponent'):
    for sys_elem in pkg.iter('SupportedSystems'):
        for brand in sys_elem.iter('Brand'):
            for model in brand.iter('Model'):
                display = model.get('Display', '')
                if '640' in display:
                    models.add(display)

print(f"\nModels with '640': {models}")

if not models:
    # Show a sample of models
    all_models = set()
    for pkg in root2.iter('SoftwareComponent'):
        for sys_elem in pkg.iter('SupportedSystems'):
            for brand in sys_elem.iter('Brand'):
                bname = brand.get('Display', '')
                for model in brand.iter('Model'):
                    display = model.get('Display', '')
                    if display:
                        all_models.add(f"{bname}/{display}")
    print(f"\nAll models ({len(all_models)} total), sample:")
    for m in sorted(all_models)[:20]:
        print(f"  {m}")
