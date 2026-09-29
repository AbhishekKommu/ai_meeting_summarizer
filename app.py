"""
================================================================================
 AI Meeting Summarizer — Professional Flask Application (Final Fixed Version)
================================================================================
"""

# =============================================================================
# STANDARD LIBRARY
# =============================================================================
import os
import re
import uuid
import time
import logging
import secrets
import threading
from datetime import datetime, timedelta
from functools import wraps
from logging.handlers import RotatingFileHandler

# =============================================================================
# THIRD-PARTY
# =============================================================================
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, abort, current_app, send_from_directory,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, desc
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge

# =============================================================================
# PATHS
# =============================================================================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
DATABASE_DIR = os.path.join(BASE_DIR, "database")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
LOG_DIR = os.path.join(BASE_DIR, "logs")

for _d in (INSTANCE_DIR, DATABASE_DIR, UPLOAD_DIR, LOG_DIR):
    os.makedirs(_d, exist_ok=True)


# =============================================================================
# ✅ FIX #1: MODULE-LEVEL CACHE GLOBALS (fixes UnboundLocalError)
# =============================================================================
_WHISPER_CACHE = None
_SUMMARIZER_CACHE = None
_WHISPER_LOCK = threading.Lock()
_SUMMARIZER_LOCK = threading.Lock()


# =============================================================================
# CONFIG
# =============================================================================
class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(DATABASE_DIR, 'meetings.db')}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 300}

    UPLOAD_FOLDER = UPLOAD_DIR
    MAX_CONTENT_LENGTH = 500 * 1024 * 1024
    ALLOWED_AUDIO_EXTENSIONS = {"mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "wma"}
    ALLOWED_VIDEO_EXTENSIONS = {"mp4", "webm", "mkv", "mov", "avi", "m4v"}
    ALLOWED_EXTENSIONS = ALLOWED_AUDIO_EXTENSIONS | ALLOWED_VIDEO_EXTENSIONS

    RATELIMIT_LOGIN_ATTEMPTS = 8
    RATELIMIT_WINDOW_SECONDS = 300

    WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
    SUMMARIZER_MODEL = os.environ.get("SUMMARIZER_MODEL", "facebook/bart-large-cnn")
    ENABLE_REAL_AI = os.environ.get("ENABLE_REAL_AI", "false").lower() == "true"

    MEETINGS_PER_PAGE = 10


class DevelopmentConfig(Config):
    DEBUG = True
    ENV = "development"


class ProductionConfig(Config):
    DEBUG = False
    ENV = "production"
    SESSION_COOKIE_SECURE = True


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


# =============================================================================
# EXTENSIONS
# =============================================================================
db = SQLAlchemy()


# =============================================================================
# LOGGING
# =============================================================================
def _configure_logging(app: Flask) -> None:
    log_file = os.path.join(LOG_DIR, "app.log")
    file_handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)s in %(module)s: %(message)s"
    ))
    file_handler.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    ))
    console_handler.setLevel(logging.DEBUG if app.debug else logging.INFO)

    app.logger.handlers.clear()
    app.logger.addHandler(file_handler)
    app.logger.addHandler(console_handler)
    app.logger.setLevel(logging.DEBUG if app.debug else logging.INFO)


# =============================================================================
# MODELS
# =============================================================================
class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_login = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    meetings = db.relationship(
        "Meeting", backref="user", lazy="dynamic", cascade="all, delete-orphan"
    )

    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw, method="pbkdf2:sha256")

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "created_at": self.created_at.isoformat(),
            "last_login": self.last_login.isoformat() if self.last_login else None,
        }

    def __repr__(self) -> str:
        return f"<User {self.username}>"


class Meeting(db.Model):
    __tablename__ = "meetings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    file_size = db.Column(db.Integer, default=0)
    duration_seconds = db.Column(db.Float, default=0.0)
    transcript = db.Column(db.Text, nullable=True)
    summary = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default="pending", nullable=False)
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self, include_full_text: bool = False) -> dict:
        data = {
            "id": self.id,
            "filename": self.filename,
            "file_size": self.file_size,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
        if include_full_text:
            data["summary"] = self.summary or ""
            data["transcript"] = self.transcript or ""
            data["error_message"] = self.error_message
        return data

    def __repr__(self) -> str:
        return f"<Meeting {self.id} {self.filename} ({self.status})>"


class LoginAttempt(db.Model):
    __tablename__ = "login_attempts"

    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(45), index=True)
    username = db.Column(db.String(64), index=True)
    attempted_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    success = db.Column(db.Boolean, default=False)


# =============================================================================
# HELPERS
# =============================================================================
def get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def generate_csrf_token() -> str:
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]


def validate_csrf_token(token: str) -> bool:
    return bool(token) and secrets.compare_digest(
        token, session.get("_csrf_token", "")
    )


def is_password_strong(password: str):
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter."
    if not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter."
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one digit."
    return True, ""


def is_valid_email(email: str) -> bool:
    pattern = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
    return bool(re.match(pattern, email))


def allowed_file(filename: str) -> bool:
    # ✅ FIX #2: use current_app (never module-level `app`)
    try:
        exts = current_app.config["ALLOWED_EXTENSIONS"]
    except RuntimeError:
        exts = Config.ALLOWED_EXTENSIONS
    return "." in filename and filename.rsplit(".", 1)[1].lower() in exts


def get_file_extension(filename: str) -> str:
    return filename.rsplit(".", 1)[1].lower() if "." in filename else ""


def check_rate_limit(ip: str, action: str) -> bool:
    try:
        window_secs = current_app.config["RATELIMIT_WINDOW_SECONDS"]
        limit = current_app.config["RATELIMIT_LOGIN_ATTEMPTS"]
        logger = current_app.logger
    except RuntimeError:
        window_secs = Config.RATELIMIT_WINDOW_SECONDS
        limit = Config.RATELIMIT_LOGIN_ATTEMPTS
        logger = logging.getLogger(__name__)

    cutoff = datetime.utcnow() - timedelta(seconds=window_secs)
    attempts = LoginAttempt.query.filter(
        LoginAttempt.ip_address == ip,
        LoginAttempt.attempted_at >= cutoff,
        LoginAttempt.success.is_(False),
    ).count()
    if attempts >= limit:
        logger.warning("Rate limit exceeded for IP %s on %s", ip, action)
        return False
    return True


def record_login_attempt(ip: str, username: str, success: bool) -> None:
    try:
        db.session.add(LoginAttempt(ip_address=ip, username=username, success=success))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logging.getLogger(__name__).error("Failed to record login attempt: %s", exc)


# =============================================================================
# DECORATORS
# =============================================================================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def api_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated


def csrf_protect(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method == "POST":
            token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not validate_csrf_token(token):
                logging.getLogger(__name__).warning(
                    "CSRF validation failed for %s", request.path
                )
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Invalid CSRF token"}), 403
                abort(403)
        return f(*args, **kwargs)
    return decorated


# =============================================================================
# ✅ FIX #3 & #4: LAZY MODEL LOADERS (safe outside app context + thread-safe)
# =============================================================================
def _get_config(key: str, default=None):
    """Safely fetch config from current_app, falling back to Config class."""
    try:
        return current_app.config[key]
    except RuntimeError:
        return getattr(Config, key, default)


def _get_logger():
    try:
        return current_app.logger
    except RuntimeError:
        return logging.getLogger(__name__)


def _load_whisper():
    """Lazily load & cache Whisper model. Thread-safe."""
    global _WHISPER_CACHE
    if _WHISPER_CACHE is not None:
        return _WHISPER_CACHE

    with _WHISPER_LOCK:
        if _WHISPER_CACHE is not None:
            return _WHISPER_CACHE

        model_name = _get_config("WHISPER_MODEL", "base")
        logger = _get_logger()

        try:
            import whisper
        except ImportError as exc:
            raise RuntimeError(
                "Whisper is not installed. Run: pip install openai-whisper"
            ) from exc

        logger.info("Loading Whisper model '%s'…", model_name)
        _WHISPER_CACHE = whisper.load_model(model_name)
        logger.info("✅ Whisper model '%s' loaded", model_name)

    return _WHISPER_CACHE


def _load_summarizer():
    """Lazily load & cache HF summarizer. Thread-safe."""
    global _SUMMARIZER_CACHE
    if _SUMMARIZER_CACHE is not None:
        return _SUMMARIZER_CACHE

    with _SUMMARIZER_LOCK:
        if _SUMMARIZER_CACHE is not None:
            return _SUMMARIZER_CACHE

        model_name = _get_config("SUMMARIZER_MODEL", "facebook/bart-large-cnn")
        logger = _get_logger()

        try:
            from transformers import pipeline
        except ImportError as exc:
            raise RuntimeError(
                "transformers is not installed. Run: pip install transformers torch"
            ) from exc

        logger.info("Loading summarizer '%s'…", model_name)
        _SUMMARIZER_CACHE = pipeline("summarization", model=model_name)
        logger.info("✅ Summarizer '%s' loaded", model_name)

    return _SUMMARIZER_CACHE


# =============================================================================
# AI PIPELINE
# =============================================================================
def _transcribe_audio(filepath: str) -> str:
    model = _load_whisper()
    result = model.transcribe(filepath, fp16=False)
    return result.get("text", "").strip()


def _summarize_text(transcript: str, max_length: int = 180) -> str:
    if not transcript:
        return ""
    summarizer = _load_summarizer()
    chunk_size = 1000
    chunks = [transcript[i:i + chunk_size] for i in range(0, len(transcript), chunk_size)]
    summaries = []
    for chunk in chunks:
        try:
            out = summarizer(
                chunk, max_length=max_length, min_length=40,
                do_sample=False, truncation=True,
            )
            summaries.append(out[0]["summary_text"])
        except Exception as exc:
            _get_logger().error("Summarizer chunk failed: %s", exc)
    return " ".join(summaries).strip()


def _mock_summary(original_name: str) -> str:
    topics = [
        "Q4 product roadmap and resource planning",
        "marketing campaign strategy and budget allocation",
        "engineering sprint retrospective and blockers",
        "customer feedback review and feature prioritization",
        "OKR alignment and quarterly goal setting",
    ]
    participants = ["Alice", "Bob", "Carol", "David", "Elena", "Frank"]
    actions = [
        "finalize the design document by Friday",
        "schedule a follow-up with the design team",
        "prepare the revised budget proposal",
        "run a competitive analysis",
        "draft the project timeline for phase 2",
    ]
    seed = len(original_name)
    topic = topics[seed % len(topics)]
    p1 = participants[seed % len(participants)]
    p2 = participants[(seed + 2) % len(participants)]
    p3 = participants[(seed + 4) % len(participants)]
    action = actions[(seed * 3) % len(actions)]

    return (
        f"📋 Meeting Summary — \"{original_name}\"\n\n"
        f"Topic: {topic}\n\n"
        f"{p1} opened with a progress update, noting that milestones are on track. "
        f"{p2} raised concerns about resource allocation, and the team agreed to revisit the budget.\n\n"
        f"🔹 Key Decisions:\n"
        f"• {p3} will {action}.\n"
        f"• Next sync scheduled for Thursday at 10 AM.\n"
        f"• Phase 2 timeline approved unanimously.\n\n"
        f"✅ Action Items:\n"
        f"1. {p1} to share updated metrics with stakeholders.\n"
        f"2. {p2} to coordinate with vendors.\n"
        f"3. All members to review the shared document by EOD Tuesday."
    )


def _run_ai_pipeline(flask_app, meeting_id: int, filepath: str, original_name: str) -> None:
    """
    ✅ FIX #4: pass Flask app explicitly (no `from app import app` circular import).
    """
    with flask_app.app_context():
        meeting = db.session.get(Meeting, meeting_id)
        if not meeting:
            return
        try:
            meeting.status = "processing"
            db.session.commit()
            current_app.logger.info("Processing meeting %s (%s)", meeting_id, original_name)

            if current_app.config["ENABLE_REAL_AI"]:
                transcript = _transcribe_audio(filepath)
                summary = _summarize_text(transcript)
                meeting.transcript = transcript
            else:
                time.sleep(2)
                summary = _mock_summary(original_name)
                meeting.transcript = "[Mock transcript — enable ENABLE_REAL_AI for real transcription]"

            meeting.summary = summary
            meeting.status = "done"
            meeting.completed_at = datetime.utcnow()
            db.session.commit()
            current_app.logger.info("✅ Meeting %s summarized", meeting_id)

        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception("❌ AI pipeline failed for meeting %s", meeting_id)
            meeting = db.session.get(Meeting, meeting_id)
            if meeting:
                meeting.status = "failed"
                meeting.error_message = str(exc)
                db.session.commit()


# =============================================================================
# APP FACTORY
# =============================================================================
def create_app(config_name: str = None) -> Flask:
    config_name = config_name or os.environ.get("FLASK_ENV", "development")
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(CONFIG_MAP.get(config_name, DevelopmentConfig))

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)

    _configure_logging(app)
    _register_routes(app)
    _register_error_handlers(app)
    _register_context_processors(app)
    _register_security_headers(app)
    _register_cli(app)

    with app.app_context():
        db.create_all()
        app.logger.info("Database initialized")

    app.logger.info("✅ App started in '%s' mode", config_name)
    return app


# =============================================================================
# ROUTES
# =============================================================================
def _register_routes(app: Flask) -> None:

    @app.route("/")
    def root():
        if "user_id" in session:
            return redirect(url_for("index"))
        return redirect(url_for("login"))

    @app.route("/health")
    def health():
        return jsonify({
            "status": "ok",
            "timestamp": datetime.utcnow().isoformat(),
            "version": "2.0.0",
        })

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if "user_id" in session:
            return redirect(url_for("index"))

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm", "")

            errors = []
            if not username or len(username) < 3:
                errors.append("Username must be at least 3 characters.")
            if not is_valid_email(email):
                errors.append("Please enter a valid email address.")
            if password != confirm:
                errors.append("Passwords do not match.")
            ok, reason = is_password_strong(password)
            if not ok:
                errors.append(reason)

            if errors:
                for err in errors:
                    flash(err, "danger")
                return redirect(url_for("register"))

            if User.query.filter(
                (User.username == username) | (User.email == email)
            ).first():
                flash("Username or email already registered.", "danger")
                return redirect(url_for("register"))

            try:
                user = User(username=username, email=email)
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                app.logger.info("New user: %s", username)
                flash("Account created! Please log in.", "success")
                return redirect(url_for("login"))
            except Exception:
                db.session.rollback()
                app.logger.exception("Registration failed")
                flash("Registration failed. Try again.", "danger")
                return redirect(url_for("register"))

        return render_template("register.html", csrf_token=generate_csrf_token())

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if "user_id" in session:
            return redirect(url_for("index"))

        if request.method == "POST":
            ip = get_client_ip()
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            remember = request.form.get("remember") == "on"

            if not check_rate_limit(ip, "login"):
                flash("Too many failed attempts. Try later.", "danger")
                return redirect(url_for("login"))

            user = User.query.filter(
                (User.username == username) | (User.email == username.lower())
            ).first()

            if user and user.is_active and user.check_password(password):
                session.clear()
                session["user_id"] = user.id
                session["username"] = user.username
                session.permanent = remember

                user.last_login = datetime.utcnow()
                db.session.commit()
                record_login_attempt(ip, username, success=True)

                flash(f"Welcome back, {user.username}!", "success")
                next_url = request.args.get("next") or url_for("index")
                if not next_url.startswith("/"):
                    next_url = url_for("index")
                return redirect(next_url)

            record_login_attempt(ip, username, success=False)
            flash("Invalid username or password.", "danger")
            return redirect(url_for("login"))

        return render_template("login.html", csrf_token=generate_csrf_token())

    @app.route("/logout")
    def logout():
        session.clear()
        flash("You have been logged out.", "info")
        return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required
    def index():
        page = request.args.get("page", 1, type=int)
        per_page = app.config["MEETINGS_PER_PAGE"]

        pagination = (
            Meeting.query
            .filter_by(user_id=session["user_id"])
            .order_by(desc(Meeting.created_at))
            .paginate(page=page, per_page=per_page, error_out=False)
        )

        return render_template(
            "index.html",
            meetings=pagination.items,
            pagination=pagination,
            username=session["username"],
            csrf_token=generate_csrf_token(),
        )

    @app.route("/summarize", methods=["POST"])
    @api_login_required
    @csrf_protect
    def summarize():
        if "audio" not in request.files:
            return jsonify({"error": "No file part in request"}), 400
        file = request.files["audio"]
        if not file or file.filename == "":
            return jsonify({"error": "No file selected"}), 400
        if not allowed_file(file.filename):
            return jsonify({"error": "File type not allowed"}), 400

        original_name = secure_filename(file.filename)
        ext = get_file_extension(original_name)
        unique_name = f"{session['user_id']}_{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)

        try:
            file.save(filepath)
            file_size = os.path.getsize(filepath)
        except Exception:
            app.logger.exception("File save failed")
            return jsonify({"error": "Could not save uploaded file."}), 500

        try:
            meeting = Meeting(
                user_id=session["user_id"],
                filename=original_name,
                stored_filename=unique_name,
                file_size=file_size,
                status="pending",
            )
            db.session.add(meeting)
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("DB insert failed")
            try:
                os.remove(filepath)
            except OSError:
                pass
            return jsonify({"error": "Database error"}), 500

        # ✅ FIX #4 again: pass the real Flask app object
        flask_app = current_app._get_current_object()
        worker = threading.Thread(
            target=_run_ai_pipeline,
            args=(flask_app, meeting.id, filepath, original_name),
            daemon=True,
        )
        worker.start()

        return jsonify({
            "success": True,
            "meeting_id": meeting.id,
            "filename": original_name,
            "status": meeting.status,
            "message": "File uploaded. Summarization in progress.",
        }), 202

    @app.route("/api/meetings/<int:meeting_id>", methods=["GET"])
    @api_login_required
    def get_meeting(meeting_id: int):
        meeting = Meeting.query.filter_by(
            id=meeting_id, user_id=session["user_id"]
        ).first()
        if not meeting:
            return jsonify({"error": "Meeting not found"}), 404
        return jsonify(meeting.to_dict(include_full_text=True))

    @app.route("/api/meetings", methods=["GET"])
    @api_login_required
    def list_meetings():
        page = request.args.get("page", 1, type=int)
        per_page = min(request.args.get("per_page", 20, type=int), 100)

        pagination = (
            Meeting.query
            .filter_by(user_id=session["user_id"])
            .order_by(desc(Meeting.created_at))
            .paginate(page=page, per_page=per_page, error_out=False)
        )
        return jsonify({
            "meetings": [m.to_dict() for m in pagination.items],
            "page": pagination.page,
            "pages": pagination.pages,
            "total": pagination.total,
            "has_next": pagination.has_next,
            "has_prev": pagination.has_prev,
        })

    @app.route("/api/meetings/<int:meeting_id>", methods=["DELETE"])
    @api_login_required
    @csrf_protect
    def delete_meeting(meeting_id: int):
        meeting = Meeting.query.filter_by(
            id=meeting_id, user_id=session["user_id"]
        ).first()
        if not meeting:
            return jsonify({"error": "Meeting not found"}), 404

        filepath = os.path.join(app.config["UPLOAD_FOLDER"], meeting.stored_filename)
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except OSError as exc:
            app.logger.warning("Could not delete file: %s", exc)

        db.session.delete(meeting)
        db.session.commit()
        return jsonify({"success": True})

    @app.route("/api/profile", methods=["GET"])
    @api_login_required
    def profile():
        user = db.session.get(User, session["user_id"])
        if not user:
            return jsonify({"error": "User not found"}), 404
        stats = {
            "total_meetings": Meeting.query.filter_by(user_id=user.id).count(),
            "completed": Meeting.query.filter_by(user_id=user.id, status="done").count(),
            "pending": Meeting.query.filter_by(user_id=user.id, status="pending").count(),
        }
        return jsonify({"user": user.to_dict(), "stats": stats})

    @app.route("/api/profile/password", methods=["POST"])
    @api_login_required
    @csrf_protect
    def change_password():
        data = request.get_json(silent=True) or request.form
        current = data.get("current_password", "")
        new = data.get("new_password", "")
        confirm = data.get("confirm_password", "")

        user = db.session.get(User, session["user_id"])
        if not user or not user.check_password(current):
            return jsonify({"error": "Current password is incorrect."}), 400
        if new != confirm:
            return jsonify({"error": "New passwords do not match."}), 400
        ok, reason = is_password_strong(new)
        if not ok:
            return jsonify({"error": reason}), 400

        user.set_password(new)
        db.session.commit()
        return jsonify({"success": True, "message": "Password updated."})

    @app.route("/api/stats", methods=["GET"])
    @api_login_required
    def stats():
        user_id = session["user_id"]
        total = Meeting.query.filter_by(user_id=user_id).count()
        done = Meeting.query.filter_by(user_id=user_id, status="done").count()
        processing = Meeting.query.filter_by(user_id=user_id, status="processing").count()
        failed = Meeting.query.filter_by(user_id=user_id, status="failed").count()
        total_size = db.session.query(
            func.coalesce(func.sum(Meeting.file_size), 0)
        ).filter(Meeting.user_id == user_id).scalar()
        return jsonify({
            "total_meetings": total,
            "completed": done,
            "processing": processing,
            "failed": failed,
            "total_size_bytes": int(total_size or 0),
        })

    @app.route("/uploads/<int:meeting_id>/<path:filename>")
    @login_required
    def serve_upload(meeting_id: int, filename: str):
        meeting = Meeting.query.filter_by(
            id=meeting_id, user_id=session["user_id"]
        ).first()
        if not meeting or meeting.stored_filename != filename:
            abort(404)
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=True)


# =============================================================================
# ✅ FIX #5: ERROR HANDLERS use inline HTML (no dependency on error.html template)
# =============================================================================
def _error_page(code: int, message: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{code} · {message}</title>
<style>
  body {{
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center; background: #0b0f1c; color: #f0f4ff;
    font-family: system-ui, -apple-system, sans-serif;
  }}
  .card {{
    background: rgba(20, 30, 50, 0.5); backdrop-filter: blur(18px);
    border: 1px solid rgba(255,255,255,0.15); border-radius: 32px;
    padding: 3rem 4rem; text-align: center;
    box-shadow: 0 30px 60px rgba(0,0,0,0.6);
  }}
  h1 {{ font-size: 5rem; margin: 0; background: linear-gradient(135deg,#a0d0ff,#c0a0ff);
        -webkit-background-clip: text; background-clip: text; color: transparent; }}
  p  {{ color: #b8c8e8; font-size: 1.1rem; margin: 1rem 0 2rem; }}
  a  {{ color: #a0c0ff; text-decoration: none; padding: 12px 28px;
        border: 1px solid rgba(160,192,255,0.4); border-radius: 40px; }}
  a:hover {{ background: rgba(160,192,255,0.15); }}
</style>
</head>
<body>
  <div class="card">
    <h1>{code}</h1>
    <p>{message}</p>
    <a href="/">← Back home</a>
  </div>
</body>
</html>"""


def _register_error_handlers(app: Flask) -> None:

    @app.errorhandler(400)
    def bad_request(e):
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"error": "Bad request"}), 400
        return _error_page(400, "Bad Request"), 400

    @app.errorhandler(403)
    def forbidden(e):
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"error": "Forbidden"}), 403
        return _error_page(403, "Forbidden"), 403

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"error": "Not found"}), 404
        return _error_page(404, "Page Not Found"), 404

    @app.errorhandler(413)
    def payload_too_large(e):
        return jsonify({"error": "File too large. Max 500 MB."}), 413

    @app.errorhandler(RequestEntityTooLarge)
    def handle_413(e):
        return jsonify({"error": "File exceeds 500 MB limit."}), 413

    @app.errorhandler(500)
    def server_error(e):
        app.logger.exception("Unhandled server error")
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"error": "Internal server error"}), 500
        return _error_page(500, "Server Error"), 500


# =============================================================================
# CONTEXT PROCESSORS
# =============================================================================
def _register_context_processors(app: Flask) -> None:

    @app.context_processor
    def inject_globals():
        return {
            "current_year": datetime.utcnow().year,
            "app_name": "AI Meeting Summarizer",
            "app_version": "2.0.0",
            "is_authenticated": "user_id" in session,
            "current_user_name": session.get("username"),
        }


# =============================================================================
# SECURITY HEADERS
# =============================================================================
def _register_security_headers(app: Flask) -> None:

    @app.after_request
    def set_secure_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        return response


# =============================================================================
# CLI COMMANDS
# =============================================================================
def _register_cli(app: Flask) -> None:

    @app.cli.command("init-db")
    def init_db_cmd():
        db.create_all()
        print("✅ Database tables created.")

    @app.cli.command("reset-db")
    def reset_db_cmd():
        confirm = input("⚠️  This will DELETE ALL DATA. Type 'yes': ")
        if confirm.strip().lower() == "yes":
            db.drop_all()
            db.create_all()
            print("✅ Database reset.")
        else:
            print("❌ Cancelled.")

    @app.cli.command("create-admin")
    def create_admin_cmd():
        import getpass
        username = input("Username: ").strip()
        email = input("Email: ").strip().lower()
        password = getpass.getpass("Password: ")

        ok, reason = is_password_strong(password)
        if not ok:
            print(f"❌ {reason}")
            return

        if User.query.filter((User.username == username) | (User.email == email)).first():
            print("❌ Username or email already exists.")
            return

        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"✅ Admin user '{username}' created.")


# =============================================================================
# APP INSTANCE
# =============================================================================
app = create_app()


# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0")
    debug = os.environ.get("FLASK_ENV", "development") == "development"

    app.logger.info("Starting server on %s:%s (debug=%s)", host, port, debug)
    app.run(host=host, port=port, debug=debug, threaded=True)