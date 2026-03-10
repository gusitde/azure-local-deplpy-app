"""
Parse Dell Catalog.xml.gz to find R640 firmware updates.
Focus on: BIOS, iDRAC, NIC, and any other relevant components.
"""
import gzip
import xml.etree.ElementTree as ET
import os

catalog_path = os.path.join("dups", "Catalog.xml.gz")
print(f"Catalog file size: {os.path.getsize(catalog_path):,} bytes")

print("Parsing catalog...")
with gzip.open(catalog_path, 'rb') as f:
    tree = ET.parse(f)
root = tree.getroot()

# Get namespace
ns = ''
if root.tag.startswith('{'):
    ns = root.tag.split('}')[0] + '}'

print(f"Root tag: {root.tag}")
print(f"Namespace: {ns or 'none'}")
print(f"Root children tags: {set(c.tag for c in root)}")

# Count total entries
all_components = root.findall(f'.//{ns}SoftwareComponent') if ns else root.findall('.//SoftwareComponent')
print(f"Total SoftwareComponents: {len(all_components)}")

if not all_components:
    # Try without namespace
    all_components = root.findall('.//SoftwareComponent')
    print(f"Without namespace: {len(all_components)}")
    
    # Try other element names
    for tag in ['Package', 'SoftwareBundle', 'Update', 'Driver', 'ManifestInformation']:
        found = root.findall(f'.//{tag}')
        if found:
            print(f"Found {len(found)} <{tag}> elements")

# If still no components, dump structure
if not all_components:
    print("\nDumping first few elements to understand structure:")
    for i, elem in enumerate(root):
        print(f"  [{i}] {elem.tag} attrib={dict(elem.attrib)}")
        for j, child in enumerate(elem):
            if j < 3:
                print(f"    [{j}] {child.tag} attrib={dict(child.attrib)}")
                for k, grandchild in enumerate(child):
                    if k < 3:
                        print(f"      [{k}] {grandchild.tag} = {grandchild.text} attrib={dict(grandchild.attrib)}")
        if i >= 5:
            break
    
    # Also try iter
    print("\nFirst 20 unique tags in document:")
    tags = set()
    for elem in root.iter():
        tags.add(elem.tag)
        if len(tags) >= 30:
            break
    for t in sorted(tags):
        print(f"  {t}")
else:
    # Search for R640-related entries
    print("\n" + "=" * 70)
    print("Searching for R640 entries...")
    print("=" * 70)
    
    r640_entries = []
    for comp in all_components:
        # Check various attributes and child elements for R640
        comp_text = ET.tostring(comp, encoding='unicode')
        if 'R640' in comp_text or 'r640' in comp_text.lower():
            name = comp.get('Name', comp.get('name', ''))
            path = comp.get('path', comp.get('Path', ''))
            version = comp.get('vendorVersion', comp.get('version', ''))
            category = ''
            
            # Try to get category
            for cat_elem in comp.iter():
                if 'Category' in cat_elem.tag or 'category' in cat_elem.tag:
                    category = cat_elem.text or cat_elem.get('display', '') or cat_elem.get('Display', '')
                    break
            
            r640_entries.append({
                'name': name,
                'path': path, 
                'version': version,
                'category': category,
                'xml': comp_text[:300]
            })
    
    print(f"Found {len(r640_entries)} R640-related entries")
    
    # Group by category
    categories = {}
    for entry in r640_entries:
        cat = entry.get('category', 'Unknown') or 'Unknown'
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(entry)
    
    for cat, entries in sorted(categories.items()):
        print(f"\n--- {cat} ({len(entries)} entries) ---")
        for e in entries[:5]:
            print(f"  {e['name'][:80]} v{e['version']}")
            if e['path']:
                print(f"    URL: https://dl.dell.com/{e['path']}")
    
    # Specifically look for BIOS
    print("\n" + "=" * 70)
    print("BIOS entries for R640:")
    print("=" * 70)
    for entry in r640_entries:
        text_lower = entry['xml'].lower()
        if 'bios' in text_lower:
            print(f"  {entry['name'][:100]}")
            print(f"  Version: {entry['version']}")
            print(f"  Path: {entry['path']}")
            print(f"  Category: {entry['category']}")
            print()
