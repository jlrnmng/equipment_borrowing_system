from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.forms import EquipmentForm
from app.models.equipment import Equipment
from app.models.staff import Staff

equipment_bp = Blueprint('equipment', __name__)


def _is_authorized_manager():
    """Only admin or authorized staff can manage equipment."""
    return current_user.role in ('admin', 'staff')


@equipment_bp.route('/equipment/add', methods=['GET', 'POST'])
@login_required
def add_equipment():
    if not _is_authorized_manager():
        flash('You are not authorized to manage equipment.', 'danger')
        return redirect(url_for('dashboard.index'))

    form = EquipmentForm()
    created_equipment = None

    if form.validate_on_submit():
        inventory_number = Equipment.get_next_inventory_number()
        
        # Check if equipment with same serial number already exists
        if form.serial_number.data:
            existing = Equipment.get_all(search=form.serial_number.data)
            if existing:
                flash(f'Equipment with this serial number already exists.', 'warning')
                return render_template('equipment/add.html', form=form)

        created_equipment = Equipment.create_equipment(
            equipment_name=form.equipment_name.data.strip(),
            inventory_number=inventory_number,
            brand=form.brand.data.strip() if form.brand.data else None,
            serial_number=form.serial_number.data.strip() if form.serial_number.data else None,
            property_stock_number=form.property_stock_number.data.strip() if form.property_stock_number.data else None,
            condition_status=form.condition_status.data,
            location=form.location.data.strip() if form.location.data else None,
            requires_supervision=form.requires_supervision.data,
            restricted_areas=form.restricted_areas.data.strip() if form.restricted_areas.data else None,
            notes=form.notes.data.strip() if form.notes.data else None,
            added_by=current_user.staff_id,
        )

        flash(f'Equipment "{form.equipment_name.data}" added successfully!', 'success')
        return render_template('equipment/add.html', form=EquipmentForm(), created_equipment=created_equipment)

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
        # Update equipment in database
        db = current_app.extensions.get('db')
        if not db:
            from app.utils.db import get_db
            db = get_db()
        
        with db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE equipment 
                SET equipment_name = %s, brand = %s, serial_number = %s,
                    property_stock_number = %s, condition_status = %s, location = %s,
                    requires_supervision = %s, restricted_areas = %s, notes = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE equipment_id = %s
                """,
                (
                    form.equipment_name.data.strip(),
                    form.brand.data.strip() if form.brand.data else None,
                    form.serial_number.data.strip() if form.serial_number.data else None,
                    form.property_stock_number.data.strip() if form.property_stock_number.data else None,
                    form.condition_status.data,
                    form.location.data.strip() if form.location.data else None,
                    form.requires_supervision.data,
                    form.restricted_areas.data.strip() if form.restricted_areas.data else None,
                    form.notes.data.strip() if form.notes.data else None,
                    equipment_id,
                ),
            )
            db.commit()

        flash('Equipment updated successfully!', 'success')
        return redirect(url_for('equipment.equipment_detail', equipment_id=equipment_id))

    elif request.method == 'GET':
        form.equipment_name.data = equipment['equipment_name']
        form.brand.data = equipment['brand']
        form.serial_number.data = equipment['serial_number']
        form.property_stock_number.data = equipment['property_stock_number']
        form.condition_status.data = equipment['condition_status']
        form.location.data = equipment['location']
        form.requires_supervision.data = equipment['requires_supervision']
        form.restricted_areas.data = equipment['restricted_areas']
        form.notes.data = equipment['notes']

    return render_template('equipment/edit.html', form=form, equipment=equipment)
