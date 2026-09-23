#!/usr/bin/env python3
"""
Standalone Thermal Printer Smoke Test CLI (Section 38.7).
Validates thermal printer drivers and hardware connectivity before POS build.
Supports Network (TCP IP:9100), USB (VID:PID), and Serial connections.
"""
import sys
import argparse
from datetime import datetime


def run_smoke_test(args):
    print("=" * 60)
    print("  ISI POS - THERMAL PRINTER HARDWARE SMOKE TEST")
    print("=" * 60)
    print(f"Target Mode: {args.mode.upper()}")
    print(f"Paper Width: {args.paper_size}")

    printer = None
    try:
        if args.mode == "network":
            print(f"Connecting to Network Printer at {args.ip}:{args.port}...")
            from escpos.printer import Network
            printer = Network(host=args.ip, port=args.port, timeout=args.timeout)

        elif args.mode == "usb":
            vid = int(args.vendor_id, 16) if args.vendor_id.startswith("0x") else int(args.vendor_id)
            pid = int(args.product_id, 16) if args.product_id.startswith("0x") else int(args.product_id)
            print(f"Connecting to USB Printer (Vendor ID: {hex(vid)}, Product ID: {hex(pid)})...")
            from escpos.printer import Usb
            printer = Usb(idVendor=vid, idProduct=pid, timeout=args.timeout)

        elif args.mode == "serial":
            print(f"Connecting to Serial Printer on {args.serial_port} at {args.baudrate} baud...")
            from escpos.printer import Serial
            printer = Serial(dev=args.serial_port, baudrate=args.baudrate, timeout=args.timeout)

        else:
            print(f"Error: Unknown printer mode '{args.mode}'.")
            sys.exit(1)

        line_width = 42 if args.paper_size == "80mm" else 32
        divider = "=" * line_width

        print("Connection successful! Sending diagnostic test slip...")
        printer.set(align="center", bold=True, double_height=True, double_width=True)
        printer.text("ISI POS SYSTEM\n")
        printer.set(align="center", bold=False, double_height=False, double_width=False)
        printer.text("HARDWARE VALIDATION TEST\n")
        printer.text(divider + "\n")
        printer.set(align="left")
        printer.text(f"Interface:    {args.mode.upper()}\n")
        if args.mode == "network":
            printer.text(f"Target IP:    {args.ip}:{args.port}\n")
        printer.text(f"Timestamp:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        printer.text(f"Paper Size:   {args.paper_size}\n")
        printer.text(divider + "\n")
        printer.set(align="center")
        printer.text("PRINTER HARDWARE VERIFIED OK\n")
        printer.text(divider + "\n\n\n")
        printer.cut()

        print("[SUCCESS] Test slip transmitted and printed successfully!")
        print("Hardware validation complete.")

    except Exception as exc:
        print(f"\n[FAILURE] Unable to communicate with thermal printer: {exc}")
        print("\nTroubleshooting tips:")
        if args.mode == "network":
            print("1. Verify the printer is powered on and connected to the local network.")
            print("2. Ping the IP address from the command line.")
            print("3. Ensure raw TCP port 9100 is not blocked by a local firewall.")
        elif args.mode == "usb":
            print("1. Check Windows Device Manager to confirm USB driver installation.")
            print("2. Verify USB Vendor ID and Product ID with 'lsusb' or device properties.")
            print("3. Install libusb / WinUSB filter if required by pyusb.")
        elif args.mode == "serial":
            print("1. Verify the COM port name and baud rate match dip-switch settings.")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Standalone ESC/POS Thermal Printer Smoke Test")
    parser.add_argument("--mode", choices=["network", "usb", "serial"], default="network", help="Printer interface mode")
    parser.add_argument("--ip", default="192.168.1.200", help="Network printer IP address")
    parser.add_argument("--port", type=int, default=9100, help="Network printer raw TCP port (default: 9100)")
    parser.add_argument("--vendor-id", default="0x04b8", help="USB Vendor ID (e.g. 0x04b8 for Epson)")
    parser.add_argument("--product-id", default="0x0202", help="USB Product ID")
    parser.add_argument("--serial-port", default="COM1", help="Serial COM port (e.g. COM1 or /dev/ttyUSB0)")
    parser.add_argument("--baudrate", type=int, default=9600, help="Serial baud rate (default: 9600)")
    parser.add_argument("--paper-size", choices=["80mm", "58mm"], default="80mm", help="Thermal paper width")
    parser.add_argument("--timeout", type=int, default=5, help="Connection timeout in seconds")

    args = parser.parse_args()
    run_smoke_test(args)


if __name__ == "__main__":
    main()
