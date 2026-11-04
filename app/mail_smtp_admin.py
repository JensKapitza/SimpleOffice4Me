"""Admin UI for SMTP send settings (no environment).

Booking confirmations and other server-sent mail use the user-home disk
store; the password is encrypted and only a presence marker is shown.
"""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from . import mail_smtp
from .federation_admin import admin_required
from .translations import translate

bp = Blueprint("mail_smtp_admin", __name__, url_prefix="/admin/mail/smtp")


@bp.get("")
@admin_required
def index():
    return render_template("admin/mail_smtp.html", smtp=mail_smtp.status())


@bp.post("")
@admin_required
def save():
    try:
        mail_smtp.save({
            "host": request.form.get("host", ""),
            "sender": request.form.get("sender", ""),
            "port": request.form.get("port", ""),
            "starttls": request.form.get("starttls", ""),
            "user": request.form.get("user", ""),
            "password": request.form.get("password", ""),
        }, g.language)
        flash(translate(g.language, "mail.smtp.saved"))
    except ValueError as exc:
        flash(translate(g.language, "mail.smtp.failed").format(exc=exc))
    return redirect(url_for("mail_smtp_admin.index"))


@bp.post("/test")
@admin_required
def test():
    try:
        mail_smtp.test_login(g.language)
        flash(translate(g.language, "mail.smtp.test.ok"))
    except ValueError as exc:
        flash(translate(g.language, "mail.smtp.test.failed").format(exc=exc))
    return redirect(url_for("mail_smtp_admin.index"))
