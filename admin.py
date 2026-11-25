import csv
import hashlib
import hmac
import io
import json
import re
import secrets
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from core import (
    ARTWORK,
    CATEGORIES,
    DEFAULTS,
    ENQUIRY_STATUSES,
    PAYMENTS,
    SOCIALS,
    STATUSES,
    admin_required,
    db,
    field,
    limit,
    now,
    password_hash,
    price_input,
    save_image,
    settings,
    today,
    valid_email,
)

admin = Blueprint("admin", __name__, url_prefix="/admin")


@admin.route("/login", methods=["GET", "POST"])
def login():
    if not db().execute("SELECT 1 FROM admins").fetchone():
        return redirect(url_for("admin.setup"))
    if request.method == "POST":
        limit("login", 12, 900)
        row = db().execute("SELECT * FROM admins WHERE id=1").fetchone()
        if (
            row
            and check_password_hash(row["password"], request.form.get("password", ""))
            and request.form.get("username", "") == row["username"]
        ):
            session.clear()
            session["admin"] = row["version"]
            session.permanent = True
            return redirect(url_for("admin.dashboard"))
        flash("Check the username and password and try again.", "error")
    return render_template("admin/login.html", title="Studio sign in")


@admin.route("/setup", methods=["GET", "POST"])
def setup():
    conn = db()
    if conn.execute("SELECT 1 FROM admins").fetchone():
        return redirect(url_for("admin.login"))
    if request.method == "POST":
        limit("setup", 8, 900)
        try:
            username = field("username", "username", 80)
            password = request.form.get("password", "")
            if not 12 <= len(password) <= 256 or password != request.form.get(
                "confirm", ""
            ):
                raise ValueError(
                    "Use 12–256 characters and enter the same password twice."
                )
            digest = hashlib.sha256(
                request.form.get("code", "").strip().encode()
            ).hexdigest()
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                stored = conn.execute(
                    "SELECT value FROM private_config WHERE key='setup_digest'"
                ).fetchone()
                if (
                    not stored
                    or not hmac.compare_digest(stored[0], digest)
                    or conn.execute("SELECT 1 FROM admins").fetchone()
                ):
                    raise ValueError("Check the single-use setup code and try again.")
                version = secrets.token_hex(24)
                conn.execute(
                    "INSERT INTO admins VALUES (1,?,?,?)",
                    (username, password_hash(password), version),
                )
                conn.execute("DELETE FROM private_config WHERE key='setup_digest'")
            (Path(current_app.config["DATABASE"]).parent / "setup-code.txt").unlink(
                missing_ok=True
            )
            session.clear()
            session["admin"] = version
            session.permanent = True
            flash("Your studio account is ready.", "success")
            return redirect(url_for("admin.dashboard"))
        except ValueError as err:
            flash(str(err), "error")
    return render_template("admin/setup.html", title="Set up studio access")


@admin.route("/account", methods=["GET", "POST"])
@admin_required
def account():
    conn = db()
    row = conn.execute("SELECT * FROM admins WHERE id=1").fetchone()
    if request.method == "POST":
        limit("account", 8, 900)
        try:
            username = field("username", "username", 80)
            if not check_password_hash(
                row["password"], request.form.get("current", "")
            ):
                raise ValueError("Check the current password.")
            new = request.form.get("password", "")
            if not 12 <= len(new) <= 256 or new != request.form.get("confirm", ""):
                raise ValueError(
                    "Use 12–256 characters and enter the same new password twice."
                )
            version = secrets.token_hex(24)
            with conn:
                conn.execute(
                    "UPDATE admins SET username=?,password=?,version=? WHERE id=1",
                    (username, password_hash(new), version),
                )
            session.clear()
            session.permanent = True
            session["admin"] = version
            flash("Studio sign-in details updated.", "success")
            return redirect(url_for("admin.account"))
        except ValueError as err:
            flash(str(err), "error")
    return render_template(
        "admin/account.html", title="Studio account", username=row["username"]
    )


@admin.post("/logout")
@admin_required
def logout():
    session.clear()
    return redirect(url_for("shop.home"))


@admin.get("/")
@admin_required
def dashboard():
    conn = db()
    metrics = conn.execute("""SELECT count(*) AS orders, count(CASE WHEN payment='Under review' THEN 1 END) AS pending,
        coalesce(sum(CASE WHEN payment='Verified' THEN total ELSE 0 END),0) AS sales FROM orders""").fetchone()
    recent = conn.execute(
        "SELECT * FROM orders ORDER BY created DESC LIMIT 6"
    ).fetchall()
    new = conn.execute("SELECT count(*) FROM enquiries WHERE status='New'").fetchone()[
        0
    ]
    return render_template(
        "admin/dashboard.html",
        title="Studio overview",
        metrics=metrics,
        recent=recent,
        new=new,
    )


@admin.get("/products")
@admin_required
def products():
    query = request.args.get("q", "").strip()[:120]
    rows = (
        db()
        .execute(
            "SELECT p.*, (SELECT filename FROM images WHERE product_id=p.id ORDER BY id LIMIT 1) AS cover FROM products p WHERE deleted=0 AND instr(lower(name),lower(?))>0 ORDER BY id DESC",
            (query,),
        )
        .fetchall()
    )
    return render_template(
        "admin/products.html", title="Products", rows=rows, query=query
    )


@admin.route("/products/new", methods=["GET", "POST"])
@admin.route("/products/<int:pid>/edit", methods=["GET", "POST"])
@admin_required
def product_form(pid=None):
    conn = db()
    prod = (
        conn.execute(
            "SELECT * FROM products WHERE id=? AND deleted=0", (pid,)
        ).fetchone()
        if pid
        else None
    )
    if pid and not prod:
        abort(404)
    photos = conn.execute(
        "SELECT * FROM images WHERE product_id=? ORDER BY id", (pid,)
    ).fetchall()
    if request.method == "POST":
        written = []
        try:
            name = field("name", "product name", 120)
            cat = field("category", "category")
            if cat not in CATEGORIES:
                raise ValueError("Select a collection.")
            desc = field("description", "description", 4000)
            price = price_input(request.form.get("price", ""))
            files = [f for f in request.files.getlist("images") if f.filename]
            removals = {str(p["id"]) for p in photos}.intersection(
                request.form.getlist("remove")
            )
            count = len(photos) - len(removals) + len(files)
            if not 1 <= count <= 8:
                raise ValueError("Keep between one and eight product photos.")
            for f in files:
                written.append(save_image(f))
            values = (
                name,
                cat,
                desc,
                price,
                int("available" in request.form),
                int("featured" in request.form),
            )
            with conn:
                if pid:
                    conn.execute(
                        "UPDATE products SET name=?,category=?,description=?,price=?,available=?,featured=? WHERE id=? AND deleted=0",
                        values + (pid,),
                    )
                else:
                    pid = conn.execute(
                        "INSERT INTO products(name,category,description,price,available,featured) VALUES (?,?,?,?,?,?)",
                        values,
                    ).lastrowid
                conn.executemany(
                    "DELETE FROM images WHERE id=? AND product_id=?",
                    [(i, pid) for i in removals],
                )
                conn.executemany(
                    "INSERT INTO images(product_id,filename) VALUES (?,?)",
                    [(pid, n) for n in written],
                )
            flash("Product saved.", "success")
            return redirect(url_for("admin.products"))
        except Exception as err:
            for filename in written:
                (Path(current_app.config["UPLOADS"]) / filename).unlink(missing_ok=True)
            if not isinstance(err, ValueError):
                raise
            flash(str(err), "error")
    return render_template(
        "admin/product_form.html",
        title="Edit product" if prod else "Add a product",
        prod=prod,
        photos=photos,
    )


@admin.post("/products/<int:pid>/delete")
@admin_required
def delete_product(pid):
    with db() as conn:
        conn.execute(
            "UPDATE products SET deleted=1,available=0,featured=0 WHERE id=?", (pid,)
        )
    flash("Product removed from the shop.", "success")
    return redirect(url_for("admin.products"))


@admin.get("/orders")
@admin_required
def orders():
    q = request.args.get("q", "").strip()[:120]
    pay = request.args.get("payment", "")
    status = request.args.get("status", "")
    sql = "SELECT * FROM orders WHERE (instr(lower(id),lower(?)) OR instr(lower(name),lower(?)) OR instr(phone,?) OR instr(utr,?))"
    args = [q] * 4
    if pay in PAYMENTS:
        sql += " AND payment=?"
        args.append(pay)
    if status in STATUSES:
        sql += " AND status=?"
        args.append(status)
    rows = db().execute(sql + " ORDER BY created DESC", args).fetchall()
    return render_template(
        "admin/orders.html",
        title="Orders",
        rows=rows,
        q=q,
        payments=PAYMENTS,
        statuses=STATUSES,
    )


@admin.route("/orders/<oid>", methods=["GET", "POST"])
@admin_required
def order(oid):
    conn = db()
    row = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not row:
        abort(404)
    if request.method == "POST":
        pay, status = request.form.get("payment", ""), request.form.get("status", "")
        if pay not in PAYMENTS or status not in STATUSES:
            abort(400)
        try:
            note = field("internal_note", "internal notes", 3000, False)
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                current = conn.execute(
                    "SELECT payment,status FROM orders WHERE id=?", (oid,)
                ).fetchone()
                for label, old, new in [
                    ("Payment", current["payment"], pay),
                    ("Order", current["status"], status),
                ]:
                    if new != old:
                        conn.execute(
                            "INSERT INTO events(order_id,happened,detail) VALUES (?,?,?)",
                            (oid, now(), f"{label} changed from {old} to {new}."),
                        )
                conn.execute(
                    "UPDATE orders SET payment=?, status=?, internal_note=? WHERE id=?",
                    (pay, status, note, oid),
                )
            flash("Order updated. Tracking now shows the latest status.", "success")
            return redirect(url_for("admin.order", oid=oid))
        except ValueError as err:
            flash(str(err), "error")
    events = conn.execute(
        "SELECT * FROM events WHERE order_id=? ORDER BY id DESC", (oid,)
    ).fetchall()
    return render_template(
        "admin/order.html",
        title=oid,
        order=row,
        items=json.loads(row["items"]),
        payments=PAYMENTS,
        statuses=STATUSES,
        events=events,
    )


@admin.route("/enquiries", methods=["GET", "POST"])
@admin_required
def enquiries():
    conn = db()
    if request.method == "POST":
        status = request.form.get("status", "")
        if status not in ENQUIRY_STATUSES:
            abort(400)
        with conn:
            conn.execute(
                "UPDATE enquiries SET status=? WHERE id=?",
                (status, request.form.get("id", type=int)),
            )
        flash("Enquiry updated.", "success")
        return redirect(url_for("admin.enquiries"))
    return render_template(
        "admin/enquiries.html",
        title="Enquiries",
        rows=conn.execute("SELECT * FROM enquiries ORDER BY id DESC").fetchall(),
        statuses=ENQUIRY_STATUSES,
    )


@admin.route("/settings", methods=["GET", "POST"])
@admin_required
def content():
    conf = settings()
    if request.method == "POST":
        try:
            editable = [k for k in DEFAULTS if k != "qr_file"]
            values = {
                k: field(
                    k,
                    k.replace("_", " "),
                    600,
                    k not in ("payee", "email", "whatsapp", *SOCIALS),
                )
                for k in editable
            }
            valid_email(values["email"])
            for key, domain in SOCIALS.items():
                if values[key]:
                    url = urlsplit(values[key])
                    if (
                        url.scheme != "https"
                        or url.hostname not in (domain, "www." + domain)
                        or url.username
                        or url.password
                    ):
                        raise ValueError(
                            f"Enter a full HTTPS {key.title()} profile URL."
                        )
            number = re.sub(r"[\s()+-]", "", values["whatsapp"])
            if number and not re.fullmatch(r"[1-9][0-9]{7,14}", number):
                raise ValueError(
                    "Enter the WhatsApp number with its country code, for example 91 followed by 10 digits."
                )
            values["whatsapp"] = number
            qr = request.files.get("qr")
            if qr and qr.filename:
                values["qr_file"] = save_image(qr, qr=True)
            elif "disable_qr" in request.form:
                values["qr_file"] = "disabled"
            with db() as conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO settings VALUES (?,?)", values.items()
                )
            flash("Store settings saved.", "success")
            return redirect(url_for("admin.content"))
        except ValueError as err:
            flash(str(err), "error")
    return render_template(
        "admin/content.html", title="Store settings", conf=conf, artwork=ARTWORK
    )


@admin.post("/settings/artwork")
@admin_required
def update_artwork():
    key = request.form.get("art_key", "")
    if key not in ARTWORK:
        abort(400)
    try:
        with db() as conn:
            if request.form.get("action") == "restore":
                conn.execute("DELETE FROM settings WHERE key=?", ("art-" + key,))
            else:
                photo = request.files.get("art_file")
                if not photo or not photo.filename:
                    raise ValueError("Select an image to upload.")
                filename = save_image(photo, transparent=(key == "wordmark"))
                conn.execute(
                    "INSERT OR REPLACE INTO settings VALUES (?,?)",
                    ("art-" + key, filename),
                )
        flash("Store artwork updated.", "success")
    except ValueError as err:
        flash(str(err), "error")
    return redirect(url_for("admin.content"))


@admin.route("/reviews", methods=["GET", "POST"])
@admin_required
def manage_reviews():
    conn = db()
    if request.method == "POST":
        rid = request.form.get("id", type=int)
        try:
            with conn:
                if request.form.get("action") == "delete":
                    conn.execute("DELETE FROM reviews WHERE id=?", (rid,))
                else:
                    stars = request.form.get("stars", type=int)
                    if stars not in range(1, 6):
                        raise ValueError("Choose a rating from 1 to 5.")
                    data = (
                        field("name", "reviewer name", 90),
                        field("review", "review", 1200),
                        stars,
                        int("visible" in request.form),
                    )
                    if rid:
                        conn.execute(
                            "UPDATE reviews SET name=?,review=?,stars=?,visible=? WHERE id=?",
                            data + (rid,),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO reviews(name,review,stars,visible) VALUES (?,?,?,?)",
                            data,
                        )
            flash("Reviews updated.", "success")
            return redirect(url_for("admin.manage_reviews"))
        except ValueError as err:
            flash(str(err), "error")
    return render_template(
        "admin/reviews.html",
        title="Customer reviews",
        rows=conn.execute("SELECT * FROM reviews ORDER BY id").fetchall(),
    )


@admin.get("/reports")
@admin_required
def reports():
    try:
        start = date.fromisoformat(
            request.args.get("start", (today() - timedelta(days=29)).isoformat())
        )
        end = date.fromisoformat(request.args.get("end", today().isoformat()))
        if start > end:
            raise ValueError()
    except ValueError:
        abort(400, "Choose a valid date range.")
    rows = (
        db()
        .execute(
            "SELECT id,created,total,payment,status,items FROM orders WHERE substr(created,1,10) BETWEEN ? AND ? ORDER BY created DESC",
            (start.isoformat(), end.isoformat()),
        )
        .fetchall()
    )
    verified = [r for r in rows if r["payment"] == "Verified"]
    total = sum(r["total"] for r in verified)
    if request.args.get("download") == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            ("Order ID", "Created (IST)", "Order value (INR)", "Payment", "Fulfilment")
        )
        writer.writerows(
            (
                r["id"],
                r["created"],
                f"{r['total'] / 100:.2f}",
                r["payment"],
                r["status"],
            )
            for r in rows
        )
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=soulful-sales.csv"},
        )
    tops = {}
    for r in verified:
        for i in json.loads(r["items"]):
            key = (i["id"], i["name"])
            qty, sales = tops.get(key, (0, 0))
            tops[key] = (qty + i["qty"], sales + i["subtotal"])
    ranking = sorted(((k[1], *v) for k, v in tops.items()), key=lambda x: -x[2])
    return render_template(
        "admin/reports.html",
        title="Sales reports",
        start=start,
        end=end,
        rows=rows,
        count=len(verified),
        total=total,
        ranking=ranking,
    )
