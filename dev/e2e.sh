#!/usr/bin/env bash
# End-to-end check against the dev environment (run ./up.sh and fixtures/generate.sh first).
# Uploads the fixtures as alice, then checks indexing, search, permissions, sharing,
# re-indexing, deletion and OCR. Leaves the indexer running with OCR=0.
set -euo pipefail
cd "$(dirname "$0")"
set -a; . ./.env; set +a
NC="http://localhost:${NC_PORT:-8089}"
DIR=e2e
FAIL=0

sql() { docker compose exec -T db psql -U postgres -d nextcloud -Atc "$1"; }
dav() { curl -sf -u "alice:$ALICE_PASSWORD" "$@"; }
ok() { echo "  ok    $*"; }
fail() { echo "  FAIL  $*"; FAIL=1; }

# Rows of alice's test files: name|status|etag
rows() {
	sql "select f.name, c.status, c.etag from oc_contentsearch c join oc_filecache f using (fileid)
	     where f.path like 'files/$DIR/%' order by f.name"
}

wait_for() { # <description> <shell condition>
	local i
	for i in $(seq 1 60); do
		if eval "$2"; then return 0; fi
		sleep 2
	done
	fail "timeout waiting for $1"
	return 1
}

# Prints "name<TAB>subline" for each hit of <user> on <term>
search() {
	local pw_var
	pw_var="$(echo "$1" | tr a-z A-Z)_PASSWORD"
	curl -sf -u "$1:${!pw_var}" -H 'OCS-APIRequest: true' \
		"$NC/ocs/v2.php/search/providers/contentsearch/search?term=$2&format=json" |
		python3 -c 'import json, sys
d = json.load(sys.stdin)["ocs"]["data"]
assert d["name"] == "Inhoud", d["name"]
for e in d["entries"]:
    print(e["title"] + "\t" + e["subline"])'
}

indexer_up() { # <OCR value>
	OCR=$1 docker compose --profile indexer up -d indexer >/dev/null 2>&1
}

echo "== upload"
indexer_up 0
dav -X DELETE "$NC/remote.php/dav/files/alice/$DIR" >/dev/null 2>&1 || true
dav -X MKCOL "$NC/remote.php/dav/files/alice/$DIR" >/dev/null
for f in fixtures/out/*; do
	dav -T "$f" "$NC/remote.php/dav/files/alice/$DIR/$(basename "$f")" >/dev/null
done
n=$(ls fixtures/out | wc -l | tr -d ' ')
ok "$n files uploaded to alice/$DIR"

echo "== index (OCR=0)"
wait_for "all fixtures indexed" '[ "$(rows | wc -l | tr -d " ")" = "$n" ]' && rows | cut -d'|' -f1,2 | sed 's/^/        /'
while IFS='|' read -r name status _; do
	case "$name" in
	fixture-scan*) [ "$status" = needs_ocr ] && ok "$name needs_ocr" || fail "$name is $status" ;;
	*) [ "$status" = ok ] || fail "$name is $status" ;;
	esac
done < <(rows)

echo "== search as alice"
for pair in pdf:flamingo docx:kangoeroe doc:pelikaan pptx:giraffe xlsx:octopus odt:walrus \
	ods:pinguin odp:dromedaris md:kolibrie txt:narwal; do
	ext=${pair%%:*} word=${pair#*:}
	hits=$(search alice "$word")
	if [ "$(echo "$hits" | cut -f1)" = "fixture-$ext.$ext" ] && echo "$hits" | grep -q "«"; then
		ok "$word -> $hits"
	else
		fail "$word -> '$hits'"
	fi
done

echo "== permissions"
hits=$(search bob walrus)
[ -z "$hits" ] && ok "bob finds nothing for walrus" || fail "bob sees '$hits'"
curl -sf -u "alice:$ALICE_PASSWORD" -H 'OCS-APIRequest: true' -X POST \
	"$NC/ocs/v2.php/apps/files_sharing/api/v1/shares" \
	-d path="/$DIR/fixture-odt.odt" -d shareType=0 -d shareWith=bob >/dev/null
hits=$(search bob walrus)
[ "$(echo "$hits" | cut -f1)" = fixture-odt.odt ] && ok "after share bob finds: $hits" || fail "after share bob sees '$hits'"
hits=$(search bob flamingo)
[ -z "$hits" ] && ok "bob still finds nothing for flamingo" || fail "bob sees '$hits'"

echo "== change and delete"
old_etag=$(rows | grep '^fixture-md.md|' | cut -d'|' -f3)
printf '# Gewijzigd\n\nNu staat hier het woord toekan.\n' | dav -T - "$NC/remote.php/dav/files/alice/$DIR/fixture-md.md" >/dev/null
dav -X DELETE "$NC/remote.php/dav/files/alice/$DIR/fixture-txt.txt" >/dev/null
wait_for "re-index of fixture-md.md" '[ "$(rows | grep "^fixture-md.md|" | cut -d"|" -f3)" != "$old_etag" ]' &&
	ok "fixture-md.md re-indexed (etag $old_etag -> $(rows | grep '^fixture-md.md|' | cut -d'|' -f3))"
hits=$(search alice toekan)
[ "$(echo "$hits" | cut -f1)" = fixture-md.md ] && ok "toekan -> $hits" || fail "toekan -> '$hits'"
[ -z "$(search alice kolibrie)" ] && ok "old word kolibrie is gone" || fail "kolibrie still found"
wait_for "removal of fixture-txt.txt" '! rows | grep -q "^fixture-txt.txt|"' && ok "fixture-txt.txt row deleted (file is in the trash bin)"
[ -z "$(search alice narwal)" ] && ok "narwal no longer found" || fail "narwal still found"

echo "== OCR=1"
indexer_up 1
cid=$(docker compose ps -q indexer)
peak=0
for i in $(seq 1 120); do
	mem=$(docker stats --no-stream --format '{{.MemUsage}}' "$cid")
	echo "        docker stats: $mem"
	[ "$(rows | grep -c '|ocr|')" = 2 ] && break
	sleep 1
done
rows | grep '^fixture-scan' | cut -d'|' -f1,2 | sed 's/^/        /'
[ "$(rows | grep -c '|ocr|')" = 2 ] && ok "both scans have status ocr" || fail "scans not OCR'd"
peak=$(docker exec "$cid" cat /sys/fs/cgroup/memory.peak)
limit=$(docker exec "$cid" cat /sys/fs/cgroup/memory.max)
echo "        cgroup memory.peak: $((peak / 1048576)) MiB of $((limit / 1048576)) MiB"
[ "$peak" -lt "$limit" ] && ok "peak memory below limit" || fail "peak memory at limit"
for word in zeppelin albatros; do
	hits=$(search alice "$word")
	echo "$hits" | grep -q '^fixture-scan' && ok "$word -> $hits" || fail "$word -> '$hits'"
done
indexer_up 0

echo
[ "$FAIL" = 0 ] && echo "ALL CHECKS PASSED" || { echo "SOME CHECKS FAILED"; exit 1; }
