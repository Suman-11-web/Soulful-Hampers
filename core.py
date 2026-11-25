import hmac
import re
import secrets
import sqlite3
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from flask import abort, current_app, g, redirect, request, session, url_for
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.security import generate_password_hash

CATEGORIES = {
    "hampers": ("Hampers", "Handpicked favourites, beautifully brought together."),
    "bouquets": ("Bouquets", "Flowers for the moments words can’t quite capture."),
    "diya-gifts": ("Diya Gifts", "Thoughtful little lights for festive occasions."),
    "personalized-gifts": (
        "Personalized Gifts",
        "A name, a note, a gift made personal.",
    ),
    "return-gifts": ("Return Gifts", "A beautiful thank-you for everyone who came."),
}
ARTWORK = {
    "wordmark": "Studio wordmark",
    "hero": "Bouquet & hamper hero",
    **{key: item[0] for key, item in CATEGORIES.items()},
    "hero-floral": "Hero floral corner",
    "gift-detail": "Four-step gift detail",
    "cta-gift": "Quick-order ribbon",
    "botanical": "Footer flowers",
}
SOCIALS = {
    "instagram": "instagram.com",
    "youtube": "youtube.com",
    "facebook": "facebook.com",
}
DEFAULTS = {
    "instagram": "",
    "youtube": "",
    "facebook": "",
    "hero_title": "Bouquets & Hampers",
    "hero_second": "That Never Fade",
    "hero_copy": "Thoughtful, personalised and elegant gifts for every occasion — handcrafted with love by Soulful Hampers.",
    "announcement": "Same-Day Delivery in Bengaluru",
    "region": "Bengaluru · Tumkur · Karnataka",
    "email": "hello@soulfulhampers.in",
    "whatsapp": "",
    "qr_file": "",
    "payee": "",
    "delivery_info": "Same-day delivery may be available in Bengaluru. Please check with our team for your date and location.",
    "quick_title": "Want to Place a Quick Order?",
    "quick_copy": "Chat with us for custom orders, bulk gifting & same-day delivery.",
}
PAYMENTS = ("Under review", "Verified", "Needs attention", "Refunded")
STATUSES = (
    "Received",
    "Confirmed",
    "Being prepared",
    "Out for delivery",
    "Delivered",
    "Cancelled",
)
ENQUIRY_STATUSES = ("New", "In progress", "Resolved")
TZ = ZoneInfo("Asia/Kolkata")


def password_hash(password):
    return generate_password_hash(password, method="pbkdf2:sha256:1000000")


def now():
    return datetime.now(TZ).isoformat(timespec="seconds")


def today():
    return datetime.now(TZ).date()


def db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"], timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def settings():
    return {
        **DEFAULTS,
        **dict(db().execute("SELECT key,value FROM settings").fetchall()),
    }


def csrf():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(28)
    return session["_csrf"]


def protect():
    if request.method == "POST":
        sent = request.form.get("_csrf", "")
        if not sent or not hmac.compare_digest(
            session.get("_csrf", "").encode(), sent.encode()
        ):
            abort(400, "Your form expired. Refresh the page and try again.")


def money(paise):
    rupees = Decimal(paise) / 100
    return "₹" + (
        f"{rupees:,.0f}" if rupees == rupees.to_integral_value() else f"{rupees:,.2f}"
    )


def price_input(raw):
    try:
        value = Decimal(raw)
        if (
            not value.is_finite()
            or value <= 0
            or value > 10000000
            or value != value.quantize(Decimal(".01"))
        ):
            raise ValueError
        return int(value * 100)
    except (ValueError, InvalidOperation):
        raise ValueError(
            "Enter a price between ₹0.01 and ₹1,00,00,000 with up to two decimal places."
        )


def field(key, label, size=160, required=True):
    value = request.form.get(key, "").strip()
    if (required and not value) or len(value) > size:
        raise ValueError(f"Check the {label} field (up to {size} characters).")
    return value


def phone_number(raw):
    value = re.sub(r"[\s()+-]", "", raw)
    if len(value) == 12 and value.startswith("91"):
        value = value[2:]
    if not re.fullmatch("[6-9][0-9]{9}", value):
        raise ValueError("Enter a valid 10-digit Indian mobile number.")
    return value


def valid_email(value):
    if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("Enter a valid email address.")
    return value


def limit(scope, maximum, seconds):
    stamp = int(time.time())
    conn = db()
    with conn:
        conn.execute("DELETE FROM attempts WHERE at < ?", (stamp - 3600,))
        count = conn.execute(
            "SELECT count(*) FROM attempts WHERE scope=? AND address=? AND at>?",
            (scope, request.remote_addr or "", stamp - seconds),
        ).fetchone()[0]
        if count >= maximum:
            abort(429, "Too many attempts. Please try again later.")
        conn.execute(
            "INSERT INTO attempts VALUES (?,?,?)",
            (scope, request.remote_addr or "", stamp),
        )


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if (
            not session.get("admin")
            or not db()
            .execute("SELECT 1 FROM admins WHERE version=?", (session["admin"],))
            .fetchone()
        ):
            return redirect(url_for("admin.login"))
        return view(*args, **kwargs)

    return wrapped


def save_image(file, qr=False, transparent=False):
    try:
        with Image.open(file.stream) as img:
            if (
                img.format not in ("PNG", "JPEG", "WEBP")
                or img.width * img.height > 20000000
                or min(img.size) < 40
            ):
                raise ValueError(
                    "Use a JPG, PNG or WebP image between 40 pixels and 20 megapixels."
                )
            img.load()
            photo = ImageOps.exif_transpose(img).convert("RGBA")
            bg = Image.new("RGB", photo.size, "white")
            bg.paste(photo, mask=photo.getchannel("A"))
            if transparent:
                bg = photo
            bg.thumbnail((1800, 1800))
            filename = secrets.token_hex(16) + (".png" if qr else ".webp")
            bg.save(Path(current_app.config["UPLOADS"]) / filename, quality=88)
            return filename
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("The image could not be read. Choose a JPG, PNG or WebP file.")


def qr_url(conf):
    if conf["qr_file"] == "disabled":
        return ""
    if (
        conf["qr_file"]
        and (Path(current_app.config["UPLOADS"]) / conf["qr_file"]).is_file()
    ):
        return url_for("media", name=conf["qr_file"])
    for name in (
        "payment/qr.png",
        "payment/qr.jpeg",
        "payment/qr.jpg",
        "qr.png",
        "qr.jpeg",
        "qr.jpg",
    ):
        if (Path(current_app.static_folder) / name).is_file():
            return url_for("static", filename=name)
    return ""


def wa_url(conf, message):
    return (
        "https://wa.me/" + conf["whatsapp"] + "?text=" + quote(message)
        if conf["whatsapp"]
        else ""
    )
