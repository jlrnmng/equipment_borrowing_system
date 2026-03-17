from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, NumberRange, Optional


class LoginForm(FlaskForm):
    email = StringField(
        'Email Address',
        validators=[DataRequired(), Email(), Length(max=100)],
    )
    password = PasswordField(
        'Password',
        validators=[DataRequired(), Length(min=6, max=255)],
    )
    submit = SubmitField('Sign In')


class MemberRegistrationForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired(), Length(max=50)])
    middle_name = StringField('Middle Name', validators=[Optional(), Length(max=50)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(max=50)])
    email = StringField('Google Email', validators=[DataRequired(), Email(), Length(max=100)])
    phone = StringField('Phone Number', validators=[Optional(), Length(max=20)])
    student_id = StringField('ID Number', validators=[Optional(), Length(max=50)])
    startup = StringField('Startup/Agency', validators=[Optional(), Length(max=100)])
    max_borrow_limit = IntegerField(
        'Max Borrow Limit',
        validators=[DataRequired(), NumberRange(min=1, max=10)],
        default=3,
    )
    submit = SubmitField('Register Member')


class StaffRegistrationForm(FlaskForm):
    full_name = StringField('Full Name', validators=[DataRequired(), Length(max=100)])
    email = StringField('Google Email', validators=[DataRequired(), Email(), Length(max=100)])
    role = SelectField(
        'Role',
        choices=[('staff', 'Staff'), ('viewer', 'Viewer')],
        validators=[DataRequired()],
        default='staff',
    )
    submit = SubmitField('Register Staff Account')


class EquipmentForm(FlaskForm):
    equipment_name = StringField(
        'Equipment Name',
        validators=[DataRequired(), Length(min=3, max=150)],
    )
    inventory_number = StringField(
        'Inventory Number',
        validators=[DataRequired(), Length(max=150)],
    )
    category = StringField(
        'Category',
        validators=[DataRequired(), Length(max=50)],
        default='General',
    )
    brand = StringField('Brand', validators=[Optional(), Length(max=100)])
    serial_number = StringField('Serial Number', validators=[Optional(), Length(max=150)])
    property_stock_number = StringField('Property/Stock Number', validators=[Optional(), Length(max=80)])
    status = SelectField(
        'Status',
        choices=[
            ('available', 'Available'),
            ('borrowed', 'Borrowed'),
            ('maintenance', 'Maintenance'),
            ('retired', 'Retired'),
        ],
        validators=[DataRequired()],
        default='available',
    )
    condition_status = SelectField(
        'Condition',
        choices=[
            ('excellent', 'Excellent'),
            ('good', 'Good'),
            ('fair', 'Fair'),
            ('poor', 'Poor'),
        ],
        validators=[DataRequired()],
        default='good',
    )
    location = StringField('Storage Location', validators=[Optional(), Length(max=100)])
    requires_supervision = BooleanField('Requires Staff Supervision During Use')
    restricted_areas = StringField(
        'Restricted Areas',
        validators=[Optional(), Length(max=255)],
        description='List areas where equipment cannot be used (e.g., "Outside facility, High-risk areas")',
    )
    notes = TextAreaField('Notes', validators=[Optional(), Length(max=1000)])
    submit = SubmitField('Add Equipment')
