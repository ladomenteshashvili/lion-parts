#!/usr/bin/env bash
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

cat > .env.production <<'ENVEOF'
VITE_API_BASE_URL=https://api-test.lionparts.ge
ENVEOF

npm install
npm run build

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
