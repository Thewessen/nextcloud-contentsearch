"""Unit tests for extract(). Run inside the indexer image:

    docker run --rm -v "$PWD/indexer:/src" -w /src --entrypoint python3 \
        contentsearch-indexer:dev -m unittest -v

Set FIXTURES to a directory with the files from dev/fixtures/generate.sh
to also test real LibreOffice output, including .doc.
"""
import os
import subprocess
import tempfile
import unittest
import zipfile
from unittest import mock

import indexer

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ODT = "application/vnd.oasis.opendocument.text"
ODS = "application/vnd.oasis.opendocument.spreadsheet"
ODP = "application/vnd.oasis.opendocument.presentation"

FILLER = "Deze zin maakt de tekst langer dan honderd tekens, zodat het geen scan is. " * 2


def text_pdf(path, lines, size=12):
    """Write a minimal one-page PDF with Helvetica text."""
    ops = [f"BT /F1 {size} Tf {size * 1.4} TL 40 800 Td"]
    for line in lines:
        ops.append("(%s) Tj T*" % line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)"))
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    with open(path, "wb") as f:
        f.write(out)


def scan_pdf(path, lines):
    """Write an image-only PDF: render a text PDF and wrap the bitmap."""
    import img2pdf  # ships with ocrmypdf
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src.pdf")
        text_pdf(src, lines, size=28)
        subprocess.run(["pdftoppm", "-r", "200", "-gray", "-png", "-singlefile",
                        src, os.path.join(tmp, "page")], check=True, stderr=subprocess.DEVNULL)
        with open(path, "wb") as f:
            f.write(img2pdf.convert(os.path.join(tmp, "page.png")))


def zip_file(path, parts):
    with zipfile.ZipFile(path, "w") as z:
        for name, xml in parts.items():
            z.writestr(name, xml)


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(indexer, "OCR", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def test_text_pdf(self):
        p = self.path("a.pdf")
        text_pdf(p, ["Het woord is flamingo.", FILLER[:80], FILLER[80:]])
        text, status = indexer.extract(p, "application/pdf")
        self.assertEqual(status, "ok")
        self.assertIn("flamingo", text)

    def test_scan_without_ocr(self):
        p = self.path("scan.pdf")
        scan_pdf(p, ["zeppelin"])
        self.assertEqual(indexer.extract(p, "application/pdf"), (None, "needs_ocr"))

    def test_scan_with_ocr(self):
        p = self.path("scan.pdf")
        scan_pdf(p, ["Gescande brief", "Het woord is zeppelin."])
        with mock.patch.object(indexer, "OCR", True):
            text, status = indexer.extract(p, "application/pdf")
        self.assertEqual(status, "ocr")
        self.assertIn("zeppelin", text)

    def test_scan_ocr_excluded(self):
        p = self.path("scan.pdf")
        scan_pdf(p, ["zeppelin"])
        for ocr in (False, True):
            with mock.patch.object(indexer, "OCR", ocr):
                self.assertEqual(indexer.extract(p, "application/pdf", allow_ocr=False),
                                 (None, "ocr_excluded"))

    def test_text_pdf_in_excluded_folder_is_indexed(self):
        p = self.path("a.pdf")
        text_pdf(p, ["Het woord is flamingo.", FILLER[:80], FILLER[80:]])
        self.assertEqual(indexer.extract(p, "application/pdf", allow_ocr=False)[1], "ok")

    def test_ocr_excluded_paths(self):
        with mock.patch.object(indexer, "OCR_EXCLUDE", ["Resources/Sheetmusic"]):
            self.assertTrue(indexer.ocr_excluded("files/Resources/Sheetmusic/a.pdf"))
            self.assertTrue(indexer.ocr_excluded("files/Resources/Sheetmusic/Bach/a.pdf"))
            self.assertFalse(indexer.ocr_excluded("files/Resources/Sheetmusic2/a.pdf"))
            self.assertFalse(indexer.ocr_excluded("files/Scans/Resources/Sheetmusic/a.pdf"))
        with mock.patch.object(indexer, "OCR_EXCLUDE", []):
            self.assertFalse(indexer.ocr_excluded("files/Resources/Sheetmusic/a.pdf"))

    def test_pages(self):
        p = self.path("a.pdf")
        text_pdf(p, ["een"])
        self.assertEqual(indexer.pages(p), "1")
        self.assertEqual(indexer.pages(self.path("missing.pdf")), "?")

    def test_docx_joins_runs_and_splits_paragraphs(self):
        p = self.path("a.docx")
        zip_file(p, {"word/document.xml": (
            '<w:document><w:body>'
            '<w:p><w:r><w:t>kan</w:t></w:r><w:r><w:t>goeroe</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>tweede</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>regel &amp; zo</w:t></w:r></w:p>'
            '</w:body></w:document>')})
        text, status = indexer.extract(p, DOCX)
        self.assertEqual(status, "ok")
        self.assertEqual(text.split(), ["kangoeroe", "tweede", "regel", "&", "zo"])

    def test_pptx_slides_in_numeric_order(self):
        p = self.path("a.pptx")
        slide = '<p:sld><p:txBody><a:p><a:r><a:t>%s</a:t></a:r></a:p></p:txBody></p:sld>'
        zip_file(p, {
            "ppt/slides/slide10.xml": slide % "tien",
            "ppt/slides/slide2.xml": slide % "twee",
            "ppt/slides/_rels/slide2.xml.rels": "<Relationships>negeren</Relationships>",
            "ppt/notesSlides/notesSlide1.xml": slide % "notities",
        })
        text, _ = indexer.extract(p, PPTX)
        self.assertEqual(text.split(), ["twee", "tien"])

    def test_xlsx_shared_strings(self):
        p = self.path("a.xlsx")
        zip_file(p, {"xl/sharedStrings.xml": (
            '<sst><si><t>octopus</t></si><si><r><t>in</t></r><r><t>kt</t></r></si></sst>')})
        text, _ = indexer.extract(p, XLSX)
        self.assertEqual(text.split(), ["octopus", "inkt"])

    def test_odf(self):
        xml = ('<office:document-content><office:body>'
               '<text:h>Kop</text:h><text:p>wal<text:span>rus</text:span>'
               '<text:s text:c="2"/>en<text:tab/>zee</text:p>'
               '<table:table-cell><text:p>a</text:p></table:table-cell>'
               '<table:table-cell><text:p>b</text:p></table:table-cell>'
               '</office:body></office:document-content>')
        for mime in (ODT, ODS, ODP):
            p = self.path("a.od")
            zip_file(p, {"content.xml": xml, "styles.xml": "<x>stijl</x>"})
            text, _ = indexer.extract(p, mime)
            self.assertEqual(text.split(), ["Kop", "walrus", "en", "zee", "a", "b"], mime)

    def test_plain_text_is_capped(self):
        p = self.path("a.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# kolibrie\n" + "x" * (indexer.MAX_CHARS + 10))
        text, status = indexer.extract(p, "text/markdown")
        self.assertEqual(status, "ok")
        self.assertTrue(text.startswith("# kolibrie"))
        self.assertEqual(len(text), indexer.MAX_CHARS)

    def test_invalid_utf8_is_replaced(self):
        p = self.path("a.txt")
        with open(p, "wb") as f:
            f.write(b"narwal \xff\xfe")
        text, _ = indexer.extract(p, "text/plain")
        self.assertIn("narwal", text)

    def test_broken_zip_raises(self):
        p = self.path("a.docx")
        with open(p, "wb") as f:
            f.write(b"not a zip")
        with self.assertRaises(zipfile.BadZipFile):
            indexer.extract(p, DOCX)

    def test_empty_file(self):
        for mime in ("application/pdf", "application/msword", DOCX, "text/plain"):
            p = self.path("leeg")
            open(p, "wb").close()
            self.assertEqual(indexer.extract(p, mime), (None, "empty"), mime)

    def test_docx_saved_as_doc(self):
        p = self.path("a.doc")
        zip_file(p, {"word/document.xml": '<w:p><w:r><w:t>pelikaan</w:t></w:r></w:p>'})
        text, status = indexer.extract(p, "application/msword")
        self.assertEqual(status, "ok")
        self.assertEqual(text.split(), ["pelikaan"])

    def test_encrypted_office_file(self):
        # Just the parts is_encrypted() looks at: the OLE2 header and the stream name
        p = self.path("a.xlsx")
        with open(p, "wb") as f:
            f.write(indexer.OLE2 + b"\x00" * 1000 + "EncryptedPackage".encode("utf-16-le"))
        for mime in (DOCX, PPTX, XLSX):
            self.assertEqual(indexer.extract(p, mime), (None, "encrypted"), mime)

    def test_ole2_without_encrypted_package_raises(self):
        p = self.path("a.xlsx")
        with open(p, "wb") as f:
            f.write(indexer.OLE2 + b"\x00" * 1000)
        with self.assertRaises(zipfile.BadZipFile):
            indexer.extract(p, XLSX)

    def test_tool_error_includes_stderr(self):
        p = self.path("a.pdf")
        with open(p, "wb") as f:
            f.write(b"not a pdf")
        with self.assertRaisesRegex(RuntimeError, r"pdftotext exit status \d+: .+"):
            indexer.extract(p, "application/pdf")


@unittest.skipUnless(os.environ.get("FIXTURES"), "FIXTURES not set")
class FixturesTest(unittest.TestCase):
    """The generated files from dev/fixtures, one unique word per format."""
    CASES = {
        "fixture-pdf.pdf": ("application/pdf", "flamingo"),
        "fixture-doc.doc": ("application/msword", "pelikaan"),
        "fixture-docx.docx": (DOCX, "kangoeroe"),
        "fixture-pptx.pptx": (PPTX, "giraffe"),
        "fixture-xlsx.xlsx": (XLSX, "octopus"),
        "fixture-odt.odt": (ODT, "walrus"),
        "fixture-ods.ods": (ODS, "pinguin"),
        "fixture-odp.odp": (ODP, "dromedaris"),
        "fixture-md.md": ("text/markdown", "kolibrie"),
        "fixture-txt.txt": ("text/plain", "narwal"),
        "fixture-renamed.doc": ("application/msword", "kameleon"),
    }
    STATUSES = {
        "fixture-empty.txt": ("text/plain", "empty"),
        "fixture-encrypted.xlsx": (XLSX, "encrypted"),
    }

    def test_fixtures(self):
        for name, (mime, word) in self.CASES.items():
            with self.subTest(name):
                text, status = indexer.extract(os.path.join(os.environ["FIXTURES"], name), mime)
                self.assertEqual(status, "ok")
                self.assertIn(word, text)

    def test_fixture_statuses(self):
        for name, (mime, status) in self.STATUSES.items():
            with self.subTest(name):
                path = os.path.join(os.environ["FIXTURES"], name)
                self.assertEqual(indexer.extract(path, mime), (None, status))

    def test_scan_fixture(self):
        path = os.path.join(os.environ["FIXTURES"], "fixture-scan.pdf")
        with mock.patch.object(indexer, "OCR", False):
            self.assertEqual(indexer.extract(path, "application/pdf"), (None, "needs_ocr"))
        with mock.patch.object(indexer, "OCR", True):
            text, status = indexer.extract(path, "application/pdf")
        self.assertEqual(status, "ocr")
        self.assertIn("zeppelin", text)


if __name__ == "__main__":
    unittest.main()
