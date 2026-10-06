from datetime import datetime, timezone
from uuid import UUID

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.barcodes import normalize_code
from app.extensions import db
from app.models import CodiceProdotto, MovimentoMagazzino, Prodotto, StatoGiacenza

bp = Blueprint('scanner', __name__, url_prefix='/scansioni')
MAX_STOCK = 2147483647


def fail(status, message, code=400, **extra):
    db.session.rollback()
    return jsonify(status=status, message=message, **extra), code


def product_data(product):
    state = product.stato_giacenza
    return {'id': product.id, 'nome': product.nome, 'quantita': product.quantita,
            'da_verificare': bool(state and state.da_verificare),
            'scheda_incompleta': bool(state and state.scheda_incompleta)}


def result(movement, repeated=False):
    return jsonify(status='ok', richiesta_id=movement.richiesta_id, ripetuta=repeated,
                   prodotto={'id': movement.prodotto_id, 'nome': movement.nome_prodotto,
                             'quantita': movement.quantita_dopo,
                             'da_verificare': movement.discrepanza},
                   modalita=movement.modalita, variazione=movement.variazione,
                   message=(('Uscita registrata.' if movement.modalita == 'uscita' else 'Rifornimento registrato.')
                            + (' Giacenza da verificare.' if movement.discrepanza else '')))


@bp.route('/')
@login_required
def index():
    return render_template('scanner.html')


@bp.route('/prodotti')
@login_required
def search():
    term = request.args.get('q', '').strip()[:255]
    if not term:
        return jsonify(prodotti=[])
    escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    products = Prodotto.query.options(selectinload(Prodotto.codice), selectinload(Prodotto.stato_giacenza)).filter(db.or_(Prodotto.nome.ilike(f'%{escaped}%', escape='\\'),
                                           Prodotto.marca.ilike(f'%{escaped}%', escape='\\'))).order_by(Prodotto.nome).limit(30).all()
    return jsonify(prodotti=[{**product_data(p), 'marca': p.marca,
                              'codice': p.codice.codice if p.codice else None} for p in products])


@bp.route('/movimento', methods=['POST'])
def scan():
    if not current_user.is_authenticated:
        return jsonify(status='sessione_scaduta', message='Accedi di nuovo al gestionale.'), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return fail('errore', 'Richiesta non valida.')
    try:
        code = normalize_code(data.get('codice'))
        request_id = str(UUID(data.get('richiesta_id', '')))
    except (ValueError, AttributeError, TypeError):
        return fail('errore', 'Codice o identificativo della scansione non valido.')
    mode = data.get('modalita')
    action = data.get('azione', 'normale')
    if mode not in ('uscita', 'rifornimento') or action not in ('normale', 'associa', 'crea', 'conferma'):
        return fail('errore', 'Modalità o azione non valida.')
    if 'conferma_zero' in data and not isinstance(data['conferma_zero'], bool):
        return fail('errore', 'Conferma non valida.')
    previous = db.session.get(MovimentoMagazzino, request_id)
    if previous:
        if previous.codice != code or previous.modalita != mode or previous.operatore != current_user.username:
            return fail('errore', 'Identificativo già usato per un’altra scansione.', 409)
        return result(previous, repeated=True)
    mapping = db.session.get(CodiceProdotto, code)
    product = None
    if mapping:
        if action == 'crea':
            return fail('errore', 'Il codice è stato associato nel frattempo. Controlla il prodotto.', 409)
        if action in ('associa', 'conferma') and data.get('prodotto_id') != mapping.prodotto_id:
            return fail('errore', 'Il codice appartiene a un altro prodotto. Controlla prima di procedere.', 409)
        product = db.session.execute(db.select(Prodotto).where(Prodotto.id == mapping.prodotto_id)
                                     .with_for_update().execution_options(populate_existing=True)).scalar_one()
    elif action == 'associa':
        product_id = data.get('prodotto_id')
        if not isinstance(product_id, int) or isinstance(product_id, bool):
            return fail('errore', 'Seleziona un prodotto valido.')
        product = db.session.execute(db.select(Prodotto).where(Prodotto.id == product_id)
                                     .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        if not product:
            return fail('errore', 'Prodotto non trovato.', 404)
        if product.codice:
            return fail('errore', 'Questo prodotto ha già un altro codice: verifica di aver scelto quello giusto.', 409)
        product.codice = CodiceProdotto(codice=code)
    elif action == 'crea':
        name = data.get('nome')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 255:
            return fail('errore', 'Inserisci il nome del prodotto (massimo 255 caratteri).')
        initial = data.get('quantita_iniziale')
        if initial is not None and (not isinstance(initial, int) or isinstance(initial, bool) or not 0 <= initial <= MAX_STOCK):
            return fail('errore', 'La quantità iniziale deve essere un intero non negativo.')
        product = Prodotto(nome=name.strip(), marca='', prezzo=0, quantita=initial or 0, descrizione='')
        product.codice = CodiceProdotto(codice=code)
        product.stato_giacenza = StatoGiacenza(da_verificare=initial is None, scheda_incompleta=True)
        db.session.add(product)
    else:
        return fail('sconosciuto', 'Questo codice non è ancora associato a un prodotto.', 404, codice=code)
    try:
        db.session.flush()
        if mode == 'uscita' and product.quantita == 0:
            if data.get('conferma_zero') is not True and action != 'crea':
                details = product_data(product)
                return fail('conferma_giacenza', 'La quantità registrata è zero. Hai il prodotto presente?', 409, prodotto=details)
            if not product.stato_giacenza:
                product.stato_giacenza = StatoGiacenza()
            product.stato_giacenza.da_verificare = True
            before = after = 0  # Record physical exit without guessing remaining shelf stock.
        else:
            delta = -1 if mode == 'uscita' else 1
            condition = Prodotto.quantita > 0 if delta < 0 else Prodotto.quantita < MAX_STOCK
            changed = db.session.execute(update(Prodotto).where(Prodotto.id == product.id, condition)
                                         .values(quantita=Prodotto.quantita + delta),
                                         execution_options={'synchronize_session': False})
            if changed.rowcount != 1:
                details = product_data(product)
                return fail('conferma_giacenza' if delta < 0 else 'errore',
                            'La giacenza è cambiata. Controlla e riprova.', 409, prodotto=details)
            db.session.refresh(product)
            after = product.quantita
            before = after - delta
        movement = MovimentoMagazzino(richiesta_id=request_id, prodotto_id=product.id,
                                      nome_prodotto=product.nome, codice=code, modalita=mode,
                                      variazione=-1 if mode == 'uscita' else 1,
                                      quantita_prima=before, quantita_dopo=after,
                                      discrepanza=bool(product.stato_giacenza and product.stato_giacenza.da_verificare),
                                      operatore=current_user.username, registrato_il=datetime.now(timezone.utc).replace(tzinfo=None))
        db.session.add(movement)
        db.session.commit()
        return result(movement)
    except IntegrityError:
        db.session.rollback()
        previous = db.session.get(MovimentoMagazzino, request_id)
        if previous and previous.codice == code and previous.modalita == mode and previous.operatore == current_user.username:
            return result(previous, repeated=True)
        return fail('errore', 'Il codice è già associato o i dati sono cambiati. Verifica il prodotto.', 409)


@bp.route('/movimenti')
@login_required
def history():
    page = request.args.get('page', 1, type=int)
    if page < 1:
        page = 1
    movements = MovimentoMagazzino.query.order_by(MovimentoMagazzino.registrato_il.desc())
    pagination = movements.paginate(page=page, per_page=50, error_out=False)
    return render_template('stock_history.html', pagination=pagination)


@bp.app_template_filter('rome_time')
def rome_time(value):
    from zoneinfo import ZoneInfo
    return value.replace(tzinfo=timezone.utc).astimezone(ZoneInfo('Europe/Rome')).strftime('%d/%m/%Y %H:%M:%S')
