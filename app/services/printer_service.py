"""
Printer Service Abstraction.
Handles ESC/POS hardware printing (Network IP:9100, USB, Serial), Browser print, and PDF.
Enqueues jobs to print_jobs table with background retry support (Section 21 & 38.7).
"""
import io
from typing import Optional, Dict, Any
from escpos.printer import Network, Usb, Serial
from PIL import Image
from app.extensions import db
from app.models.print_job import PrintJob
from app.models.setting import Setting


class PrinterService:
    """
    Central printer abstraction. Application code interacts exclusively via this service.
    """

    @classmethod
    def get_printer_connection(cls, settings_dict: Optional[Dict[str, Any]] = None):
        """Builds an escpos printer connection based on configured or provided settings."""
        cfg = settings_dict or cls.load_settings()
        mode = cfg.get("printer_mode", "network").lower()

        if mode == "network":
            host = cfg.get("printer_ip", "127.0.0.1")
            port = int(cfg.get("printer_port", 9100))
            return Network(host=host, port=port, timeout=5)
        elif mode == "usb":
            vendor_id = int(str(cfg.get("printer_usb_vendor", "0x04b8")), 16)
            product_id = int(str(cfg.get("printer_usb_product", "0x0202")), 16)
            return Usb(idVendor=vendor_id, idProduct=product_id, timeout=5)
        elif mode == "serial":
            dev = cfg.get("printer_serial_port", "COM1")
            baud = int(cfg.get("printer_baud_rate", 9600))
            return Serial(dev=dev, baudrate=baud, timeout=5)
        else:
            raise ValueError(f"Unsupported printer mode: {mode}")

    @classmethod
    def load_settings(cls) -> Dict[str, Any]:
        """Loads printer configuration from database settings."""
        return {
            "printer_mode": Setting.get_value("printer_mode", "network"),
            "printer_ip": Setting.get_value("printer_ip", "192.168.1.200"),
            "printer_port": Setting.get_value("printer_port", 9100),
            "printer_usb_vendor": Setting.get_value("printer_usb_vendor", "0x04b8"),
            "printer_usb_product": Setting.get_value("printer_usb_product", "0x0202"),
            "printer_serial_port": Setting.get_value("printer_serial_port", "COM1"),
            "printer_baud_rate": Setting.get_value("printer_baud_rate", 9600),
            "receipt_paper_size": Setting.get_value("receipt_paper_size", "80mm"),
            "cash_drawer_enabled": Setting.get_value("cash_drawer_enabled", False),
        }

    @classmethod
    def enqueue_print_job(cls, invoice_id: Optional[int], payload: str, job_type: str = "receipt") -> PrintJob:
        """Enqueues a print job into the local print_jobs table for asynchronous processing."""
        job = PrintJob(
            invoice_id=invoice_id,
            printer_name="thermal_default",
            job_type=job_type,
            job_status="pending",
            payload=payload,
            attempt_count=0,
        )
        db.session.add(job)
        db.session.commit()
        return job

    @classmethod
    def print_test_slip(cls, settings_dict: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Connects to the configured printer, formats a diagnostic slip, and prints it.
        Returns: {'success': bool, 'message': str}
        """
        try:
            printer = cls.get_printer_connection(settings_dict)
            cfg = settings_dict or cls.load_settings()
            paper_size = cfg.get("receipt_paper_size", "80mm")

            printer.set(align="center", bold=True, double_height=True, double_width=True)
            printer.text("ISI POS SYSTEM\n")
            printer.set(align="center", bold=False)
            printer.text("PRINTER HARDWARE DIAGNOSTIC\n")
            printer.text("=" * (42 if paper_size == "80mm" else 30) + "\n")
            printer.set(align="left")
            printer.text(f"Mode: {cfg.get('printer_mode')}\n")
            if cfg.get("printer_mode") == "network":
                printer.text(f"Target: {cfg.get('printer_ip')}:{cfg.get('printer_port')}\n")
            printer.text(f"Paper Size: {paper_size}\n")
            printer.text("Status: Connection Established\n")
            printer.text("Timestamp: OK\n")
            printer.text("=" * (42 if paper_size == "80mm" else 30) + "\n")
            printer.set(align="center")
            printer.text("Diagnostic test printed successfully.\n\n\n")
            printer.cut()
            return {"success": True, "message": "Test slip printed successfully."}
        except Exception as exc:
            return {"success": False, "message": f"Printer communication error: {str(exc)}"}
