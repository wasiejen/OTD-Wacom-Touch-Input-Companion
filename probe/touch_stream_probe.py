import hid
import time

# Wacom Intuos5 / Pro Large parameters
VENDOR_ID = 0x056A
PRODUCT_ID = 791
TOUCH_INTERFACE = 0  # Standard for Intuos touch data

def test_raw_touch_stream():
    # 1. Locate the path for Interface 1
    device_path = None
    #print(hid.enumerate())
    for dev in hid.enumerate(VENDOR_ID, PRODUCT_ID):
        if dev['interface_number'] == TOUCH_INTERFACE:
            device_path = dev['path']
            break

    if not device_path:
        print(f"Error: Could not find Interface {TOUCH_INTERFACE}")
        return

    # 2. Open the device
    tablet = hid.device()
    try:
        tablet.open_path(device_path)
        tablet.set_nonblocking(True)
        print(f"Successfully connected to Interface {TOUCH_INTERFACE}!")
        print("Touch the tablet with 1 or 2 fingers to see raw reports. Press Ctrl+C to exit.\n")

        # 3. Read loop
        while True:
            # Read up to 64 bytes
            report = tablet.read(64)
            if report:
                # Print non-zero reports to keep the console clean
                # Show first 16 bytes as hex values for easier reading
                hex_data = [f"{b:02X}" for b in report[:63]]
                #if hex_data[1] != "01" or hex_data[2] != "81" or hex_data[3] != "00":
                print(f"Report ID: 0x{report[0]:02X} | Bytes: {' '.join(hex_data)}")

            time.sleep(0.001) # ~200Hz polling rate

    except KeyboardInterrupt:
        print("\nTest stopped by user.")
    except Exception as e:
        print(f"\nError opening/reading device: {e}")
        print("Tip: If reading fails, try running your command prompt / IDE as Administrator.")
    finally:
        tablet.close()

if __name__ == "__main__":
    test_raw_touch_stream()