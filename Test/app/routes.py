import hashlib
import hmac
from datetime import date, datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import case, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app import db
from app.models import LoginAttemptBucket, Note, Task, User

main = Blueprint("main", __name__)
LOGIN_RATE_LIMIT = 5
LOGIN_RATE_WINDOW_SECONDS = 60


def _login_bucket_key(kind, value):
    secret_key = current_app.config["SECRET_KEY"]
    if isinstance(secret_key, str):
        secret_key = secret_key.encode("utf-8")
    message = f"{kind}:{value}".encode("utf-8")
    return hmac.new(secret_key, message, hashlib.sha256).hexdigest()


def _record_login_attempt(bucket_key, now, window_start):
    dialect = db.session.get_bind().dialect.name
    if dialect == "postgresql":
        insert = postgresql_insert
    elif dialect == "sqlite":
        insert = sqlite_insert
    else:
        raise RuntimeError(f"Login throttling does not support database dialect {dialect!r}")

    expired = LoginAttemptBucket.window_started_at <= window_start
    statement = insert(LoginAttemptBucket).values(
        key=bucket_key,
        window_started_at=now,
        attempts=1,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[LoginAttemptBucket.key],
        set_={
            "window_started_at": case(
                (expired, now),
                else_=LoginAttemptBucket.window_started_at,
            ),
            "attempts": case(
                (expired, 1),
                else_=LoginAttemptBucket.attempts + 1,
            ),
        },
    ).returning(LoginAttemptBucket.attempts)
    return db.session.execute(statement).scalar_one()


def _lock_login_buckets(bucket_keys):
    dialect = db.session.get_bind().dialect.name
    if dialect == "postgresql":
        lock_ids = sorted(
            int(bucket_key[:16], 16) - (1 << 64)
            if int(bucket_key[:16], 16) >= (1 << 63)
            else int(bucket_key[:16], 16)
            for bucket_key in bucket_keys
        )
        for lock_id in lock_ids:
            db.session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": lock_id},
            )
    elif dialect != "sqlite":
        raise RuntimeError(f"Login throttling does not support database dialect {dialect!r}")


def _release_login_attempt(bucket_key):
    bucket = db.session.get(LoginAttemptBucket, bucket_key)
    if bucket is None:
        raise RuntimeError("Login attempt bucket disappeared before authentication completed")
    if bucket.attempts == 1:
        db.session.delete(bucket)
    else:
        bucket.attempts -= 1


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("main.login"))
        return view(*args, **kwargs)

    return wrapped_view


def owned_record(model, record_id):
    record = model.query.filter_by(id=record_id, user_id=g.user.id).first()
    if record is None:
        abort(404)
    return record


@main.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(url_for("main.home"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(seconds=LOGIN_RATE_WINDOW_SECONDS)
        username_bucket = _login_bucket_key("username", username.casefold())
        ip_bucket = _login_bucket_key("ip", request.remote_addr or "unknown")
        _lock_login_buckets((username_bucket, ip_bucket))
        db.session.query(LoginAttemptBucket).filter(
            LoginAttemptBucket.window_started_at <= window_start
        ).delete(synchronize_session=False)
        username_attempts = _record_login_attempt(username_bucket, now, window_start)
        ip_attempts = _record_login_attempt(ip_bucket, now, window_start)

        if max(username_attempts, ip_attempts) > LOGIN_RATE_LIMIT:
            db.session.commit()
            flash("Check your username and password, then try again.", "error")
            return render_template("login.html")

        user = User.query.filter_by(username=username).first()
        if user is None or not user.check_password(password):
            db.session.commit()
            flash("Check your username and password, then try again.", "error")
        else:
            _release_login_attempt(username_bucket)
            _release_login_attempt(ip_bucket)
            db.session.commit()
            session.clear()
            session["user_id"] = user.id
            current_app.session_interface.regenerate(session)
            return redirect(url_for("main.home"))
    return render_template("login.html")


@main.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("main.login"))


@main.get("/")
@login_required
def home():
    notes = Note.query.filter_by(user_id=g.user.id).order_by(Note.created_at.desc()).limit(4).all()
    tasks = Task.query.filter_by(user_id=g.user.id).order_by(Task.completed, Task.due_date).limit(5).all()
    return render_template("home.html", notes=notes, tasks=tasks)


@main.get("/notes")
@login_required
def notes():
    items = Note.query.filter_by(user_id=g.user.id).order_by(Note.created_at.desc()).all()
    return render_template("notes.html", notes=items)


@main.route("/notes/new", methods=["GET", "POST"])
@login_required
def create_note():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        if not title:
            flash("A title is required.", "error")
        else:
            note = Note(user_id=g.user.id, title=title[:160], content=request.form.get("content", "").strip())
            db.session.add(note)
            db.session.commit()
            flash("Note created.", "success")
            return redirect(url_for("main.notes"))
    return render_template("note_form.html", note=None)


@main.route("/notes/<int:note_id>/edit", methods=["GET", "POST"])
@login_required
def edit_note(note_id):
    note = owned_record(Note, note_id)
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        if not title:
            flash("A title is required.", "error")
        else:
            note.title = title[:160]
            note.content = request.form.get("content", "").strip()
            db.session.commit()
            flash("Note updated.", "success")
            return redirect(url_for("main.notes"))
    return render_template("note_form.html", note=note)


@main.post("/notes/<int:note_id>/delete")
@login_required
def delete_note(note_id):
    db.session.delete(owned_record(Note, note_id))
    db.session.commit()
    flash("Note deleted.", "success")
    return redirect(url_for("main.notes"))


@main.get("/tasks")
@login_required
def tasks():
    items = Task.query.filter_by(user_id=g.user.id).order_by(Task.completed, Task.due_date).all()
    return render_template("tasks.html", tasks=items)


@main.route("/tasks/new", methods=["GET", "POST"])
@login_required
def create_task():
    if request.method == "POST":
        task = task_from_form()
        if task:
            task.user_id = g.user.id
            db.session.add(task)
            db.session.commit()
            flash("Task created.", "success")
            return redirect(url_for("main.tasks"))
    return render_template("task_form.html", task=None)


@main.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
@login_required
def edit_task(task_id):
    task = owned_record(Task, task_id)
    if request.method == "POST":
        updated = task_from_form()
        if updated:
            task.title = updated.title
            task.description = updated.description
            task.due_date = updated.due_date
            task.completed = updated.completed
            db.session.commit()
            flash("Task updated.", "success")
            return redirect(url_for("main.tasks"))
    return render_template("task_form.html", task=task)


def task_from_form():
    title = request.form.get("title", "").strip()
    raw_due_date = request.form.get("due_date", "")
    try:
        due_date = date.fromisoformat(raw_due_date)
    except ValueError:
        due_date = None
    if not title or due_date is None:
        flash("A title and valid due date are required.", "error")
        return None
    return Task(
        title=title[:160],
        description=request.form.get("description", "").strip(),
        due_date=due_date,
        completed=request.form.get("completed") == "on",
    )


@main.post("/tasks/<int:task_id>/delete")
@login_required
def delete_task(task_id):
    db.session.delete(owned_record(Task, task_id))
    db.session.commit()
    flash("Task deleted.", "success")
    return redirect(url_for("main.tasks"))


@main.post("/tasks/<int:task_id>/toggle")
@login_required
def toggle_task(task_id):
    task = owned_record(Task, task_id)
    task.completed = not task.completed
    db.session.commit()
    return redirect(request.referrer or url_for("main.tasks"))


@main.get("/search")
@login_required
def search():
    query = request.args.get("q", "").strip()
    results = []
    if query:
        pattern = f"%{query}%"
        results = Note.query.filter(
            Note.user_id == g.user.id,
            db.or_(Note.title.ilike(pattern), Note.content.ilike(pattern)),
        ).order_by(Note.created_at.desc()).all()
    return render_template("search.html", query=query, notes=results)