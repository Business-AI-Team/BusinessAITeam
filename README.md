# LoanWise

**Commercial name:** Smart Loan Eligibility Checker  
**Team:** Business AI Team — Tantely, Hasina, Hardi, Frederic

LoanWise is a **Django + DRF** fintech demo: an **API-first** REST backend with a **modern web UI** (Tailwind CSS + Alpine.js), **FR/EN** interface, **AI-assisted document analysis**, **deterministic eligibility scoring**, **RAG-powered policy guidance**, and **PDF reports**.

---

## Features

| Area | Description |
|------|-------------|
| **Loan applications** | Customers create dossiers, fill in loan details (type, amount, currency, term, income), and track progress. |
| **Document management** | Upload required documents per loan type (payslips, CIN, proof of address); **SHA-256** fingerprinting; configurable delete-after-analysis. |
| **AI pipeline** | One-click orchestration: document extraction → consistency checks → deterministic eligibility score → RAG policy alignment → final decision. |
| **Eligibility scoring** | Deterministic **debt-to-income** formula (income, amount, term, annual rate); cross-checked against uploaded document data; penalty for inconsistencies (e.g. address mismatch). |
| **RAG guidance** | Policy PDFs uploaded in the admin are indexed in **Chroma** (OpenAI embeddings) and surfaced as loan-type-specific advice in the UI and reports. |
| **AI assistant** | Contextual chatbot (OpenAI); different tone and detail level for **customers** vs **backoffice** agents. |
| **PDF report** | Clean, branded **ReportLab** A4 report: verdict, loan details, repayment simulation, financial analysis bullets, policy notes, recommendations — in the user's language. |
| **Backoffice dashboard** | Backoffice staff see all applications in a filterable table (eligibility, loan type, date range). |
| **Multi-currency** | MGA, EUR, MUR — with configurable FX rates for DTI calculations. All monetary values in the PDF follow the loan's `amount_currency`. |
| **FR / EN** | Full interface and AI flow localisation; language switcher in the header; PDF language follows the UI selection. |
| **Dark / light mode** | User-level theme preference, persisted. |
| **REST API** | Token or session auth; endpoints for applications, documents, orchestration, assistant, PDF export. |

---

## User roles

| Role | Who | Access |
|------|-----|--------|
| **Customer** | Default registered users | Own loan applications only; customer dashboard; create/edit until the pipeline runs; upload documents; trigger orchestration; download PDF; chat as customer. |
| **Backoffice** | Promoted via **Setup → BackOffice** | All applications; document download for any dossier; backoffice dashboard with filters; chat with richer technical context. |
| **Superuser / Staff** | Django admin users | Full **Setup** panel (`/admin/`); treated as backoffice for application access. |

Navigation adapts automatically: backoffice and staff users see the **Backoffice** link; customers see **My applications** — never both.

---

## Quick start

### 1. Python environment

```bash
cd LoanWise
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and adjust the variables (see below).

### 2. Database

```bash
python manage.py migrate
python manage.py createsuperuser   # admin at /admin/ ("Setup" in the UI)
```

### 3. Run

```bash
python manage.py runserver
```

- **Web app:** [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Admin (Setup):** [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/)

### 4. Optional AI extras

LangGraph, PyTorch, and OCR dependencies are in a separate file to avoid bloating the base install:

```bash
pip install -r requirements-ai.txt
```

### 5. OpenAI key

Set `OPENAI_API_KEY` in `.env` **or** add it via **Setup → Integration settings** in the admin. Without a key the app runs in **demo / fallback mode** (template replies, no vector search).

---

## Environment variables

**Django core**

| Variable | Default | Description |
|----------|---------|-------------|
| `DJANGO_SECRET_KEY` | insecure dev key | Change in production. |
| `DJANGO_DEBUG` | `true` | Set `false` in production. |
| `DJANGO_ALLOWED_HOSTS` | `*` | Comma-separated hosts. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | — | Required in production for HTTPS. |
| `DJANGO_DB_ENGINE` | `django.db.backends.sqlite3` | Any Django DB backend. |
| `DJANGO_DB_NAME` | `db.sqlite3` | Database name / path. |

**Upload limits**

| Variable | Default |
|----------|---------|
| `DATA_UPLOAD_MAX_MEMORY_SIZE` | 15 MB |
| `FILE_UPLOAD_MAX_MEMORY_SIZE` | 15 MB |

**Business logic**

| Variable | Default | Description |
|----------|---------|-------------|
| `LOANWISE_CURRENCY` | `EUR` | Default currency for the platform. |
| `LOANWISE_FX_MGA_PER_EUR` | `4700` | Ariary per euro (indicative). |
| `LOANWISE_FX_MUR_PER_EUR` | `49` | Mauritian rupee per euro (indicative). |
| `LOANWISE_INTEREST_RATE_ANNUAL` | `0.05` | Annual rate used in repayment simulation. |
| `LOANWISE_APPROVAL_THRESHOLD` | `55` | Score (0–100) above which a dossier is automatically validated. |
| `LOANWISE_DELETE_FILES_AFTER_ANALYSIS` | `true` | Delete uploaded files once the AI has processed them. |

**AI / OpenAI / RAG**

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | Also configurable via Admin. Env value takes precedence. |
| `LOANWISE_LLM_PROVIDER` | `none` | `openai` to enable AI features. |
| `LOANWISE_OPENAI_MODEL` | `gpt-4o-mini` | Chat / analysis model. |
| `LOANWISE_OPENAI_VISION_MODEL` | same | Vision model for scanned documents. |
| `LOANWISE_RAG_OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model for Chroma. |
| `LOANWISE_RAG_LLM_MAX_CHARS` | `120000` | Max chars of policy text sent to the LLM. |
| `LOANWISE_DOCUMENT_ANALYSIS_BACKEND` | `auto` | `openai`, `fallback`, or `auto`. |
| `LOANWISE_USE_LANGGRAPH` | `false` | Enable LangGraph orchestration agent. |

**Consistency checks**

| Variable | Default |
|----------|---------|
| `LOANWISE_DOC_INCOME_TOLERANCE_PERCENT` | `20` |
| `LOANWISE_ADDRESS_OVERLAP_MIN` | `0.55` |
| `LOANWISE_CONSISTENCY_MAX_SCORE_PENALTY` | `30` |

---

## API overview

Authentication: `Authorization: Token <key>` or a browser session (CSRF required for session POSTs).

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/auth/register/` | Create account. |
| `POST` | `/api/auth/login/` | Returns `{ "token": "...", "user": {...} }`. |
| `GET / PATCH` | `/api/auth/me/` | Profile (`preferred_language`, `theme_preference`, …). |
| `GET / POST` | `/api/applications/` | List or create loan applications. |
| `PATCH` | `/api/applications/{id}/` | Update loan details. |
| `POST` | `/api/applications/{id}/orchestrate/` | Run full AI + scoring pipeline. |
| `POST` | `/api/applications/{id}/force_validate/` | Backoffice — manually validate a dossier. |
| `GET` | `/api/applications/{id}/export_pdf/` | Download PDF report (language follows the current UI language). |
| `POST` | `/api/documents/upload/{application_id}/` | Upload a document (multipart). |
| `GET` | `/api/documents/{document_id}/download/` | Download a document (owner or backoffice). |
| `GET` | `/api/requirements/` | Document requirements per loan type. |
| `POST` | `/api/seed/` | Seed default document requirements. |
| `POST` | `/api/assistant/chat/` | Send a message to the AI assistant. |
| `GET` | `/api/health/` | Health check. |

---

## Internationalization

- Languages: **Français** (default, no URL prefix) and **English** (`/en/…`).
- Language switcher in the header updates both the UI and the URL prefix.
- PDF language follows the UI language at the time of download.
- To update translations after changing strings:

```bash
pip install polib
python scripts/build_i18n.py
```

---

## Project layout

```
LoanWise/
├── core/
│   ├── models.py                  # User, Customer, BackOffice, LoanApplication, …
│   ├── api_views.py               # DRF viewsets and actions
│   ├── web_views.py               # Server-rendered pages
│   ├── loan_engine.py             # Deterministic eligibility scoring + ROI
│   ├── loan_orchestrator_agent.py # Pipeline: doc analysis → scoring → decision
│   ├── document_processor.py      # AI document extraction (OpenAI vision / fallback)
│   ├── document_consistency.py    # Cross-check declared vs extracted data
│   ├── eligibility_explanation.py # Structured FR/EN explanation builder
│   ├── rag_eligibility.py         # LangChain + Chroma RAG on policy PDFs
│   ├── assistant_chat.py          # AI assistant with role-aware context
│   ├── pdf_report.py              # ReportLab PDF generation
│   ├── currency_fx.py             # MGA / EUR / MUR conversions
│   ├── admin.py                   # Custom Django admin (Setup panel)
│   ├── serializers.py
│   ├── urls.py                    # API routes
│   └── web_urls.py                # Web routes
├── loanwise/
│   ├── settings.py
│   ├── urls.py
│   └── context_processors.py
├── templates/loanwise/
│   ├── base.html
│   ├── dashboard.html             # Customer: own applications
│   ├── application_detail.html    # Per-dossier workspace
│   ├── backoffice/dashboard.html  # Staff: all applications, filterable
│   └── components/                # Reusable UI fragments (chat, uploads)
├── templates/admin/               # Custom Setup panel skin
├── static/                        # JS, logos
├── locale/                        # Compiled gettext catalogs (FR / EN)
├── data/chroma_langchain/         # Chroma vector store (auto-created)
├── media/                         # User uploads (runtime)
├── scripts/build_i18n.py
├── manage.py
├── requirements.txt
├── requirements-ai.txt
└── .env.example
```

---

## Security notes

- Change `DJANGO_SECRET_KEY`, set `DEBUG=false`, configure `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`, and use HTTPS in any non-development environment.
- Document files are deleted after AI analysis by default (`LOANWISE_DELETE_FILES_AFTER_ANALYSIS=true`).
- The OpenAI key stored in the database is **not** exposed through the API.

---

## License

Hackathon / demo use — adapt licensing to your organisation as needed.
