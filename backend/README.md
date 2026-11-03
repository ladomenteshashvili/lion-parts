# Lion Parts backend

Django + DRF API. Contract and rules: `AI_IMPLEMENTATION_GUIDE.md`.

## Setup

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in real values privately; never commit .env
python manage.py migrate
python manage.py createcachetable
python manage.py runserver 127.0.0.1:8000
```

## Checks

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test                                   # PostgreSQL (DB_* from .env)
python manage.py test --settings=config.e2e_settings    # SQLite, no .env needed
```

Every environment variable is listed in `.env.example` (G§12).
