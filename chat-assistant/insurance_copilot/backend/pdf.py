"""A tiny single-page PDF writer (no dependencies) for generated policy documents.

Text only: a title, then lines of Helvetica. Enough for a declarations page, proof of insurance or an
ID card in the demo; a real deployment would render documents from the document-management system.
"""

from __future__ import annotations


def _escape(text: str) -> str:
    text = text.encode("latin-1", "replace").decode("latin-1")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def text_pdf(title: str, lines: list[str]) -> bytes:
    """A letter-size PDF page with a bold title and the given lines (blank strings leave a gap)."""
    ops = ["BT", "/F2 16 Tf", "56 740 Td", f"({_escape(title)}) Tj", "/F1 11 Tf", "0 -28 Td"]
    for line in lines[:52]:
        ops.append(f"({_escape(line)}) Tj" if line else "")
        ops.append("0 -14 Td")
    ops.append("ET")
    stream = "\n".join(o for o in ops if o).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
