import os

import qrcode
from flask import current_app


def generate_member_qr(member_code):
    """Generate a QR PNG for a member code and return static-relative path."""
    relative_dir = os.path.join('qr', 'members')
    output_dir = os.path.join(current_app.static_folder, relative_dir)
    os.makedirs(output_dir, exist_ok=True)

    filename = f"{member_code}.png"
    abs_path = os.path.join(output_dir, filename)
    rel_path = f"qr/members/{filename}"

    qr = qrcode.QRCode(version=1, box_size=10, border=3)
    qr.add_data(f"MEMBER:{member_code}")
    qr.make(fit=True)
    image = qr.make_image(fill_color='black', back_color='white')
    image.save(abs_path)

    return rel_path
