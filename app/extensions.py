from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

login_manager = LoginManager()
login_manager.login_view = "auth.login"

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],  # nessun limite globale, solo sui singoli endpoint
)

csrf = CSRFProtect()