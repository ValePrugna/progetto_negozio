from flask import Blueprint, render_template, redirect, url_for, request
from urllib.parse import urlparse, urljoin
from app.extensions import db, limiter
from app.models import accedi, Admin
from flask_login import login_user, login_required, current_user

bp = Blueprint('auth', __name__)


def _is_safe_url(target):
    """Valida che il redirect non punti a un host esterno (Open Redirect)."""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc


@bp.route('/', methods=['GET', 'POST'])
@limiter.limit("10 per minute")   # HIGH-4: max 10 tentativi/minuto per IP
def login():
    if current_user.is_authenticated:
        return redirect(url_for("routes.index"))

    form = accedi()

    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data

        admin = Admin.query.filter_by(username=username).first()
        if admin and admin.check_password(password):
            login_user(admin)
            from flask import session
            session.permanent = True

            # LOW-11: redirect sicuro — valida il parametro `next`
            next_page = request.args.get('next')
            if next_page and _is_safe_url(next_page):
                return redirect(next_page)
            return redirect(url_for("routes.index"))

        # Credenziali errate: non rivelare quale campo è sbagliato
        form.username.errors.append("Credenziali non valide.")

    return render_template("login.html", form=form)