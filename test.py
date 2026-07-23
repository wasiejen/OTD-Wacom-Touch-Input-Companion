import hid

# Wacom Vendor ID: 0x056A
wacom_vid = 0x056A

print("Searching for Wacom HID interfaces...")
found = False

for device in hid.enumerate():
    if device['vendor_id'] == wacom_vid:
        found = True
        print(f"Interface: {device['interface_number']} | Path: {device['path']} | Product: {device['product_string']}")

if not found:
    print("No Wacom devices detected. Make sure the tablet is plugged in directly via USB.")