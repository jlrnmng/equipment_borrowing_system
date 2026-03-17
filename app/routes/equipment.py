import pymysql

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.forms import EquipmentForm
from app.models.equipment import Equipment
from app.utils.db import get_db

equipment_bp = Blueprint('equipment', __name__)


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
        
        # Check if equipment with same serial number already exists
        if form.serial_number.data:
            existing = Equipment.get_all(search=form.serial_number.data)
            if existing:
                flash(f'Equipment with this serial number already exists.', 'warning')
                return render_template('equipment/add.html', form=form)

        try:
            created_equipment = Equipment.create_equipment(
                equipment_name=form.equipment_name.data.strip(),
                category=form.category.data.strip(),
                inventory_number=inventory_number,
                brand=form.brand.data.strip() if form.brand.data else None,
                serial_number=form.serial_number.data.strip() if form.serial_number.data else None,
                property_stock_number=form.property_stock_number.data.strip() if form.property_stock_number.data else None,
                status=form.status.data,
                condition_status=form.condition_status.data,
                location=form.location.data.strip() if form.location.data else None,
                requires_supervision=form.requires_supervision.data,
                restricted_areas=form.restricted_areas.data.strip() if form.restricted_areas.data else None,
                notes=form.notes.data.strip() if form.notes.data else None,
                added_by=current_user.staff_id,
            )
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

    return render_template('equipment/detail.html', equipment=equipment)


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
            Equipment.update_equipment(
                equipment_id=equipment_id,
                equipment_name=form.equipment_name.data.strip(),
                category=form.category.data.strip(),
                inventory_number=form.inventory_number.data.strip(),
                brand=form.brand.data.strip() if form.brand.data else None,
                serial_number=form.serial_number.data.strip() if form.serial_number.data else None,
                property_stock_number=form.property_stock_number.data.strip() if form.property_stock_number.data else None,
                status=form.status.data,
                condition_status=form.condition_status.data,
                location=form.location.data.strip() if form.location.data else None,
                requires_supervision=form.requires_supervision.data,
                restricted_areas=form.restricted_areas.data.strip() if form.restricted_areas.data else None,
                notes=form.notes.data.strip() if form.notes.data else None,
            )
        except pymysql.err.IntegrityError as exc:
            _rollback_db_safely()
            _flash_integrity_error(exc)
            return render_template('equipment/edit.html', form=form, equipment=equipment)
        except Exception:
            _rollback_db_safely()
            current_app.logger.exception('Failed to edit equipment id=%s', equipment_id)
            flash('An unexpected error occurred while updating equipment. Please try again.', 'danger')
            return render_template('equipment/edit.html', form=form, equipment=equipment)

        flash('Equipment updated successfully!', 'success')
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
