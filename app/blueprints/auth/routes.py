"""
Authentication Blueprint Routes.
Enforces login rate-limiting, account lockout (5 attempts / 15 minutes),
forced password change on first login, and login history logging with IP and user agent.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from app.extensions import db, limiter
from app.models.user import User, LoginHistory
from app.blueprints.auth.forms import LoginForm, ChangePasswordForm
from app.utils.timezone import utc_now

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        password = form.password.data

        user = User.query.filter_by(username=username).first()
        ip_addr = request.remote_addr
        user_agent = request.headers.get("User-Agent", "")[:250]

        if not user:
            # Record failed login for non-existent user
            history = LoginHistory(
                user_id=None,
                username_attempted=username,
                ip_address=ip_addr,
                user_agent=user_agent,
                login_status="failed",
                failure_reason="Invalid credentials",
                attempted_at=utc_now(),
            )
            db.session.add(history)
            db.session.commit()
            flash("Invalid username or password.", "danger")
            return render_template("auth/login.html", form=form)

        if not user.is_active:
            flash("This account has been deactivated. Please contact an administrator.", "danger")
            return render_template("auth/login.html", form=form)

        if user.is_locked():
            history = LoginHistory(
                user_id=user.id,
                username_attempted=username,
                ip_address=ip_addr,
                user_agent=user_agent,
                login_status="locked",
                failure_reason="Account temporarily locked",
                attempted_at=utc_now(),
            )
            db.session.add(history)
            db.session.commit()
            flash("Account temporarily locked due to multiple failed login attempts. Please try again later.", "danger")
            return render_template("auth/login.html", form=form)

        if not user.check_password(password):
            user.record_failed_login()
            if user.is_locked():
                history = LoginHistory(
                    user_id=user.id,
                    username_attempted=username,
                    ip_address=ip_addr,
                    user_agent=user_agent,
                    login_status="locked",
                    failure_reason="Account temporarily locked",
                    attempted_at=utc_now(),
                )
                db.session.add(history)
                db.session.commit()
                flash("Account temporarily locked due to multiple failed login attempts. Please try again later.", "danger")
                return render_template("auth/login.html", form=form)

            history = LoginHistory(
                user_id=user.id,
                username_attempted=username,
                ip_address=ip_addr,
                user_agent=user_agent,
                login_status="failed",
                failure_reason="Invalid credentials",
                attempted_at=utc_now(),
            )
            db.session.add(history)
            db.session.commit()
            flash("Invalid username or password.", "danger")
            return render_template("auth/login.html", form=form)

        # Successful login
        user.reset_failed_logins()
        login_user(user, remember=form.remember.data)

        history = LoginHistory(
            user_id=user.id,
            username_attempted=username,
            ip_address=ip_addr,
            user_agent=user_agent,
            login_status="success",
            failure_reason=None,
            attempted_at=utc_now(),
        )
        db.session.add(history)
        db.session.commit()

        # Enforce password change on first login
        if user.must_change_pwd:
            flash("You must change your password before continuing.", "warning")
            return redirect(url_for("auth.change_password"))

        next_page = request.args.get("next")
        flash(f"Welcome back, {user.full_name}!", "success")
        return redirect(next_page or url_for("dashboard.index"))

    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been successfully logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash("Current password entered is incorrect.", "danger")
            return render_template("auth/change_password.html", form=form)

        current_user.set_password(form.new_password.data)
        current_user.must_change_pwd = 0
        db.session.commit()

        flash("Your password has been updated successfully.", "success")
        return redirect(url_for("dashboard.index"))

    return render_template("auth/change_password.html", form=form)
