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

## Turni: due slot per giorno

![Calendario con slot mattina e pomeriggio](docs/calendario-turni.png)

Dal menu **Turni dei volontari** apri il mese che vuoi organizzare.
Ogni giorno ha esattamente due slot: **Mattina** e **Pomeriggio**.
Non si inseriscono titoli o orari per i turni.

- **Rosso / Libero**: nessun volontario assegnato.
- **Verde / Coperto**: uno o più volontari assegnati; i nomi appaiono nello slot.

Clicca sullo slot, spunta i volontari e premi **Salva assegnazioni**.
Puoi assegnare più persone allo stesso slot e la stessa persona a mattina e pomeriggio.
Per rimuovere tutti i nomi usa **Libera slot**, con conferma. Il riquadro tornerà rosso.
Il numero di slot non cambia e non è possibile creare un terzo turno.
Su telefono ogni giorno mostra i due slot affiancati, senza scorrimento orizzontale.

In **Volontari** puoi aggiungere, rinominare o disattivare un nome.
Un volontario disattivato resta nelle assegnazioni esistenti ma non può essere aggiunto
agli altri slot. Non ha un account di accesso. Sono supportate le date dal 2000 al 2100.

## Eventi del mese

Gli eventi hanno una pagina separata, **Eventi del mese**: seleziona il mese e premi
**Aggiungi evento**. Inserisci cosa succede, giorno, eventuale ultimo giorno, luogo e dettagli.
L’ultimo giorno è incluso; se lo lasci vuoto l’evento dura un solo giorno.
Puoi modificare o eliminare ogni evento. Gli eventi non occupano slot e non cambiano
i colori del calendario dei turni.

## Aggiornare la prima versione su Windows

1. Ferma il sito con **Ctrl+C** e fai una copia della cartella `instance` come backup.
2. Scarica il nuovo ZIP dal ramo `codex/calendario-volontari-20261006`.
3. Copia le cartelle `app` e `static` dalla nuova versione nella cartella già usata,
   sostituendo i file. Puoi aggiornare anche `tests` e `README.md`.
   Conserva `.venv`, `instance` e l’eventuale `.env`: contengono ambiente, account e dati.
4. Nello stesso terminale, con `(.venv)` attivo, esegui:

```bat
python -m flask --app run init-db
python -m flask --app run run --debug
```

`init-db` aggiunge le tabelle degli slot e converte i vecchi turni. Per questa conversione,
le porzioni del vecchio intervallo prima delle 12:00 vengono assegnate alla mattina,
quelle dopo le 12:00 al pomeriggio. Un vecchio turno che attraversa mezzogiorno copre entrambi.
Gli slot nuovi non hanno orari da scegliere: questa regola serve solo a recuperare i dati precedenti.
Controlla le assegnazioni importate e correggile secondo le necessità del negozio.
I vecchi record, con titoli, note e orari, restano nel database e gli eventi restano invariati.
La conversione si esegue una sola volta per ogni vecchio turno: ripetere `init-db`
non ripristina volontari rimossi dagli slot né duplica assegnazioni.
Non occorre ricreare l’account del capo.

## TiDB esistente

Copia `.env.example` in `.env` e compila `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASS`,
`DB_NAME` e `DB_SSL_CA`. Non condividere il file né includerlo negli archivi da distribuire.
Usare il certificato CA corretto per il proprio cluster; `certs/tidb-ca.pem` è quello
fornito nell’archivio originale. La connessione verifica certificato e nome del server.
Le password con caratteri speciali vengono gestite senza concatenare stringhe URL.
`DATABASE_URL`, se presente, ha precedenza: non impostarla se vuoi usare le variabili TiDB.

Con backup disponibile, esegui `python -m flask --app run init-db` sul database selezionato:
crea le tabelle mancanti, incluse `turni_slot`, `slot_volontari` e `turni_migrati`,
esegue la conversione dei turni descritta sopra e conserva `admin`, `prodotti` ed eventi. Il comando **non** aggiorna lo schema di tabelle già presenti:
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
file e numeri non validi, due soli slot giornalieri, colori rosso/verde,
assegnazioni multiple e rimozione dei volontari, eventi separati, volontari disattivati,
limiti del mese e conversione ripetibile dei turni precedenti.
La suite viene eseguita anche senza fusi orari di sistema, usando `tzdata` come su Windows.
Non contattano TiDB né modificano dati reali.

## Correzioni principali

- Avvio locale senza credenziali, configurazione TiDB validata e inizializzazione esplicita.
- Chiave di sessione persistente in sviluppo e obbligatoria in produzione.
- Pillow al posto di `imghdr`, con verifica immagini e MIME coerente.
- Nessuna modifica parziale al prodotto quando il nuovo upload è invalido; rifiuto di prezzi NaN/infinito.
- JavaScript esterno compatibile con CSP: ricerca e conferme funzionano anche con nomi contenenti apostrofi.
- Logout POST con CSRF, bootstrap dell’amministratore solo da CLI e pagine di errore specifiche.
- Menu disponibile su telefono e layout responsive per calendario e dettagli prodotto.
