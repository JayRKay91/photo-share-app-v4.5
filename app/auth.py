from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import login_user, login_required, logout_user, current_user
from itsdangerous import URLSafeTimedSerializer
from flask_mailman import EmailMessage  # switched from flask_mail
from .models import db, User
from . import login_manager, mail
import os
from pathlib import Path

auth = Blueprint("auth", __name__, template_folder="templates")

# Serializer for generating tokens
s = URLSafeTimedSerializer(os.getenv("SECRET_KEY", "dev_key"))

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def create_user_folder(user_id):
    """Create a personal uploads folder for the user if it doesn't exist."""
    upload_base = Path(current_app.config["UPLOAD_FOLDER"]).parent.parent.parent / "uploads"
    user_folder = upload_base / str(user_id)
    user_folder.mkdir(parents=True, exist_ok=True)

# ---------- Register ----------
@auth.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        email = request.form.get("email")
        password = request.form.get("password")

        existing_user = User.query.filter((User.username == username) | (User.email == email)).first()
        if existing_user:
            flash("Username or email already exists.", "error")
            return redirect(url_for("auth.register"))

        new_user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            is_verified=True  # ✅ Automatically verify user for now
        )
        db.session.add(new_user)
        db.session.commit()

        # Create personal upload folder after user is added
        create_user_folder(new_user.id)

        # Send verification email
        token = s.dumps(email, salt="email-confirm")
        link = url_for("auth.verify_email", token=token, _external=True)
        msg = EmailMessage(
            subject="Confirm Your Email",
            body=f"Click the link to verify your account: {link}",
            from_email=current_app.config['MAIL_DEFAULT_SENDER'],
            to=[email]
        )
        mail.send(msg)

        flash("A verification email has been sent. Please check your inbox.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html")

# ---------- Verify Email ----------
@auth.route("/verify/<token>")
def verify_email(token):
    try:
        email = s.loads(token, salt="email-confirm", max_age=3600)
        user = User.query.filter_by(email=email).first()
        if user:
            user.is_verified = True
            db.session.commit()
            flash("Email verified. You can now log in.", "success")
            return redirect(url_for("auth.login"))
    except Exception:
        flash("The confirmation link is invalid or has expired.", "error")

    return redirect(url_for("auth.login"))

# ---------- Login ----------
@auth.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid email or password.", "error")
            return redirect(url_for("auth.login"))

        if not user.is_verified:
            flash("Please verify your email before logging in.", "warning")
            return redirect(url_for("auth.login"))

        login_user(user)
        flash("Login successful!", "success")
        return redirect(url_for("main.index"))

    return render_template("auth/login.html")

# ---------- Logout ----------
@auth.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))

# ---------- Password Reset Request ----------
@auth.route("/reset", methods=["GET", "POST"])
def reset_request():
    if request.method == "POST":
        email = request.form.get("email")
        user = User.query.filter_by(email=email).first()

        if user:
            token = s.dumps(email, salt="password-reset")
            link = url_for("auth.reset_token", token=token, _external=True)
            msg = EmailMessage(
                subject="Reset Your Password",
                body=f"Click to reset your password: {link}",
                from_email=current_app.config['MAIL_DEFAULT_SENDER'],
                to=[email]
            )
            mail.send(msg)
            flash("Password reset email sent.", "success")
        else:
            flash("No account with that email.", "error")

        return redirect(url_for("auth.login"))

    return render_template("auth/reset_request.html")

# ---------- Password Reset via Token ----------
@auth.route("/reset/<token>", methods=["GET", "POST"])
def reset_token(token):
    try:
        email = s.loads(token, salt="password-reset", max_age=3600)
    except Exception:
        flash("Reset link is invalid or has expired.", "error")
        return redirect(url_for("auth.reset_request"))

    user = User.query.filter_by(email=email).first()
    if request.method == "POST":
        new_password = request.form.get("password")
        user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        flash("Your password has been updated.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html")
