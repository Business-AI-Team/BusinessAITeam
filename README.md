# LoanWise

**Commercial name:** Smart Loan Eligibility Checker  

**Team:** Business AI Team — Tantely, Hasina, Hardi, Frederic  

LoanWise is a **Django + DRF** hackathon project: **API-first** REST backend, **Swagger / Redoc** documentation, a **modern fintech-style** web UI (**Tailwind CSS** + **Alpine.js**), **full French/English** support for the interface and AI flows, and **dark mode** by default with a light/dark toggle.

---

## Features

| Area | Description |
|------|-------------|
| **REST API** | Applications, chat, document upload, orchestration, PDF export — documented via OpenAPI. |
| **Auth** | Registration, **email verification** (token link), **hashed passwords** (Django default). |
| **Documents** | **SHA-256** fingerprinting; optional **delete after analysis** (`LOANWISE_DELETE_FILES_AFTER_ANALYSIS`). |
| **Requirements** | Dynamic, DB-driven **document requirements** per loan type (extensible). |
| **AI** | Configurable **LangGraph** bridge + step machine; **DeepFace** (optional); **Florence-2** (optional); **LLM** via OpenAI / Ollama / offline templates. |
| **Business** | **ROI / impact** JSON on each application; **PDF** report (ReportLab). |
| **UX** | Dashboard, per-application workspace, **progress** indicators, structured API errors. |

---

## Quick start

### 1. Python environment

```bash
cd LoanWise
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate       # Linux / macOS
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and adjust variables (see below).

### 2. Database

```bash
python manage.py migrate
python manage.py createsuperuser   # optional — admin at /admin/
```

### 3. Run

```bash
python manage.py runserver
```

- **Web app:** [http://127.0.0.1:8000/](http://127.0.0.1:8000/)  
- **Swagger:** [http://127.0.0.1:8000/api/docs/swagger/](http://127.0.0.1:8000/api/docs/swagger/)  
- **ReDoc:** [http://127.0.0.1:8000/api/docs/redoc/](http://127.0.0.1:8000/api/docs/redoc/)  
- **OpenAPI schema:** [http://127.0.0.1:8000/api/schema/](http://127.0.0.1:8000/api/schema/)  

Email verification links in development use the **console email backend** (link printed in the terminal).

### 4. Optional AI dependencies

Heavy packages (LangChain, PyTorch, Transformers, DeepFace) are listed in `requirements-ai.txt`:

```bash
pip install -r requirements-ai.txt
```

Then configure `.env` (`LOANWISE_LLM_PROVIDER`, `OPENAI_API_KEY`, or Ollama URL). Without them, the app runs in **demo mode** (template replies and Florence/DeepFace fallbacks).

---

## Internationalization (FR / EN)

- UI languages: **Français** (default) and **English** — switcher in the header (`set_language` + `LocaleMiddleware`).
- Each **loan application** stores a `language` field so the **chatbot** and **document analysis fallback text** follow the user’s choice.
- To refresh translation catalogs after changing strings, update the `FR` dictionary in `scripts/build_i18n.py`, then run:

```bash
pip install polib
python scripts/build_i18n.py
```

(On systems with GNU gettext you can alternatively use `makemessages` / `compilemessages`.)

---

## API overview (session or token)

- `POST /api/auth/register/` — create account (verification email).  
- `GET /api/auth/verify/?token=...` — verify email.  
- `POST /api/auth/login/` — returns `{ "token": "...", "user": {...} }`.  
- `GET/PATCH /api/auth/me/` — profile (e.g. `preferred_language`, `theme_preference`).  
- `GET/POST /api/applications/` — list/create loan applications.  
- `POST /api/applications/{id}/chat/` — `{ "message": "..." }`.  
- `GET /api/applications/{id}/messages/` — chat history.  
- `POST /api/documents/upload/{application_id}/` — multipart `file`, optional `requirement_id`, `kind`.  
- `POST /api/applications/{id}/orchestrate/` — full pipeline (requires **verified email**).  
- `GET /api/applications/{id}/export_pdf/` — PDF download.  
- `GET /api/requirements/?lang=fr` — document requirements (labels by language).  
- `POST /api/seed/` — seed default document requirements (authenticated).  

Use `Authorization: Token <key>` or a logged-in **browser session** (CSRF required for session POSTs).

---

## Project layout

```
loanwise/
├── core/                 # Models, services, agents, API, admin
├── templates/loanwise/   # HTML (Tailwind + Alpine)
├── static/
├── locale/               # optional: compiled gettext catalogs
├── manage.py
├── requirements.txt
├── requirements-ai.txt
├── .env.example
└── README.md
```

---

## Security notes (hackathon vs production)

- Change `DJANGO_SECRET_KEY`, disable `DEBUG`, use a real **SMTP** backend, HTTPS, and strict `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` in production.
- Face and vision models may pull large weights; run them only on trusted infrastructure.

---

## License

Hackathon / demo use — adapt licensing to your organization as needed.
