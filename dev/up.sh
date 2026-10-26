#!/usr/bin/env bash
# Start the dev environment in the order the install runbook uses:
# Nextcloud first, then sql/setup.sql, then the app and the indexer.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
	{
		for v in POSTGRES_PASSWORD NC_DB_PASSWORD INDEXER_DB_PASSWORD NC_ADMIN_PASSWORD ALICE_PASSWORD BOB_PASSWORD; do
			printf '%s=%s\n' "$v" "$(openssl rand -hex 16)"
		done
		printf 'NC_PORT=8089\nINTERVAL=15\nOCR=0\n'
	} >.env
	chmod 600 .env
	echo "created dev/.env"
fi
set -a; . ./.env; set +a

occ() { docker compose exec -T -u www-data app php occ "$@"; }

docker compose up -d db app

printf 'waiting for Nextcloud to install'
until occ status --output=json 2>/dev/null | grep -q '"installed":true'; do
	printf '.'; sleep 3
done
echo

if [ "$(docker compose exec -T db psql -U postgres -d nextcloud -Atc "select to_regclass('oc_contentsearch') is not null")" != t ]; then
	docker compose exec -T db psql -U postgres -d nextcloud -v ON_ERROR_STOP=1 -v pw="$INDEXER_DB_PASSWORD" <../sql/setup.sql
fi

occ app:enable contentsearch

for u in alice bob; do
	pw_var="$(echo "$u" | tr a-z A-Z)_PASSWORD"
	if ! occ user:info "$u" >/dev/null 2>&1; then
		docker compose exec -T -u www-data -e OC_PASS="${!pw_var}" app php occ user:add --password-from-env "$u"
	fi
done

docker compose --profile indexer up -d --build indexer
echo "Nextcloud: http://localhost:${NC_PORT:-8089} (admin, alice, bob; passwords in dev/.env)"
