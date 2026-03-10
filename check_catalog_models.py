import xml.etree.ElementTree as ET
import os

catalog_path = os.path.join(os.environ['TEMP'], 'Catalog.xml')
tree = ET.parse(catalog_path)
root = tree.getroot()

# Strip namespaces
for elem in root.iter():
    if '}' in elem.tag:
        elem.tag = elem.tag.split('}')[1]

# Find all unique model names that contain "640" or "R6"
models = set()
system_ids = set()
count = 0
for pkg in root.iter('SoftwareComponent'):
    count += 1
    for sys_elem in pkg.iter('SupportedSystems'):
        for brand in sys_elem.iter('Brand'):
            brand_name = brand.get('Display', '')
            for model in brand.iter('Model'):
                display = model.get('Display', '')
                sid = model.get('systemID', '')
                if '640' in display or 'R6' in display:
                    models.add(f"{brand_name} / {display} (systemID={sid})")
                    system_ids.add(sid)

print(f"Total packages in catalog: {count}")
print(f"\nModels containing '640' or 'R6':")
for m in sorted(models):
    print(f"  {m}")

# Also show a sample of 5 random package names to understand structure
print(f"\n=== Sample packages (first 5) ===")
i = 0
for pkg in root.iter('SoftwareComponent'):
    if i >= 5:
        break
    path = pkg.get('path', '')
    for sys_elem in pkg.iter('SupportedSystems'):
        for brand in sys_elem.iter('Brand'):
            for model in brand.iter('Model'):
                display = model.get('Display', '')
                if display:
                    print(f"  Model: {display}, Path: {path[:80]}")
                    i += 1
                    break
