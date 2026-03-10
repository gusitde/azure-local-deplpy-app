import xml.etree.ElementTree as ET
import os

catalog_path = os.path.join(os.environ['TEMP'], 'Catalog.xml')
tree = ET.parse(catalog_path)
root = tree.getroot()

# Strip namespaces
for elem in root.iter():
    if '}' in elem.tag:
        elem.tag = elem.tag.split('}')[1]

# Show ALL unique brand/model display names
models = set()
for pkg in root.iter('SoftwareComponent'):
    for sys_elem in pkg.iter('SupportedSystems'):
        for brand in sys_elem.iter('Brand'):
            brand_name = brand.get('Display', '')
            for model in brand.iter('Model'):
                display = model.get('Display', '')
                models.add(f"{brand_name} / {display}")

print(f"Total unique Brand/Model combos: {len(models)}")
for m in sorted(models)[:30]:
    print(f"  {m}")
print("...")
for m in sorted(models)[-10:]:
    print(f"  {m}")

# Check the baseLocation attribute on root
print(f"\nRoot tag: {root.tag}")
print(f"Root attribs: {root.attrib}")
