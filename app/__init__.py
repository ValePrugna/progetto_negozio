import os
import re
from datetime import timedelta
from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify
from flask_talisman import Talisman
from flask_wtf.csrf import CSRFError
from sqlalchemy import URL
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import csrf, db, limiter, login_manager


def create_app(test_config=None):
    load_dotenv(Path(__file__).resolve().parent.parent / '.env')
    app = Flask(__name__, static_folder='../static')
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    development = os.environ.get('APP_ENV', 'development') != 'production'
    key = (test_config or {}).get('SECRET_KEY') or os.environ.get('SECRET_KEY')
    if not key and development:
        # Persist across restarts/workers without checking the key into Git.
        key_file = Path(app.instance_path) / 'secret-key'
        try:
            with open(key_file, 'x', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
                stream.write(os.urandom(32).hex())
        except FileExistsError:
            pass
        key = key_file.read_text().strip()
    if not key and not (test_config and test_config.get('SECRET_KEY')):
        raise RuntimeError('Impostare SECRET_KEY per la produzione.')

    uri = os.environ.get('DATABASE_URL')
    engine_options = {'pool_pre_ping': True}
    if not uri and os.environ.get('DB_HOST'):
        required = ['DB_USER', 'DB_PASS', 'DB_NAME', 'DB_SSL_CA']
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise RuntimeError('Variabili database mancanti: ' + ', '.join(missing))
        name = os.environ['DB_NAME']
        if not re.fullmatch(r'[a-zA-Z0-9_]+', name):
            raise RuntimeError('DB_NAME deve contenere solo lettere, numeri e underscore.')
        uri = URL.create('mysql+pymysql', username=os.environ['DB_USER'],
                         password=os.environ['DB_PASS'], host=os.environ['DB_HOST'],
                         port=int(os.environ.get('DB_PORT', '4000')), database=name)
        engine_options['connect_args'] = {
            'ssl_ca': os.environ['DB_SSL_CA'], 'ssl_verify_cert': True,
            'ssl_verify_identity': True, 'connect_timeout': 10,
        }
    if uri and make_url(uri).get_backend_name() == 'mysql':
        ca = os.environ.get('DB_SSL_CA') or make_url(uri).query.get('ssl_ca')
        if not ca:
            raise RuntimeError('DB_SSL_CA è obbligatorio per connessioni MySQL/TiDB verificate.')
        engine_options['connect_args'] = {
            'ssl_ca': ca, 'ssl_verify_cert': True, 'ssl_verify_identity': True, 'connect_timeout': 10,
        }
    if not uri:
        if not development and not (test_config and test_config.get('SQLALCHEMY_DATABASE_URI')):
            raise RuntimeError('Configurare il database per la produzione.')
        uri = 'sqlite:///' + str(Path(app.instance_path) / 'negozio.sqlite')

    app.config.update(
        SECRET_KEY=key, SQLALCHEMY_DATABASE_URI=uri,
        SQLALCHEMY_ENGINE_OPTIONS=engine_options,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=not development, MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        RATELIMIT_STORAGE_URI=os.environ.get('RATELIMIT_STORAGE_URI', 'memory://'),
    )
    if test_config:
        app.config.update(test_config)
    db.init_app(app)
    login_manager.init_app(app)
    limiter.init_app(app)
    csrf.init_app(app)
    Talisman(app, force_https=not development, session_cookie_secure=not development,
             content_security_policy={
                 'default-src': "'self'", 'script-src': "'self'",
                 'style-src': ["'self'", "'unsafe-inline'"],
                 'img-src': ["'self'", 'data:'], 'font-src': "'self'",
                 'base-uri': "'self'", 'form-action': "'self'",
             }, referrer_policy='strict-origin-when-cross-origin')

    from app.blueprint.routes import bp as routes
    from app.blueprint.auth import bp as auth
    from app.blueprint.calendar import bp as calendar
    from app.blueprint.scanner import bp as scanner
    from app import models  # register all tables before init-db
    app.register_blueprint(routes)
    app.register_blueprint(auth)
    app.register_blueprint(calendar)
    app.register_blueprint(scanner)

    @app.cli.command('init-db')
    def init_db():
        """Create missing tables; never reset existing data."""
        db.create_all()
        from app.slot_migration import migrate_legacy_shifts
        count = migrate_legacy_shifts()
        click.echo('Tabelle create; dati esistenti conservati.')
        click.echo(f'Turni precedenti convertiti negli slot: {count}.')

    @app.cli.command('create-admin')
    @click.option('--username', prompt='Username')
    @click.password_option(confirmation_prompt=True)
    def create_admin(username, password):
        """Create the first chief account from the trusted CLI."""
        from app.models import Admin
        username = username.strip()
        if not 3 <= len(username) <= 255 or len(password) < 8:
            raise click.ClickException('Username: 3–255 caratteri; password: almeno 8 caratteri.')
        if db.session.query(Admin).first():
            raise click.ClickException('Un amministratore esiste già.')
        admin = Admin(username=username)
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        click.echo('Account del capo creato.')

    def error_page(code, title, message):
        return render_template('error.html', code=code, title=title, message=message), code

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        if request.endpoint == 'scanner.scan':
            return jsonify(status='sessione_scaduta', message='Modulo scaduto: ricarica la pagina e accedi se richiesto.'), 400
        return error_page(400, 'Modulo scaduto', 'Ricarica la pagina e riprova a inviare il modulo.')

    @app.errorhandler(413)
    def upload_error(error):
        return error_page(413, 'File troppo grande', 'Il limite del caricamento è 5 MB.')

    @app.errorhandler(429)
    def rate_error(error):
        return error_page(429, 'Troppi tentativi', 'Attendi un minuto e riprova.')

    @app.errorhandler(SQLAlchemyError)
    def database_error(error):
        db.session.rollback()
        if request.endpoint == 'scanner.scan':
            return jsonify(status='errore', message='Database non disponibile. Riprova la stessa scansione.'), 503
        app.logger.error('Operazione database fallita (%s).', type(error).__name__)
        return error_page(503, 'Database non disponibile',
                          'Riprova più tardi. Il capo deve verificare connessione e inizializzazione del database.')

    return app
