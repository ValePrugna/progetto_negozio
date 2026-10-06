import calendar as month_calendar
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from app.extensions import db
from app.models import EventoForm, Impegno, SlotForm, TurnoSlot, Volontario, VolontarioForm

bp = Blueprint('calendar', __name__, url_prefix='/calendario')
MONTHS = ('Gennaio', 'Febbraio', 'Marzo', 'Aprile', 'Maggio', 'Giugno',
          'Luglio', 'Agosto', 'Settembre', 'Ottobre', 'Novembre', 'Dicembre')
PERIODS = (('mattina', 'Mattina'), ('pomeriggio', 'Pomeriggio'))


def today():
    return datetime.now(ZoneInfo('Europe/Rome')).date()


def month_value(raw):
    try:
        value = datetime.strptime(raw, '%Y-%m').date().replace(day=1)
        if not 2000 <= value.year <= 2100:
            raise ValueError
        return value
    except (TypeError, ValueError):
        abort(400, description='Mese non valido.')


def day_value(raw):
    try:
        value = date.fromisoformat(raw)
        if not 2000 <= value.year <= 2100:
            raise ValueError
        return value
    except (TypeError, ValueError):
        abort(400, description='Giorno non valido.')


def month_context():
    month = month_value(request.args.get('mese', today().strftime('%Y-%m')))
    next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    previous = (month - timedelta(days=1)).strftime('%Y-%m') if month != date(2000, 1, 1) else None
    following = next_month.strftime('%Y-%m') if next_month.year <= 2100 else None
    return {'month': month, 'next_month': next_month,
            'label': f'{MONTHS[month.month-1]} {month.year}', 'previous': previous, 'following': following}


@bp.route('')
@bp.route('/')
@login_required
def index():
    context = month_context()
    month = context['month']
    weeks = month_calendar.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month)
    slots = TurnoSlot.query.options(selectinload(TurnoSlot.volontari)).filter(TurnoSlot.giorno >= month,
                                    TurnoSlot.giorno < context['next_month']).all()
    slot_map = {(slot.giorno, slot.fascia): slot for slot in slots}
    covered = sum(bool(slot.volontari) for slot in slots)
    total = month_calendar.monthrange(month.year, month.month)[1] * 2
    return render_template('calendar.html', **context, weeks=weeks, slot_map=slot_map,
                           periods=PERIODS, today=today(), covered=covered, total=total)


@bp.route('/slot/<giorno>/<fascia>', methods=['GET', 'POST'])
@login_required
def slot(giorno, fascia):
    day = day_value(giorno)
    if fascia not in dict(PERIODS):
        abort(404)
    query = TurnoSlot.query.filter_by(giorno=day, fascia=fascia)
    saved = (query.with_for_update() if request.method == 'POST' else query).first()
    existing_ids = {v.id for v in saved.volontari} if saved else set()
    volunteers = Volontario.query.filter(db.or_(Volontario.attivo.is_(True),
                                               Volontario.id.in_(existing_ids))).order_by(Volontario.nome).all()
    form = SlotForm()
    form.volontari.choices = [(v.id, v.nome + (' (non attivo)' if not v.attivo else '')) for v in volunteers]
    if request.method == 'GET':
        form.volontari.data = sorted(existing_ids)
    if form.validate_on_submit():
        ids = sorted(set(form.volontari.data))
        selected = db.session.execute(db.select(Volontario).where(Volontario.id.in_(ids))
                                      .order_by(Volontario.id).with_for_update()
                                      .execution_options(populate_existing=True)).scalars().all() if ids else []
        if any(not v.attivo and v.id not in existing_ids for v in selected):
            form.volontari.errors.append('Un volontario è stato disattivato. Ricarica la pagina.')
        else:
            if saved is None:
                saved = TurnoSlot(giorno=day, fascia=fascia)
                db.session.add(saved)
            saved.volontari = selected
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash('Questo slot è stato aggiornato nel frattempo. Controlla le assegnazioni e riprova.', 'info')
                return redirect(url_for('calendar.slot', giorno=giorno, fascia=fascia))
            flash('Assegnazioni salvate.' if selected else 'Slot liberato: nessun volontario assegnato.', 'success')
            return redirect(url_for('calendar.index', mese=day.strftime('%Y-%m')))
    return render_template('slot_form.html', form=form, day=day, period=dict(PERIODS)[fascia],
                           saved=saved, volunteers=volunteers)


@bp.route('/eventi')
@login_required
def events():
    context = month_context()
    entries = Impegno.query.filter_by(tipo='evento').filter(
        Impegno.inizio < datetime.combine(context['next_month'], time.min),
        Impegno.fine > datetime.combine(context['month'], time.min)).order_by(Impegno.inizio, Impegno.id).all()
    return render_template('events.html', **context, entries=entries, timedelta=timedelta)


def save_event(entry=None):
    if entry and entry.tipo != 'evento':
        abort(404)
    form = EventoForm(obj=entry)
    if request.method == 'GET':
        if entry:
            form.giorno.data = entry.inizio.date()
            last = (entry.fine - timedelta(microseconds=1)).date()
            form.ultimo_giorno.data = last if last != form.giorno.data else None
        else:
            form.giorno.data = day_value(request.args.get('giorno', today().isoformat()))
    if form.validate_on_submit():
        if entry is None:
            entry = Impegno(tipo='evento')
            db.session.add(entry)
        entry.titolo = form.titolo.data
        entry.inizio = datetime.combine(form.giorno.data, time.min)
        entry.fine = datetime.combine((form.ultimo_giorno.data or form.giorno.data) + timedelta(days=1), time.min)
        entry.volontario_id = None
        entry.luogo = form.luogo.data or ''
        entry.note = form.note.data or ''
        db.session.commit()
        flash('Evento salvato.', 'success')
        return redirect(url_for('calendar.events', mese=entry.inizio.strftime('%Y-%m')))
    return render_template('event_form.html', form=form, entry=entry)


@bp.route('/eventi/nuovo', methods=['GET', 'POST'])
@login_required
def new_event():
    return save_event()


@bp.route('/eventi/<int:entry_id>/modifica', methods=['GET', 'POST'])
@login_required
def edit_event(entry_id):
    return save_event(db.get_or_404(Impegno, entry_id))


@bp.route('/eventi/<int:entry_id>/elimina', methods=['POST'])
@login_required
def delete_event(entry_id):
    entry = db.get_or_404(Impegno, entry_id)
    if entry.tipo != 'evento':
        abort(404)
    month = entry.inizio.strftime('%Y-%m')
    db.session.delete(entry)
    db.session.commit()
    flash('Evento eliminato.', 'success')
    return redirect(url_for('calendar.events', mese=month))


@bp.route('/volontari', methods=['GET', 'POST'])
@login_required
def volunteers():
    form = VolontarioForm()
    if form.validate_on_submit():
        volunteer = Volontario(nome=form.nome.data, attivo=form.attivo.data)
        db.session.add(volunteer)
        db.session.commit()
        flash('Volontario aggiunto.', 'success')
        return redirect(url_for('calendar.volunteers'))
    return render_template('volunteers.html', form=form,
                           volunteers=Volontario.query.order_by(Volontario.nome).all())


@bp.route('/volontari/<int:volunteer_id>/modifica', methods=['GET', 'POST'])
@login_required
def edit_volunteer(volunteer_id):
    volunteer = db.get_or_404(Volontario, volunteer_id)
    form = VolontarioForm(obj=volunteer)
    if form.validate_on_submit():
        volunteer.nome = form.nome.data
        volunteer.attivo = form.attivo.data
        db.session.commit()
        flash('Volontario aggiornato. Le assegnazioni esistenti sono conservate.', 'success')
        return redirect(url_for('calendar.volunteers'))
    return render_template('volunteer_form.html', form=form, volunteer=volunteer)
