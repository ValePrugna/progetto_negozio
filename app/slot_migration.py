"""One-time additive conversion of old timed shifts; originals remain intact."""
from datetime import datetime, time, timedelta
from app.extensions import db
from app.models import Impegno, TurnoMigrato, TurnoSlot, Volontario


def migrate_legacy_shifts():
    converted = 0
    old_shifts = Impegno.query.filter_by(tipo='turno').order_by(Impegno.id).all()
    for entry in old_shifts:
        if db.session.get(TurnoMigrato, entry.id):
            continue
        volunteer = db.session.get(Volontario, entry.volontario_id)
        if volunteer is None:
            raise ValueError('Un turno precedente non ha un volontario valido. Correggere i dati prima di migrare.')
        day = entry.inizio.date()
        while datetime.combine(day, time.min) < entry.fine:
            start = datetime.combine(day, time.min)
            noon = datetime.combine(day, time(12))
            end = start + timedelta(days=1)
            for period, lower, upper in [('mattina', start, noon), ('pomeriggio', noon, end)]:
                if entry.inizio < upper and entry.fine > lower:
                    slot = TurnoSlot.query.filter_by(giorno=day, fascia=period).first()
                    if slot is None:
                        slot = TurnoSlot(giorno=day, fascia=period)
                        db.session.add(slot)
                    if volunteer not in slot.volontari:
                        slot.volontari.append(volunteer)
            day += timedelta(days=1)
        db.session.add(TurnoMigrato(impegno_id=entry.id))
        converted += 1
    db.session.commit()
    return converted
