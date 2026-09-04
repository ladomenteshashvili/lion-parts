#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo ""
echo "== Backend tests =="
cd backend
source venv/bin/activate
python manage.py test

echo ""
echo "== Frontend E2E tests =="
cd ../frontend
npm run test:e2e

echo ""
echo "✅ All checks passed"
