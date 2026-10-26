#!/usr/bin/env bash
# Generate one small test file per format into dev/fixtures/out/.
# Every file contains its own unique word (see WORDS below).
set -euo pipefail
cd "$(dirname "$0")"

if [ "${1:-}" != --inside ]; then
	docker build -q -t contentsearch-fixtures:dev . >/dev/null
	mkdir -p out
	exec docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp \
		-v "$PWD:/fixtures" -w /fixtures contentsearch-fixtures:dev ./generate.sh --inside
fi

# format -> unique word
declare -A WORDS=(
	[pdf]=flamingo [scan]=zeppelin [docx]=kangoeroe [doc]=pelikaan
	[pptx]=giraffe [xlsx]=octopus [odt]=walrus [ods]=pinguin
	[odp]=dromedaris [md]=kolibrie [txt]=narwal [scanmulti]=albatros
)

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

body() {
	cat <<EOF
Testbestand voor het formaat $1.
Het unieke woord in dit bestand is $2.
Deze zin staat er om het document lang genoeg te maken, zodat pdftotext
meer dan honderd tekens vindt en het bestand niet als scan wordt gezien.
EOF
}

soffice_to() { # <target-format> <source> [infilter]
	soffice -env:UserInstallation="file://$work/profile" --headless ${3:+--infilter="$3"} \
		--convert-to "$1" --outdir "$work" "$2" >/dev/null 2>&1
}

for f in pdf docx doc odt; do
	body "$f" "${WORDS[$f]}" >"$work/$f.txt"
done
soffice_to pdf "$work/pdf.txt"
soffice_to docx "$work/docx.txt"
soffice_to 'doc:MS Word 97' "$work/doc.txt"
soffice_to odt "$work/odt.txt"

for f in xlsx ods; do
	printf 'formaat,woord\n%s,%s\n' "$f" "${WORDS[$f]}" >"$work/$f.csv"
done
soffice_to xlsx "$work/xlsx.csv"
soffice_to ods "$work/ods.csv"

# Presentations: import a text PDF into Impress
for f in pptx odp; do
	body "$f" "${WORDS[$f]}" >"$work/$f-src.txt"
	soffice_to pdf "$work/$f-src.txt"
	soffice_to "$f" "$work/$f-src.pdf" impress_pdf_import
	mv "$work/$f-src.$f" "$work/$f.$f"
done

# Scan: render a text PDF to an image and wrap that in a PDF without a text layer
printf 'Gescande brief\n\nHet unieke woord is %s.\n' "${WORDS[scan]}" >"$work/scan-src.txt"
soffice_to pdf "$work/scan-src.txt"
pdftoppm -r 200 -gray -png -singlefile "$work/scan-src.pdf" "$work/scan"
img2pdf -o "$work/scan.pdf" "$work/scan.png"

# Multi-page scan at 300 dpi to check OCR memory use; the word is on the last page
for i in $(seq 1 8); do
	printf 'Pagina %s van een lange scan.\n' "$i"
	[ "$i" = 8 ] && printf 'Het unieke woord is %s.\n' "${WORDS[scanmulti]}"
	printf '\f'
done >"$work/scanmulti-src.txt"
soffice_to pdf "$work/scanmulti-src.txt"
pdftoppm -r 300 -gray -png "$work/scanmulti-src.pdf" "$work/scanmulti"
img2pdf -o "$work/scanmulti.pdf" "$work"/scanmulti-*.png

body md "${WORDS[md]}" | sed '1s/^/# /' >"$work/md.md"
body txt "${WORDS[txt]}" >"$work/txt.txt"

rm -f out/*
for f in pdf docx doc odt xlsx ods pptx odp md txt; do
	cp "$work/$f.$f" "out/fixture-$f.$f"
done
cp "$work/scan.pdf" out/fixture-scan.pdf
cp "$work/scanmulti.pdf" out/fixture-scan-multi.pdf
ls -l out
