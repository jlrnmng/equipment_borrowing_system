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


def generate_member_qr(member_code):
    """Generate a QR PNG for a member code and return static-relative path."""
    filename = f"{member_code}.png"
    return _save_qr(os.path.join('qr', 'members'), filename, f"MEMBER:{member_code}")


def generate_equipment_qr(equipment_code):
    """Generate a QR PNG for an equipment code and return static-relative path."""
    filename = f"{equipment_code}.png"
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
