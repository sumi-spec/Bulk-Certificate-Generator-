"""High-Fidelity Vector PDF Certificate Generator using ReportLab."""

import io
import re
import unicodedata
import uuid
from datetime import date, datetime

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

from app.core.logging import logger

# Professional Color Palette
COLOR_PRIMARY_NAVY = HexColor("#0F2027")
COLOR_SECONDARY_NAVY = HexColor("#203A43")
COLOR_GOLD = HexColor("#C59B27")
COLOR_LIGHT_GOLD = HexColor("#DFBA53")
COLOR_CHARCOAL = HexColor("#333333")
COLOR_MUTED = HexColor("#666666")
COLOR_CREAM = HexColor("#FAF9F6")


class PDFCertificateGenerator:
    """Renders professional, vector-based PDF certificates from a predefined template."""

    PAGE_WIDTH, PAGE_HEIGHT = landscape(A4)  # 841.89 x 595.27 points

    @classmethod
    def sanitize_text(cls, text: str) -> str:
        """
        Sanitizes text to prevent canvas corruption or font encoding errors.
        Strips control characters and normalizes Unicode.
        """
        if not text:
            return ""
        # Remove control characters
        cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(text))
        # Normalize Unicode characters (NFKD)
        normalized = unicodedata.normalize("NFKD", cleaned)
        # Fallback encode for standard PDF Type-1 fonts (Helvetica/Times)
        safe_ascii = normalized.encode("latin-1", "replace").decode("latin-1")
        return safe_ascii.strip()

    @classmethod
    def format_date(cls, raw_date: date | datetime | str) -> str:
        """Formats dates into an elegant presentation format."""
        if isinstance(raw_date, datetime):
            return raw_date.strftime("%B %d, %Y")
        if isinstance(raw_date, date):
            return raw_date.strftime("%B %d, %Y")
        if isinstance(raw_date, str):
            try:
                dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                return dt.strftime("%B %d, %Y")
            except ValueError:
                return raw_date
        return str(raw_date)

    @classmethod
    def generate_safe_filename(cls, recipient_name: str, certificate_id: uuid.UUID | str) -> str:
        """Constructs a deterministic, safe filename for downloads and storage."""
        cleaned_name = re.sub(r"[^A-Za-z0-9]+", "_", recipient_name.strip()).strip("_")
        short_id = str(certificate_id)[:8]
        return f"certificate_{cleaned_name}_{short_id}.pdf"

    @classmethod
    def validate_pdf_bytes(cls, pdf_bytes: bytes) -> bool:
        """Validates that the rendered output is a non-empty, well-formed PDF binary."""
        if not pdf_bytes or len(pdf_bytes) < 500:
            return False
        # Valid PDFs begin with magic bytes %PDF-
        if not pdf_bytes.startswith(b"%PDF-"):
            return False
        # Valid PDFs end with %%EOF (often followed by trailing whitespace/newlines)
        return b"%%EOF" in pdf_bytes[-1024:]

    @classmethod
    def draw_decorative_borders(cls, c: canvas.Canvas):
        """Draws multi-tier ornamental borders and corner flourishes."""
        w, h = cls.PAGE_WIDTH, cls.PAGE_HEIGHT

        # 1. Subtle background fill
        c.setFillColor(COLOR_CREAM)
        c.rect(0, 0, w, h, fill=1, stroke=0)

        # 2. Outer Navy Border
        c.setStrokeColor(COLOR_PRIMARY_NAVY)
        c.setLineWidth(5)
        c.rect(20, 20, w - 40, h - 40)

        # 3. Inner Gold Accent Border
        c.setStrokeColor(COLOR_GOLD)
        c.setLineWidth(1.5)
        c.rect(28, 28, w - 56, h - 56)

        # 4. Corner Diamonds / Flourishes
        corner_offsets = [(28, 28), (w - 28, 28), (28, h - 28), (w - 28, h - 28)]
        c.setFillColor(COLOR_GOLD)
        c.setStrokeColor(COLOR_PRIMARY_NAVY)
        c.setLineWidth(1)
        for cx, cy in corner_offsets:
            p = c.beginPath()
            p.moveTo(cx - 8, cy)
            p.lineTo(cx, cy + 8)
            p.lineTo(cx + 8, cy)
            p.lineTo(cx, cy - 8)
            p.close()
            c.drawPath(p, fill=1, stroke=1)

    @classmethod
    def draw_gold_seal(cls, c: canvas.Canvas, x: float, y: float):
        """Draws a vector gold rosette seal on the certificate canvas."""
        c.saveState()
        # Gold Rosette Outer
        c.setFillColor(COLOR_GOLD)
        c.setStrokeColor(COLOR_LIGHT_GOLD)
        c.setLineWidth(1)
        c.circle(x, y, 32, fill=1, stroke=1)

        # Inner ring
        c.setFillColor(COLOR_CREAM)
        c.setStrokeColor(COLOR_GOLD)
        c.circle(x, y, 26, fill=1, stroke=1)

        # Inner emblem ring
        c.setFillColor(COLOR_GOLD)
        c.circle(x, y, 22, fill=1, stroke=0)

        # Center star/text
        c.setFillColor(HexColor("#FFFFFF"))
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(x, y - 3, "VERIFIED")
        c.restoreState()

    @classmethod
    def render(
        cls,
        recipient_name: str,
        event_name: str,
        issue_date: date | datetime | str,
        issuer_name: str,
        certificate_id: uuid.UUID | str,
        verification_url: str | None = None,
    ) -> bytes:
        """
        Renders a vector certificate in memory and returns raw PDF bytes.
        """
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=(cls.PAGE_WIDTH, cls.PAGE_HEIGHT))
        w, h = cls.PAGE_WIDTH, cls.PAGE_HEIGHT

        # 1. Background & Borders
        cls.draw_decorative_borders(c)

        # 2. Header Title
        c.setFillColor(COLOR_PRIMARY_NAVY)
        c.setFont("Helvetica-Bold", 26)
        c.drawCentredString(w / 2, h - 90, "CERTIFICATE OF ACHIEVEMENT")

        # Decorative Divider line below header
        c.setStrokeColor(COLOR_GOLD)
        c.setLineWidth(2)
        c.line(w / 2 - 120, h - 105, w / 2 + 120, h - 105)

        # Center small diamond on line
        p = c.beginPath()
        p.moveTo(w / 2 - 4, h - 105)
        p.lineTo(w / 2, h - 101)
        p.lineTo(w / 2 + 4, h - 105)
        p.lineTo(w / 2, h - 109)
        p.close()
        c.setFillColor(COLOR_GOLD)
        c.drawPath(p, fill=1, stroke=0)

        # 3. Presentation Subtitle
        c.setFillColor(COLOR_MUTED)
        c.setFont("Helvetica", 12)
        c.drawCentredString(w / 2, h - 145, "THIS IS PROUDLY PRESENTED TO")

        # 4. Recipient Name (with Dynamic Font Scaling for Long Names)
        clean_name = cls.sanitize_text(recipient_name)
        max_name_width = w - 220  # Keep 110 points safe margin on left & right
        font_size = 34
        c.setFont("Helvetica-Bold", font_size)
        while (
            font_size > 16
            and c.stringWidth(clean_name, "Helvetica-Bold", font_size) > max_name_width
        ):
            font_size -= 2
            c.setFont("Helvetica-Bold", font_size)

        c.setFillColor(COLOR_SECONDARY_NAVY)
        c.drawCentredString(w / 2, h - 200, clean_name)

        # Underline under recipient name
        name_width = min(
            c.stringWidth(clean_name, "Helvetica-Bold", font_size) + 40, max_name_width
        )
        c.setStrokeColor(COLOR_GOLD)
        c.setLineWidth(1)
        c.line(w / 2 - name_width / 2, h - 212, w / 2 + name_width / 2, h - 212)

        # 5. Narrative Text
        c.setFillColor(COLOR_CHARCOAL)
        c.setFont("Helvetica", 12)
        c.drawCentredString(
            w / 2,
            h - 245,
            "for successfully completing all requirements and demonstrating excellence in",
        )

        # 6. Event / Course Name (with Dynamic Font Scaling)
        clean_event = cls.sanitize_text(event_name)
        event_font_size = 20
        c.setFont("Helvetica-Bold", event_font_size)
        while (
            event_font_size > 14
            and c.stringWidth(clean_event, "Helvetica-Bold", event_font_size) > max_name_width
        ):
            event_font_size -= 1
            c.setFont("Helvetica-Bold", event_font_size)

        c.setFillColor(COLOR_PRIMARY_NAVY)
        c.drawCentredString(w / 2, h - 280, clean_event)

        # 7. Issuing Organization
        clean_issuer = cls.sanitize_text(issuer_name)
        c.setFillColor(COLOR_MUTED)
        c.setFont("Helvetica-Oblique", 11)
        c.drawCentredString(w / 2, h - 315, f"Presented by {clean_issuer}")

        # 8. Decorative Vector Seal
        cls.draw_gold_seal(c, 120, 110)

        # 9. Left Column: Date and Identification Details
        formatted_date = cls.format_date(issue_date)
        cert_id_str = str(certificate_id)

        c.setFillColor(COLOR_CHARCOAL)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(200, 125, "Date of Issuance:")
        c.setFont("Helvetica", 10)
        c.drawString(300, 125, formatted_date)

        c.setFont("Helvetica-Bold", 10)
        c.drawString(200, 105, "Certificate ID:")
        c.setFont("Helvetica", 9)
        c.drawString(300, 105, cert_id_str)

        verify_url = verification_url or f"https://verify.example.org/cert/{cert_id_str}"
        c.setFont("Helvetica-Bold", 10)
        c.drawString(200, 85, "Verification:")
        c.setFont("Helvetica-Oblique", 8.5)
        c.setFillColor(HexColor("#2980B9"))
        c.drawString(300, 85, verify_url)

        # 10. Right Column: Signature Block
        sig_x = w - 180
        # Artistic flourish signature simulation
        c.setStrokeColor(COLOR_SECONDARY_NAVY)
        c.setLineWidth(1.5)
        p = c.beginPath()
        p.moveTo(sig_x - 70, 125)
        p.curveTo(sig_x - 30, 140, sig_x - 10, 110, sig_x + 20, 135)
        p.curveTo(sig_x + 35, 145, sig_x + 50, 120, sig_x + 70, 128)
        c.drawPath(p, fill=0, stroke=1)

        # Line under signature
        c.setStrokeColor(COLOR_GOLD)
        c.setLineWidth(1)
        c.line(sig_x - 80, 112, sig_x + 80, 112)

        # Signature text label
        c.setFillColor(COLOR_CHARCOAL)
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(sig_x, 98, "Authorized Signature")
        c.setFont("Helvetica", 9)
        c.setFillColor(COLOR_MUTED)
        c.drawCentredString(sig_x, 85, clean_issuer)

        # 11. Finalize PDF canvas
        c.showPage()
        c.save()

        pdf_bytes = buffer.getvalue()
        buffer.close()

        if not cls.validate_pdf_bytes(pdf_bytes):
            logger.error("Rendered PDF validation failed for cert_id: %s", certificate_id)
            raise ValueError("Generated PDF output failed integrity validation.")

        logger.debug(
            "Successfully generated PDF certificate for '%s' (%d bytes)", clean_name, len(pdf_bytes)
        )
        return pdf_bytes


pdf_generator = PDFCertificateGenerator()
