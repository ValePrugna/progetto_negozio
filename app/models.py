from app.extensions import db, login_manager
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import (StringField, IntegerField, TextAreaField, SubmitField,
                     PasswordField, FloatField, BooleanField, DateField, SelectMultipleField)
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange, ValidationError, Optional
import math
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

class Prodotto(db.Model):
    __tablename__ = "prodotti"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    nome = db.Column(db.String(255), nullable=False)
    marca = db.Column(db.String(255), nullable=False)
    prezzo = db.Column(db.Float, nullable=False)
    quantita = db.Column(db.Integer, nullable=False)
    descrizione = db.Column(db.Text)
    nome_file = db.Column(db.String(255))
    file_data = db.Column(db.LargeBinary(length=16777215))

    def __init__(self, nome=None, marca=None, prezzo=None, quantita=None, descrizione=None, nome_file=None, file_data=None):
        self.nome = nome
        self.marca = marca
        self.prezzo = prezzo
        self.quantita = quantita
        self.descrizione = descrizione
        self.nome_file = nome_file
        self.file_data = file_data


class Admin(db.Model, UserMixin):
    __tablename__ = "admin"

    username = db.Column(db.String(255), primary_key=True)
    password = db.Column(db.String(255), nullable=False)

    def __init__(self, username=None, password=None):
        self.username = username
        self.password = password

    def set_password(self, password):
        self.password = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password, password)

    def get_id(self):
        return self.username

@login_manager.user_loader
def load_admin(username):
    return db.session.get(Admin, username)

class accedi(FlaskForm):
    username = StringField('username', validators=[DataRequired()])
    password = PasswordField('password', validators=[DataRequired()])
    submit = SubmitField('accedi')

def finite_price(form, field):
    if field.data is not None and not math.isfinite(field.data):
        raise ValidationError('Inserisci un prezzo valido e finito.')


class AggiungiProdotto(FlaskForm):
    nome = StringField('Nome', validators=[DataRequired(), Length(max=255)])
    marca = StringField('Marca', validators=[DataRequired(), Length(max=255)])
    # FIX MED-2: validazione numeri positivi
    prezzo = FloatField('Prezzo (€)', validators=[InputRequired(), finite_price, NumberRange(min=0.01, max=1000000000, message='Il prezzo deve essere compreso tra 0,01 e 1.000.000.000.')])
    quantita = IntegerField('Quantità in Magazzino', validators=[InputRequired(), NumberRange(min=0, max=2147483647, message='La quantità deve essere compresa tra 0 e 2.147.483.647.')])
    descrizione = TextAreaField('Descrizione', validators=[DataRequired()])
    # HIGH-6: solo immagini ammesse
    file = FileField('Immagine Prodotto', validators=[
        FileAllowed(['jpg', 'jpeg', 'png', 'gif', 'webp'], 'Solo immagini! (jpg, png, gif, webp)')
    ])
    submit = SubmitField('Salva Prodotto')


class Volontario(db.Model):
    __tablename__ = 'volontari'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    attivo = db.Column(db.Boolean, nullable=False, default=True)


class Impegno(db.Model):
    __tablename__ = 'impegni_calendario'
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(10), nullable=False)
    titolo = db.Column(db.String(160), nullable=False)
    inizio = db.Column(db.DateTime, nullable=False, index=True)
    fine = db.Column(db.DateTime, nullable=False, index=True)
    volontario_id = db.Column(db.Integer, db.ForeignKey('volontari.id'), index=True)
    volontario = db.relationship('Volontario')
    luogo = db.Column(db.String(160), nullable=False, default='')
    note = db.Column(db.Text, nullable=False, default='')
    __table_args__ = (
        db.CheckConstraint('fine > inizio', name='impegno_durata_positiva'),
        db.CheckConstraint("(tipo = 'turno' AND volontario_id IS NOT NULL) OR "
                           "(tipo = 'evento' AND volontario_id IS NULL)", name='impegno_tipo_volontario'),
    )




class VolontarioForm(FlaskForm):
    nome = StringField('Nome e cognome', validators=[DataRequired(), Length(max=120)],
                       filters=[lambda s: s.strip() if s else s])
    attivo = BooleanField('Disponibile per nuovi turni', default=True)
    submit = SubmitField('Salva volontario')


slot_volontari = db.Table(
    'slot_volontari',
    db.Column('slot_id', db.Integer, db.ForeignKey('turni_slot.id'), primary_key=True),
    db.Column('volontario_id', db.Integer, db.ForeignKey('volontari.id'), primary_key=True),
)


class TurnoSlot(db.Model):
    __tablename__ = 'turni_slot'
    id = db.Column(db.Integer, primary_key=True)
    giorno = db.Column(db.Date, nullable=False, index=True)
    fascia = db.Column(db.String(12), nullable=False)
    volontari = db.relationship('Volontario', secondary=slot_volontari, order_by='Volontario.nome')
    __table_args__ = (
        db.UniqueConstraint('giorno', 'fascia', name='slot_giorno_fascia_unico'),
        db.CheckConstraint("fascia IN ('mattina', 'pomeriggio')", name='slot_fascia_valida'),
    )


class TurnoMigrato(db.Model):
    __tablename__ = 'turni_migrati'
    impegno_id = db.Column(db.Integer, db.ForeignKey('impegni_calendario.id'), primary_key=True)


from wtforms.widgets import ListWidget, CheckboxInput


class SlotForm(FlaskForm):
    volontari = SelectMultipleField('Volontari assegnati', coerce=int,
                                   widget=ListWidget(prefix_label=False), option_widget=CheckboxInput())
    submit = SubmitField('Salva assegnazioni')


class EventoForm(FlaskForm):
    titolo = StringField('Cosa succede?', validators=[DataRequired(), Length(max=160)],
                         filters=[lambda s: s.strip() if s else s])
    giorno = DateField('Giorno', validators=[InputRequired()])
    ultimo_giorno = DateField('Ultimo giorno (facoltativo)', validators=[Optional()])
    luogo = StringField('Luogo (facoltativo)', validators=[Optional(), Length(max=160)])
    note = TextAreaField('Dettagli (facoltativi)', validators=[Optional(), Length(max=3000)])
    submit = SubmitField('Salva evento')

    def validate_giorno(self, field):
        if field.data and not 2000 <= field.data.year <= 2100:
            raise ValidationError('Scegli una data tra il 2000 e il 2100.')

    def validate_ultimo_giorno(self, field):
        if field.data:
            if not 2000 <= field.data.year <= 2100:
                raise ValidationError('Scegli una data tra il 2000 e il 2100.')
            if self.giorno.data and field.data < self.giorno.data:
                raise ValidationError('L’ultimo giorno non può precedere il primo.')
