#!/usr/bin/env python3
"""Index the text of Nextcloud files into oc_contentsearch (PostgreSQL full-text search)."""
import html
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile

import psycopg

DATA = "/data"
BATCH = int(os.environ.get("BATCH", "200"))
INTERVAL = int(os.environ.get("INTERVAL", "300"))
OCR = os.environ.get("OCR", "0") == "1"
# Folders whose scans are never OCR'd, relative to each user's files, e.g. "Resources/Sheetmusic"
OCR_EXCLUDE = [p.strip().strip("/") for p in os.environ.get("OCR_EXCLUDE", "").split(",") if p.strip()]
MAX_CHARS = 500_000  # tsvector is capped at 1 MB
MAX_PX = 3500  # longest side of a page for OCR: A4 at 300 dpi, larger pages are scaled down

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Office formats are zip files; text lives in these XML parts
ZIP_PARTS = {
    DOCX: r"word/document\.xml",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": r"ppt/slides/slide\d+\.xml",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": r"xl/sharedStrings\.xml",
    "application/vnd.oasis.opendocument.text": r"content\.xml",
    "application/vnd.oasis.opendocument.spreadsheet": r"content\.xml",
    "application/vnd.oasis.opendocument.presentation": r"content\.xml",
}
# Password-protected Office files are OLE2 containers, not zip files
OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MIMES = ["application/pdf", "application/msword", "text/plain", "text/markdown", *ZIP_PARTS]

# Tags that end a block of text; all other tags are dropped without a separator,
# because Word and Calc split single words over several runs/spans.
BREAK_TAGS = re.compile(
    r"</(?:w:p|w:tc|a:p|a:tc|si|text:p|text:h|text:list-item|table:table-cell)>"
    r"|<(?:w:tab|w:br|w:cr|a:br|text:tab|text:line-break)\b[^>]*/>")
SPACE_TAGS = re.compile(r'<text:s(?:\s+text:c="(\d+)")?\s*/>')
ANY_TAG = re.compile(r"<[^>]+>")

PENDING = """
SELECT f.fileid, f.storage, s.id, f.path, coalesce(f.etag, ''), m.mimetype
FROM oc_filecache f
JOIN oc_storages s ON s.numeric_id = f.storage
JOIN oc_mimetypes m ON m.id = f.mimetype
LEFT JOIN oc_contentsearch c ON c.fileid = f.fileid
WHERE s.id LIKE 'home::%%' AND f.path LIKE 'files/%%'
  AND m.mimetype = ANY(%(mimes)s)
  AND (c.fileid IS NULL OR c.etag <> coalesce(f.etag, '') OR (%(ocr)s AND c.status = 'needs_ocr'))
ORDER BY f.mtime DESC
LIMIT %(batch)s
"""

# Gone from the filecache, or moved to the trash bin (path no longer under files/)
CLEANUP = """
DELETE FROM oc_contentsearch c
WHERE NOT EXISTS (SELECT 1 FROM oc_filecache f WHERE f.fileid = c.fileid AND f.path LIKE 'files/%')
"""

UPSERT = """
INSERT INTO oc_contentsearch (fileid, storage, etag, status, content)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (fileid) DO UPDATE
SET storage = EXCLUDED.storage, etag = EXCLUDED.etag, status = EXCLUDED.status,
    content = EXCLUDED.content, indexed_at = now()
"""


def run(cmd, timeout=600):
    p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if p.returncode:
        # The tool's own message says why, "exit status 1" does not
        err = p.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"{cmd[0]} exit status {p.returncode}: {err}")
    return p.stdout.decode("utf-8", "replace")


def xml_text(xml):
    xml = BREAK_TAGS.sub("\n", xml)
    xml = SPACE_TAGS.sub(lambda m: " " * int(m.group(1) or 1), xml)
    return html.unescape(ANY_TAG.sub("", xml))


def from_zip(path, pattern):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if re.fullmatch(pattern, n)]
        # slide10.xml sorts after slide9.xml
        names.sort(key=lambda n: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", n)])
        return "\n".join(xml_text(z.read(n).decode("utf-8", "replace")) for n in names)


def is_encrypted(path):
    # OLE2 stream names are UTF-16LE; an encrypted docx/xlsx/pptx has an EncryptedPackage stream
    with open(path, "rb") as f:
        if f.read(8) != OLE2:
            return False
        return "EncryptedPackage".encode("utf-16-le") in f.read()


def ocr_excluded(path):
    rel = path.removeprefix("files/")
    return any(rel.startswith(p + "/") for p in OCR_EXCLUDE)


def pages(path):
    # "?" instead of an exception, so the log line never turns a file into an error
    try:
        return re.search(r"^Pages:\s+(\d+)", run(["pdfinfo", path]), re.M).group(1)
    except Exception:
        return "?"


def ocr(path):
    # One page at a time with a size cap, so memory stays small whatever the scan resolution
    n = int(pages(path))  # "?" raises, so the file becomes an error
    text = []
    with tempfile.TemporaryDirectory() as tmp:
        png = os.path.join(tmp, "page")
        for p in range(1, n + 1):
            run(["pdftoppm", "-f", str(p), "-l", str(p), "-scale-to", str(MAX_PX),
                 "-gray", "-png", "-singlefile", path, png])
            text.append(run(["tesseract", png + ".png", "-", "-l", "nld+eng"]))
    return "\n".join(text)


def clean_tmp():
    # Leftovers from a run that was killed mid-OCR
    tmp = tempfile.gettempdir()
    for name in os.listdir(tmp):
        path = os.path.join(tmp, name)
        try:
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        except OSError:
            pass


def extract(path, mime, allow_ocr=True):
    if os.path.getsize(path) == 0:
        return None, "empty"
    if mime == "application/msword":
        with open(path, "rb") as f:
            if f.read(4) == b"PK\x03\x04":  # .docx saved as .doc
                mime = DOCX
    if mime == "application/pdf":
        text = run(["pdftotext", "-enc", "UTF-8", path, "-"])
        if len(text.strip()) >= 100:
            return text, "ok"
        if not allow_ocr:
            return None, "ocr_excluded"
        return (ocr(path), "ocr") if OCR else (None, "needs_ocr")
    if mime == "application/msword":
        return run(["catdoc", "-d", "utf-8", path]), "ok"
    if mime in ZIP_PARTS:
        if is_encrypted(path):
            return None, "encrypted"
        return from_zip(path, ZIP_PARTS[mime]), "ok"
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read(MAX_CHARS), "ok"


def main():
    clean_tmp()
    # Connection settings come from the PG* environment variables
    with psycopg.connect(autocommit=True) as db:
        while True:
            db.execute(CLEANUP)
            rows = db.execute(PENDING, {"mimes": MIMES, "ocr": OCR, "batch": BATCH}).fetchall()
            for fileid, storage, storage_id, path, etag, mime in rows:
                full = os.path.join(DATA, storage_id.split("::", 1)[1], path)
                start = time.monotonic()
                try:
                    text, status = extract(full, mime, not ocr_excluded(path))
                    info = f"{time.monotonic() - start:.1f} s"
                    if status == "ocr":
                        info = f"{pages(full)} p, {info}"
                    print(f"{status} {fileid} {path} ({info})", flush=True)
                except Exception as e:
                    print(f"error {fileid} {path}: {e}", flush=True)
                    text, status = None, "error"
                if text is not None:
                    text = text.replace("\x00", "")[:MAX_CHARS]
                try:
                    db.execute(UPSERT, (fileid, storage, etag, status, text))
                except psycopg.Error as e:
                    print(f"error {fileid} {path}: {e}", flush=True)
                    db.execute(UPSERT, (fileid, storage, etag, "error", None))
            print(f"indexed {len(rows)}", flush=True)
            if len(rows) < BATCH:
                time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
