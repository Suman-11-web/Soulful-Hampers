import hashlib
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path

import click
from flask import Flask, render_template, request, send_from_directory, session

from core import (
    ARTWORK,
    CATEGORIES,
    csrf,
    db,
    money,
    password_hash,
    protect,
    qr_url,
    settings,
    today,
    wa_url,
)

ROOT = Path(__file__).resolve().parent


def create_app(config=None):
    folder = Path(os.environ.get("SOULFUL_DATA", ROOT / "instance"))
    app = Flask(__name__)
    app.config.update(
        DATABASE=str(folder / "shop.sqlite3"),
        UPLOADS=str(folder / "uploads"),
        MAX_CONTENT_LENGTH=24 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("HTTPS_ONLY") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=6),
    )
    app.config.update(config or {})
    Path(app.config["DATABASE"]).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    Path(app.config["UPLOADS"]).mkdir(parents=True, exist_ok=True)

    @app.teardown_appcontext
    def close_db(error):
        from flask import g

        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    with app.app_context(), db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript((ROOT / "schema.sql").read_text())
        conn.execute(
            "CREATE TABLE IF NOT EXISTS private_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO private_config VALUES (?,?)",
            ("signing_key", secrets.token_hex(40)),
        )
        app.secret_key = (
            os.environ.get("SECRET_KEY")
            or conn.execute(
                "SELECT value FROM private_config WHERE key='signing_key'"
            ).fetchone()[0]
        )
        seeded = conn.execute(
            "INSERT OR IGNORE INTO private_config VALUES ('initial_reviews','1')"
        ).rowcount
        if seeded:
            conn.executemany(
                "INSERT INTO reviews(name,review,stars,photo) VALUES (?,?,5,?)",
                [
                    (
                        "Priya S.",
                        "The bouquet was absolutely beautiful and so fresh! The packaging and overall experience were premium. Highly recommend Soulful Hampers!",
                        "review-1.webp",
                    ),
                    (
                        "Aishwarya R.",
                        "Loved the personalized hamper! It was so thoughtfully curated and delivered on time. Perfect for gifting!",
                        "review-2.webp",
                    ),
                    (
                        "Meghana K.",
                        "Amazing quality and stunning presentation. Their return gifts are elegant and totally worth it. Will definitely order again!",
                        "review-3.webp",
                    ),
                ],
            )

    from admin import admin
    from storefront import shop

    app.register_blueprint(shop)
    app.register_blueprint(admin)
    app.before_request(protect)

    @app.context_processor
    def shared():
        conf = settings()
        return dict(
            site=conf,
            categories=CATEGORIES,
            csrf=csrf,
            today=today(),
            qr=qr_url(conf),
            cart_count=sum(session.get("cart", {}).values()),
            whatsapp=lambda msg: wa_url(conf, msg),
        )

    app.jinja_env.filters["money"] = money
    app.jinja_env.filters["nice_date"] = lambda value: datetime.fromisoformat(
        value
    ).strftime("%d %b %Y")
    app.jinja_env.filters["nice_time"] = lambda value: datetime.fromisoformat(
        value
    ).strftime("%d %b %Y, %I:%M %p")

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        if request.endpoint not in ("static", "media", "artwork"):
            response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.get("/art/<key>.webp")
    def artwork(key):
        from flask import abort

        if key not in ARTWORK:
            abort(404)
        override = (
            db()
            .execute("SELECT value FROM settings WHERE key=?", ("art-" + key,))
            .fetchone()
        )
        if override:
            return send_from_directory(app.config["UPLOADS"], override[0], max_age=0)
        return send_from_directory(ROOT / "static" / "art", key + ".webp", max_age=0)

    @app.get("/media/<name>")
    def media(name):
        return send_from_directory(app.config["UPLOADS"], name, max_age=86400)

    def error_page(error):
        return render_template("error.html", error=error), error.code

    for code in (400, 403, 404, 405, 413, 429, 500):
        app.register_error_handler(code, error_page)

    @app.cli.command("set-admin")
    @click.option("--username", prompt=True, default="admin")
    @click.password_option(confirmation_prompt=True)
    def set_admin(username, password):
        """Create or reset the shop administrator and invalidate prior sessions."""
        if not username.strip() or len(username) > 80 or len(password) < 12:
            raise click.ClickException(
                "Enter a username and a password of at least 12 characters."
            )
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO admins VALUES (1,?,?,?)",
                (username.strip(), password_hash(password), secrets.token_hex(24)),
            )
            conn.execute("DELETE FROM private_config WHERE key='setup_digest'")
        (Path(app.config["DATABASE"]).parent / "setup-code.txt").unlink(missing_ok=True)
        click.echo("Administrator saved. Sign in at /admin.")

    @app.cli.command("setup-code")
    def setup_code():
        """Issue a single-use first-admin code in the private instance directory."""
        conn = db()
        if conn.execute("SELECT 1 FROM admins").fetchone():
            raise click.ClickException(
                "An administrator already exists. Use set-admin for password recovery."
            )
        code = secrets.token_urlsafe(24)
        target = Path(app.config["DATABASE"]).parent / "setup-code.txt"
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO private_config VALUES (?,?)",
                ("setup_digest", hashlib.sha256(code.encode()).hexdigest()),
            )
            with open(
                os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
            ) as stream:
                stream.write(code + chr(10))
        click.echo(
            f"First-admin setup code saved to {target}. Open /admin/setup to choose your password."
        )

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=int(os.environ.get("PORT", "5055")))
