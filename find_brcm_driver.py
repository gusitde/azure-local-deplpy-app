"""Find Broadcom NIC Windows driver from Dell Catalog."""
import gzip, xml.etree.ElementTree as ET, os

catalog_path = r"C:\Users\gus\Documents\GitHub\azure-local-deplpy-app\dups\Catalog.xml.gz"

with gzip.open(catalog_path, 'rb') as f:
    tree = ET.parse(f)
root = tree.getroot()
ns = {'ns': root.tag.split('}')[0].strip('{')} if '}' in root.tag else {}

# Search for Broadcom NIC driver packages for R640
results = []
for pkg in root.iter('{%s}SoftwareComponent' % ns.get('ns', '') if ns else 'SoftwareComponent'):
    path = pkg.get('path', '')
    pkg_type = pkg.get('packageType', '')
    
    # Get all text content for searching
    all_text = ET.tostring(pkg, encoding='unicode', method='text').lower()
    xml_text = ET.tostring(pkg, encoding='unicode').lower()
    
    # Look for Broadcom network driver (not firmware)
    if ('broadcom' in all_text or 'bcm' in all_text or 'brcm' in all_text or '14e4' in xml_text) and \
       ('network' in all_text or 'nic' in all_text or 'ethernet' in all_text):
        # Check if it targets R640
        if 'r640' in all_text or '1814' in xml_text:
            # Get details
            name_el = pkg.find('.//{%s}Display' % ns['ns']) if ns else pkg.find('.//Display')
            name = ''
            if name_el is not None:
                lang_el = name_el.find('.//{%s}Name' % ns['ns']) if ns else name_el.find('.//Name')
                if lang_el is not None:
                    name = lang_el.text or ''
            
            ver = pkg.get('vendorVersion', pkg.get('dellVersion', ''))
            size = pkg.get('size', '')
            date = pkg.get('dateTime', pkg.get('releaseDate', ''))
            cat = pkg.get('packageType', '')
            
            # Check category - look for LWXP (Windows driver) vs FRMW (firmware)
            category_el = pkg.find('.//{%s}Category' % ns['ns']) if ns else pkg.find('.//Category')
            cat_name = ''
            if category_el is not None:
                cat_display = category_el.find('.//{%s}Display' % ns['ns']) if ns else category_el.find('.//Display')
                if cat_display is not None:
                    cat_name_el = cat_display.find('.//{%s}Name' % ns['ns']) if ns else cat_display.find('.//Name')
                    if cat_name_el is not None:
                        cat_name = cat_name_el.text or ''
            
            results.append({
                'path': path,
                'name': name,
                'version': ver,
                'size': size,
                'date': date,
                'type': cat,
                'category': cat_name,
            })

# Sort by date descending
results.sort(key=lambda x: x['date'], reverse=True)

print(f"Found {len(results)} Broadcom NIC packages for R640:\n")
for i, r in enumerate(results):
    size_mb = int(r['size']) / 1048576 if r['size'].isdigit() else r['size']
    print(f"  [{i+1}] {r['name'][:80]}")
    print(f"      Path: {r['path']}")
    print(f"      Version: {r['version']}  Date: {r['date']}  Type: {r['type']}  Category: {r['category']}")
    print(f"      Size: {size_mb:.1f} MB" if isinstance(size_mb, float) else f"      Size: {size_mb}")
    print()

# Also search more broadly for any "driver" type broadcom packages
print("\n" + "=" * 60)
print("All Broadcom packages (broader search):")
print("=" * 60)
broad_results = []
for pkg in root.iter('{%s}SoftwareComponent' % ns.get('ns', '') if ns else 'SoftwareComponent'):
    path = pkg.get('path', '')
    all_text = ET.tostring(pkg, encoding='unicode', method='text').lower()
    xml_text = ET.tostring(pkg, encoding='unicode').lower()
    
    if ('broadcom' in all_text or '14e4' in xml_text) and ('r640' in all_text or '1814' in xml_text):
        name_el = pkg.find('.//{%s}Display' % ns['ns']) if ns else pkg.find('.//Display')
        name = ''
        if name_el is not None:
            for child in name_el:
                if child.text:
                    name = child.text
                    break
        ver = pkg.get('vendorVersion', '')
        pkg_type = pkg.get('packageType', '')
        broad_results.append((name, path, ver, pkg_type))

broad_results.sort(key=lambda x: x[0])
for name, path, ver, ptype in broad_results:
    print(f"  {name[:70]:72s} v{ver:15s} type={ptype}")
    print(f"    {path}")
