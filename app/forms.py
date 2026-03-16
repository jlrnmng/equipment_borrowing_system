from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, Length


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
