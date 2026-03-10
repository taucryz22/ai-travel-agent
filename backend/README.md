# Backend

FastAPI backend for AI-Travel Agent.

## Endpoints
- `GET /health`
- `POST /api/plan`

## Run
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

## Smoke test
```bash
python scripts/smoke_test.py
```
