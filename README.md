# ECHO MVP

ECHO is a local-first prototype for personalized acoustic change monitoring. It is a research tool, not a diagnostic device.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000.

## MVP workflow

1. Upload a short WAV or FLAC recording as a **Baseline sample**. Use several recordings from the person's normal state for a more stable reference.
2. Upload a later recording as an **Assessment**.
3. The app extracts eight local acoustic features and calculates a 0-100 deviation score from the user's baseline.
4. The dashboard shows the recent trend and the three features with the largest standardized shifts.

The current baseline is a transparent z-score model stored in SQLite. Raw audio is never written to disk by the app. This is intentionally a foundation for later work such as controlled protocol capture, MFCC/delta features, One-Class SVM comparison, calibration, and clinical validation.

## Medical technologies used

- **Audio capture:** WAV/FLAC recordings are decoded locally with SoundFile. The guided stages cover normal breathing, deep breathing, sustained phonation, and cough.
- **Digital signal processing:** NumPy and SciPy calculate RMS energy, zero-crossing rate, spectral centroid, bandwidth, rolloff, dominant frequency, crest factor, and duration.
- **Personal baseline:** ECHO stores encrypted feature vectors and compares a new recording with the selected baseline using standardized feature deviation. The score is a monitoring signal, not a clinical measurement.
- **Explainability:** The dashboard lists the largest feature shifts so the score is not a black-box number.
- **Optional ML classifier:** A dataset-specific Random Forest can be trained from labeled folders. It must be evaluated with patient-level splits and is not a diagnosis engine.
- **AI report guide:** Ollama can summarize the score and feature shifts in plain English. The prompt forbids diagnosis and prescriptions and directs emergencies to local medical services.

The system does not measure oxygen saturation, airflow, spirometry, or lung function. It cannot diagnose, rule out, or treat a medical condition.

For a jury demonstration, use the synthetic files in [test audio/README.md](test%20audio/README.md). They demonstrate stage-aware feature deviation only; the asthma-like proxy is not clinical asthma audio and cannot support a disease claim.

## Public ML data

Dataset downloading and the source-specific ML layout are implemented in [download_datasets.py](download_datasets.py) and documented in [datasets/README.md](datasets/README.md). Coswara, COUGHVID, and ICBHI should be evaluated in separate experiments because their sound types, labels, and participant structures differ. ECHO's personal deviation score is not a disease classifier.

## API

- `GET /api/overview?user_id=demo-user`
- `POST /api/sessions` with multipart fields `audio` and `kind` (`baseline` or `assessment`)
- `POST /api/report-summary` for the logged-in user's AI report summary

## Scope and safety

The MVP does not diagnose, rule out, or treat any condition. Public datasets should be used for offline engineering validation only, with their licenses and metadata requirements respected. Do not upload identifiable clinical recordings to an unsecured development server.

## Security configuration

- Feature vectors are encrypted in SQLite with Fernet. The app creates `.echo.key` for local development; protect it like a password and back it up securely. For deployment, set `ECHO_DB_KEY` to a managed Fernet key instead.
- Login sessions are signed with `.echo-session.key` so server restarts do not unexpectedly log users out. Protect it like a password, or set `ECHO_SESSION_SECRET` in deployment.
- Set `ECHO_API_TOKEN` to require `Authorization: Bearer <token>` on API requests.
- Set `ECHO_TLS_CERT` and `ECHO_TLS_KEY` to serve Flask over HTTPS for a prototype network deployment. Use a proper reverse proxy and production WSGI server before exposing this outside a trusted network.
- Raw uploaded audio is processed in memory and is not written by the app.

## Accounts

New users use the dashboard flow: email + CAPTCHA, one-time email code, then a password. Returning users log in with email/password. Google login is available when `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are configured in `.env`.

### Gmail OTP setup

1. Enable 2-Step Verification on the Gmail account that will send ECHO emails.
2. Create a Google App Password for Mail. Do not use the normal Gmail password.
3. Copy `.env.example` to `.env` and set:

```text
GMAIL_ADDRESS=your Gmail address
GMAIL_APP_PASSWORD=your 16-character app password
ECHO_ALLOW_DEV_OTP=0
```

Restart ECHO. New verification codes will be sent to the user's Gmail address. Development OTP output is disabled by default; only set `ECHO_ALLOW_DEV_OTP=1` for local testing without Gmail.

## Local ECHO Guide

The floating ECHO Guide uses Ollama locally, so user messages do not need to leave the device:

```powershell
ollama serve
ollama pull mistral:latest
```

Then start ECHO. The default model is `mistral:latest`, matching the local setup. Change it with `ECHO_OLLAMA_MODEL` if you install another model. The default endpoint is `http://127.0.0.1:11434/api/chat`. If Ollama is offline or the configured model is missing, the dashboard shows a safe fallback instead of failing.

Check the local integration with `GET /api/ollama/status`. If it reports unavailable, install Ollama, keep `ollama serve` running, and pull the configured model. Ollama is local software; it is not automatically available merely because the Flask app is running. On hosted Render deployments, use a private reachable Ollama server and set `ECHO_OLLAMA_URL`, or keep the safe fallback.

### Ollama bind error

If `ollama serve` prints `listen tcp 127.0.0.1:11434: bind: Only one usage of each socket address`, Ollama is already running. Do not start it again. Verify it with:

```powershell
Invoke-WebRequest http://127.0.0.1:11434/api/tags
```

Then start only ECHO with `C:/Python314/python.exe app.py`.

## Jury presentation

Run `C:/Python314/python.exe make_presentation.py` to regenerate the visual deck at `C:\Users\Codemantra\OneDrive\ECHO_MedVision26 1.pptx`. The deck covers the product idea, architecture, stage workflow, scoring logic, encryption, authentication, Ollama, file map, hosting, Android path, and demo order.

## Host on Render

The repository includes [Dockerfile](Dockerfile) and [render.yaml](render.yaml). Render is the simplest deployment path for this MVP:

1. Push this folder to a private GitHub repository. Do not commit `.env`, `.echo.key`, `.echo-session.key`, or the Google client-secret JSON.
2. Create a Render Blueprint and select the repository. Render will use `render.yaml`, Docker, `/health`, and a persistent `/data` disk.
3. In the Render service environment, add `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ECHO_SMTP_HOST`, `ECHO_SMTP_PORT`, `ECHO_SMTP_USER`, `ECHO_SMTP_PASSWORD`, and `ECHO_SMTP_FROM`.
4. Set `ECHO_OLLAMA_URL` to a reachable Ollama server. `127.0.0.1` inside the hosted container is not your personal computer; leave the guide fallback enabled or use a private Ollama host.
5. After Render gives you a URL, add `https://your-render-domain.onrender.com/api/auth/google/callback` to Google Cloud's authorized redirect URIs.
6. Open the Render URL and test `/health`, registration, Google login, baseline creation, and assessment upload.

The Render `starter` plan is intentional because SQLite, encryption keys, and sessions need persistent disk storage. For multiple instances or larger usage, move users/sessions to managed PostgreSQL and object storage before scaling.

## Netlify and Android path

The current app is Flask-rendered, so Netlify cannot host this repository directly as the backend. Deploy the current full app to Render first. Netlify is appropriate after extracting the dashboard into a static React/Vite frontend:

1. Keep Flask, SQLite, authentication, audio processing, and Ollama integration on Render.
2. Create a separate `web/` React/Vite frontend and move the visual dashboard there.
3. Replace same-origin calls such as `/api/sessions` with `${VITE_API_ORIGIN}/api/sessions`.
4. Configure Flask CORS for the Netlify domain and allow credentials for the auth cookie, or move to token-based auth.
5. Deploy `web/` to Netlify with build command `npm run build` and publish directory `dist`.
6. Set `VITE_API_ORIGIN` to the Render backend URL in Netlify environment variables.
7. Add the Netlify and Render callback URLs to Google OAuth before testing login.

For Android Studio, reuse the Render API rather than embedding secrets in the APK. The Android app should implement login, upload WAV over HTTPS, show the score/report summary, and use the same authenticated API contract. A WebView wrapper is possible, but a native Kotlin client is the better next step for microphone permissions and offline capture.
