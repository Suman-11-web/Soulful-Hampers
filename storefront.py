import json
import re
import secrets
import sqlite3
import time
from datetime import date

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from core import (
    CATEGORIES,
    db,
    field,
    limit,
    now,
    phone_number,
    qr_url,
    settings,
    today,
    valid_email,
)

shop = Blueprint("shop", __name__)
CARD = "SELECT p.*, (SELECT filename FROM images WHERE product_id=p.id ORDER BY id LIMIT 1) AS cover FROM products p"


@shop.get("/")
def home():
    featured = (
        db()
        .execute(
            CARD
            + " WHERE deleted=0 AND featured=1 AND available=1 ORDER BY id DESC LIMIT 8"
        )
        .fetchall()
    )
    reviews = (
        db()
        .execute("SELECT * FROM reviews WHERE visible=1 ORDER BY id LIMIT 3")
        .fetchall()
    )
    return render_template("home.html", featured=featured, reviews=reviews)


@shop.get("/shop")
@shop.get("/collections/<slug>")
def catalog(slug=None):
    if slug and slug not in CATEGORIES and slug != "gifts":
        abort(404)
    query = request.args.get("q", "").strip()[:120]
    sort = request.args.get("sort", "newest")
    sql, args = CARD + " WHERE p.deleted=0", []
    if slug == "gifts":
        sql += " AND p.category IN ('diya-gifts','personalized-gifts','return-gifts')"
    elif slug:
        sql += " AND p.category=?"
        args.append(slug)
    if query:
        sql += " AND (instr(lower(p.name),lower(?))>0 OR instr(lower(p.description),lower(?))>0)"
        args.extend((query, query))
    sql += " ORDER BY " + {
        "price-low": "p.price,p.id DESC",
        "price-high": "p.price DESC,p.id DESC",
    }.get(sort, "p.id DESC")
    title, intro = CATEGORIES.get(
        slug,
        ("Gifts", "Personal touches for the people you love.")
        if slug
        else ("The Gift Shop", "Explore gifts for the people and moments that matter."),
    )
    return render_template(
        "catalog.html",
        products=db().execute(sql, args).fetchall(),
        slug=slug,
        title=title,
        intro=intro,
        query=query,
        sort=sort,
    )


@shop.get("/products/<int:pid>")
def product(pid):
    row = (
        db()
        .execute("SELECT * FROM products WHERE id=? AND deleted=0", (pid,))
        .fetchone()
    )
    if not row:
        abort(404)
    imgs = (
        db()
        .execute("SELECT * FROM images WHERE product_id=? ORDER BY id", (pid,))
        .fetchall()
    )
    return render_template("product.html", product=row, photos=imgs, title=row["name"])


def cart_lines():
    contents = session.get("cart", {})
    rows = []
    for pid, qty in list(contents.items()):
        p = (
            db()
            .execute(CARD + " WHERE p.id=? AND p.deleted=0 AND p.available=1", (pid,))
            .fetchone()
        )
        if p:
            rows.append(
                dict(
                    id=p["id"],
                    name=p["name"],
                    price=p["price"],
                    qty=qty,
                    cover=p["cover"],
                    subtotal=p["price"] * qty,
                )
            )
        else:
            del contents[pid]
            session["cart"] = contents
            flash(
                "An item is no longer available and has been removed from your bag.",
                "info",
            )
    return rows


@shop.post("/cart/<int:pid>")
def change_cart(pid):
    items = dict(session.get("cart", {}))
    action = request.form.get("action", "add")
    if action == "remove":
        items.pop(str(pid), None)
    else:
        if (
            not db()
            .execute(
                "SELECT 1 FROM products WHERE id=? AND available=1 AND deleted=0",
                (pid,),
            )
            .fetchone()
        ):
            flash("This gift is currently unavailable.", "error")
            return redirect(url_for("shop.cart"))
        qty = request.form.get("quantity", 1, type=int)
        if (
            qty is None
            or qty < 1
            or qty > 99
            or (action == "add" and items.get(str(pid), 0) + qty > 99)
        ):
            flash("Choose between 1 and 99 per gift.", "error")
            return redirect(url_for("shop.cart"))
        if str(pid) not in items and len(items) >= 40:
            flash("For a larger order, please send a bulk gifting enquiry.", "info")
            return redirect(url_for("shop.cart"))
        items[str(pid)] = (items.get(str(pid), 0) if action == "add" else 0) + qty
    session["cart"] = items
    session.pop("quote", None)
    return redirect(url_for("shop.cart"))


@shop.get("/cart")
def cart():
    lines = cart_lines()
    return render_template(
        "cart.html",
        lines=lines,
        total=sum(i["subtotal"] for i in lines),
        title="Your Bag",
    )


def get_quote():
    stamp = int(time.time())
    quote = (
        db()
        .execute(
            "SELECT * FROM quotes WHERE token=? AND created>? AND order_id IS NULL",
            (session.get("quote", ""), stamp - 86400),
        )
        .fetchone()
    )
    if quote:
        return quote
    lines = cart_lines()
    if not lines:
        return None
    token = secrets.token_urlsafe(24)
    with db() as conn:
        conn.execute(
            "DELETE FROM quotes WHERE created<? AND order_id IS NULL", (stamp - 86400,)
        )
        conn.execute(
            "INSERT INTO quotes VALUES (?,?,?,?,NULL)",
            (token, json.dumps(lines), sum(i["subtotal"] for i in lines), stamp),
        )
    session["quote"] = token
    return db().execute("SELECT * FROM quotes WHERE token=?", (token,)).fetchone()


@shop.route("/checkout", methods=["GET", "POST"])
def checkout():
    if request.method == "GET":
        q = get_quote()
        if not q:
            return redirect(url_for("shop.cart"))
    else:
        token = request.form.get("quote", "")
        if not token or token != session.get("quote"):
            if session.get("last_order") and token == session.get("last_quote"):
                return redirect(url_for("shop.success", oid=session["last_order"]))
            abort(400, "Your bag changed. Open checkout again for the current total.")
        q = db().execute("SELECT * FROM quotes WHERE token=?", (token,)).fetchone()
        if not q or q["created"] < time.time() - 86400:
            flash(
                "This checkout expired. If you have already paid, contact us with your transaction reference.",
                "error",
            )
            return redirect(url_for("shop.cart"))
        if not qr_url(settings()):
            flash(
                "The shop is setting up QR payments. Contact us to arrange your order.",
                "error",
            )
        else:
            try:
                name = field("name", "buyer name", 90)
                phone = phone_number(field("phone", "phone", 25))
                email = valid_email(field("email", "email", 180, False))
                recipient = field("recipient", "recipient name", 90, False) or name
                address = field("address", "street address", 450)
                city = field("city", "town or city", 90)
                postcode = field("postcode", "PIN code", 6)
                if not re.fullmatch("[1-9][0-9]{5}", postcode):
                    raise ValueError("Enter a valid six-digit delivery PIN code.")
                delivery = field("delivery_date", "preferred delivery date", 10)
                try:
                    valid_date = date.fromisoformat(delivery) >= today()
                except ValueError:
                    valid_date = False
                if not valid_date:
                    raise ValueError("Choose today or a future delivery date.")
                message = field("message", "gift note", 700, False)
                utr = field("utr", "UPI UTR", 12)
                if not re.fullmatch("[0-9]{12}", utr):
                    raise ValueError(
                        "Enter the 12-digit UPI reference from your payment receipt."
                    )
                conn = db()
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    existing = conn.execute(
                        "SELECT order_id FROM quotes WHERE token=?", (token,)
                    ).fetchone()
                    if not existing:
                        raise ValueError(
                            "Your checkout expired. Contact us if you have already paid."
                        )
                    oid = existing["order_id"]
                    if not oid:
                        oid = "SH-" + secrets.token_hex(6).upper()
                        conn.execute(
                            "INSERT INTO orders(id,created,name,phone,email,recipient,address,city,postcode,delivery_date,message,utr,items,total,payment,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                oid,
                                now(),
                                name,
                                phone,
                                email,
                                recipient,
                                address,
                                city,
                                postcode,
                                delivery,
                                message,
                                utr,
                                q["items"],
                                q["total"],
                                "Under review",
                                "Received",
                            ),
                        )
                        conn.execute(
                            "INSERT INTO events(order_id,happened,detail) VALUES (?,?,?)",
                            (oid, now(), "Order received for payment review."),
                        )
                        conn.execute(
                            "UPDATE quotes SET order_id=? WHERE token=?", (oid, token)
                        )
                session.update(last_order=oid, last_quote=token, cart={})
                session.pop("quote", None)
                return redirect(url_for("shop.success", oid=oid))
            except ValueError as err:
                flash(str(err), "error")
            except sqlite3.IntegrityError:
                flash(
                    "This payment reference has already been submitted. Track the existing order or contact us.",
                    "error",
                )
    return render_template(
        "checkout.html", title="Checkout", quote=q, lines=json.loads(q["items"])
    )


@shop.get("/order/<oid>/success")
def success(oid):
    if oid != session.get("last_order"):
        abort(404)
    row = db().execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not row:
        abort(404)
    return render_template("success.html", order=row, title="Order received")


@shop.route("/track", methods=["GET", "POST"])
def track():
    query, rows = "", []
    if request.method == "POST":
        limit("track", 30, 600)
        query = request.form.get("lookup", "").strip()[:60]
        try:
            phone = phone_number(query)
        except ValueError:
            phone = ""
        rows = (
            db()
            .execute(
                "SELECT id,created,payment,status FROM orders WHERE id=? OR phone=? ORDER BY created DESC",
                (query.upper(), phone),
            )
            .fetchall()
        )
    return render_template(
        "track.html", title="Track Your Order", query=query, rows=rows
    )


@shop.get("/reviews")
def reviews():
    return render_template(
        "reviews.html",
        title="Customer Reviews",
        reviews=db()
        .execute("SELECT * FROM reviews WHERE visible=1 ORDER BY id")
        .fetchall(),
    )


@shop.route("/contact", methods=["GET", "POST"])
@shop.route("/customize", methods=["GET", "POST"])
def enquiry():
    custom = request.path == "/customize"
    ref = request.values.get("order", "").strip()[:40]
    if request.method == "POST":
        limit("enquiry", 12, 600)
        try:
            name = field("name", "name", 90)
            phone = phone_number(field("phone", "phone", 25))
            message = field("message", "message", 2000)
            kind = field("kind", "enquiry type", 40)
            if kind not in (
                "Custom gift",
                "Bulk & return gifts",
                "Order enquiry",
                "Delivery",
                "General enquiry",
            ):
                raise ValueError("Choose an enquiry type.")
            with db() as conn:
                conn.execute(
                    "INSERT INTO enquiries(created,kind,name,phone,message,order_ref) VALUES (?,?,?,?,?,?)",
                    (now(), kind, name, phone, message, ref),
                )
            flash(
                "Your enquiry is with the studio. We’ll get in touch using the number you shared.",
                "success",
            )
            return redirect(
                url_for("shop.enquiry", _external=False) if custom else "/contact"
            )
        except ValueError as err:
            flash(str(err), "error")
    return render_template(
        "enquiry.html",
        title="Customize Your Gift" if custom else "Get in Touch",
        custom=custom,
        ref=ref,
    )
