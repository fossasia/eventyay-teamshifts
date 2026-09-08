import json
from io import BytesIO

from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _
from eventyay.base.pdf import Renderer
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from .models import CertificateSettings

CERTIFICATE_PLACEHOLDERS = (
    ("event_logo", _("Event logo"), ""),
    ("certificate_title", _("Certificate title"), "Certificate of Appreciation"),
    ("certificate_intro", _("Intro line"), "presents this"),
    ("member_name", _("Member name"), "Jane Member"),
    ("member_email", _("Member email"), "jane@example.com"),
    ("certificate_body_line1", _("Body text (line 1)"), "For your dedication and outstanding contributions at"),
    (
        "certificate_body_line2",
        _("Body text (line 2)"),
        "the Sample Event, held from 1 Jan to 3 Jan 2026, in Berlin, Germany.",
    ),
    ("event_name", _("Event name"), "Sample Event"),
    ("event_dates", _("Event dates (combined)"), "1\u20133 January 2026"),
    ("event_date_from", _("Event start date"), "1 January 2026"),
    ("event_date_to", _("Event end date"), "3 January 2026"),
    ("event_location", _("Event location"), "Berlin, Germany"),
    ("organizer_name", _("Organizer name"), "Sample Organizer"),
    ("completed_shift_count", _("Completed shift count"), "2"),
    ("assigned_shift_count", _("Assigned shift count"), "3"),
    ("roles", _("Assigned roles"), "Registration, Info desk"),
    ("issued_date", _("Date issued"), "24 August 2026"),
)

CERTIFICATE_DEFAULT_STATIC = "teamshifts/certificates/certificate_default.pdf"


NAVY = [27, 54, 93, 1]

DEFAULT_CERTIFICATE_LAYOUT = [
    {
        "type": "imagearea",
        "left": "119.00",
        "bottom": "152.25",
        "width": "100.00",
        "height": "22.00",
        "content": "event_logo",
    },
    {
        "type": "textarea",
        "left": "40.00",
        "bottom": "141.25",
        "fontsize": "13.0",
        "color": [107, 107, 107, 1],
        "fontfamily": "Open Sans",
        "bold": False,
        "italic": False,
        "width": "260.00",
        "content": "certificate_intro",
        "text": "presents this",
        "align": "center",
    },
    {
        "type": "textarea",
        "left": "10.00",
        "bottom": "118.25",
        "fontsize": "30.0",
        "color": NAVY,
        "fontfamily": "Open Sans",
        "bold": True,
        "italic": False,
        "width": "320.00",
        "content": "certificate_title",
        "text": "Certificate of Appreciation",
        "align": "center",
    },
    {
        "type": "textarea",
        "left": "40.00",
        "bottom": "108.25",
        "fontsize": "13.0",
        "color": [107, 107, 107, 1],
        "fontfamily": "Open Sans",
        "bold": False,
        "italic": False,
        "width": "260.00",
        "content": "other",
        "text": "to",
        "align": "center",
    },
    {
        "type": "textarea",
        "left": "40.00",
        "bottom": "90.25",
        "fontsize": "24.0",
        "color": NAVY,
        "fontfamily": "Open Sans",
        "bold": True,
        "italic": False,
        "width": "260.00",
        "content": "member_name",
        "text": "Jane Member",
        "align": "center",
    },
    {
        "type": "textarea",
        "left": "40.00",
        "bottom": "78.00",
        "fontsize": "13.0",
        "color": [107, 107, 107, 1],
        "fontfamily": "Open Sans",
        "bold": False,
        "italic": False,
        "width": "260.00",
        "content": "certificate_body_line1",
        "text": "For your dedication and outstanding contributions at",
        "align": "center",
    },
    {
        "type": "textarea",
        "left": "30.00",
        "bottom": "69.00",
        "fontsize": "13.0",
        "color": [107, 107, 107, 1],
        "fontfamily": "Open Sans",
        "bold": False,
        "italic": False,
        "width": "280.00",
        "content": "certificate_body_line2",
        "text": "the Sample Event, held from 1 Jan to 3 Jan 2026, in Berlin, Germany.",
        "align": "center",
    },
    {
        "type": "textarea",
        "left": "100.00",
        "bottom": "16.25",
        "fontsize": "11.0",
        "color": [107, 107, 107, 1],
        "fontfamily": "Open Sans",
        "bold": False,
        "italic": False,
        "width": "140.00",
        "content": "issued_date",
        "text": "Date Issued: 24 August 2026",
        "align": "center",
    },
]


def editor_variables():
    return {key: {"label": label, "editor_sample": sample} for key, label, sample in CERTIFICATE_PLACEHOLDERS if key != "event_logo"}


def editor_image_variables():
    return {"event_logo": {"label": _("Event logo")}}


def preview_context(event):
    from django.utils.translation import gettext

    location = str(event.location) if event.location else "Berlin, Germany"
    return {
        "certificate_title": gettext("Certificate of Appreciation"),
        "certificate_intro": gettext("presents this"),
        "certificate_body_line1": gettext("For your dedication and outstanding contributions at"),
        "certificate_body_line2": gettext("the %(event_name)s, held from %(date_from)s to %(date_to)s, in %(location)s.")
        % {
            "event_name": str(event.name),
            "date_from": format_event_date_from(event),
            "date_to": format_event_date_to(event),
            "location": location,
        },
        "member_name": "Jane Member",
        "member_email": "jane@example.com",
        "event_name": str(event.name),
        "event_dates": format_event_dates(event),
        "event_date_from": format_event_date_from(event),
        "event_date_to": format_event_date_to(event),
        "event_location": location,
        "organizer_name": str(event.organizer.name),
        "completed_shift_count": "2",
        "assigned_shift_count": "3",
        "roles": "Registration, Info desk",
        "issued_date": gettext("Date Issued: %(date)s") % {"date": "24 August 2026"},
    }


def format_event_dates(event):
    start = date_format(event.date_from, "DATE_FORMAT")
    if event.date_to:
        return f"{start} \u2013 {date_format(event.date_to, 'DATE_FORMAT')}"
    return start


def format_event_date_from(event):
    return date_format(event.date_from, "DATE_FORMAT")


def format_event_date_to(event):
    if event.date_to:
        return date_format(event.date_to, "DATE_FORMAT")
    return date_format(event.date_from, "DATE_FORMAT")


def default_layout():
    return json.loads(json.dumps(DEFAULT_CERTIFICATE_LAYOUT))


def layout_is_initial_overlay(layout_json: str) -> bool:
    try:
        items = json.loads(layout_json or "[]")
    except json.JSONDecodeError:
        return True
    contents = [item.get("content") for item in items if item.get("type") == "textarea"]
    types = {item.get("type") for item in items}
    if types <= {"textarea", "poweredby", "imagearea"}:
        # Recognize any variant of the default layout as "initial" so it gets replaced on upgrade
        initial_patterns = (
            ["member_name", "event_name", "event_dates"],
            ["volunteer_name", "event_name", "event_dates"],
            ["member_name", "event_name", "event_dates", "organizer_name"],
            ["certificate_intro", "certificate_title", "member_name", "certificate_body", "issued_date"],
        )
        if contents in initial_patterns:
            return True
        content_set = set(contents)
        old_sets = (
            {"certificate_intro", "certificate_title", "member_name", "certificate_body", "issued_date"},
            {"member_name", "event_name", "event_dates", "organizer_name"},
            {
                "certificate_intro",
                "certificate_title",
                "member_name",
                "certificate_body_line1",
                "certificate_body_line2",
                "issued_date",
            },
        )
        if content_set in old_sets:
            return True
        content_set_without_other = content_set - {"other"}
        if content_set_without_other in old_sets:
            return True
    return False


def get_default_background_path():
    return CERTIFICATE_DEFAULT_STATIC


def get_default_background_url():
    return static(CERTIFICATE_DEFAULT_STATIC)


def open_default_background():
    path = finders.find(CERTIFICATE_DEFAULT_STATIC)
    if path:
        return open(path, "rb")
    from reportlab.lib.pagesizes import landscape as ls

    buffer = BytesIO()
    c = Canvas(buffer, pagesize=ls(A4))
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer


def image_file_to_pdf(uploaded) -> BytesIO:
    uploaded.seek(0)
    image = ImageReader(uploaded)
    width, height = image.getSize()
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=(width, height))
    canvas.drawImage(image, 0, 0, width=width, height=height, preserveAspectRatio=True, mask="auto")
    canvas.showPage()
    canvas.save()
    buffer.seek(0)
    return buffer


class CertificateRenderer(Renderer):
    def __init__(self, event, layout, background_file, context: dict):
        super().__init__(event, layout, background_file)
        self.context = context
        self.variables = {}
        self.images = {}

    def _get_text_content(self, op, order, o, inner=False):
        content = o.get("content")
        if not content:
            return ""
        if content == "other":
            return o.get("text") or ""
        return str(self.context.get(content, ""))

    def _draw_imagearea(self, canvas: Canvas, op, order, o):
        content = o.get("content")
        if content != "event_logo":
            return
        event = self.event
        if not event.settings.event_logo_image:
            return
        from eventyay.base.invoice import ThumbnailingImageReader

        logo_file = event.settings.get("event_logo_image", binary_file=True)
        if not logo_file:
            return
        file_name = getattr(logo_file, "name", "") or ""
        if file_name.lower().endswith(".svg"):
            return
        try:
            ir = ThumbnailingImageReader(logo_file)
            ir.resize(float(o["width"]) * mm, float(o["height"]) * mm, 300)
        except Exception:
            return
        canvas.drawImage(
            ir,
            float(o["left"]) * mm,
            float(o["bottom"]) * mm,
            width=float(o["width"]) * mm,
            height=float(o["height"]) * mm,
            preserveAspectRatio=True,
            anchor="c",
            mask="auto",
        )

    def draw_page(self, canvas: Canvas, order=None, op=None, show_page=True):
        allowed = {"textarea", "poweredby", "imagearea"}
        layout = [obj for obj in self.layout if obj.get("type") in allowed]
        original = self.layout
        self.layout = layout
        try:
            if self.bg_pdf:
                bg_page = self.bg_pdf.pages[0]
                page_size = (
                    bg_page.mediabox[2] - bg_page.mediabox[0],
                    bg_page.mediabox[3] - bg_page.mediabox[1],
                )
                if bg_page.get("/Rotate") in (90, 270):
                    page_size = page_size[::-1]
                canvas.setPageSize(page_size)
            for o in self.layout:
                if o["type"] == "imagearea":
                    self._draw_imagearea(canvas, None, None, o)
                elif o["type"] == "textarea":
                    self._draw_textarea(canvas, None, None, o)
                elif o["type"] == "poweredby":
                    self._draw_poweredby(canvas, None, o)
            if show_page:
                canvas.showPage()
        finally:
            self.layout = original


def render_certificate_pdf(settings: CertificateSettings, context: dict, layout=None, background_file=None) -> bytes:
    layout = layout if layout is not None else (json.loads(settings.layout) if settings.layout else default_layout())
    if background_file is not None:
        background = background_file
    elif settings.background and settings.background.name:
        background = settings.background.open("rb")
    else:
        background = open_default_background()
    Renderer._register_fonts()
    buffer = BytesIO()
    renderer = CertificateRenderer(settings.event, layout, background, context)
    canvas = Canvas(buffer, pagesize=landscape(A4))
    renderer.draw_page(canvas)
    canvas.save()
    return renderer.render_background(buffer, str(_("Member certificate"))).read()
