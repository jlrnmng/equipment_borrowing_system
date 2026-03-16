from flask_wtf import FlaskForm
from wtforms import IntegerField, PasswordField, SelectField, StringField, SubmitField
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
