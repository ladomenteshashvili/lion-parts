#!/usr/bin/env bash
# One-time host setup for the isolated staging: docs/staging-runbook.md section 2.
# Prints the plan by default and changes nothing. Only `provision.sh --apply`, run by the owner as root, carries it out.
# It creates only new resources and stops if any already exists. It does not touch other users, databases, sites or services,
# does not enable nginx, and starts nothing.
set -euo pipefail

USER_NAME=lionparts-stage
HOME_DIR=/home/$USER_NAME
ROLE=lp_stage
DB=lp_stage_v1
RESTORE_DB=lp_stage_restore_check
REPO=https://github.com/ladomenteshashvili/lion-parts.git
UNITS=(lionparts-stage.service lionparts-stage-mailpit.service)
HERE="$(cd "$(dirname "$0")" && pwd)"

psql_q() { sudo -u postgres psql -v ON_ERROR_STOP=1 -Atc "$1"; }

conflicts() {
    local found=0
    if id "$USER_NAME" > /dev/null 2>&1; then echo "exists: Linux user $USER_NAME"; found=1; fi
    if [[ -e "$HOME_DIR" ]]; then echo "exists: $HOME_DIR"; found=1; fi
    for unit in "${UNITS[@]}"; do
        if [[ -e "/etc/systemd/system/$unit" ]]; then echo "exists: $unit"; found=1; fi
    done
    if ss -ltn | grep -qE ':(8011|1025|8025)\b'; then echo "in use: port 8011, 1025 or 8025"; found=1; fi
    if [[ "$(id -u)" == 0 ]]; then
        [[ -z "$(psql_q "SELECT 1 FROM pg_roles WHERE rolname = '$ROLE'")" ]] || { echo "exists: role $ROLE"; found=1; }
        [[ -z "$(psql_q "SELECT 1 FROM pg_database WHERE datname IN ('$DB', '$RESTORE_DB')")" ]] || { echo "exists: database $DB or $RESTORE_DB"; found=1; }
    else
        echo "not root: PostgreSQL role and database not checked"
    fi
    return "$found"
}

plan() {
    cat <<PLAN
Would create, and nothing else:
  Linux user   $USER_NAME, home $HOME_DIR (no sudo, no login password)
  Directories  $HOME_DIR/media mode 755 (nginx reads it), $HOME_DIR/backups mode 700
               $HOME_DIR, lion-parts and backend mode 711: nginx can pass through but not list them
  Checkout     $HOME_DIR/lion-parts from $REPO, with its own virtual environment
  PostgreSQL   role $ROLE (generated password, no superuser, no createdb)
               empty databases $DB and $RESTORE_DB, owned by $ROLE, connection limit 30
  Config       $HOME_DIR/lion-parts/backend/.env, mode 600, from env.staging.example, with generated secrets
  systemd      ${UNITS[*]} copied, not enabled and not started
Left to the owner: Mailpit binary, DNS, TLS, nginx site, starting services, crontab, the two approved staging superusers.
PLAN
}

apply() {
    [[ "$(id -u)" == 0 ]] || { echo "REFUSED: --apply must run as root" >&2; exit 2; }
    set -E
    trap 'echo "FAILED part-way. Nothing was removed. Do not run --apply again: docs/staging-runbook.md, If --apply fails part-way." >&2' ERR
    local db_password secret
    db_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
    secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')"

    echo "creating: Linux user $USER_NAME and $HOME_DIR"
    useradd --create-home --home-dir "$HOME_DIR" --shell /bin/bash "$USER_NAME"
    passwd --lock "$USER_NAME" > /dev/null
    chmod 711 "$HOME_DIR"
    install -d -m 755 -o "$USER_NAME" -g "$USER_NAME" "$HOME_DIR/media"
    install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "$HOME_DIR/backups"
    sudo -u "$USER_NAME" git clone --quiet "$REPO" "$HOME_DIR/lion-parts"
    chmod 711 "$HOME_DIR/lion-parts" "$HOME_DIR/lion-parts/backend"
    sudo -u "$USER_NAME" python3 -m venv "$HOME_DIR/lion-parts/backend/venv"

    echo "creating: PostgreSQL role $ROLE, databases $DB and $RESTORE_DB"
    # The password travels on psql's stdin, so it is not in the process list or in any file except .env.
    sudo -u postgres psql -v ON_ERROR_STOP=1 -q <<SQL
CREATE ROLE $ROLE LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 30 PASSWORD '$db_password';
CREATE DATABASE $DB OWNER $ROLE ENCODING 'UTF8' TEMPLATE template0;
CREATE DATABASE $RESTORE_DB OWNER $ROLE ENCODING 'UTF8' TEMPLATE template0;
REVOKE ALL ON DATABASE $DB FROM PUBLIC;
REVOKE ALL ON DATABASE $RESTORE_DB FROM PUBLIC;
SQL

    echo "creating: $HOME_DIR/lion-parts/backend/.env"
    local env_file="$HOME_DIR/lion-parts/backend/.env"
    install -m 600 -o "$USER_NAME" -g "$USER_NAME" "$HERE/env.staging.example" "$env_file"
    DB_PASSWORD_VALUE="$db_password" SECRET_VALUE="$secret" python3 - "$env_file" <<'PY'
import os, sys
path = sys.argv[1]
lines = open(path, encoding="utf8").read().splitlines()
values = {"DB_PASSWORD=": os.environ["DB_PASSWORD_VALUE"], "SECRET_KEY=": os.environ["SECRET_VALUE"]}
with open(path, "w", encoding="utf8") as out:
    for line in lines:
        out.write(line + values[line] + "\n" if line in values else line + "\n")
PY
    echo "creating: ${UNITS[*]}"
    for unit in "${UNITS[@]}"; do install -m 644 "$HERE/$unit" /etc/systemd/system/; done
    systemctl daemon-reload
    echo "Created. Next: docs/staging-runbook.md section 3. Nothing was started."
}

case "${1:-}" in
    --apply)
        conflicts || { echo "REFUSED: remove the conflict or stop here; nothing was changed." >&2; exit 2; }
        apply ;;
    "")
        plan; echo; echo "Conflict check:"; conflicts && echo "  none found" || echo "  resolve these before --apply" ;;
    *) echo "usage: provision.sh [--apply]" >&2; exit 2 ;;
esac
