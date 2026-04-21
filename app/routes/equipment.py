import os
import uuid
from io import BytesIO

import pymysql
from PIL import Image, UnidentifiedImageError
from PIL import ImageOps

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from app.forms import EquipmentForm
from app.models.equipment import Equipment
from app.realtime import emit_app_data_changed
from app.utils.db import get_db
from app.utils.qr import build_equipment_qr_filename, generate_equipment_qr

equipment_bp = Blueprint('equipment', __name__)

ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}


def _is_authorized_manager():
    """Only admin or authorized staff can manage equipment."""
    return current_user.role in ('admin', 'staff')


def _rollback_db_safely():
    """Rollback request DB transaction if a write fails."""
    try:
        get_db().rollback()
    except Exception:
        pass


def _flash_integrity_error(exc):
    """Show user-friendly duplicate/constraint messages for DB write errors."""
    message = str(exc).lower()

    if 'inventory_number' in message or 'uq_equipment_inventory_number' in message:
        flash('Inventory number already exists. Please use a unique value.', 'warning')
        return

    if 'property_stock_number' in message or 'uq_equipment_property_stock_number' in message:
        flash('Property/Stock number already exists. Please use a unique value.', 'warning')
        return

    if 'equipment_code' in message:
        flash('Equipment code conflict detected. Try changing the inventory number.', 'warning')
        return

    if 'serial_number' in message:
        flash('Serial number already exists. Please verify and try again.', 'warning')
        return

    flash('Unable to save equipment due to a data constraint. Please review your entries.', 'danger')


def _is_allowed_image_filename(filename):
    if not filename or '.' not in filename:
        return False
    return filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def _save_equipment_image(file_storage, equipment_id):
    """Validate and save a normalized equipment image under static/uploads/equipment."""
    image_bytes = _prepare_equipment_image_bytes(file_storage)
    return _save_equipment_image_bytes(image_bytes, equipment_id)


def _prepare_equipment_image_bytes(file_storage):
    """Validate, normalize, and return JPEG bytes for an uploaded equipment image."""
    filename = secure_filename(file_storage.filename or '')
    if not _is_allowed_image_filename(filename):
        raise ValueError('Please upload a valid image file (PNG, JPG, JPEG, or WEBP).')

    max_size_bytes = int(current_app.config.get('EQUIPMENT_IMAGE_MAX_BYTES', 5 * 1024 * 1024))
    file_storage.stream.seek(0)
    raw_bytes = file_storage.stream.read()
    file_storage.stream.seek(0)

    if len(raw_bytes) > max_size_bytes:
        raise ValueError('Image is too large. Maximum size is 5MB.')

    if not raw_bytes:
        raise ValueError('Uploaded image file is empty.')

    try:
        with Image.open(BytesIO(raw_bytes)) as image:
            # Apply EXIF orientation before resizing to avoid visually broken results.
            image = ImageOps.exif_transpose(image)
            image = image.convert('RGB')
            image.thumbnail((720, 720))

            buffer = BytesIO()
            image.save(buffer, format='JPEG', quality=88, optimize=True)
            buffer.seek(0)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError('Uploaded file is not a valid image.') from exc

    return buffer.getvalue()


def _save_equipment_image_bytes(image_bytes, equipment_id):
    """Store normalized equipment image bytes and return the static-relative path."""
    relative_dir = os.path.join('uploads', 'equipment')
    absolute_dir = os.path.join(current_app.static_folder, relative_dir)
    os.makedirs(absolute_dir, exist_ok=True)

    output_name = f"equipment_{equipment_id}_{uuid.uuid4().hex[:10]}.jpg"
    absolute_path = os.path.join(absolute_dir, output_name)
    with open(absolute_path, 'wb') as file_handle:
        file_handle.write(image_bytes)

    return os.path.join(relative_dir, output_name).replace('\\', '/')


@equipment_bp.route('/equipment/add', methods=['GET', 'POST'])
@login_required
def add_equipment():
    if not _is_authorized_manager():
        flash('You are not authorized to manage equipment.', 'danger')
        return redirect(url_for('dashboard.index'))

    form = EquipmentForm()
    created_equipment = None

    if request.method == 'GET' and not form.inventory_number.data:
        form.inventory_number.data = Equipment.get_next_inventory_number()

    if form.validate_on_submit():
        inventory_number = form.inventory_number.data.strip()
        serial_number = form.serial_number.data.strip() if form.serial_number.data else None
        property_stock_number = form.property_stock_number.data.strip() if form.property_stock_number.data else None
        equipment_image_path = None
        uploaded_image = request.files.get('equipment_photo')
        prepared_image_bytes = None

        duplicate_conflicts = Equipment.find_duplicate_fields(
            inventory_number=inventory_number,
            serial_number=serial_number,
            property_stock_number=property_stock_number,
        )
        if duplicate_conflicts:
            duplicate_labels = {
                'inventory_number': 'Inventory number',
                'serial_number': 'Serial number',
                'property_stock_number': 'Property/Stock number',
            }
            messages = [
                f"{duplicate_labels.get(conflict['field'], conflict['field'])} already exists ({conflict.get('value')})."
                for conflict in duplicate_conflicts
            ]
            flash(' '.join(messages), 'warning')
            return render_template('equipment/add.html', form=form)

        if uploaded_image and uploaded_image.filename:
            try:
                prepared_image_bytes = _prepare_equipment_image_bytes(uploaded_image)
            except ValueError as exc:
                flash(str(exc), 'warning')
                return render_template('equipment/add.html', form=form)

        try:
            created_equipment = Equipment.create_equipment(
                equipment_name=form.equipment_name.data.strip(),
                category=form.category.data.strip(),
                inventory_number=inventory_number,
                brand=form.brand.data.strip() if form.brand.data else None,
                serial_number=serial_number,
                property_stock_number=property_stock_number,
                status=form.status.data,
                condition_status=form.condition_status.data,
                location=form.location.data.strip() if form.location.data else None,
                requires_supervision=form.requires_supervision.data,
                restricted_areas=form.restricted_areas.data.strip() if form.restricted_areas.data else None,
                notes=form.notes.data.strip() if form.notes.data else None,
                added_by=current_user.id,
            )

            if prepared_image_bytes:
                equipment_image_path = _save_equipment_image_bytes(prepared_image_bytes, created_equipment['equipment_id'])
                Equipment.update_equipment(
                    equipment_id=created_equipment['equipment_id'],
                    equipment_name=created_equipment['equipment_name'],
                    category=created_equipment['category'],
                    inventory_number=created_equipment['inventory_number'],
                    brand=created_equipment['brand'],
                    serial_number=created_equipment['serial_number'],
                    property_stock_number=created_equipment['property_stock_number'],
                    status=created_equipment['status'],
                    condition_status=created_equipment['condition_status'],
                    location=created_equipment['location'],
                    requires_supervision=bool(created_equipment.get('requires_supervision')),
                    restricted_areas=created_equipment.get('restricted_areas'),
                    notes=created_equipment.get('notes'),
                    equipment_image_path=equipment_image_path,
                )
                created_equipment['equipment_image_path'] = equipment_image_path

            try:
                qr_path = generate_equipment_qr(
                    created_equipment['equipment_code'] or created_equipment['inventory_number'],
                    created_equipment['equipment_name'],
                    created_equipment['serial_number'],
                    created_equipment.get('property_stock_number')
                )
                Equipment.update_qr_path(created_equipment['equipment_id'], qr_path)
                created_equipment['qr_code_path'] = qr_path
            except Exception:
                current_app.logger.exception('Failed to generate equipment QR for id=%s', created_equipment.get('equipment_id'))
        except pymysql.err.IntegrityError as exc:
            _rollback_db_safely()
            _flash_integrity_error(exc)
            return render_template('equipment/add.html', form=form)
        except Exception:
            _rollback_db_safely()
            current_app.logger.exception('Failed to add equipment')
            flash('An unexpected error occurred while adding equipment. Please try again.', 'danger')
            return render_template('equipment/add.html', form=form)

        flash(f'Equipment "{form.equipment_name.data}" added successfully!', 'success')
        emit_app_data_changed(reason='equipment_added', include_staff=True, include_members=True)
        fresh_form = EquipmentForm()
        fresh_form.inventory_number.data = Equipment.get_next_inventory_number()
        return render_template('equipment/add.html', form=fresh_form, created_equipment=created_equipment)

    if request.method == 'POST':
        flash('Please correct the highlighted fields and try again.', 'warning')

    return render_template('equipment/add.html', form=form)


@equipment_bp.route('/equipment', methods=['GET'])
@login_required
def list_equipment():
    """Display list of all equipment with filters."""
    status_filter = request.args.get('status')
    location_filter = request.args.get('location')
    search_query = request.args.get('search')

    equipment_list = Equipment.get_all(
        status=status_filter,
        location=location_filter,
        search=search_query,
    )

    stats = Equipment.get_statistics()

    # Get unique locations for filter dropdown
    all_equipment = Equipment.get_all()
    locations = sorted(set(e['location'] for e in all_equipment if e['location']))

    return render_template(
        'equipment/list.html',
        equipment=equipment_list,
        stats=stats,
        locations=locations,
        current_status=status_filter,
        current_location=location_filter,
        search_query=search_query,
    )


@equipment_bp.route('/equipment/<int:equipment_id>', methods=['GET'])
@login_required
def equipment_detail(equipment_id):
    """Display equipment details."""
    equipment = Equipment.get_by_id(equipment_id)
    if not equipment:
        flash('Equipment not found.', 'danger')
        return redirect(url_for('equipment.list_equipment'))

    if not equipment.get('qr_code_path'):
        try:
            qr_path = generate_equipment_qr(
                equipment.get('equipment_code') or equipment.get('inventory_number'),
                equipment.get('equipment_name'),
                equipment.get('serial_number'),
                equipment.get('property_stock_number')
            )
            Equipment.update_qr_path(equipment['equipment_id'], qr_path)
            equipment['qr_code_path'] = qr_path
        except Exception:
            current_app.logger.exception('Failed to auto-generate missing equipment QR id=%s', equipment.get('equipment_id'))

    return render_template('equipment/detail.html', equipment=equipment)


@equipment_bp.route('/equipment/<int:equipment_id>/qr/download', methods=['GET'])
@login_required
def download_equipment_qr(equipment_id):
    """Download equipment QR with normalized filename rules regardless of stored file path."""
    equipment = Equipment.get_by_id(equipment_id)
    if not equipment:
        flash('Equipment not found.', 'danger')
        return redirect(url_for('equipment.list_equipment'))

    equipment_code = equipment.get('equipment_code') or equipment.get('inventory_number')
    download_name = build_equipment_qr_filename(
        equipment_code=equipment_code,
        equipment_name=equipment.get('equipment_name'),
        serial_number=equipment.get('serial_number'),
        property_stock_number=equipment.get('property_stock_number'),
    )

    qr_path = equipment.get('qr_code_path')
    if not qr_path:
        qr_path = generate_equipment_qr(
            equipment_code,
            equipment.get('equipment_name'),
            equipment.get('serial_number'),
            equipment.get('property_stock_number'),
        )
        Equipment.update_qr_path(equipment_id, qr_path)

    abs_qr_path = os.path.join(current_app.static_folder, qr_path)
    if not os.path.exists(abs_qr_path):
        qr_path = generate_equipment_qr(
            equipment_code,
            equipment.get('equipment_name'),
            equipment.get('serial_number'),
            equipment.get('property_stock_number'),
        )
        Equipment.update_qr_path(equipment_id, qr_path)
        abs_qr_path = os.path.join(current_app.static_folder, qr_path)

    return send_file(abs_qr_path, as_attachment=True, download_name=download_name, mimetype='image/png')


@equipment_bp.route('/equipment/<int:equipment_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_equipment(equipment_id):
    """Edit equipment details."""
    if not _is_authorized_manager():
        flash('You are not authorized to manage equipment.', 'danger')
        return redirect(url_for('dashboard.index'))

    equipment = Equipment.get_by_id(equipment_id)
    if not equipment:
        flash('Equipment not found.', 'danger')
        return redirect(url_for('equipment.list_equipment'))

    form = EquipmentForm()

    if form.validate_on_submit():
        try:
            equipment_image_path = equipment.get('equipment_image_path')
            uploaded_image = request.files.get('equipment_photo')

            if uploaded_image and uploaded_image.filename:
                equipment_image_path = _save_equipment_image(uploaded_image, equipment_id)

            updated_equipment = Equipment.update_equipment(
                equipment_id=equipment_id,
                equipment_name=form.equipment_name.data.strip(),
                category=form.category.data.strip(),
                inventory_number=form.inventory_number.data.strip(),
                brand=form.brand.data.strip() if form.brand.data else None,
                serial_number=form.serial_number.data.strip() if form.serial_number.data else None,
                property_stock_number=form.property_stock_number.data.strip() if form.property_stock_number.data else None,
                status=form.status.data,
                condition_status=form.condition_status.data,
                location=equipment.get('location'),
                requires_supervision=bool(equipment.get('requires_supervision')),
                restricted_areas=equipment.get('restricted_areas'),
                notes=form.notes.data.strip() if form.notes.data else None,
                equipment_image_path=equipment_image_path,
            )

            try:
                qr_path = generate_equipment_qr(
                    updated_equipment.get('equipment_code') or updated_equipment.get('inventory_number'),
                    updated_equipment.get('equipment_name'),
                    updated_equipment.get('serial_number'),
                    updated_equipment.get('property_stock_number')
                )
                Equipment.update_qr_path(equipment_id, qr_path)
            except Exception:
                current_app.logger.exception('Failed to refresh equipment QR id=%s', equipment_id)
        except pymysql.err.IntegrityError as exc:
            _rollback_db_safely()
            _flash_integrity_error(exc)
            return render_template('equipment/edit.html', form=form, equipment=equipment)
        except ValueError as exc:
            flash(str(exc), 'warning')
            return render_template('equipment/edit.html', form=form, equipment=equipment)
        except Exception:
            _rollback_db_safely()
            current_app.logger.exception('Failed to edit equipment id=%s', equipment_id)
            flash('An unexpected error occurred while updating equipment. Please try again.', 'danger')
            return render_template('equipment/edit.html', form=form, equipment=equipment)

        flash('Equipment updated successfully!', 'success')
        emit_app_data_changed(reason='equipment_updated', include_staff=True, include_members=True)
        return redirect(url_for('equipment.equipment_detail', equipment_id=equipment_id))

    elif request.method == 'GET':
        form.equipment_name.data = equipment['equipment_name']
        form.inventory_number.data = equipment['inventory_number']
        form.category.data = equipment['category']
        form.brand.data = equipment['brand']
        form.serial_number.data = equipment['serial_number']
        form.property_stock_number.data = equipment['property_stock_number']
        form.status.data = equipment['status']
        form.condition_status.data = equipment['condition_status']
        form.location.data = equipment['location']
        form.requires_supervision.data = equipment['requires_supervision']
        form.restricted_areas.data = equipment['restricted_areas']
        form.notes.data = equipment['notes']

    if request.method == 'POST':
        flash('Please correct the highlighted fields and try again.', 'warning')

    return render_template('equipment/edit.html', form=form, equipment=equipment)


@equipment_bp.route('/equipment/<int:equipment_id>/qr/regenerate', methods=['POST'])
@login_required
def regenerate_equipment_qr(equipment_id):
    if not _is_authorized_manager():
        flash('You are not authorized to manage equipment.', 'danger')
        return redirect(url_for('dashboard.index'))

    equipment = Equipment.get_by_id(equipment_id)
    if not equipment:
        flash('Equipment not found.', 'danger')
        return redirect(url_for('equipment.list_equipment'))

    try:
        qr_path = generate_equipment_qr(
            equipment.get('equipment_code') or equipment.get('inventory_number'),
            equipment.get('equipment_name'),
            equipment.get('serial_number'),
            equipment.get('property_stock_number')
        )
        Equipment.update_qr_path(equipment_id, qr_path)
    except Exception:
        current_app.logger.exception('Failed to regenerate equipment QR id=%s', equipment_id)
        flash('Unable to regenerate equipment QR right now.', 'danger')
        return redirect(url_for('equipment.equipment_detail', equipment_id=equipment_id))

    flash('Equipment QR code regenerated successfully.', 'success')
    emit_app_data_changed(reason='equipment_qr_regenerated', include_staff=True, include_members=False)
    return redirect(url_for('equipment.equipment_detail', equipment_id=equipment_id))
