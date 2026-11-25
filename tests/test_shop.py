"""Exercise the paths that move money, customer details and storefront content."""

import io
import re
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app import create_app
from core import DEFAULTS, db, today


def photo():
    image = io.BytesIO()
    Image.new("RGB", (90, 90), "pink").save(image, "PNG")
    image.seek(0)
    return image, "test.png"


class ShopFlow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.app = create_app(
            dict(
                TESTING=True,
                DATABASE=str(folder / "shop.db"),
                UPLOADS=str(folder / "uploads"),
            )
        )
        self.admin = self.app.test_client()
        self.buyer = self.app.test_client()
        runner = self.app.test_cli_runner()
        self.assertEqual(runner.invoke(args=["setup-code"]).exit_code, 0)
        self.assertEqual(self.admin.get("/admin/setup").status_code, 200)
        password = "only-used-in-this-temporary-shop"
        code = (folder / "setup-code.txt").read_text().strip()
        self.post(
            self.admin,
            "/admin/setup",
            code=code,
            username="studio",
            password=password,
            confirm=password,
        )
        self.assertFalse((folder / "setup-code.txt").exists())
        self.assertIn(b"Studio overview", self.admin.get("/admin/").data)
        self.assertEqual(self.buyer.get("/").status_code, 200)

    def post(self, client, url, **data):
        with client.session_transaction() as session:
            csrf = session.get("_csrf")
        return client.post(url, data={**data, "_csrf": csrf}, follow_redirects=True)

    def test_order_lifecycle(self):
        self.assertNotIn(b'class="product-card"', self.buyer.get("/").data)
        data = dict(
            name="Test rose box",
            category="hampers",
            description="A test fixture.",
            price="1499.50",
            available="on",
            featured="on",
        )
        self.post(self.admin, "/admin/products/new", **data, images=[photo(), photo()])
        self.assertIn(b"Test rose box", self.buyer.get("/").data)
        self.assertIn(b"data-gallery", self.buyer.get("/products/1").data)
        self.assertIn(
            b"Test rose box", self.post(self.buyer, "/cart/1", quantity="2").data
        )
        self.assertNotIn(b'name="utr"', self.buyer.get("/checkout").data)
        conf = {k: v for k, v in DEFAULTS.items() if k != "qr_file"}
        conf.update(whatsapp="91 90000 00000", payee="Test Studio")
        self.assertIn(
            b"Store settings saved",
            self.post(self.admin, "/admin/settings", **conf, qr=photo()).data,
        )
        page = self.buyer.get("/checkout").text
        self.assertIn("Test Studio", page)
        quote = re.search(r'name="quote"\s+value="([^"]+)"', page)[1]
        self.post(self.admin, "/admin/products/1/edit", **{**data, "price": "1800"})
        details = dict(
            quote=quote,
            name="Test Buyer",
            phone="9000000001",
            email="person@example.test",
            recipient="Test Recipient",
            address="Unit Test Street",
            city="Bengaluru",
            postcode="560001",
            delivery_date=today().isoformat(),
            message="Fixture gift note",
            utr="123456789012",
        )
        self.assertIn(
            "12-digit UPI",
            self.post(self.buyer, "/checkout", **{**details, "utr": "123"}).text,
        )
        result = self.post(self.buyer, "/checkout", **details)
        self.assertIn("Thank you, Test Buyer", result.text)
        self.assertIn("https://wa.me/919000000000", result.text)
        self.assertIn("₹2,999", result.text)
        self.assertIn("Thank you", self.post(self.buyer, "/checkout", **details).text)
        with self.app.app_context():
            orders = db().execute("SELECT * FROM orders").fetchall()
            self.assertEqual(len(orders), 1)
            order = orders[0]
            self.assertEqual(
                (order["payment"], order["total"]), ("Under review", 299900)
            )
            oid = order["id"]
        self.assertIn("Unit Test Street", self.admin.get("/admin/orders/" + oid).text)
        self.post(
            self.admin,
            "/admin/orders/" + oid,
            payment="Verified",
            status="Being prepared",
            internal_note="Studio only",
        )
        guest = self.app.test_client()
        guest.get("/")
        self.assertEqual(guest.get("/order/" + oid + "/success").status_code, 404)
        for lookup in (oid.lower(), "+91 90000 00001"):
            found = self.post(guest, "/track", lookup=lookup).text
            self.assertIn("Being prepared", found)
            self.assertIn("Verified", found)
            for private in (
                "Unit Test Street",
                "Test Buyer",
                "Studio only",
                "123456789012",
            ):
                self.assertNotIn(private, found)
        self.assertIn("2999.00", self.admin.get("/admin/reports?download=csv").text)
        self.assertIn("Test rose box", self.admin.get("/admin/reports").text)
        self.post(self.buyer, "/cart/1", quantity="1")
        new_quote = re.search(
            r'name="quote"\s+value="([^"]+)"', self.buyer.get("/checkout").text
        )[1]
        self.assertIn(
            "already been submitted",
            self.post(self.buyer, "/checkout", **{**details, "quote": new_quote}).text,
        )
        self.post(self.admin, "/admin/products/1/delete")
        self.assertEqual(self.buyer.get("/products/1").status_code, 404)
        self.assertIn("Test rose box", self.admin.get("/admin/orders/" + oid).text)

    def test_content_enquiries_and_protection(self):
        for path in (
            "/shop",
            "/collections/gifts",
            "/collections/bouquets",
            "/collections/hampers",
            "/collections/diya-gifts",
            "/collections/personalized-gifts",
            "/collections/return-gifts",
            "/reviews",
            "/contact",
            "/customize",
            "/track",
            "/cart",
        ):
            self.assertEqual(self.buyer.get(path).status_code, 200, path)
        for path in (
            "",
            "products",
            "products/new",
            "orders",
            "enquiries",
            "reports",
            "settings",
            "reviews",
            "account",
        ):
            self.assertIn(
                "Studio sign in",
                self.buyer.get("/admin/" + path, follow_redirects=True).text,
                path,
            )
            self.assertEqual(self.admin.get("/admin/" + path).status_code, 200, path)
        self.assertEqual(
            self.buyer.post("/contact", data={"name": "Fixture"}).status_code, 400
        )
        result = self.post(
            self.buyer,
            "/customize",
            name="Guest",
            phone="9000000000",
            kind="Bulk & return gifts",
            order="",
            message="Six gifts in rose wrapping.",
        )
        self.assertIn("Your enquiry is with the studio", result.text)
        self.assertIn(
            "Six gifts in rose wrapping.", self.admin.get("/admin/enquiries").text
        )
        self.post(self.admin, "/admin/enquiries", id="1", status="Resolved")
        self.assertIn(
            'class="status">Resolved', self.admin.get("/admin/enquiries").text
        )
        self.post(
            self.admin,
            "/admin/reviews",
            name="Fixture",
            review="Lovely flowers.",
            stars="4",
            visible="on",
        )
        self.assertIn("Lovely flowers.", self.buyer.get("/reviews").text)
        self.post(
            self.admin,
            "/admin/reviews",
            id="4",
            name="Fixture",
            review="Lovely flowers.",
            stars="4",
        )
        self.assertNotIn("Lovely flowers.", self.buyer.get("/reviews").text)
        self.post(
            self.admin, "/admin/settings/artwork", art_key="hero", art_file=photo()
        )
        with self.buyer.get("/art/hero.webp") as art:
            self.assertEqual(Image.open(io.BytesIO(art.data)).size, (90, 90))
        self.post(
            self.admin, "/admin/settings/artwork", art_key="hero", action="restore"
        )
        with self.buyer.get("/art/hero.webp") as art:
            self.assertEqual(Image.open(io.BytesIO(art.data)).size, (609, 306))
        self.post(self.admin, "/admin/logout")
        self.post(
            self.admin,
            "/admin/login",
            username="studio",
            password="only-used-in-this-temporary-shop",
        )
        self.assertIn("Studio overview", self.admin.get("/admin/").text)


if __name__ == "__main__":
    unittest.main()
