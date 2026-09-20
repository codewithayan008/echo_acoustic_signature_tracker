from __future__ import annotations

import io
import json
import os
import secrets
import sqlite3
import uuid
import smtplib
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf
from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, jsonify, render_template, request, session
from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash

from echo_features import extract_features

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DATA_DIR = Path(os.getenv("ECHO_DATA_DIR", str(BASE_DIR)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "echo.db"
KEY_PATH = DATA_DIR / ".echo.key"
SESSION_KEY_PATH = DATA_DIR / ".echo-session.key"
MODEL_PATH = BASE_DIR / "models" / "audio_classifier.joblib"


def get_session_secret():
    configured_secret = os.getenv("ECHO_SESSION_SECRET")
    if configured_secret:
        return configured_secret
    if not SESSION_KEY_PATH.exists():
        SESSION_KEY_PATH.write_text(secrets.token_hex(32), encoding="ascii")
        try:
            SESSION_KEY_PATH.chmod(0o600)
        except OSError:
            pass
    return SESSION_KEY_PATH.read_text(encoding="ascii").strip()


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
app.secret_key = get_session_secret()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    ALLOW_DEV_OTP=os.getenv("ECHO_ALLOW_DEV_OTP", "0") == "1",
)


def get_cipher():
    configured_key = os.getenv("ECHO_DB_KEY")
    if configured_key:
        return Fernet(configured_key.encode())
    if not KEY_PATH.exists():
        KEY_PATH.write_bytes(Fernet.generate_key())
        try:
            KEY_PATH.chmod(0o600)
        except OSError:
            pass
    return Fernet(KEY_PATH.read_bytes().strip())


CIPHER = get_cipher()


def encrypt_features(features: list[float]) -> str:
    return CIPHER.encrypt(json.dumps(features).encode()).decode()


def decrypt_features(value: str) -> list[float]:
    try:
        return json.loads(CIPHER.decrypt(value.encode()).decode())
    except (InvalidToken, ValueError, TypeError):
        return json.loads(value)


def get_db():
    database = sqlite3.connect(DB_PATH)
    database.row_factory = sqlite3.Row
    return database


def init_db():
    with get_db() as database:
        database.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                password_hash TEXT,
                email_verified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        try:
            database.execute("ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        database.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_challenges (
                email TEXT PRIMARY KEY,
                otp_hash TEXT NOT NULL,
                expires_at REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        database.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                score REAL,
                status TEXT NOT NULL,
                features TEXT NOT NULL
            )
            """
        )
        try:
            database.execute("ALTER TABLE sessions ADD COLUMN stage TEXT NOT NULL DEFAULT 'normal_breathing'")
        except sqlite3.OperationalError:
            pass
        rows = database.execute("SELECT id, features FROM sessions").fetchall()
        for row in rows:
            try:
                CIPHER.decrypt(row["features"].encode())
            except InvalidToken:
                database.execute(
                    "UPDATE sessions SET features = ? WHERE id = ?",
                    (encrypt_features(json.loads(row["features"])), row["id"]),
                )
        database.commit()


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with get_db() as database:
        return database.execute("SELECT id, email, display_name FROM users WHERE id = ?", (user_id,)).fetchone()


def normalized_email(value: str) -> str:
    return value.strip().lower()


def send_otp(email: str, otp: str):
    host = os.getenv("ECHO_SMTP_HOST", "smtp.gmail.com")
    smtp_user = os.getenv("ECHO_SMTP_USER") or os.getenv("GMAIL_ADDRESS")
    smtp_password = os.getenv("ECHO_SMTP_PASSWORD") or os.getenv("GMAIL_APP_PASSWORD")
    if not host:
        return False
    if not smtp_user or not smtp_password:
        return False
    message = EmailMessage()
    message["Subject"] = "Your ECHO verification code"
    message["From"] = os.getenv("ECHO_SMTP_FROM", smtp_user)
    message["To"] = email
    message.set_content(f"Your ECHO verification code is {otp}. It expires in 10 minutes.")
    with smtplib.SMTP(host, int(os.getenv("ECHO_SMTP_PORT", "587"))) as smtp:
        smtp.starttls()
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(message)
    return True


def auth_error(message: str, status=400):
    return jsonify({"error": message}), status


def baseline_for(user_id: str, stage: str):
    with get_db() as database:
        rows = database.execute(
            "SELECT features FROM sessions WHERE user_id = ? AND kind = 'baseline' AND stage = ?",
            (user_id, stage),
        ).fetchall()
    if not rows:
        return None
    vectors = np.array([decrypt_features(row["features"]) for row in rows], dtype=float)
    return {
        "mean": vectors.mean(axis=0),
        "scale": np.maximum(vectors.std(axis=0), 0.05),
        "count": len(vectors),
    }


def score_against_baseline(features: np.ndarray, baseline: dict) -> float:
    z_scores = np.abs((features - baseline["mean"]) / baseline["scale"])
    return float(np.clip(np.mean(z_scores) / 3.0 * 100.0, 0.0, 100.0))


def simple_result(status: str, score: float | None, stage: str = "normal_breathing") -> str:
    if status == "baseline_required":
        return "Save a normal baseline sample before checking for change."
    if status == "baseline_saved":
        return "Your normal reference was saved. Add more normal samples for a steadier baseline."
    if score is not None and score >= 35:
        return f"Your {stage.replace('_', ' ')} recording differs from your own baseline. Review the signal shifts and symptoms."
    return f"Your {stage.replace('_', ' ')} recording is close to your own baseline. Continue regular assessments."


def optional_model_result(features: np.ndarray):
    if not MODEL_PATH.exists():
        return None
    try:
        import joblib

        bundle = joblib.load(MODEL_PATH)
        model = bundle["model"]
        label = str(model.predict([features])[0])
        probabilities = model.predict_proba([features])[0] if hasattr(model, "predict_proba") else None
        confidence = max(probabilities) if probabilities is not None else None
        return {"label": label, "confidence": round(float(confidence), 3) if confidence is not None else None}
    except Exception:
        return None


def ask_ollama(message: str, context: dict):
    model = os.getenv("ECHO_OLLAMA_MODEL", "mistral:latest")
    endpoint = os.getenv("ECHO_OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
    system = (
        "You are ECHO Guide, a warm, charismatic health-monitoring companion. "
        "Use clear English and a calm, welcoming presenter tone, but do not imitate or claim to be any real person. "
        "You are not a doctor and must not diagnose, prescribe, or give emergency certainty. "
        "Explain that ECHO detects change from a personal acoustic baseline, not disease. "
        "For severe breathing trouble, chest pain, blue lips, confusion, or an emergency, tell the user to contact local emergency services now. "
        "For home-care questions, offer only low-risk general steps such as rest, hydration, avoiding smoke or known triggers, and following an existing clinician plan; never prescribe medication or replace medical care. "
        "Keep answers under 90 words, ask one useful follow-up question when appropriate, and protect privacy."
    )
    prompt = f"Assessment context: {json.dumps(context)}\nUser: {message[:1000]}"
    body = json.dumps(
        {"model": model, "stream": False, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    ).encode()
    request = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode())
        return payload.get("message", {}).get("content", "").strip() or None
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


@app.get("/api/ollama/status")
def ollama_status():
    endpoint = os.getenv("ECHO_OLLAMA_URL", "http://127.0.0.1:11434/api/tags")
    try:
        with urllib.request.urlopen(endpoint, timeout=3) as response:
            payload = json.loads(response.read().decode())
        models = [item.get("name") for item in payload.get("models", [])]
        model = os.getenv("ECHO_OLLAMA_MODEL", "mistral:latest")
        return jsonify({"available": model in models, "model": model, "models": models})
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return jsonify({"available": False, "model": os.getenv("ECHO_OLLAMA_MODEL", "mistral:latest"), "models": []})
def load_audio(upload):
    try:
        audio, sample_rate = sf.read(io.BytesIO(upload.read()), dtype="float32")
    except Exception as error:
        raise ValueError("Upload a readable WAV or FLAC recording.") from error
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if len(audio) < sample_rate // 2:
        raise ValueError("Recording must be at least 0.5 seconds long.")
    return audio, sample_rate


@app.before_request
def require_api_token_when_configured():
    expected = os.getenv("ECHO_API_TOKEN")
    if expected and request.path.startswith("/api/") and not request.path.startswith("/api/auth/"):
        supplied = request.headers.get("Authorization", "")
        browser_session = bool(session.get("user_id"))
        if not browser_session and not secrets.compare_digest(supplied, f"Bearer {expected}"):
            return jsonify({"error": "Authentication required."}), 401


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.path.startswith("/static/") or request.path == "/":
        response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; "
        "img-src 'self' https://images.unsplash.com data:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com"
    )
    return response


@app.get("/api/auth/captcha")
def captcha():
    first = secrets.randbelow(8) + 2
    second = secrets.randbelow(8) + 2
    challenge_id = secrets.token_urlsafe(12)
    challenges = session.get("captcha_challenges", {})
    challenges[challenge_id] = {"answer": str(first + second), "expires": time.time() + 600}
    session["captcha_challenges"] = dict(list(challenges.items())[-5:])
    session.modified = True
    return jsonify({"question": f"What is {first} + {second}?", "challenge_id": challenge_id})


@app.post("/api/auth/request-otp")
def request_otp():
    payload = request.get_json(silent=True) or {}
    email = normalized_email(payload.get("email", ""))
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        return auth_error("Enter a valid email address.")
    challenge_id = payload.get("captcha_id", "")
    challenges = session.get("captcha_challenges", {})
    challenge = challenges.pop(challenge_id, None)
    session["captcha_challenges"] = challenges
    session.modified = True
    if not challenge or time.time() > challenge["expires"] or payload.get("captcha", "").strip() != challenge["answer"]:
        return auth_error("CAPTCHA answer is incorrect.")
    otp = f"{secrets.randbelow(1_000_000):06d}"
    with get_db() as database:
        database.execute(
            "INSERT OR REPLACE INTO auth_challenges (email, otp_hash, expires_at, attempts) VALUES (?, ?, ?, 0)",
            (email, generate_password_hash(otp), time.time() + 600),
        )
        database.commit()
    try:
        delivered = send_otp(email, otp)
    except smtplib.SMTPAuthenticationError:
        if app.config["ALLOW_DEV_OTP"] and request.host.split(":", 1)[0] in {"127.0.0.1", "localhost"}:
            return jsonify({"message": "Gmail is unavailable in local mode. Use the development code below.", "development_otp": otp, "mode": "development"})
        return auth_error("Gmail rejected the login. Use a Google App Password, not your normal Gmail password.", 503)
    except (OSError, KeyError, smtplib.SMTPException):
        if app.config["ALLOW_DEV_OTP"] and request.host.split(":", 1)[0] in {"127.0.0.1", "localhost"}:
            return jsonify({"message": "Email is unavailable in local mode. Use the development code below.", "development_otp": otp, "mode": "development"})
        return auth_error("Email delivery is not configured correctly.", 503)
    if not delivered and app.config["ALLOW_DEV_OTP"] and request.host.split(":", 1)[0] in {"127.0.0.1", "localhost"}:
        return jsonify({"message": "Gmail is not configured in local mode. Use the development code below.", "development_otp": otp, "mode": "development"})
    if not delivered:
        return auth_error("Gmail OTP is not configured. Add GMAIL_ADDRESS and GMAIL_APP_PASSWORD to .env.", 503)
    response = {"message": "Verification code sent to your email."}
    if not delivered and app.config["ALLOW_DEV_OTP"]:
        response["development_otp"] = otp
    return jsonify(response)


@app.post("/api/auth/verify-otp")
def verify_otp():
    payload = request.get_json(silent=True) or {}
    email = normalized_email(payload.get("email", ""))
    with get_db() as database:
        challenge = database.execute("SELECT * FROM auth_challenges WHERE email = ?", (email,)).fetchone()
    if not challenge or time.time() > challenge["expires_at"]:
        return auth_error("That code is missing or expired.")
    if challenge["attempts"] >= 5 or not check_password_hash(challenge["otp_hash"], payload.get("otp", "")):
        with get_db() as database:
            database.execute("UPDATE auth_challenges SET attempts = attempts + 1 WHERE email = ?", (email,))
            database.commit()
        return auth_error("Verification code is incorrect.")
    session["verified_email"] = email
    return jsonify({"message": "Email verified. Set your password."})


@app.post("/api/auth/set-password")
def set_password():
    email = session.pop("verified_email", None)
    payload = request.get_json(silent=True) or {}
    password = payload.get("password", "")
    display_name = str(payload.get("display_name", "")).strip()[:80]
    if not email:
        return auth_error("Verify your email before setting a password.", 401)
    if len(password) < 10:
        return auth_error("Password must be at least 10 characters.")
    user_id = uuid.uuid4().hex
    with get_db() as database:
        try:
            database.execute(
                "INSERT INTO users (id, email, display_name, password_hash, email_verified, created_at) VALUES (?, ?, ?, ?, 1, ?)",
                (user_id, email, display_name, generate_password_hash(password), datetime.now(timezone.utc).isoformat()),
            )
            database.commit()
        except sqlite3.IntegrityError:
            return auth_error("An account already exists for this email. Log in instead.", 409)
    session["user_id"] = user_id
    return jsonify({"message": "Account created.", "email": email, "display_name": display_name})


@app.post("/api/auth/login")
def login():
    payload = request.get_json(silent=True) or {}
    email = normalized_email(payload.get("email", ""))
    with get_db() as database:
        user = database.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not user or not user["password_hash"] or not check_password_hash(user["password_hash"], payload.get("password", "")):
        return auth_error("Email or password is incorrect.", 401)
    session["user_id"] = user["id"]
    return jsonify({"message": "Logged in.", "email": email, "display_name": user["display_name"]})


@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify({"message": "Logged out."})


@app.get("/api/auth/me")
def me():
    user = current_user()
    return jsonify({"authenticated": bool(user), "email": user["email"] if user else None, "display_name": user["display_name"] if user else None})


@app.get("/api/auth/google")
def google_login():
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    if not client_id or not os.getenv("GOOGLE_CLIENT_SECRET"):
        return auth_error("Google login is not configured.", 503)
    state = secrets.token_urlsafe(32)
    session["google_state"] = state
    redirect_uri = request.url_root.rstrip("/") + "/api/auth/google/callback"
    query = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    })
    return jsonify({"url": f"https://accounts.google.com/o/oauth2/v2/auth?{query}"})


@app.get("/api/auth/google/callback")
def google_callback():
    expected_state = session.pop("google_state", "")
    if not expected_state or not secrets.compare_digest(request.args.get("state", ""), expected_state):
        return auth_error("Google login state validation failed.", 400)
    code = request.args.get("code")
    if not code:
        return auth_error("Google login was cancelled.", 400)
    redirect_uri = request.url_root.rstrip("/") + "/api/auth/google/callback"
    token_body = urllib.parse.urlencode({
        "code": code,
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    token_request = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=token_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(token_request, timeout=15) as response:
            token_data = json.loads(response.read().decode())
        id_token = token_data["id_token"]
        token_info_url = "https://oauth2.googleapis.com/tokeninfo?id_token=" + urllib.parse.quote(id_token)
        with urllib.request.urlopen(token_info_url, timeout=15) as response:
            identity = json.loads(response.read().decode())
    except (urllib.error.URLError, KeyError, json.JSONDecodeError):
        return auth_error("Google login could not be completed.", 502)
    if identity.get("aud") != os.environ["GOOGLE_CLIENT_ID"] or identity.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        return auth_error("Google identity validation failed.", 401)
    email = normalized_email(identity.get("email", ""))
    if not email or identity.get("email_verified") != "true":
        return auth_error("Google did not provide a verified email.", 401)
    with get_db() as database:
        user = database.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        user_id = user["id"] if user else uuid.uuid4().hex
        if not user:
            database.execute(
                "INSERT INTO users (id, email, password_hash, email_verified, created_at) VALUES (?, ?, NULL, 1, ?)",
                (user_id, email, datetime.now(timezone.utc).isoformat()),
            )
        database.commit()
    session["user_id"] = user_id
    return render_template("google_complete.html")


@app.post("/api/chat")
def chat():
    if not current_user():
        return auth_error("Log in to use ECHO Guide.", 401)
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    if not message:
        return auth_error("Enter a message.")
    context = payload.get("context", {})
    reply = ask_ollama(message, context)
    if reply is None:
        reply = (
            "I’m ECHO Guide. Ollama is not available right now, so I can’t generate a live reply. "
            "I can still show your personal baseline result. For severe breathing trouble or chest pain, seek urgent medical care."
        )
    return jsonify({"reply": reply, "provider": "ollama" if reply and not reply.startswith("I’m ECHO Guide") else "fallback"})


@app.post("/api/report-summary")
def report_summary():
    if not current_user():
        return auth_error("Log in to summarize your report.", 401)
    payload = request.get_json(silent=True) or {}
    report = {
        "deviation_score": payload.get("score"),
        "assessment_status": payload.get("status"),
        "largest_feature_shifts": payload.get("contributions", [])[:5],
        "dataset_model_signal": payload.get("model_result"),
    }
    local_summary = (
        f"Your ECHO deviation score is {report['deviation_score']}. "
        f"This is a comparison with your personal acoustic baseline for this assessment, not a diagnosis. "
        "Review the highlighted feature shifts and contact a healthcare professional if you feel unwell."
    )
    if request.args.get("fast") == "1":
        return jsonify({"summary": local_summary, "provider": "local"})
    reply = ask_ollama(
        "Summarize this ECHO assessment for the user in 3 short sentences. "
        "Explain what changed from their personal baseline, what they should do next, "
        "and clearly say this is not a diagnosis. Do not invent symptoms or medical facts.",
        report,
    )
    return jsonify({"summary": reply or local_summary, "provider": "ollama" if reply else "local"})


@app.get("/")
def dashboard():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/overview")
def overview():
    user = current_user()
    if not user:
        return auth_error("Log in to view your assessments.", 401)
    user_id = user["id"]
    with get_db() as database:
        sessions = database.execute(
            "SELECT id, created_at, kind, score, status FROM sessions "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 24",
            (user_id,),
        ).fetchall()
    baseline = baseline_for(user_id, "normal_breathing")
    return jsonify(
        {
            "user_id": user_id,
            "baseline_sessions": baseline["count"] if baseline else 0,
            "sessions": [dict(session) for session in reversed(sessions)],
        }
    )


@app.post("/api/sessions")
def create_session():
    user = current_user()
    if not user:
        return auth_error("Log in before submitting an assessment.", 401)
    user_id = user["id"]
    kind = request.form.get("kind", "assessment")
    stage = request.form.get("stage", "normal_breathing")
    upload = request.files.get("audio")
    if kind not in {"baseline", "assessment"}:
        return jsonify({"error": "kind must be baseline or assessment"}), 400
    if stage not in {"normal_breathing", "deep_breathing", "phonation", "cough"}:
        return jsonify({"error": "Invalid assessment stage."}), 400
    if upload is None or not upload.filename:
        return jsonify({"error": "An audio file is required."}), 400

    try:
        audio, sample_rate = load_audio(upload)
        features, labels = extract_features(audio, sample_rate)
    except (ValueError, RuntimeError, OverflowError) as error:
        return jsonify({"error": str(error)}), 400

    baseline = baseline_for(user_id, stage)
    score = None if kind == "baseline" or baseline is None else score_against_baseline(features, baseline)
    if kind == "baseline":
        status = "baseline_saved"
    elif score is None:
        status = "baseline_required"
    else:
        status = "review" if score >= 35 else "within_baseline"
    session_id = uuid.uuid4().hex[:12]
    created_at = datetime.now(timezone.utc).isoformat()
    with get_db() as database:
        database.execute(
            "INSERT INTO sessions (id, user_id, created_at, kind, score, status, features, stage) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, user_id, created_at, kind, score, status, encrypt_features(features.tolist()), stage),
        )
        database.commit()

    contributions = []
    if baseline is not None and score is not None:
        deviations = np.abs((features - baseline["mean"]) / baseline["scale"])
        order = np.argsort(deviations)[::-1][:3]
        contributions = [
            {"feature": labels[index], "shift": round(float(deviations[index]), 2)}
            for index in order
        ]

    return jsonify(
        {
            "id": session_id,
            "created_at": created_at,
            "kind": kind,
            "status": status,
            "score": round(score, 1) if score is not None else None,
            "stage": stage,
            "simple_result": simple_result(status, score, stage),
            "model_result": optional_model_result(features),
            "features": dict(zip(labels, np.round(features, 4).tolist())),
            "contributions": contributions,
        }
    ), 201


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": "Audio file must be smaller than 10 MB."}), 413


@app.errorhandler(Exception)
def api_exception(error):
    if request.path.startswith("/api/"):
        app.logger.exception("API request failed: %s", error)
        return jsonify({"error": "The server could not analyze this request."}), 500
    raise error


init_db()

if __name__ == "__main__":
    cert_file = os.getenv("ECHO_TLS_CERT")
    key_file = os.getenv("ECHO_TLS_KEY")
    ssl_context = (cert_file, key_file) if cert_file and key_file else None
    app.run(
        host=os.getenv("ECHO_HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("ECHO_DEBUG", "0") == "1",
        ssl_context=ssl_context,
    )
