import os
import re

import qrcode
from flask import current_app


def _build_qr_image(payload):
    qr = qrcode.QRCode(version=1, box_size=10, border=3)
    qr.add_data(payload)
    qr.make(fit=True)
    return qr.make_image(fill_color='black', back_color='white')


def _save_qr(relative_dir, filename, payload):
    output_dir = os.path.join(current_app.static_folder, relative_dir)
    os.makedirs(output_dir, exist_ok=True)

    abs_path = os.path.join(output_dir, filename)
    rel_path = f"{relative_dir.replace(os.sep, '/')}/{filename}"

    image = _build_qr_image(payload)
    image.save(abs_path)
    return rel_path


def _sanitize_filename_part(value):
    """Normalize a value for safe cross-platform filename usage."""
    text = str(value or '').strip()
    if not text:
        return ''

    # Replace common invalid path characters and collapse whitespace.
    text = re.sub(r'[<>:"/\\|?*]+', '_', text)
    text = re.sub(r'\s+', '_', text)
    return text.strip('._')


def build_member_qr_filename(member_code):
    """Build deterministic member QR filename based on unique member code."""
    safe_code = _sanitize_filename_part(member_code) or 'member'
    return f"{safe_code}.png"


def build_member_qr_download_filename(last_name=None, student_id=None, member_code=None):
    """Build member QR download name: lastname_IDnumber (with safe fallbacks)."""
    safe_last_name = _sanitize_filename_part(last_name) or 'member'
    safe_student_id = _sanitize_filename_part(student_id) or _sanitize_filename_part(member_code) or 'id'
    return f"{safe_last_name}_{safe_student_id}.png"


def generate_member_qr(member_code):
    """Generate a QR PNG for a member code and return static-relative path."""
    filename = build_member_qr_filename(member_code)
    return _save_qr(os.path.join('qr', 'members'), filename, f"MEMBER:{member_code}")


def build_equipment_qr_filename(equipment_code, equipment_name=None, serial_number=None, property_stock_number=None):
    """Build download/generation filename for equipment QR based on naming rules."""
    safe_name = _sanitize_filename_part(equipment_name) or _sanitize_filename_part(equipment_code) or 'equipment'
    safe_serial = _sanitize_filename_part(serial_number)
    safe_property_stock = _sanitize_filename_part(property_stock_number)

    identifier = safe_serial or safe_property_stock or _sanitize_filename_part(equipment_code)
    return f"{safe_name}_{identifier}.png" if identifier else f"{safe_name}.png"


def generate_equipment_qr(equipment_code, equipment_name=None, serial_number=None, property_stock_number=None):
    """Generate a QR PNG for an equipment code and return static-relative path."""
    filename = build_equipment_qr_filename(
        equipment_code=equipment_code,
        equipment_name=equipment_name,
        serial_number=serial_number,
        property_stock_number=property_stock_number,
    )

    return _save_qr(os.path.join('qr', 'equipment'), filename, f"EQUIPMENT:{equipment_code}")


def extract_member_code(value):
    text = (value or '').strip().upper()
    if not text:
        return None

    match = re.search(r'(MEM\d{3,})', text)
    return match.group(1) if match else None


def extract_equipment_code(value):
    text = (value or '').strip().upper()
    if not text:
        return None

    # Accept payload variants: EQUIPMENT:EQ-XXXX or raw equipment code.
    match = re.search(r'(EQ-[A-Z0-9]{6,}|EQP\d{3,})', text)
    return match.group(1) if match else None
