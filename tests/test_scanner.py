import re
import unittest
from uuid import uuid4
from werkzeug.security import generate_password_hash
from app import create_app
from app.extensions import db
from app.models import Admin, CodiceProdotto, MovimentoMagazzino, Prodotto, StatoGiacenza


class ScannerTests(unittest.TestCase):
    password_hash = generate_password_hash('password-test')

    def setUp(self):
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-key',
                              'SQLALCHEMY_DATABASE_URI': 'sqlite://',
                              'SQLALCHEMY_ENGINE_OPTIONS': {}, 'RATELIMIT_ENABLED': False})
        self.context = self.app.app_context(); self.context.push()
        db.create_all()
        db.session.add(Admin(username='capo', password=self.password_hash))
        p = Prodotto(nome='Pasta', marca='Marca', prezzo=2, quantita=2, descrizione='Pasta 500 g')
        p.codice = CodiceProdotto(codice='0012345678901')
        db.session.add(p)
        db.session.add(Prodotto(nome='Riso', marca='Marca', prezzo=3, quantita=5, descrizione='Riso 1 kg'))
        db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as s:
            s['_user_id'] = 'capo'; s['_fresh'] = True
        page = self.client.get('/scansioni/')
        self.csrf = re.search(rb'id="scan-csrf"[^>]+value="([^"]+)"', page.data).group(1).decode()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.context.pop()

    def payload(self, **changes):
        data = {'codice': '0012345678901', 'modalita': 'uscita', 'richiesta_id': str(uuid4())}
        data.update(changes)
        return data

    def scan(self, data=None, **changes):
        return self.client.post('/scansioni/movimento', json=data or self.payload(**changes),
                                headers={'X-CSRFToken': self.csrf})

    def product(self, product_id=1):
        db.session.expire_all()
        return db.session.get(Prodotto, product_id)

    def test_exit_and_restock_keep_product_and_leading_zeros(self):
        response = self.scan()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.product().quantita, 1)
        self.assertEqual(CodiceProdotto.query.one().codice, '0012345678901')
        response = self.scan(modalita='rifornimento')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.product().quantita, 2)
        self.assertEqual(MovimentoMagazzino.query.count(), 2)
        self.assertEqual(MovimentoMagazzino.query.filter_by(modalita='uscita').one().quantita_prima, 2)

    def test_same_request_is_only_applied_once(self):
        data = self.payload()
        first = self.scan(data); second = self.scan(data)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(second.json['ripetuta'])
        self.assertEqual(self.product().quantita, 1)
        self.assertEqual(MovimentoMagazzino.query.count(), 1)
        data['modalita'] = 'rifornimento'
        self.assertEqual(self.scan(data).status_code, 409)
        self.assertEqual(self.product().quantita, 1)

    def test_zero_stock_requires_explicit_presence_confirmation(self):
        self.product().quantita = 0; db.session.commit()
        data = self.payload()
        response = self.scan(data)
        self.assertEqual(response.json['status'], 'conferma_giacenza')
        self.assertEqual(MovimentoMagazzino.query.count(), 0)
        self.assertEqual(self.product().quantita, 0)
        data.update(azione='conferma', prodotto_id=1, conferma_zero=True)
        response = self.scan(data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.product().quantita, 0)
        self.assertTrue(self.product().stato_giacenza.da_verificare)
        movement = MovimentoMagazzino.query.one()
        self.assertEqual(movement.variazione, -1)
        self.assertEqual(movement.quantita_prima, 0)
        self.assertEqual(movement.quantita_dopo, 0)
        self.assertTrue(movement.discrepanza)
        self.assertTrue(self.scan(data).json['ripetuta'])

    def test_second_exit_at_zero_does_not_go_negative(self):
        self.product().quantita = 1; db.session.commit()
        self.scan()
        response = self.scan()
        self.assertEqual(response.json['status'], 'conferma_giacenza')
        self.assertEqual(self.product().quantita, 0)
        self.assertEqual(MovimentoMagazzino.query.count(), 1)
        self.assertEqual(Prodotto.query.count(), 2)

    def test_unknown_code_does_not_mutate_anything(self):
        response = self.scan(codice='NEWCODE')
        self.assertEqual(response.json['status'], 'sconosciuto')
        self.assertEqual(MovimentoMagazzino.query.count(), 0)
        self.assertEqual(Prodotto.query.count(), 2)

    def test_associate_existing_product_and_exit_atomically(self):
        data = self.payload(codice='NEWCODE', azione='associa', prodotto_id=2)
        self.assertEqual(self.scan(data).status_code, 200)
        self.assertEqual(self.product(2).quantita, 4)
        self.assertEqual(self.product(2).codice.codice, 'NEWCODE')
        self.assertEqual(Prodotto.query.count(), 2)
        self.assertTrue(self.scan(data).json['ripetuta'])

    def test_zero_stock_association_waits_for_confirmation(self):
        self.product(2).quantita = 0; db.session.commit()
        data = self.payload(codice='NEWCODE', azione='associa', prodotto_id=2)
        response = self.scan(data)
        self.assertEqual(response.json['status'], 'conferma_giacenza')
        self.assertIsNone(self.product(2).codice)
        self.assertEqual(MovimentoMagazzino.query.count(), 0)
        data['conferma_zero'] = True
        self.assertEqual(self.scan(data).status_code, 200)
        self.assertEqual(self.product(2).codice.codice, 'NEWCODE')
        self.assertTrue(self.product(2).stato_giacenza.da_verificare)

    def test_association_cannot_steal_or_replace_existing_barcode(self):
        self.assertEqual(self.scan(azione='associa', prodotto_id=2).status_code, 409)
        self.assertEqual(self.scan(codice='NEWCODE', azione='associa', prodotto_id=1).status_code, 409)
        self.assertEqual(self.product().quantita, 2)
        self.assertEqual(CodiceProdotto.query.count(), 1)

    def test_quick_creation_with_only_name_registers_exit_and_flags(self):
        data = self.payload(codice='NEWCODE', azione='crea', nome=' Sapone ', quantita_iniziale=None)
        response = self.scan(data)
        self.assertEqual(response.status_code, 200)
        p = self.product(response.json['prodotto']['id'])
        self.assertEqual(p.nome, 'Sapone')
        self.assertEqual(p.quantita, 0)
        self.assertTrue(p.stato_giacenza.scheda_incompleta)
        self.assertTrue(p.stato_giacenza.da_verificare)
        self.assertEqual(MovimentoMagazzino.query.one().variazione, -1)
        self.assertTrue(self.scan(data).json['ripetuta'])
        self.assertEqual(Prodotto.query.count(), 3)
        page = self.client.get('/prodotti')
        self.assertIn(b'Scheda da completare', page.data)
        self.assertIn(b'Da verificare', page.data)

    def test_quick_creation_with_known_quantity_subtracts_one(self):
        response = self.scan(codice='NEWCODE', azione='crea', nome='Sapone', quantita_iniziale=30)
        p = self.product(response.json['prodotto']['id'])
        self.assertEqual(p.quantita, 29)
        self.assertFalse(p.stato_giacenza.da_verificare)
        self.assertTrue(p.stato_giacenza.scheda_incompleta)

    def test_restock_unknown_counts_one_and_marks_unknown_remainder(self):
        response = self.scan(codice='NEWCODE', modalita='rifornimento', azione='crea', nome='Sapone')
        p = self.product(response.json['prodotto']['id'])
        self.assertEqual(p.quantita, 1)
        self.assertTrue(p.stato_giacenza.da_verificare)
        self.assertIn('Rifornimento', response.json['message'])
        self.assertEqual(MovimentoMagazzino.query.one().variazione, 1)

    def test_invalid_payloads_do_not_write(self):
        for changes in [{'codice': 123}, {'codice': ''}, {'codice': 'x'*129}, {'codice': 'x\ny'},
                        {'richiesta_id': 'invalid'}, {'modalita': 'bad'}, {'azione': 'bad'},
                        {'conferma_zero': 'true'}, {'codice': 'NEW', 'azione': 'crea', 'nome': ''},
                        {'codice': 'NEW', 'azione': 'crea', 'nome': 'Name', 'quantita_iniziale': -1},
                        {'codice': 'NEW', 'azione': 'crea', 'nome': 'Name', 'quantita_iniziale': 1.5},
                        {'codice': 'NEW', 'azione': 'associa', 'prodotto_id': 999}]:
            with self.subTest(changes=changes):
                self.assertIn(self.scan(**changes).status_code, [400, 404])
                self.assertEqual(MovimentoMagazzino.query.count(), 0)
                self.assertEqual(Prodotto.query.count(), 2)

    def test_login_and_csrf_required(self):
        self.assertEqual(self.client.post('/scansioni/movimento', json=self.payload()).status_code, 400)
        self.client.post('/logout', data={'csrf_token': self.csrf})
        self.assertEqual(self.client.get('/scansioni/').status_code, 302)
        self.assertEqual(self.scan().status_code, 401)
        self.assertEqual(MovimentoMagazzino.query.count(), 0)

    def test_search_and_history_keep_plain_text_safe(self):
        response = self.client.get('/scansioni/prodotti?q=Pasta')
        self.assertEqual(response.json['prodotti'][0]['codice'], '0012345678901')
        self.assertEqual(self.client.get('/scansioni/prodotti?q=missing').json['prodotti'], [])
        self.scan()
        history = self.client.get('/scansioni/movimenti')
        self.assertEqual(history.status_code, 200)
        self.assertIn(b'Pasta', history.data)
        self.assertIn(b'capo', history.data)

    def test_restock_retains_discrepancy_until_explicit_stock_count(self):
        self.product().stato_giacenza = StatoGiacenza(da_verificare=True)
        db.session.commit()
        self.scan(modalita='rifornimento')
        self.assertTrue(self.product().stato_giacenza.da_verificare)
        page = self.client.get('/prodotti/modifica/1')
        token = re.search(rb'name="csrf_token"[^>]+value="([^"]+)"', page.data).group(1).decode()
        data = {'csrf_token':token, 'nome':'Pasta', 'marca':'Marca', 'prezzo':'2', 'quantita':'10',
                'descrizione':'Pasta 500 g', 'codice_barre':'0012345678901'}
        self.client.post('/prodotti/modifica/1', data=data)
        self.assertTrue(self.product().stato_giacenza.da_verificare)
        data['giacenza_confermata'] = 'y'
        self.client.post('/prodotti/modifica/1', data=data)
        self.assertFalse(self.product().stato_giacenza.da_verificare)
        self.assertEqual(self.product().quantita, 10)

    def test_existing_inventory_form_barcode_uniqueness_and_search(self):
        page = self.client.get('/prodotti/modifica/2')
        token = re.search(rb'name="csrf_token"[^>]+value="([^"]+)"', page.data).group(1).decode()
        data = {'csrf_token':token, 'nome':'Do not overwrite', 'marca':'Marca', 'prezzo':'2', 'quantita':'5',
                'descrizione':'Description', 'codice_barre':'0012345678901'}
        response = self.client.post('/prodotti/modifica/2', data=data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.product(2).nome, 'Riso')
        self.assertIn(b'Pasta', self.client.get('/prodotti?q=0012345678901').data)

    def test_product_deletion_preserves_movement_history(self):
        self.scan()
        page = self.client.get('/prodotti/modifica/1')
        token = re.search(rb'name="csrf_token"[^>]+value="([^"]+)"', page.data).group(1).decode()
        self.client.post('/prodotti/elimina/1', data={'csrf_token': token})
        self.assertIsNone(self.product())
        self.assertEqual(CodiceProdotto.query.count(), 0)
        self.assertEqual(MovimentoMagazzino.query.count(), 1)
        self.assertIn(b'Pasta', self.client.get('/scansioni/movimenti').data)

    def test_initialization_does_not_reset_existing_stock(self):
        self.scan()
        runner = self.app.test_cli_runner()
        self.assertEqual(runner.invoke(args=['init-db']).exit_code, 0)
        self.assertEqual(runner.invoke(args=['init-db']).exit_code, 0)
        self.assertEqual(self.product().quantita, 1)
        self.assertEqual(MovimentoMagazzino.query.count(), 1)
        self.assertEqual(CodiceProdotto.query.count(), 1)

    def test_concurrent_retries_only_apply_one_movement(self):
        import tempfile
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as folder:
            app = create_app({'TESTING': True, 'SECRET_KEY': 'concurrent-test',
                              'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + folder + '/stock.sqlite',
                              'SQLALCHEMY_ENGINE_OPTIONS': {}, 'RATELIMIT_ENABLED': False})
            with app.app_context():
                db.create_all()
                db.session.add(Admin(username='capo', password=self.password_hash))
                product = Prodotto(nome='Pasta', marca='Marca', prezzo=2, quantita=2, descrizione='Pasta')
                product.codice = CodiceProdotto(codice='0012345678901')
                db.session.add(product); db.session.commit()
            data = self.payload()
            def send(payload):
                client = app.test_client()
                with client.session_transaction() as session:
                    session['_user_id'] = 'capo'; session['_fresh'] = True
                page = client.get('/scansioni/')
                csrf = re.search(rb'id="scan-csrf"[^>]+value="([^"]+)"', page.data).group(1).decode()
                return client.post('/scansioni/movimento', json=payload, headers={'X-CSRFToken': csrf})
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(send, [data, data]))
            self.assertTrue(all(r.status_code == 200 for r in responses), [r.json for r in responses])
            with app.app_context():
                self.assertEqual(Prodotto.query.one().quantita, 1)
                self.assertEqual(MovimentoMagazzino.query.count(), 1)
                Prodotto.query.one().quantita = 1
                db.session.commit()
            with ThreadPoolExecutor(max_workers=2) as pool:
                exits = list(pool.map(send, [self.payload(), self.payload()]))
            self.assertEqual(sorted(r.status_code for r in exits), [200, 409])
            with app.app_context():
                self.assertEqual(Prodotto.query.one().quantita, 0)
                self.assertEqual(MovimentoMagazzino.query.count(), 2)
                db.session.remove(); db.engine.dispose()

    def test_association_and_quantity_roll_back_together_on_commit_failure(self):
        from unittest.mock import patch
        from sqlalchemy.exc import IntegrityError
        with patch('app.blueprint.scanner.db.session.commit', side_effect=IntegrityError('test', {}, Exception('conflict'))):
            response = self.scan(codice='NEWCODE', azione='associa', prodotto_id=2)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.product(2).quantita, 5)
        self.assertIsNone(self.product(2).codice)
        self.assertEqual(MovimentoMagazzino.query.count(), 0)

    def test_stock_upper_bound_is_not_exceeded(self):
        self.product().quantita = 2147483647; db.session.commit()
        self.assertEqual(self.scan(modalita='rifornimento').status_code, 409)
        self.assertEqual(self.product().quantita, 2147483647)
        self.assertEqual(MovimentoMagazzino.query.count(), 0)


if __name__ == '__main__':
    unittest.main()
