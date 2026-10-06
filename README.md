# Gestionale negozio

Applicazione Flask in italiano per inventario prodotti, turni dei volontari ed eventi.
Il progetto importato da `progetto negozio.zip` è indipendente dall’archivio del gestionale film.

## Avvio in sviluppo

Python 3.12 è la versione verificata nell’ambiente cloud. La dipendenza `tzdata`
fornisce i fusi orari anche su Windows, dove non è presente il database IANA di sistema.
Dalla cartella del progetto:

```sh
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m flask --app run init-db
python -m flask --app run create-admin
python -m flask --app run run --debug
```

`create-admin` chiede username e password (almeno 8 caratteri) senza mostrarla.
L’account amministratore è l’account del capo; tutte le pagine operative richiedono accesso.
La creazione del primo amministratore via `/admin` è disabilitata: usare il comando dalla macchina fidata.
I volontari sono un’anagrafica, senza account o password.

Senza variabili database si usa SQLite in `instance/negozio.sqlite`. Questa cartella,
la chiave di sessione locale e `.env` sono esclusi da Git. La chiave locale viene generata
una sola volta, con permessi riservati, così le sessioni restano valide dopo il riavvio.
La modalità sviluppo è il valore predefinito; per produzione impostare esplicitamente `APP_ENV=production`.

## Calendario

Dal menu **Calendario e turni**:

1. Apri **Volontari** e aggiungi nome e cognome. Puoi rinominare o disattivare un volontario; lo storico resta disponibile.
2. Clicca su un giorno o su **Nuovo impegno**.
3. Scegli **Turno volontario** o **Evento particolare**, titolo, inizio/fine, luogo e note.
4. Per un turno seleziona un volontario attivo. Per un evento il volontario viene ignorato.
5. Clicca su un impegno per modificarlo o eliminarlo, con conferma.

Vista mensile con mese precedente/successivo, pulsante Oggi, filtro per tipo e volontario
ed agenda leggibile anche da telefono. Gli impegni su più giorni appaiono su ciascun giorno
coinvolto; una fine a mezzanotte non occupa il giorno successivo. Gli intervalli sono
`[inizio, fine)`: due turni adiacenti sono ammessi, due sovrapposti per lo stesso volontario no.
La verifica blocca il record del volontario durante il salvataggio su MySQL/TiDB.
Date supportate: 2000–2100. Gli orari sono locali di **Europe/Rome**; gli orari inesistenti
o ambigui durante il cambio dell’ora vengono rifiutati per evitare assegnazioni equivoche.

## TiDB esistente

Copia `.env.example` in `.env` e compila `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASS`,
`DB_NAME` e `DB_SSL_CA`. Non condividere il file né includerlo negli archivi da distribuire.
Usare il certificato CA corretto per il proprio cluster; `certs/tidb-ca.pem` è quello
fornito nell’archivio originale. La connessione verifica certificato e nome del server.
Le password con caratteri speciali vengono gestite senza concatenare stringhe URL.
`DATABASE_URL`, se presente, ha precedenza: non impostarla se vuoi usare le variabili TiDB.

Con backup disponibile, esegui `python -m flask --app run init-db` sul database selezionato:
crea solo le tabelle mancanti `volontari` e `impegni_calendario` e conserva le tabelle
`admin` e `prodotti` esistenti. Il comando **non** aggiorna lo schema di tabelle già presenti:
per cambiamenti futuri alle colonne serviranno migrazioni. Il server non crea tabelle
all’avvio e non nasconde gli errori di connessione. Gli errori database nelle richieste
mostrano una pagina 503 senza esporre credenziali.

TiDB non è stato verificato da questo ambiente cloud: la risoluzione DNS dell’host fornito
è fallita prima dell’autenticazione. I test funzionali usano un database SQLite isolato.
La connessione MySQL/TLS richiede una rete compatibile; un proxy solo HTTPS non basta.

## Produzione

Imposta `APP_ENV=production`, `SECRET_KEY` casuale stabile e il database prima dell’avvio.
La modalità produzione richiede configurazione esplicita, forza HTTPS e cookie sicuri.
Termina TLS davanti all’applicazione; configura il reverse proxy secondo il tuo deployment
senza fidarti di header inoltrati da client arbitrari. Non usare il server debug in produzione.

Su Linux:

```sh
gunicorn --bind 127.0.0.1:5000 --workers 1 run:app
```

Il limite dei tentativi usa memoria del processo per impostazione predefinita; non è
condiviso tra worker e riparte al riavvio. Per più processi/configurazioni distribuite,
configura uno storage condiviso supportato da Flask-Limiter e la relativa dipendenza.
Fai backup regolari del database. I processi server vanno riavviati nei nuovi ambienti.

## Verifiche

```sh
python -m pip check
python -m unittest discover -s tests -v
```

I test esercitano autenticazione reale, CSRF, logout, inventario, upload PNG/MIME,
file e numeri non validi, creazione/modifica/eliminazione di turni ed eventi,
sovrapposizioni, volontari disattivati, filtri, cambio dell’ora e limiti del mese.
Non contattano TiDB né modificano dati reali.

## Correzioni principali

- Avvio locale senza credenziali, configurazione TiDB validata e inizializzazione esplicita.
- Chiave di sessione persistente in sviluppo e obbligatoria in produzione.
- Pillow al posto di `imghdr`, con verifica immagini e MIME coerente.
- Nessuna modifica parziale al prodotto quando il nuovo upload è invalido; rifiuto di prezzi NaN/infinito.
- JavaScript esterno compatibile con CSP: ricerca e conferme funzionano anche con nomi contenenti apostrofi.
- Logout POST con CSRF, bootstrap dell’amministratore solo da CLI e pagine di errore specifiche.
- Menu disponibile su telefono e layout responsive per calendario e dettagli prodotto.
