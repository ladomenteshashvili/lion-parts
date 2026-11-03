#!/usr/bin/env bash
# DISABLED (M6, LP-BE-1204 review). This script belongs to the LEGACY installation (/home/lado/lion-parts,
# api-test.lionparts.ge). It checks out `main` (now the v1 backend) and runs `migrate` against the legacy
# database, which v1 cannot migrate in place (release blocker B2). It stops here, before any command.
# A v1 staging is built fresh and isolated: docs/operations.md section 2.
# Any OLD copy of this script already on a server is still dangerous until an authorized operator disables it.
echo "ERROR: scripts/deploy-staging.sh is disabled." >&2
echo "It belongs to the legacy Lion Parts environment and must NOT be used with the v1 backend:" >&2
echo "it would apply the v1 migrations to the legacy database (release blocker B2)." >&2
echo "Build an isolated fresh staging instead: see docs/operations.md, section 2." >&2
exit 1

# ---- legacy script below: unreachable, kept for reference only ----
set -e

echo "Deploying Lion Parts staging..."

cd /home/lado/lion-parts

echo "Pull latest main..."
git checkout main
git pull --ff-only origin main

echo "Install backend requirements..."
cd backend
source venv/bin/activate
pip install -r requirements.txt

echo "Run migrations..."
python manage.py migrate

echo "Collect Django static files..."
python manage.py collectstatic --noinput

echo "Publish Django static files..."
sudo mkdir -p /var/www/lionparts-backend-static
sudo rm -rf /var/www/lionparts-backend-static/*
sudo cp -r staticfiles/* /var/www/lionparts-backend-static/
sudo chown -R www-data:www-data /var/www/lionparts-backend-static

echo "Build frontend..."
cd ../frontend

BUILD_COMMIT="$(git rev-parse --short HEAD)"
BUILD_TIME="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
BUILD_ID="${BUILD_COMMIT}-${BUILD_TIME}"

cat > .env.production <<ENVEOF
VITE_API_BASE_URL=https://api-test.lionparts.ge
VITE_APP_BUILD_ID=${BUILD_ID}
ENVEOF

npm install
npm run build

cat > dist/build-info.json <<ENVEOF
{
  "build_id": "${BUILD_ID}",
  "commit_sha": "${BUILD_COMMIT}",
  "built_at": "${BUILD_TIME}"
}
ENVEOF

echo "Frontend build id: ${BUILD_ID}"

echo "Publish frontend..."
sudo mkdir -p /var/www/lionparts-staging
sudo rm -rf /var/www/lionparts-staging/*
sudo cp -r dist/* /var/www/lionparts-staging/
sudo chown -R www-data:www-data /var/www/lionparts-staging

echo "Restart backend and reload nginx..."
sudo systemctl restart lionparts-backend
sudo nginx -t
sudo systemctl reload nginx

echo ""
echo "Staging deployed successfully:"
echo "Frontend: https://test.lionparts.ge"
echo "Admin:    https://api-test.lionparts.ge/admin/"
echo "Health:   https://api-test.lionparts.ge/api/health/"
