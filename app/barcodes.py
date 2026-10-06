import re


def normalize_code(value):
    if not isinstance(value, str):
        raise ValueError('Il codice deve essere testo, non un numero.')
    value = value.strip()
    if not re.fullmatch(r'[\x21-\x7e]{1,128}', value):
        raise ValueError('Usa un codice di 1–128 caratteri, senza spazi o caratteri di controllo.')
    return value


def barcode_validator(form, field):
    if field.data:
        from wtforms.validators import ValidationError
        try:
            field.data = normalize_code(field.data)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
