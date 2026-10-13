"""
Thermal ESC/POS receipt printing engine for Cafe POS.
Supports 80mm and 58mm paper widths, USB / Network / Serial / Dummy backends,
safe Tamil transliteration/skipping for standard codepages, drawer-kick, paper cut,
full receipts, and kitchen/counter slips.
"""

import re
import socket
from decimal import Decimal
from django.utils import timezone


TAMIL_TRANSLITERATION = {
    "மசாலா சாய்": "Masala Chai",
    "தேநீர்": "Tea",
    "பில்": "Bill",
    "கூட்டுத்தொகை": "Subtotal",
    "மொத்தம்": "Total",
    "காபி": "Coffee",
    "வடிகட்டு காபி": "Filter Coffee",
    "வடிகட்டு": "Filter",
    "ரொட்டி": "Roti",
    "சமோசா": "Samosa",
}


def sanitize_for_escpos(text, fallback=""):
    """
    Safely sanitizes text for standard ESC/POS single-byte thermal printers.
    Tamil characters are replaced with known transliterations or English fallback,
    and any non-printable characters are stripped.
    """
    if not text:
        return fallback or ""

    clean = str(text).strip()

    # Check for known Tamil phrases and replace
    for tamil_phrase, english_rep in TAMIL_TRANSLITERATION.items():
        if tamil_phrase in clean:
            clean = clean.replace(tamil_phrase, english_rep)

    # If text contains Tamil characters (Unicode block U+0B80 - U+0BFF)
    if re.search(r"[\u0B80-\u0BFF]", clean):
        # Look for English text inside brackets or parentheses
        match = re.search(r"[\(\[\{]([A-Za-z0-9\s\.\,\-\/\&]+)[\)\]\}]", clean)
        if match and match.group(1).strip():
            clean = match.group(1).strip()
        elif fallback and not re.search(r"[\u0B80-\u0BFF]", str(fallback)):
            clean = str(fallback).strip()
        else:
            # Strip Tamil characters and collapse whitespace
            clean = re.sub(r"[\u0B80-\u0BFF]+", "", clean).strip()

    if not clean and fallback:
        clean = str(fallback).strip()

    # Normalize to ASCII/CP437 compatible characters
    clean = clean.replace("₹", "Rs.").replace("“", '"').replace("”", '"').replace("’", "'")
    # Replace non-ascii chars safely
    clean = clean.encode("ascii", "replace").decode("ascii").replace("?", " ")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean or fallback or ""


def format_qty(qty):
    """Formats quantity cleanly (e.g. 2.00 -> '2', 2.50 -> '2.5')."""
    if isinstance(qty, Decimal):
        if qty == qty.to_integral():
            return str(int(qty))
        return f"{qty.normalize():f}"
    elif isinstance(qty, (int, float)):
        if isinstance(qty, float) and qty.is_integer():
            return str(int(qty))
        return str(qty)
    return str(qty)


def get_column_width(settings):
    """Returns character column width: 48 for 80mm, 32 for 58mm."""
    width_setting = getattr(settings, "printer_paper_width", "80")
    return 32 if str(width_setting) == "58" else 48


def pad_line(left, right, width):
    """Formats a left-and-right justified line for thermal printing."""
    left_str = str(left)
    right_str = str(right)
    space_needed = width - len(left_str) - len(right_str)
    if space_needed < 1:
        max_left = max(1, width - len(right_str) - 1)
        left_str = left_str[:max_left]
        space_needed = 1
    return f"{left_str}{' ' * space_needed}{right_str}"


def center_line(text, width):
    """Centers text within the specified column width."""
    text_str = str(text)
    if len(text_str) >= width:
        return text_str[:width]
    return text_str.center(width)


def divider(width, char="-"):
    return char * width


# ESC/POS Byte Constants
ESC = b"\x1b"
GS = b"\x1d"

CMD_INIT = ESC + b"@"
CMD_BOLD_ON = ESC + b"E\x01"
CMD_BOLD_OFF = ESC + b"E\x00"
CMD_ALIGN_LEFT = ESC + b"a\x00"
CMD_ALIGN_CENTER = ESC + b"a\x01"
CMD_ALIGN_RIGHT = ESC + b"a\x02"
CMD_DOUBLE_ON = GS + b"!\x11"
CMD_DOUBLE_OFF = GS + b"!\x00"
CMD_CUT = GS + b"V\x42\x00"       # Full/partial cut with feed
CMD_DRAWER_KICK = ESC + b"p\x00\x19\xfa"  # Drawer pulse (pin 2, 50ms pulse)


def build_receipt_bytes(bill, settings, is_duplicate=False):
    """
    Builds the ESC/POS byte stream for a complete Bill receipt.
    Includes shop header, token, items with modifiers, totals, tax lines, footer,
    duplicate label, drawer-kick and paper cut.
    """
    width = get_column_width(settings)
    out = bytearray()

    # 1. Initialize
    out.extend(CMD_INIT)

    # 2. Drawer Kick (if configured, pulse at receipt start)
    if getattr(settings, "printer_drawer_kick", False):
        out.extend(CMD_DRAWER_KICK)

    # 3. Shop Header (Centered)
    out.extend(CMD_ALIGN_CENTER)
    shop_name = sanitize_for_escpos(settings.business_name, "Cafe POS")
    out.extend(CMD_BOLD_ON)
    out.extend(CMD_DOUBLE_ON)
    out.extend(f"{shop_name}\n".encode("ascii"))
    out.extend(CMD_DOUBLE_OFF)
    out.extend(CMD_BOLD_OFF)

    if settings.address:
        for addr_line in settings.address.splitlines():
            clean_addr = sanitize_for_escpos(addr_line)
            if clean_addr:
                out.extend(f"{center_line(clean_addr, width)}\n".encode("ascii"))

    if settings.phone:
        out.extend(f"{center_line('Tel: ' + settings.phone, width)}\n".encode("ascii"))
    if settings.tax_number:
        out.extend(f"{center_line('GSTIN: ' + settings.tax_number, width)}\n".encode("ascii"))
    if settings.fssai_number:
        out.extend(f"{center_line('FSSAI: ' + settings.fssai_number, width)}\n".encode("ascii"))

    out.extend(f"{divider(width, '=')}\n".encode("ascii"))

    # 4. Duplicate Label (if duplicate or reprint)
    is_dup = is_duplicate or (getattr(bill, "print_count", 0) > 1)
    if is_dup:
        out.extend(CMD_BOLD_ON)
        out.extend(f"{center_line('*** DUPLICATE RECEIPT ***', width)}\n".encode("ascii"))
        out.extend(CMD_BOLD_OFF)
        out.extend(f"{divider(width, '-')}\n".encode("ascii"))

    # 5. Token Number (Prominent if present)
    if bill.token_number:
        out.extend(CMD_BOLD_ON)
        out.extend(CMD_DOUBLE_ON)
        out.extend(f"TOKEN: #{bill.token_number}\n".encode("ascii"))
        out.extend(CMD_DOUBLE_OFF)
        out.extend(CMD_BOLD_OFF)
        out.extend(f"{divider(width, '-')}\n".encode("ascii"))

    # 6. Bill Metadata
    out.extend(CMD_ALIGN_LEFT)
    created_dt = timezone.localtime(bill.created_at).strftime("%d-%m-%Y %I:%M %p")
    out.extend(f"{pad_line('Date: ' + created_dt, 'Bill #: ' + str(bill.bill_number), width)}\n".encode("ascii"))

    if getattr(settings, "show_cashier", True) and bill.created_by:
        cashier_name = bill.created_by.get_full_name() or bill.created_by.username
        out.extend(f"Cashier: {sanitize_for_escpos(cashier_name)}\n".encode("ascii"))

    if bill.customer:
        cust_label = sanitize_for_escpos(bill.customer.name)
        if bill.customer.phone:
            cust_label += f" ({bill.customer.phone})"
        out.extend(f"Customer: {cust_label}\n".encode("ascii"))

    out.extend(f"{divider(width, '-')}\n".encode("ascii"))

    # 7. Table Header
    qty_header = "Qty x Rate"
    amt_header = "Amount"
    item_header = "Item"
    if width == 48:
        # 48 cols: Item (24) Qty (6) Rate (8) Amount (10)
        table_hdr = f"{'Item':<22}{'Qty':>6}{'Rate':>9}{'Amount':>11}"
        out.extend(f"{table_hdr}\n".encode("ascii"))
    else:
        # 32 cols: Item (18) Qty x Rate (6) Amt (8)
        table_hdr = pad_line(f"{item_header}", f"{qty_header}  {amt_header}", width)
        out.extend(f"{table_hdr}\n".encode("ascii"))
    out.extend(f"{divider(width, '-')}\n".encode("ascii"))

    # 8. Items and Modifiers
    for item in bill.items.all().select_related("menu_item"):
        fallback_name = item.menu_item.name if item.menu_item else item.item_name
        item_title = sanitize_for_escpos(item.item_name, fallback_name)
        qty_str = format_qty(item.quantity)
        rate_str = f"{item.price:.2f}"
        amt_str = f"{item.total:.2f}"

        if width == 48:
            truncated_title = item_title[:21]
            line = f"{truncated_title:<22}{qty_str:>6}{rate_str:>9}{amt_str:>11}"
            out.extend(f"{line}\n".encode("ascii"))
            if len(item_title) > 21:
                out.extend(f"  {item_title[21:]}\n".encode("ascii"))
        else:
            right_part = f"{qty_str}x{rate_str} {amt_str}"
            line = pad_line(item_title, right_part, width)
            out.extend(f"{line}\n".encode("ascii"))

        # Modifier details
        mod_names = getattr(item, "modifier_names", []) or []
        for mod in mod_names:
            clean_mod = sanitize_for_escpos(str(mod))
            if clean_mod:
                out.extend(f"  + {clean_mod}\n".encode("ascii"))

        # Note details
        if getattr(item, "note", ""):
            clean_note = sanitize_for_escpos(item.note)
            if clean_note:
                out.extend(f"  Note: {clean_note}\n".encode("ascii"))

    out.extend(f"{divider(width, '-')}\n".encode("ascii"))

    # 9. Totals
    out.extend(f"{pad_line('Subtotal:', f'{bill.subtotal:.2f}', width)}\n".encode("ascii"))

    if bill.discount and bill.discount > Decimal("0.00"):
        out.extend(f"{pad_line('Discount:', f'-{bill.discount:.2f}', width)}\n".encode("ascii"))

    if bill.tax_amount and bill.tax_amount > Decimal("0.00"):
        tax_pct = bill.gst_percent if bill.gst_percent else Decimal("0.00")
        tax_label = f"GST ({tax_pct:g}%):" if tax_pct else "Tax:"
        out.extend(f"{pad_line(tax_label, f'{bill.tax_amount:.2f}', width)}\n".encode("ascii"))

    out.extend(CMD_BOLD_ON)
    out.extend(f"{pad_line('TOTAL:', f'Rs. {bill.grand_total:.2f}', width)}\n".encode("ascii"))
    out.extend(CMD_BOLD_OFF)
    out.extend(f"{divider(width, '-')}\n".encode("ascii"))

    # 10. Payment Method Breakdown
    pm = bill.payment_method or "CASH"
    payments = list(bill.payments.all())
    if payments and len(payments) > 1:
        out.extend("Split Payments:\n".encode("ascii"))
        for p in payments:
            out.extend(f"{pad_line('  ' + p.get_method_display(), f'{p.amount:.2f}', width)}\n".encode("ascii"))
    else:
        out.extend(f"{pad_line('Payment Method:', bill.get_payment_method_display() if hasattr(bill, 'get_payment_method_display') else pm, width)}\n".encode("ascii"))

    if pm == "CASH" and bill.tendered:
        out.extend(f"{pad_line('Tendered:', f'{bill.tendered:.2f}', width)}\n".encode("ascii"))
        if bill.change_due is not None:
            out.extend(f"{pad_line('Change Due:', f'{bill.change_due:.2f}', width)}\n".encode("ascii"))

    # 11. Bill / Order Note
    if getattr(bill, "note", ""):
        clean_bill_note = sanitize_for_escpos(bill.note)
        if clean_bill_note:
            out.extend(f"Order Note: {clean_bill_note}\n".encode("ascii"))

    out.extend(f"{divider(width, '=')}\n".encode("ascii"))

    # 12. Footer
    out.extend(CMD_ALIGN_CENTER)
    footer_text = sanitize_for_escpos(settings.invoice_footer, "Thank you for visiting!")
    out.extend(f"{center_line(footer_text, width)}\n".encode("ascii"))
    out.extend(b"\n\n\n")

    # 13. Paper Cut
    if getattr(settings, "printer_cut", True):
        out.extend(CMD_CUT)

    return bytes(out)


def build_counter_slip_bytes(bill, settings):
    """
    Builds a compact kitchen/counter slip ESC/POS byte stream.
    Prints only token, order time, and item list with modifiers and order notes.
    """
    width = get_column_width(settings)
    out = bytearray()

    # 1. Initialize
    out.extend(CMD_INIT)
    out.extend(CMD_ALIGN_CENTER)

    # 2. Header
    out.extend(CMD_BOLD_ON)
    out.extend(f"{center_line('*** KITCHEN / COUNTER SLIP ***', width)}\n".encode("ascii"))
    out.extend(CMD_BOLD_OFF)

    # 3. Token Number (Extra Prominent)
    if bill.token_number:
        out.extend(CMD_BOLD_ON)
        out.extend(CMD_DOUBLE_ON)
        out.extend(f"TOKEN: #{bill.token_number}\n".encode("ascii"))
        out.extend(CMD_DOUBLE_OFF)
        out.extend(CMD_BOLD_OFF)

    out.extend(f"{divider(width, '=')}\n".encode("ascii"))

    # 4. Meta
    out.extend(CMD_ALIGN_LEFT)
    created_dt = timezone.localtime(bill.created_at).strftime("%d-%m-%Y %I:%M %p")
    out.extend(f"{pad_line('Bill #: ' + str(bill.bill_number), created_dt, width)}\n".encode("ascii"))

    if bill.customer:
        out.extend(f"Customer: {sanitize_for_escpos(bill.customer.name)}\n".encode("ascii"))

    out.extend(f"{divider(width, '-')}\n".encode("ascii"))

    # 5. Items with Quantities & Modifiers
    out.extend(CMD_BOLD_ON)
    for item in bill.items.all().select_related("menu_item"):
        fallback_name = item.menu_item.name if item.menu_item else item.item_name
        item_title = sanitize_for_escpos(item.item_name, fallback_name)
        qty_str = format_qty(item.quantity)

        out.extend(f"{qty_str} x {item_title}\n".encode("ascii"))

        # Modifiers
        mod_names = getattr(item, "modifier_names", []) or []
        for mod in mod_names:
            clean_mod = sanitize_for_escpos(str(mod))
            if clean_mod:
                out.extend(f"     + {clean_mod}\n".encode("ascii"))

        # Item note
        if getattr(item, "note", ""):
            clean_note = sanitize_for_escpos(item.note)
            if clean_note:
                out.extend(f"     Note: {clean_note}\n".encode("ascii"))

    out.extend(CMD_BOLD_OFF)

    # 6. Order Note
    if getattr(bill, "note", ""):
        clean_bill_note = sanitize_for_escpos(bill.note)
        if clean_bill_note:
            out.extend(f"{divider(width, '-')}\n".encode("ascii"))
            out.extend(CMD_BOLD_ON)
            out.extend(f"ORDER NOTE: {clean_bill_note}\n".encode("ascii"))
            out.extend(CMD_BOLD_OFF)

    out.extend(f"{divider(width, '=')}\n".encode("ascii"))
    out.extend(b"\n\n\n")

    # 7. Paper Cut
    if getattr(settings, "printer_cut", True):
        out.extend(CMD_CUT)

    return bytes(out)


def build_test_receipt_bytes(settings):
    """
    Builds a sample test receipt byte stream to verify ESC/POS printer hardware connectivity,
    formatting, drawer kick, and auto-cut functionality.
    """
    width = get_column_width(settings)
    out = bytearray()

    out.extend(CMD_INIT)
    if getattr(settings, "printer_drawer_kick", False):
        out.extend(CMD_DRAWER_KICK)

    out.extend(CMD_ALIGN_CENTER)
    shop_name = sanitize_for_escpos(settings.business_name, "Cafe POS")
    out.extend(CMD_BOLD_ON)
    out.extend(CMD_DOUBLE_ON)
    out.extend(f"{shop_name}\n".encode("ascii"))
    out.extend(CMD_DOUBLE_OFF)
    out.extend(f"{center_line('*** HARDWARE TEST PRINT ***', width)}\n".encode("ascii"))
    out.extend(CMD_BOLD_OFF)
    out.extend(f"{divider(width, '=')}\n".encode("ascii"))

    out.extend(CMD_ALIGN_LEFT)
    now_str = timezone.localtime().strftime("%d-%m-%Y %I:%M:%S %p")
    out.extend(f"Date: {now_str}\n".encode("ascii"))
    out.extend(f"Printer Type: {getattr(settings, 'printer_type', 'usb').upper()}\n".encode("ascii"))
    out.extend(f"Address: {getattr(settings, 'printer_address', '')}\n".encode("ascii"))
    out.extend(f"Paper Width: {width} mm ({width} columns)\n".encode("ascii"))
    out.extend(f"Drawer Kick: {'Enabled' if getattr(settings, 'printer_drawer_kick', False) else 'Disabled'}\n".encode("ascii"))
    out.extend(f"Auto-Cut: {'Enabled' if getattr(settings, 'printer_cut', True) else 'Disabled'}\n".encode("ascii"))
    out.extend(f"{divider(width, '-')}\n".encode("ascii"))

    out.extend(f"{pad_line('1 x Filter Coffee', '40.00', width)}\n".encode("ascii"))
    out.extend(f"{pad_line('2 x Masala Chai', '60.00', width)}\n".encode("ascii"))
    out.extend(f"{divider(width, '-')}\n".encode("ascii"))
    out.extend(CMD_BOLD_ON)
    out.extend(f"{pad_line('TOTAL:', 'Rs. 100.00', width)}\n".encode("ascii"))
    out.extend(CMD_BOLD_OFF)
    out.extend(f"{divider(width, '=')}\n".encode("ascii"))

    out.extend(CMD_ALIGN_CENTER)
    out.extend(f"{center_line('Printer test successful!', width)}\n".encode("ascii"))
    out.extend(b"\n\n\n")

    if getattr(settings, "printer_cut", True):
        out.extend(CMD_CUT)

    return bytes(out)


def get_escpos_printer_instance(settings, dummy=False):
    """
    Instantiates an escpos printer connection based on BusinessSettings.
    Returns (printer_instance, error_message).
    """
    if dummy or getattr(settings, "printer_type", "dummy") == "dummy":
        try:
            from escpos.printer import Dummy
            return Dummy(), None
        except Exception as exc:
            return None, f"Failed to instantiate Dummy printer: {exc}"

    ptype = getattr(settings, "printer_type", "usb").lower()
    address = getattr(settings, "printer_address", "").strip()

    if ptype == "usb":
        try:
            from escpos.printer import Usb
            # Parse hex IDs e.g. 04b8:0e15 or comma-separated
            parts = address.replace(",", ":").split(":")
            if len(parts) < 2:
                return None, f"Invalid USB Vendor/Product ID '{address}'. Expected format: '04b8:0e15'"
            idVendor = int(parts[0].strip(), 16)
            idProduct = int(parts[1].strip(), 16)
            # Short timeout to avoid blocking requests
            printer = Usb(idVendor=idVendor, idProduct=idProduct, timeout=2)
            return printer, None
        except ValueError:
            return None, f"Could not parse hexadecimal USB IDs from '{address}'."
        except Exception as exc:
            return None, f"USB thermal printer connection error: {exc}"

    elif ptype == "network":
        try:
            from escpos.printer import Network
            # Parse IP:Port e.g. 192.168.1.100:9100
            parts = address.split(":")
            host = parts[0].strip()
            port = int(parts[1].strip()) if len(parts) > 1 else 9100
            printer = Network(host=host, port=port, timeout=2)
            return printer, None
        except Exception as exc:
            return None, f"Network thermal printer connection error: {exc}"

    elif ptype == "serial":
        try:
            from escpos.printer import Serial
            # Address is COM port / device path e.g. COM3 or /dev/ttyUSB0
            dev = address or "COM1"
            printer = Serial(dev=dev, timeout=2)
            return printer, None
        except Exception as exc:
            return None, f"Serial thermal printer connection error: {exc}"

    return None, f"Unsupported printer type '{ptype}'."


def print_raw_escpos_bytes(payload_bytes, settings, dummy=False):
    """
    Sends raw ESC/POS byte sequence to the configured thermal printer.
    Returns (success: bool, error_message: str, output_bytes: bytes).
    """
    printer, err = get_escpos_printer_instance(settings, dummy=dummy)
    if err:
        return False, err, b""

    try:
        # If it's a Dummy printer, output is collected in printer.output
        if hasattr(printer, "output"):
            # Dummy backend has _raw method or accepts write
            if hasattr(printer, "_raw"):
                printer._raw(payload_bytes)
            elif hasattr(printer, "device") and hasattr(printer.device, "write"):
                printer.device.write(payload_bytes)
            else:
                printer.output += payload_bytes
            return True, "", bytes(printer.output)

        # Hardware backends (Usb, Network, Serial)
        if hasattr(printer, "_raw"):
            printer._raw(payload_bytes)
        elif hasattr(printer, "device") and hasattr(printer.device, "write"):
            printer.device.write(payload_bytes)
        elif hasattr(printer, "send"):
            printer.send(payload_bytes)
        else:
            return False, "Printer backend does not support raw byte transmission", b""

        return True, "", payload_bytes
    except Exception as exc:
        return False, f"Printing hardware transmission failure: {exc}", b""
    finally:
        try:
            if hasattr(printer, "close"):
                printer.close()
        except Exception:
            pass

