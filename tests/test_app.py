import io
import re
import unittest
from unittest.mock import patch

from PIL import Image
from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import Admin, Impegno, Prodotto, Volontario, TurnoSlot, TurnoMigrato
from datetime import date, datetime


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


    def test_authentication_and_logout_csrf(self):
        self.login()
        self.assertEqual(self.client.get('/prodotti').status_code, 200)
        self.assertEqual(self.client.get('/logout').status_code, 405)
        self.assertEqual(self.client.post('/logout').status_code, 400)
        token = self.token('/calendario/slot/2026-10-06/mattina')
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


    slot_path = '/calendario/slot/2026-10-06/mattina'

    def add_second_volunteer(self):
        db.session.add(Volontario(nome='Luca Bianchi'))
        db.session.commit()

    def slot_post(self, ids, path=None):
        path = path or self.slot_path
        return self.client.post(path, data={'volontari': [str(i) for i in ids], 'csrf_token': self.token(path)})

    def event_data(self, **changes):
        data = {'titolo': 'Raccolta solidale', 'giorno': '2026-10-10', 'ultimo_giorno': '',
                'luogo': 'Piazza', 'note': 'Una giornata speciale',
                'csrf_token': self.token('/calendario/eventi/nuovo')}
        data.update(changes)
        return data

    def test_calendar_requires_login(self):
        for path in ['/calendario', self.slot_path, '/calendario/eventi', '/calendario/eventi/nuovo', '/calendario/volontari']:
            self.assertEqual(self.client.get(path).status_code, 302)
        response = self.client.post(self.slot_path, data={'csrf_token': self.token('/')})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TurnoSlot.query.count(), 0)

    def test_exactly_two_empty_red_slots_per_day(self):
        self.login()
        response = self.client.get('/calendario?mese=2026-10')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.count(b'class="shift-slot uncovered"'), 62)
        self.assertEqual(response.data.count(b'data-period="mattina"'), 31)
        self.assertEqual(response.data.count(b'data-period="pomeriggio"'), 31)
        self.assertEqual(response.data.count(b'class="shift-slot covered"'), 0)
        self.assertEqual(TurnoSlot.query.count(), 0)  # GET does not create records.

    def test_assign_multiple_volunteers_and_green_coverage(self):
        self.login()
        self.add_second_volunteer()
        self.assertEqual(self.slot_post([1, 2]).status_code, 302)
        slot = TurnoSlot.query.one()
        self.assertEqual({v.id for v in slot.volontari}, {1, 2})
        response = self.client.get('/calendario?mese=2026-10')
        self.assertEqual(response.data.count(b'class="shift-slot covered"'), 1)
        self.assertEqual(response.data.count(b'class="shift-slot uncovered"'), 61)
        self.assertIn(b'Anna Rossi', response.data)
        self.assertIn(b'Luca Bianchi', response.data)
        form = self.client.get(self.slot_path)
        self.assertEqual(form.data.count(b'checked'), 2)

    def test_update_slot_then_clear_back_to_red(self):
        self.login()
        self.add_second_volunteer()
        self.slot_post([1, 2])
        self.slot_post([2])
        self.assertEqual(TurnoSlot.query.count(), 1)
        self.assertEqual([v.id for v in TurnoSlot.query.one().volontari], [2])
        self.slot_post([])
        self.assertEqual(TurnoSlot.query.one().volontari, [])
        response = self.client.get('/calendario?mese=2026-10')
        self.assertEqual(response.data.count(b'class="shift-slot uncovered"'), 62)

    def test_morning_and_afternoon_are_independent(self):
        self.login()
        self.slot_post([1])
        self.slot_post([1], '/calendario/slot/2026-10-06/pomeriggio')
        self.assertEqual(TurnoSlot.query.count(), 2)
        response = self.client.get('/calendario?mese=2026-10')
        self.assertEqual(response.data.count(b'class="shift-slot covered"'), 2)
        self.slot_post([])
        afternoon = TurnoSlot.query.filter_by(fascia='pomeriggio').one()
        self.assertEqual([v.id for v in afternoon.volontari], [1])

    def test_duplicate_ids_do_not_duplicate_assignment(self):
        self.login()
        self.assertEqual(self.slot_post([1, 1]).status_code, 302)
        self.assertEqual(len(TurnoSlot.query.one().volontari), 1)

    def test_invalid_volunteer_does_not_overwrite_slot(self):
        self.login()
        self.slot_post([1])
        response = self.slot_post([999])
        self.assertEqual(response.status_code, 200)
        self.assertEqual([v.id for v in TurnoSlot.query.one().volontari], [1])

    def test_slot_validation_and_month_boundaries(self):
        self.login()
        for path in ['/calendario/slot/invalid/mattina', '/calendario/slot/1999-12-31/mattina',
                     '/calendario?mese=bad', '/calendario?mese=9999-12']:
            self.assertEqual(self.client.get(path).status_code, 400)
        self.assertEqual(self.client.get('/calendario/slot/2026-10-06/sera').status_code, 404)
        for month, count in [('2000-01', 62), ('2100-12', 62), ('2028-02', 58)]:
            response = self.client.get('/calendario?mese=' + month)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data.count(b'class="shift-slot uncovered"'), count)

    def test_slot_post_requires_csrf(self):
        self.login()
        self.assertEqual(self.client.post(self.slot_path, data={'volontari': ['1']}).status_code, 400)
        self.assertEqual(TurnoSlot.query.count(), 0)

    def test_volunteer_archive_preserves_existing_assignments(self):
        self.login()
        token = self.token('/calendario/volontari')
        self.client.post('/calendario/volontari', data={'nome': ' Luca Bianchi ', 'attivo': 'y', 'csrf_token': token})
        self.assertEqual(db.session.get(Volontario, 2).nome, 'Luca Bianchi')
        self.slot_post([1])
        self.client.post('/calendario/volontari/1/modifica', data={'nome': 'Anna Rossi', 'csrf_token': token})
        self.assertFalse(db.session.get(Volontario, 1).attivo)
        self.assertEqual(len(TurnoSlot.query.one().volontari), 1)
        self.assertEqual(self.slot_post([1]).status_code, 302)
        self.assertEqual(self.slot_post([1], '/calendario/slot/2026-10-07/mattina').status_code, 200)
        self.assertEqual(TurnoSlot.query.count(), 1)

    def test_events_are_separate_and_do_not_cover_slots(self):
        self.login()
        response = self.client.post('/calendario/eventi/nuovo', data=self.event_data())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Impegno.query.one().tipo, 'evento')
        self.assertIsNone(Impegno.query.one().volontario_id)
        shifts = self.client.get('/calendario?mese=2026-10')
        self.assertNotIn(b'Raccolta solidale', shifts.data)
        self.assertEqual(shifts.data.count(b'class="shift-slot uncovered"'), 62)
        events = self.client.get('/calendario/eventi?mese=2026-10')
        self.assertIn(b'Raccolta solidale', events.data)
        self.assertNotIn(b'shift-slot', events.data)
        self.assertNotIn(b'Anna Rossi', events.data)
        self.assertNotIn(b'Raccolta solidale', self.client.get('/calendario/eventi?mese=2026-11').data)

    def test_event_edit_delete_and_csrf(self):
        self.login()
        self.client.post('/calendario/eventi/nuovo', data=self.event_data())
        response = self.client.post('/calendario/eventi/1/modifica', data=self.event_data(titolo='Incontro', giorno='2026-11-03'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Impegno.query.one().titolo, 'Incontro')
        self.assertEqual(self.client.get('/calendario/eventi/1/elimina').status_code, 405)
        self.assertEqual(self.client.post('/calendario/eventi/1/elimina').status_code, 400)
        self.client.post('/calendario/eventi/1/elimina', data={'csrf_token': self.token('/calendario/eventi/1/modifica')})
        self.assertEqual(Impegno.query.count(), 0)

    def test_event_validation_and_html_escaping(self):
        self.login()
        for changes in [{'giorno': 'invalid'}, {'giorno': '0001-01-01'}, {'ultimo_giorno': '2026-10-09'}, {'titolo': ' '}]:
            self.assertEqual(self.client.post('/calendario/eventi/nuovo', data=self.event_data(**changes)).status_code, 200)
            self.assertEqual(Impegno.query.count(), 0)
        self.client.post('/calendario/eventi/nuovo', data=self.event_data(titolo='<script>alert(1)</script>'))
        response = self.client.get('/calendario/eventi?mese=2026-10')
        self.assertIn(b'&lt;script&gt;', response.data)
        self.assertNotIn(b'<script>alert(1)</script>', response.data)

    def test_multiday_event_month_intersection_and_inclusive_last_day(self):
        self.login()
        self.client.post('/calendario/eventi/nuovo', data=self.event_data(giorno='2026-09-30', ultimo_giorno='2026-10-02'))
        self.assertIn(b'Raccolta solidale', self.client.get('/calendario/eventi?mese=2026-09').data)
        response = self.client.get('/calendario/eventi?mese=2026-10')
        self.assertEqual(response.data.count(b'Raccolta solidale'), 1)
        self.assertIn(b'02/10/2026', response.data)
        self.assertEqual(Impegno.query.one().fine.date(), date(2026, 10, 3))

    def test_legacy_conversion_preserves_data_and_is_repeatable(self):
        self.add_second_volunteer()
        for volunteer_id in [1, 2]:
            db.session.add(Impegno(tipo='turno', titolo='Turno precedente', volontario_id=volunteer_id,
                                  inizio=datetime(2026, 10, 6, 9), fine=datetime(2026, 10, 6, 13),
                                  luogo='Negozio', note='Dettagli conservati'))
        event = Impegno(tipo='evento', titolo='Evento precedente', inizio=datetime(2026, 10, 10, 9),
                        fine=datetime(2026, 10, 10, 18), luogo='', note='')
        db.session.add(event)
        db.session.commit()
        runner = self.app.test_cli_runner()
        self.assertEqual(runner.invoke(args=['init-db']).exit_code, 0)
        self.assertEqual(TurnoSlot.query.count(), 2)
        self.assertEqual(TurnoMigrato.query.count(), 2)
        self.assertEqual(Impegno.query.count(), 3)
        for slot in TurnoSlot.query.all():
            self.assertEqual({v.id for v in slot.volontari}, {1, 2})
        morning = TurnoSlot.query.filter_by(fascia='mattina').one()
        morning.volontari = []
        db.session.commit()
        self.assertEqual(runner.invoke(args=['init-db']).exit_code, 0)
        self.assertEqual(morning.volontari, [])  # Must not resurrect cleared assignments.
        self.assertEqual(Impegno.query.filter_by(tipo='evento').one().titolo, 'Evento precedente')

    def test_legacy_midnight_end_and_slot_uniqueness(self):
        db.session.add(Impegno(tipo='turno', titolo='Notturno precedente', volontario_id=1,
                              inizio=datetime(2026, 10, 6, 15), fine=datetime(2026, 10, 7), luogo='', note=''))
        db.session.commit()
        self.app.test_cli_runner().invoke(args=['init-db'])
        slot = TurnoSlot.query.one()
        self.assertEqual(slot.giorno, date(2026, 10, 6))
        self.assertEqual(slot.fascia, 'pomeriggio')
        from sqlalchemy.exc import IntegrityError
        db.session.add(TurnoSlot(giorno=slot.giorno, fascia=slot.fascia))
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

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
        for path in ['/calendario/eventi/999/modifica', '/calendario/volontari/999/modifica', '/prodotti/999']:
            self.assertEqual(self.client.get(path).status_code, 404)
        with self.client.get('/static/app.js') as script:
            self.assertEqual(script.status_code, 200)
        csp = self.client.get('/prodotti').headers['Content-Security-Policy']
        self.assertIn("script-src 'self'", csp)

    def test_database_failure_returns_safe_service_error(self):
        self.login()
        TurnoSlot.__table__.drop(db.engine)
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
