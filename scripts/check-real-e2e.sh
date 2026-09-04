#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

BACKEND_PORT="${E2E_BACKEND_PORT:-8010}"
BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
BACKEND_LOG="${TMPDIR:-/tmp}/lion-parts-real-e2e-backend.log"

cleanup() {
  if [[ -n "${BACKEND_PID:-}" ]]; then
    kill "${BACKEND_PID}" >/dev/null 2>&1 || true
  fi

  rm -f backend/e2e.sqlite3
}
trap cleanup EXIT

rm -f backend/e2e.sqlite3

export DJANGO_SETTINGS_MODULE=config.e2e_settings
export SECRET_KEY=e2e-secret-key
export DEBUG=True
export ALLOWED_HOSTS=localhost,127.0.0.1

echo ""
echo "== Real backend E2E: migrate sqlite test DB =="
cd backend
source venv/bin/activate
python manage.py migrate --noinput

echo ""
echo "== Real backend E2E: start Django on ${BACKEND_URL} =="
python manage.py runserver "127.0.0.1:${BACKEND_PORT}" > "${BACKEND_LOG}" 2>&1 &
BACKEND_PID=$!

for _ in {1..40}; do
  if curl -fsS "${BACKEND_URL}/api/health/" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -fsS "${BACKEND_URL}/api/health/" >/dev/null 2>&1; then
  echo "Backend did not start. Log:"
  cat "${BACKEND_LOG}"
  exit 1
fi

echo ""
echo "== Real backend E2E: Playwright =="
cd ../frontend

export VITE_API_BASE_URL="${BACKEND_URL}"
export PLAYWRIGHT_BASE_URL=http://127.0.0.1:4173

CI=1 npm run test:e2e:real

echo ""
echo "✅ Real backend E2E passed"
