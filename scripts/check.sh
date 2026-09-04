#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo ""
echo "== Backend tests =="
cd backend
source venv/bin/activate
python manage.py test

echo ""
echo "== Frontend mock E2E tests =="
cd ../frontend
npm run test:e2e:mock

echo ""
echo "== Frontend + real backend E2E tests =="
cd ..
./scripts/check-real-e2e.sh

echo ""
echo "✅ All checks passed"
