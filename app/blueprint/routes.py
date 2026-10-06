from flask import Blueprint, render_template, redirect, url_for, abort, request, flash
from app.extensions import db, limiter
from app.models import Prodotto, AggiungiProdotto, CodiceProdotto
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
import base64
from app.images import image_mime
from werkzeug.utils import secure_filename
from flask_login import logout_user, login_required

bp = Blueprint('routes', __name__)


def available_code(form, product_id=None):
    if form.codice_barre.data:
        existing = db.session.get(CodiceProdotto, form.codice_barre.data)
        if existing and existing.prodotto_id != product_id:
            form.codice_barre.errors.append('Questo codice è già associato a un altro prodotto.')
            return False
    return True


@bp.app_template_filter('image_mime')
def image_mime_filter(data):
    return image_mime(data) or 'application/octet-stream'


@bp.app_template_filter('b64encode')
def b64encode_filter(data):
    if data:
        return base64.b64encode(data).decode('utf-8')
    return ''


@bp.route('/prodotti')
@login_required
def index():
    q = request.args.get('q', '').strip()
    if q:
        # FIX HIGH-1: escape dei caratteri speciali LIKE (% e _)
        q_escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        prodotti = Prodotto.query.options(selectinload(Prodotto.codice), selectinload(Prodotto.stato_giacenza)).filter(
            db.or_(
                Prodotto.codice.has(CodiceProdotto.codice == q),
                Prodotto.nome.ilike(f'%{q_escaped}%', escape='\\'),
                Prodotto.marca.ilike(f'%{q_escaped}%', escape='\\'),
                Prodotto.descrizione.ilike(f'%{q_escaped}%', escape='\\'),
            )
        ).all()
    else:
        prodotti = Prodotto.query.options(selectinload(Prodotto.codice), selectinload(Prodotto.stato_giacenza)).all()
    valore_totale = sum(p.prezzo * p.quantita for p in prodotti)
    return render_template('index.html', prodotti=prodotti, q=q, valore_totale=valore_totale)


@bp.route('/prodotti/<int:id>')
@login_required
def prodotto(id):
    prodotto = db.get_or_404(Prodotto, id)
    return render_template('prodotto.html', prodotto=prodotto)



@bp.route('/admin')
def admin():
    abort(403)


@bp.route('/prodotti/aggiungi', methods=['GET', 'POST'])
@login_required
def aggiungi():
    form = AggiungiProdotto()

    if form.validate_on_submit() and available_code(form):
        uploaded_file = form.file.data

        # FIX HIGH-2: validazione contenuto file upload
        file_name = None
        file_data = None
        if uploaded_file and uploaded_file.filename:
            file_name = secure_filename(uploaded_file.filename)
            file_data = uploaded_file.read()
            if not image_mime(file_data):
                flash('Il file caricato non è un\'immagine valida.', 'danger')
                return render_template('aggiungi.html', form=form)

        prodotto = Prodotto(
            nome=form.nome.data,
            marca=form.marca.data,
            prezzo=form.prezzo.data,
            quantita=form.quantita.data,
            descrizione=form.descrizione.data,
            nome_file=file_name,
            file_data=file_data,
        )

        if form.codice_barre.data:
            prodotto.codice = CodiceProdotto(codice=form.codice_barre.data)
        db.session.add(prodotto)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            form.codice_barre.errors.append('Questo codice è già associato a un altro prodotto.')
            return render_template('aggiungi.html', form=form)

        return redirect(url_for("routes.index"))

    return render_template('aggiungi.html', form=form)


@bp.route("/prodotti/modifica/<int:id>", methods=['GET', 'POST'])
@login_required
def modifica(id):
    prodotto = db.get_or_404(Prodotto, id)
    form = AggiungiProdotto(obj=prodotto)
    if request.method == 'GET':
        form.codice_barre.data = prodotto.codice.codice if prodotto.codice else ''

    if form.validate_on_submit() and available_code(form, prodotto.id):
        uploaded_file = form.file.data
        if uploaded_file and uploaded_file.filename:
            file_data = uploaded_file.read()
            if not image_mime(file_data):
                flash('Il file caricato non è un\'immagine valida.', 'danger')
                return render_template('modifica.html', form=form, prodotto=prodotto)
            prodotto.nome_file = secure_filename(uploaded_file.filename)
            prodotto.file_data = file_data

        prodotto.nome = form.nome.data
        prodotto.marca = form.marca.data
        prodotto.prezzo = form.prezzo.data
        prodotto.quantita = form.quantita.data
        prodotto.descrizione = form.descrizione.data
        code = form.codice_barre.data
        if code:
            if prodotto.codice:
                prodotto.codice.codice = code
            else:
                prodotto.codice = CodiceProdotto(codice=code)
        else:
            prodotto.codice = None
        if prodotto.stato_giacenza:
            prodotto.stato_giacenza.scheda_incompleta = False
            if form.giacenza_confermata.data:
                prodotto.stato_giacenza.da_verificare = False
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            form.codice_barre.errors.append('Questo codice è già associato a un altro prodotto.')
            return render_template('modifica.html', form=form, prodotto=prodotto)

        return redirect(url_for("routes.index"))

    return render_template("modifica.html", form=form, prodotto=prodotto)

@bp.route('/prodotti/elimina/<int:id>', methods=['POST'])
@login_required
def elimina(id):
    prodotto = db.get_or_404(Prodotto, id)
    db.session.delete(prodotto)
    db.session.commit()

    return redirect(url_for("routes.index"))


@bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))

@bp.app_errorhandler(404)
def pagina_non_trovata(error):
    return render_template("404.html"), 404


@bp.app_errorhandler(403)
def accesso_negato(error):
    return render_template("error.html", code=403, title="Accesso negato", message="Non hai accesso a questa pagina. Il primo account si crea dal comando create-admin."), 403
