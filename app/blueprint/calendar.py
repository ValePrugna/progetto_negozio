import calendar as month_calendar
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required
from app.extensions import db
from app.models import Impegno, ImpegnoForm, Volontario, VolontarioForm

bp = Blueprint('calendar', __name__, url_prefix='/calendario')
MONTHS = ('Gennaio', 'Febbraio', 'Marzo', 'Aprile', 'Maggio', 'Giugno',
          'Luglio', 'Agosto', 'Settembre', 'Ottobre', 'Novembre', 'Dicembre')


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


@bp.route('')
@bp.route('/')
@login_required
def index():
    current = today()
    month = month_value(request.args.get('mese', current.strftime('%Y-%m')))
    weeks = month_calendar.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month)
    start = datetime.combine(weeks[0][0], time.min)
    end = datetime.combine(weeks[-1][-1] + timedelta(days=1), time.min)
    query = Impegno.query.filter(Impegno.inizio < end, Impegno.fine > start)
    kind = request.args.get('tipo', '')
    if kind not in ('', 'turno', 'evento'):
        abort(400)
    if kind:
        query = query.filter_by(tipo=kind)
    volunteer = request.args.get('volontario', '')
    if volunteer:
        try:
            volunteer_id = int(volunteer)
        except ValueError:
            abort(400)
        db.get_or_404(Volontario, volunteer_id)
        query = query.filter_by(volontario_id=volunteer_id)
    entries = query.order_by(Impegno.inizio, Impegno.id).all()
    by_day = {}
    for week in weeks:
        for day in week:
            day_start = datetime.combine(day, time.min)
            day_end = day_start + timedelta(days=1)
            by_day[day] = [entry for entry in entries if entry.inizio < day_end and entry.fine > day_start]
    previous = (month - timedelta(days=1)).strftime('%Y-%m') if month.year > 2000 or month.month > 1 else None
    following_date = month.replace(day=28) + timedelta(days=4)
    following = following_date.strftime('%Y-%m') if following_date.year <= 2100 else None
    return render_template('calendar.html', month=month, label=f'{MONTHS[month.month-1]} {month.year}',
                           weeks=weeks, by_day=by_day, today=current, previous=previous,
                           following=following, kind=kind, volunteer=volunteer,
                           volunteers=Volontario.query.order_by(Volontario.nome).all(),
                           entries=[e for e in entries if e.inizio < datetime.combine(following_date.replace(day=1), time.min)
                                    and e.fine > datetime.combine(month, time.min)])


def save_entry(entry=None):
    form = ImpegnoForm(obj=entry)
    volunteers = Volontario.query.filter_by(attivo=True).order_by(Volontario.nome).all()
    if entry and entry.volontario and entry.volontario not in volunteers:
        volunteers.append(entry.volontario)
    form.volontario_id.choices = [(0, '— Nessun volontario —')] + [(v.id, v.nome) for v in volunteers]
    if request.method == 'GET' and entry is None:
        try:
            day = date.fromisoformat(request.args.get('giorno', today().isoformat()))
            if not 2000 <= day.year <= 2100:
                raise ValueError
        except ValueError:
            abort(400)
        form.inizio.data = datetime.combine(day, time(9))
        form.fine.data = datetime.combine(day, time(13))
        form.tipo.data = request.args.get('tipo', 'turno')
    if form.validate_on_submit():
        valid = True
        if form.tipo.data == 'turno':
            selected = db.session.execute(db.select(Volontario).where(Volontario.id == form.volontario_id.data).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none() if form.volontario_id.data else None
            if not selected or (not selected.attivo and (entry is None or entry.volontario_id != selected.id)):
                form.volontario_id.errors.append('Scegli un volontario attivo.')
                valid = False
            if selected:
                conflicts = Impegno.query.filter_by(tipo='turno', volontario_id=selected.id).filter(
                    Impegno.inizio < form.fine.data, Impegno.fine > form.inizio.data)
                if entry:
                    conflicts = conflicts.filter(Impegno.id != entry.id)
                if conflicts.with_for_update().first():
                    form.volontario_id.errors.append('Il volontario ha già un turno in questo intervallo.')
                    valid = False
        if valid:
            if entry is None:
                entry = Impegno()
                db.session.add(entry)
            for name in ('tipo', 'titolo', 'inizio', 'fine', 'luogo', 'note'):
                setattr(entry, name, getattr(form, name).data or '')
            entry.volontario_id = form.volontario_id.data if form.tipo.data == 'turno' else None
            db.session.commit()
            flash('Impegno salvato.', 'success')
            return redirect(url_for('calendar.index', mese=entry.inizio.strftime('%Y-%m')))
    return render_template('calendar_form.html', form=form, entry=entry)


@bp.route('/nuovo', methods=['GET', 'POST'])
@login_required
def new():
    return save_entry()


@bp.route('/<int:entry_id>/modifica', methods=['GET', 'POST'])
@login_required
def edit(entry_id):
    return save_entry(db.get_or_404(Impegno, entry_id))


@bp.route('/<int:entry_id>/elimina', methods=['POST'])
@login_required
def delete(entry_id):
    entry = db.get_or_404(Impegno, entry_id)
    month = entry.inizio.strftime('%Y-%m')
    db.session.delete(entry)
    db.session.commit()
    flash('Impegno eliminato.', 'success')
    return redirect(url_for('calendar.index', mese=month))


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
        flash('Volontario aggiornato. I turni esistenti sono conservati.', 'success')
        return redirect(url_for('calendar.volunteers'))
    return render_template('volunteer_form.html', form=form, volunteer=volunteer)
