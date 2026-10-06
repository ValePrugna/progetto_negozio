import io
import re
import unittest
from unittest.mock import patch

from PIL import Image
from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import Admin, Impegno, Prodotto, Volontario


class AppTests(unittest.TestCase):
    password_hash = generate_password_hash('password-test')

    def setUp(self):
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-key',
                              'SQLALCHEMY_DATABASE_URI': 'sqlite://',
                              'SQLALCHEMY_ENGINE_OPTIONS': {}, 'RATELIMIT_ENABLED': False})
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        db.session.add(Admin(username='capo', password=self.password_hash))
        db.session.add(Volontario(nome='Anna Rossi'))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def token(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.data[:200])
        match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def login(self, next_url=''):
        response = self.client.post('/' + next_url, data={
            'username': 'capo', 'password': 'password-test', 'csrf_token': self.token('/')})
        self.assertEqual(response.status_code, 302)
        return response

    def entry_data(self, **changes):
        data = {'tipo': 'turno', 'titolo': 'Apertura negozio', 'volontario_id': '1',
                'inizio': '2026-10-06T09:00', 'fine': '2026-10-06T13:00',
                'luogo': 'Negozio', 'note': 'Preparare il banco',
                'csrf_token': self.token('/calendario/nuovo')}
        data.update(changes)
        return data

    def test_calendar_requires_login(self):
        for path in ['/calendario', '/calendario/nuovo', '/calendario/volontari']:
            self.assertEqual(self.client.get(path).status_code, 302)
        response = self.client.post('/calendario/nuovo', data={'csrf_token': self.token('/')})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Impegno.query.count(), 0)

    def test_authentication_and_logout_csrf(self):
        self.login()
        self.assertEqual(self.client.get('/prodotti').status_code, 200)
        self.assertEqual(self.client.get('/logout').status_code, 405)
        self.assertEqual(self.client.post('/logout').status_code, 400)
        token = self.token('/calendario/nuovo')
        self.assertEqual(self.client.post('/logout', data={'csrf_token': token}).status_code, 302)
        self.assertEqual(self.client.get('/calendario').status_code, 302)

    def test_external_next_is_rejected(self):
        response = self.login('?next=https://example.com')
        self.assertEqual(response.headers['Location'], '/prodotti')

    def test_bad_password_does_not_login(self):
        response = self.client.post('/', data={'username': 'capo', 'password': 'bad', 'csrf_token': self.token('/')})
        self.assertIn(b'Credenziali non valide', response.data)
        self.assertEqual(self.client.get('/calendario').status_code, 302)

    def test_public_admin_creation_disabled(self):
        self.assertEqual(self.client.get('/admin').status_code, 403)
        self.assertEqual(self.client.post('/admin', data={'csrf_token': self.token('/')}).status_code, 405)

    def test_shift_create_edit_delete(self):
        self.login()
        self.assertEqual(self.client.post('/calendario/nuovo', data=self.entry_data()).status_code, 302)
        entry = Impegno.query.one()
        self.assertEqual(entry.volontario.nome, 'Anna Rossi')
        page = self.client.get('/calendario?mese=2026-10')
        self.assertIn(b'Apertura negozio', page.data)
        self.assertIn(b'Anna Rossi', page.data)
        data = self.entry_data(titolo='Chiusura', inizio='2026-10-06T14:00', fine='2026-10-06T18:00')
        self.assertEqual(self.client.post(f'/calendario/{entry.id}/modifica', data=data).status_code, 302)
        self.assertEqual(db.session.get(Impegno, entry.id).titolo, 'Chiusura')
        self.assertEqual(self.client.get(f'/calendario/{entry.id}/elimina').status_code, 405)
        self.assertEqual(self.client.post(f'/calendario/{entry.id}/elimina').status_code, 400)
        self.assertEqual(self.client.post(f'/calendario/{entry.id}/elimina', data={'csrf_token': data['csrf_token']}).status_code, 302)
        self.assertEqual(Impegno.query.count(), 0)

    def test_event_without_volunteer_and_html_escaped(self):
        self.login()
        self.client.post('/calendario/nuovo', data=self.entry_data(
            tipo='evento', volontario_id='0', titolo='<script>alert(1)</script>'))
        self.assertIsNone(Impegno.query.one().volontario_id)
        response = self.client.get('/calendario?mese=2026-10')
        self.assertIn(b'&lt;script&gt;', response.data)
        self.assertNotIn(b'<script>alert(1)</script>', response.data)

    def test_overlapping_shifts_rejected_but_adjacent_allowed(self):
        self.login()
        self.client.post('/calendario/nuovo', data=self.entry_data())
        response = self.client.post('/calendario/nuovo', data=self.entry_data(
            inizio='2026-10-06T12:00', fine='2026-10-06T14:00'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('già un turno'.encode(), response.data)
        self.assertEqual(Impegno.query.count(), 1)
        response = self.client.post('/calendario/nuovo', data=self.entry_data(
            inizio='2026-10-06T13:00', fine='2026-10-06T14:00'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Impegno.query.count(), 2)

    def test_shift_can_keep_its_own_interval_on_edit(self):
        self.login()
        self.client.post('/calendario/nuovo', data=self.entry_data())
        self.assertEqual(self.client.post('/calendario/1/modifica', data=self.entry_data(titolo='Aggiornato')).status_code, 302)

    def test_overlap_on_edit_does_not_change_saved_entry(self):
        self.login()
        self.client.post('/calendario/nuovo', data=self.entry_data())
        self.client.post('/calendario/nuovo', data=self.entry_data(inizio='2026-10-06T14:00', fine='2026-10-06T16:00'))
        response = self.client.post('/calendario/2/modifica', data=self.entry_data(titolo='Non salvare'))
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(db.session.get(Impegno, 2).titolo, 'Non salvare')

    def test_invalid_dates_and_missing_volunteer(self):
        self.login()
        for changes in [{'fine': '2026-10-06T08:00'}, {'inizio': 'invalid'}, {'fine': '2026-10-06T09:00'},
                        {'volontario_id': '0'}, {'volontario_id': '999'}, {'inizio': '1900-01-01T10:00'}, {'inizio': '0001-01-01T00:00'}]:
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post('/calendario/nuovo', data=self.entry_data(**changes)).status_code, 200)
                self.assertEqual(Impegno.query.count(), 0)

    def test_rome_clock_change_validation(self):
        self.login()
        for start, end in [('2026-03-29T02:30', '2026-03-29T04:00'),
                           ('2026-10-25T02:30', '2026-10-25T04:00')]:
            response = self.client.post('/calendario/nuovo', data=self.entry_data(inizio=start, fine=end))
            self.assertEqual(response.status_code, 200)
            self.assertIn('cambio dell'.encode(), response.data)
            self.assertEqual(Impegno.query.count(), 0)

    def test_multiday_event_and_month_boundaries(self):
        self.login()
        self.client.post('/calendario/nuovo', data=self.entry_data(tipo='evento', volontario_id='0',
            inizio='2026-09-30T20:00', fine='2026-10-02T00:00', titolo='Evento lungo'))
        response = self.client.get('/calendario?mese=2026-10')
        # September 30 and October 1 cells plus monthly agenda; exclusive midnight end.
        self.assertEqual(response.data.count(b'Evento lungo'), 3)
        self.assertIn('↳ continua'.encode(), response.data)

    def test_calendar_filters_and_invalid_parameters(self):
        self.login()
        self.client.post('/calendario/nuovo', data=self.entry_data())
        self.client.post('/calendario/nuovo', data=self.entry_data(tipo='evento', titolo='Raccolta', volontario_id='0'))
        response = self.client.get('/calendario?mese=2026-10&tipo=evento')
        self.assertIn(b'Raccolta', response.data)
        self.assertNotIn(b'Apertura negozio', response.data)
        response = self.client.get('/calendario?mese=2026-10&volontario=1')
        self.assertNotIn(b'Raccolta', response.data)
        for suffix in ['mese=bad', 'mese=9999-12', 'volontario=bad', 'tipo=bad']:
            self.assertEqual(self.client.get('/calendario?' + suffix).status_code, 400)
        for month in ['2000-01', '2100-12', '2028-02']:
            self.assertEqual(self.client.get('/calendario?mese=' + month).status_code, 200)

    def test_volunteer_create_update_and_archive(self):
        self.login()
        token = self.token('/calendario/volontari')
        response = self.client.post('/calendario/volontari', data={'nome': ' Luca Bianchi ', 'attivo': 'y', 'csrf_token': token})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(db.session.get(Volontario, 2).nome, 'Luca Bianchi')
        self.client.post('/calendario/nuovo', data=self.entry_data())
        self.client.post('/calendario/volontari/1/modifica', data={'nome': 'Anna Rossi', 'csrf_token': token})
        self.assertFalse(db.session.get(Volontario, 1).attivo)
        self.assertEqual(Impegno.query.count(), 1)
        self.assertEqual(self.client.post('/calendario/nuovo', data=self.entry_data(
            inizio='2026-10-07T09:00', fine='2026-10-07T13:00')).status_code, 200)
        self.assertEqual(Impegno.query.count(), 1)
        self.assertEqual(self.client.get('/calendario/1/modifica').status_code, 200)

    def test_post_without_csrf_does_not_write(self):
        self.login()
        data = self.entry_data()
        del data['csrf_token']
        self.assertEqual(self.client.post('/calendario/nuovo', data=data).status_code, 400)
        self.assertEqual(Impegno.query.count(), 0)

    def product_data(self, **changes):
        data = {'nome': 'Prodotto', 'marca': 'Marca', 'prezzo': '12.50', 'quantita': '0',
                'descrizione': 'Descrizione', 'csrf_token': self.token('/prodotti/aggiungi')}
        data.update(changes)
        return data

    def test_inventory_create_search_edit_delete(self):
        self.login()
        response = self.client.post('/prodotti/aggiungi', data=self.product_data(nome="L'oggetto 100%"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Prodotto.query.one().quantita, 0)
        self.assertIn(b'100%', self.client.get('/prodotti?q=%25').data)
        page = self.client.get('/prodotti/1')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'data-confirm=', page.data)
        self.assertNotIn(b'onsubmit=', page.data)
        self.client.post('/prodotti/modifica/1', data=self.product_data(prezzo='15.00', quantita='2'))
        self.assertEqual(Prodotto.query.one().quantita, 2)
        self.client.post('/prodotti/elimina/1', data={'csrf_token': self.token('/prodotti/aggiungi')})
        self.assertEqual(Prodotto.query.count(), 0)

    def test_invalid_image_edit_preserves_product(self):
        self.login()
        self.client.post('/prodotti/aggiungi', data=self.product_data())
        response = self.client.post('/prodotti/modifica/1', data=self.product_data(
            nome='Non salvare', file=(io.BytesIO(b'not an image'), 'fake.png')))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Prodotto.query.one().nome, 'Prodotto')

    def test_png_upload_has_correct_mime(self):
        self.login()
        image = io.BytesIO()
        Image.new('RGB', (2, 2)).save(image, 'PNG')
        image.seek(0)
        response = self.client.post('/prodotti/aggiungi', data=self.product_data(file=(image, 'image.png')))
        self.assertEqual(response.status_code, 302)
        self.assertIn(b'data:image/png;base64,', self.client.get('/prodotti/1').data)

    def test_invalid_price_and_negative_quantity(self):
        self.login()
        for changes in [{'prezzo': 'nan'}, {'prezzo': 'inf'}, {'prezzo': '-1'}, {'quantita': '-1'}, {'quantita': '2147483648'}, {'prezzo': '1e100'}]:
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post('/prodotti/aggiungi', data=self.product_data(**changes)).status_code, 200)
                self.assertEqual(Prodotto.query.count(), 0)

    def test_missing_record_and_error_pages(self):
        self.login()
        for path in ['/calendario/999/modifica', '/calendario/volontari/999/modifica', '/prodotti/999']:
            self.assertEqual(self.client.get(path).status_code, 404)
        with self.client.get('/static/app.js') as script:
            self.assertEqual(script.status_code, 200)
        csp = self.client.get('/prodotti').headers['Content-Security-Policy']
        self.assertIn("script-src 'self'", csp)

    def test_database_failure_returns_safe_service_error(self):
        self.login()
        Impegno.__table__.drop(db.engine)
        response = self.client.get('/calendario')
        self.assertEqual(response.status_code, 503)
        self.assertIn(b'Database non disponibile', response.data)
        self.assertNotIn(b'SELECT', response.data)
        self.assertNotIn(b'Traceback', response.data)

    def test_tidb_config_keeps_special_password_and_requires_tls(self):
        variables = {'DB_HOST': 'example.test', 'DB_PORT': '4000', 'DB_USER': 'test-user',
                     'DB_PASS': 'p@ss:/?#', 'DB_NAME': 'negozio', 'DB_SSL_CA': '/tmp/example-ca.pem'}
        with patch.dict('os.environ', variables, clear=True):
            app = create_app({'SECRET_KEY': 'test-key'})
            self.assertEqual(app.config['SQLALCHEMY_DATABASE_URI'].password, 'p@ss:/?#')
            args = app.config['SQLALCHEMY_ENGINE_OPTIONS']['connect_args']
            self.assertTrue(args['ssl_verify_cert'])
            self.assertTrue(args['ssl_verify_identity'])
        with patch.dict('os.environ', {'DATABASE_URL': 'mysql+pymysql://test:test@example.test/test'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'DB_SSL_CA'):
                create_app({'SECRET_KEY': 'test-key'})

    def test_production_requires_key_and_database(self):
        with patch.dict('os.environ', {'APP_ENV': 'production'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'SECRET_KEY'):
                create_app()
        with patch.dict('os.environ', {'APP_ENV': 'production', 'SECRET_KEY': 'test-key'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'database'):
                create_app()

    def test_init_db_repeatable_and_admin_cli(self):
        runner = self.app.test_cli_runner()
        self.assertEqual(runner.invoke(args=['init-db']).exit_code, 0)
        self.assertEqual(Admin.query.count(), 1)
        db.session.delete(Admin.query.one())
        db.session.commit()
        result = runner.invoke(args=['create-admin', '--username', 'nuovocapo'], input='password-new\npassword-new\n')
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(Admin.query.one().check_password('password-new'))
        self.assertNotEqual(runner.invoke(args=['create-admin', '--username', 'altro'], input='password-new\npassword-new\n').exit_code, 0)


if __name__ == '__main__':
    unittest.main()
