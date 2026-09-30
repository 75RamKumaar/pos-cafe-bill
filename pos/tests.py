import json
import re
from decimal import Decimal
from django.conf import settings
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from pos.forms import MenuItemForm
from pos.models import Bill, BillItem, Customer, Expense, Product, KhataTransaction, AuditLog
from pos.views import parse_decimal, TWO_PLACES
import pos.urls


class POSRoleAccessTests(TestCase):
    """
    Test suite asserting access and HTTP status codes for every URL in pos/urls.py
    for Anonymous, Cashier, and Owner roles.
    """

    OWNER_ONLY_URLS = {
        "bill_delete",
        "menu_items",
        "menu_item_create",
        "menu_item_edit",
        "menu_item_delete",
        "customer_edit",
        "customer_delete",
        "expenses",
        "expense_create",
        "expense_edit",
        "expense_delete",
        "dashboard",
        "reports",
        "export_sales_csv",
        "settings",
        "audit_logs",
    }

    EXPECTED_CASHIER_STATUS = {
        "pos": 302,
        "billing": 200,
        "bill_create": 405,
        "billing_history": 200,
        "customer_search": 200,
        "bill_detail": 200,
        "customers": 200,
        "customer_create": 200,
        "customer_detail": 200,
        "khata": 200,
        "khata_payment": 302,
    }

    EXPECTED_OWNER_STATUS = {
        "pos": 302,
        "billing": 200,
        "bill_create": 405,
        "billing_history": 200,
        "customer_search": 200,
        "bill_detail": 200,
        "bill_delete": 302,
        "menu_items": 200,
        "menu_item_create": 200,
        "menu_item_edit": 200,
        "menu_item_delete": 302,
        "customers": 200,
        "customer_create": 200,
        "customer_detail": 200,
        "customer_edit": 200,
        "customer_delete": 302,
        "khata": 200,
        "khata_payment": 302,
        "expenses": 200,
        "expense_create": 200,
        "expense_edit": 200,
        "expense_delete": 302,
        "dashboard": 200,
        "reports": 200,
        "export_sales_csv": 200,
        "settings": 200,
        "audit_logs": 200,
    }

    @classmethod
    def setUpTestData(cls):
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")

        cls.cashier_user = User.objects.create_user(
            username="test_cashier",
            password="cashierpassword123",
        )
        cls.cashier_user.groups.add(cls.cashier_group)

        cls.owner_user = User.objects.create_user(
            username="test_owner",
            password="ownerpassword123",
        )
        cls.owner_user.groups.add(cls.owner_group)

        cls.product = Product.objects.create(
            name="Test Coffee",
            category="Beverages",
            price=Decimal("40.00"),
            cost_price=Decimal("15.00"),
            stock=Decimal("50.00"),
        )
        cls.customer = Customer.objects.create(
            name="Test Customer",
            phone="9876543210",
        )
        cls.bill = Bill.objects.create(
            bill_number="BILL-TEST-1001",
            customer=cls.customer,
            subtotal=Decimal("40.00"),
            discount=Decimal("0.00"),
            grand_total=Decimal("40.00"),
            payment_method=Bill.CASH,
        )
        BillItem.objects.create(
            bill=cls.bill,
            menu_item=cls.product,
            item_name=cls.product.name,
            price=cls.product.price,
            cost_price=cls.product.cost_price,
            quantity=Decimal("1.00"),
            total=Decimal("40.00"),
        )
        cls.expense = Expense.objects.create(
            title="Coffee Beans",
            category="Supplies",
            amount=Decimal("500.00"),
            expense_date=timezone.localdate(),
        )

    def get_url(self, name):
        kwargs = {}
        if name in {"bill_detail", "bill_delete"}:
            kwargs = {"bill_id": self.bill.id}
        elif name in {"menu_item_edit", "menu_item_delete"}:
            kwargs = {"item_id": self.product.id}
        elif name in {"customer_detail", "customer_edit", "customer_delete", "khata_payment"}:
            kwargs = {"customer_id": self.customer.id}
        elif name in {"expense_edit", "expense_delete"}:
            kwargs = {"expense_id": self.expense.id}
        return reverse(name, kwargs=kwargs)

    def test_anonymous_role_access(self):
        """Anonymous users must be redirected (302) to login for every URL in pos/urls.py."""
        self.client.logout()
        for pattern in pos.urls.urlpatterns:
            url_name = pattern.name
            with self.subTest(role="anonymous", url_name=url_name):
                url = self.get_url(url_name)
                response = self.client.get(url)
                self.assertEqual(
                    response.status_code,
                    302,
                    f"Anonymous user should receive 302 redirect for {url_name} ({url}), got {response.status_code}",
                )

    def test_cashier_role_access(self):
        """
        Cashier users must receive 403 Forbidden for Owner-only URLs,
        and permitted status codes for Cashier-accessible URLs.
        """
        self.client.login(username="test_cashier", password="cashierpassword123")
        for pattern in pos.urls.urlpatterns:
            url_name = pattern.name
            with self.subTest(role="cashier", url_name=url_name):
                url = self.get_url(url_name)
                response = self.client.get(url)
                if url_name in self.OWNER_ONLY_URLS:
                    self.assertEqual(
                        response.status_code,
                        403,
                        f"Cashier should receive 403 Forbidden for Owner-only page {url_name} ({url}), got {response.status_code}",
                    )
                else:
                    expected_status = self.EXPECTED_CASHIER_STATUS.get(url_name, 200)
                    self.assertEqual(
                        response.status_code,
                        expected_status,
                        f"Cashier should receive {expected_status} for {url_name} ({url}), got {response.status_code}",
                    )

    def test_owner_role_access(self):
        """Owner users must be allowed access to every URL in pos/urls.py with expected status codes."""
        self.client.login(username="test_owner", password="ownerpassword123")
        for pattern in pos.urls.urlpatterns:
            url_name = pattern.name
            with self.subTest(role="owner", url_name=url_name):
                url = self.get_url(url_name)
                response = self.client.get(url)
                expected_status = self.EXPECTED_OWNER_STATUS.get(url_name, 200)
                self.assertEqual(
                    response.status_code,
                    expected_status,
                    f"Owner should receive {expected_status} for {url_name} ({url}), got {response.status_code}",
                )


class ParseDecimalUnitTests(TestCase):
    """Unit tests for the parse_decimal helper function."""

    def test_valid_decimal_inputs(self):
        self.assertEqual(parse_decimal("10.50"), Decimal("10.50"))
        self.assertEqual(parse_decimal(15), Decimal("15.00"))
        self.assertEqual(parse_decimal(Decimal("25.75")), Decimal("25.75"))

    def test_quantize_round_half_up(self):
        self.assertEqual(parse_decimal("1.005"), Decimal("1.01"))
        self.assertEqual(parse_decimal("1.004"), Decimal("1.00"))
        self.assertEqual(parse_decimal("1.015"), Decimal("1.02"))

    def test_reject_non_finite_values(self):
        for invalid in ["NaN", "nan", "Infinity", "-Infinity", "+Infinity", "sNaN"]:
            with self.assertRaises(ValueError):
                parse_decimal(invalid)

    def test_range_check_before_quantize_overflow(self):
        # 1e30 overflows quantize(0.01) if not checked beforehand
        with self.assertRaises(ValueError):
            parse_decimal("1e30")
        with self.assertRaises(ValueError):
            parse_decimal("-1e30")

    def test_min_and_max_value_constraints(self):
        with self.assertRaises(ValueError):
            parse_decimal("-5.00", min_value=Decimal("0.00"))
        with self.assertRaises(ValueError):
            parse_decimal("10000.00", max_value=Decimal("9999.00"))
        self.assertEqual(
            parse_decimal("9999.00", min_value=Decimal("0.00"), max_value=Decimal("9999.00")),
            Decimal("9999.00"),
        )

    def test_reject_empty_or_invalid_strings(self):
        for bad in [None, "", "abc", "   ", "12.34.56"]:
            with self.assertRaises(ValueError):
                parse_decimal(bad)


class BillCreateBugFixTests(TestCase):
    """
    Tests covering the bug fixes in bill_create:
    - Malformed inputs (discount: 'NaN', quantity: 'NaN', items: 5, customer_id: 'abc') returning 400.
    - Preventing overselling and returning informative error with product name and available stock.
    - Row locking and atomic F('stock') - qty decrement.
    - SQLite transaction_mode IMMEDIATE setting.
    - Fractional quantities quantized identically for quantity and line_total.
    - Merging duplicate product IDs, capping at 100 lines and capping qty at 9999.
    - Per-day sequence bill numbers (e.g. 20260929-0007).
    - Preserving JSON API response shape.
    """

    @classmethod
    def setUpTestData(cls):
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")
        cls.user = User.objects.create_user(username="cashier_tester", password="password123")
        cls.user.groups.add(cls.cashier_group)

        cls.product1 = Product.objects.create(
            name="Masala Tea",
            category="Beverages",
            price=Decimal("20.00"),
            cost_price=Decimal("8.00"),
            stock=Decimal("50.00"),
            is_active=True,
        )
        cls.product2 = Product.objects.create(
            name="Bun Maska",
            category="Snacks",
            price=Decimal("35.00"),
            cost_price=Decimal("16.00"),
            stock=Decimal("5.00"),
            is_active=True,
        )
        cls.customer = Customer.objects.create(
            name="Suresh Kumar",
            phone="9000000005",
        )

    def setUp(self):
        self.client.login(username="cashier_tester", password="password123")
        self.url = reverse("bill_create")

    def test_sqlite_transaction_mode_immediate_configured(self):
        """Verify DATABASES default has OPTIONS transaction_mode set to IMMEDIATE."""
        db_opts = settings.DATABASES["default"].get("OPTIONS", {})
        self.assertEqual(db_opts.get("transaction_mode"), "IMMEDIATE")

    def test_malformed_inputs_return_http_400(self):
        """Every malformed input must return HTTP 400 with a JSON error."""
        test_cases = [
            # discount: "NaN"
            (
                {"items": [{"id": self.product1.id, "quantity": 1}], "payment_method": "CASH", "discount": "NaN"},
                "discount: 'NaN'",
            ),
            # discount: 1e30 overflow
            (
                {"items": [{"id": self.product1.id, "quantity": 1}], "payment_method": "CASH", "discount": "1e30"},
                "discount: 1e30",
            ),
            # quantity: "NaN"
            (
                {"items": [{"id": self.product1.id, "quantity": "NaN"}], "payment_method": "CASH"},
                "quantity: 'NaN'",
            ),
            # quantity: 1e30 overflow
            (
                {"items": [{"id": self.product1.id, "quantity": "1e30"}], "payment_method": "CASH"},
                "quantity: 1e30",
            ),
            # items: 5 (not a list)
            (
                {"items": 5, "payment_method": "CASH"},
                "items: 5",
            ),
            # items: "abc"
            (
                {"items": "abc", "payment_method": "CASH"},
                "items: 'abc'",
            ),
            # customer_id: "abc"
            (
                {"items": [{"id": self.product1.id, "quantity": 1}], "payment_method": "CASH", "customer_id": "abc"},
                "customer_id: 'abc'",
            ),
            # non-existent customer_id
            (
                {"items": [{"id": self.product1.id, "quantity": 1}], "payment_method": "CASH", "customer_id": 999999},
                "non-existent customer_id",
            ),
            # empty items list
            (
                {"items": [], "payment_method": "CASH"},
                "empty items list",
            ),
            # invalid item element (missing/non-int id)
            (
                {"items": [{"id": "bad_id", "quantity": 1}], "payment_method": "CASH"},
                "bad product id",
            ),
            # invalid payment method
            (
                {"items": [{"id": self.product1.id, "quantity": 1}], "payment_method": "BITCOIN"},
                "invalid payment method",
            ),
            # negative discount
            (
                {"items": [{"id": self.product1.id, "quantity": 1}], "payment_method": "CASH", "discount": "-5"},
                "negative discount",
            ),
            # discount exceeds subtotal
            (
                {"items": [{"id": self.product1.id, "quantity": 1}], "payment_method": "CASH", "discount": "100.00"},
                "discount exceeds subtotal",
            ),
        ]

        for payload, description in test_cases:
            with self.subTest(case=description):
                response = self.client.post(
                    self.url,
                    data=json.dumps(payload),
                    content_type="application/json",
                )
                self.assertEqual(
                    response.status_code,
                    400,
                    f"Expected 400 for {description}, got {response.status_code}: {response.content}",
                )
                data = response.json()
                self.assertIn("error", data, f"Response missing 'error' key for {description}")

    def test_overselling_rejected_with_product_name_and_available_qty(self):
        """Reject bill if product has less stock than requested, naming product and available stock."""
        initial_stock = self.product2.stock  # 5.00
        payload = {
            "items": [{"id": self.product2.id, "quantity": 10}],
            "payment_method": "CASH",
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)
        # Message must name the product and available quantity
        error_msg = data["error"]
        self.assertIn(self.product2.name, error_msg)
        self.assertIn(str(initial_stock), error_msg)

        # Confirm stock was NOT decremented
        self.product2.refresh_from_db()
        self.assertEqual(self.product2.stock, initial_stock)
        self.assertEqual(Bill.objects.count(), 0)

    def test_stock_update_decrements_with_f_expression(self):
        """Valid order must decrement stock by the purchased quantity using atomic F expression."""
        initial_stock = self.product2.stock  # 5.00
        qty_to_buy = Decimal("3.00")
        payload = {
            "items": [{"id": self.product2.id, "quantity": str(qty_to_buy)}],
            "payment_method": "CASH",
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.product2.refresh_from_db()
        self.assertEqual(self.product2.stock, initial_stock - qty_to_buy)

    def test_fractional_quantities_quantized_identically(self):
        """qty 1.005 is quantized to 1.01 and line total is calculated consistently."""
        payload = {
            "items": [{"id": self.product1.id, "quantity": "1.005"}],
            "payment_method": "CASH",
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        bill_data = response.json()
        bill = Bill.objects.get(id=bill_data["bill_id"])

        item = bill.items.first()
        # 1.005 rounded half up -> 1.01
        self.assertEqual(item.quantity, Decimal("1.01"))
        # price 20.00 * 1.01 = 20.20
        self.assertEqual(item.total, Decimal("20.20"))
        self.assertEqual(bill.subtotal, Decimal("20.20"))
        self.assertEqual(bill.grand_total, Decimal("20.20"))

        # Verify product stock decremented by quantized quantity 1.01
        self.product1.refresh_from_db()
        self.assertEqual(self.product1.stock, Decimal("50.00") - Decimal("1.01"))

    def test_merge_duplicate_product_ids(self):
        """Duplicate product IDs in items list must be merged into a single line item."""
        payload = {
            "items": [
                {"id": self.product1.id, "quantity": "2.00"},
                {"id": self.product2.id, "quantity": "1.00"},
                {"id": self.product1.id, "quantity": "3.00"},
            ],
            "payment_method": "UPI",
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        bill_data = response.json()
        bill = Bill.objects.get(id=bill_data["bill_id"])

        # Should only have 2 distinct BillItems
        self.assertEqual(bill.items.count(), 2)

        p1_item = bill.items.get(menu_item=self.product1)
        # 2 + 3 = 5
        self.assertEqual(p1_item.quantity, Decimal("5.00"))
        self.assertEqual(p1_item.total, Decimal("100.00"))

        # Stock check
        self.product1.refresh_from_db()
        self.assertEqual(self.product1.stock, Decimal("45.00"))

    def test_cap_at_100_lines(self):
        """Reject bills with more than 100 lines."""
        # Create 101 raw items
        many_items = [{"id": self.product1.id, "quantity": "1"} for _ in range(101)]
        payload = {
            "items": many_items,
            "payment_method": "CASH",
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_cap_quantity_at_9999(self):
        """Reject line quantities or merged quantities exceeding 9999."""
        # Single line > 9999
        payload1 = {
            "items": [{"id": self.product1.id, "quantity": "10000"}],
            "payment_method": "CASH",
        }
        response1 = self.client.post(
            self.url,
            data=json.dumps(payload1),
            content_type="application/json",
        )
        self.assertEqual(response1.status_code, 400)

        # Merged lines > 9999
        payload2 = {
            "items": [
                {"id": self.product1.id, "quantity": "5000"},
                {"id": self.product1.id, "quantity": "5000"},
            ],
            "payment_method": "CASH",
        }
        response2 = self.client.post(
            self.url,
            data=json.dumps(payload2),
            content_type="application/json",
        )
        self.assertEqual(response2.status_code, 400)

    def test_per_day_bill_number_sequence(self):
        """Bill numbers must be formatted as YYYYMMDD-XXXX (e.g. 20260929-0001, 20260929-0002)."""
        today_str = f"{timezone.localdate():%Y%m%d}"

        # Create first bill
        resp1 = self.client.post(
            self.url,
            data=json.dumps({"items": [{"id": self.product1.id, "quantity": 1}], "payment_method": "CASH"}),
            content_type="application/json",
        )
        self.assertEqual(resp1.status_code, 200)
        num1 = resp1.json()["bill_number"]
        self.assertEqual(num1, f"{today_str}-0001")

        # Create second bill
        resp2 = self.client.post(
            self.url,
            data=json.dumps({"items": [{"id": self.product1.id, "quantity": 1}], "payment_method": "CARD"}),
            content_type="application/json",
        )
        self.assertEqual(resp2.status_code, 200)
        num2 = resp2.json()["bill_number"]
        self.assertEqual(num2, f"{today_str}-0002")

        # Verify pattern regex: 8 digits, hyphen, 4+ digits
        self.assertTrue(re.match(r"^\d{8}-\d{4}$", num1))
        self.assertTrue(re.match(r"^\d{8}-\d{4}$", num2))

    def test_json_api_response_shape(self):
        """Successful response must preserve the expected JSON shape."""
        payload = {
            "items": [{"id": self.product1.id, "quantity": 2}],
            "payment_method": "CASH",
            "customer_id": self.customer.id,
            "discount": "5.00",
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("ok"))
        self.assertIn("bill_id", data)
        self.assertIn("bill_number", data)
        self.assertEqual(data.get("total"), "35.00")  # (20 * 2) - 5 = 35.00
        self.assertIn("detail_url", data)
        self.assertTrue(data["detail_url"].endswith("?print=1"))


class KhataCheckoutTests(TestCase):
    """
    Tests covering Khata credit checkout:
    - khata sale creates credit transaction
    - no-customer khata -> 400
    - deleting the bill restores stock, cascades credit transaction, zeroes balance, and logs reversal
    """

    @classmethod
    def setUpTestData(cls):
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")

        cls.cashier = User.objects.create_user(username="khata_cashier", password="password123")
        cls.cashier.groups.add(cls.cashier_group)

        cls.owner = User.objects.create_user(username="khata_owner", password="password123")
        cls.owner.groups.add(cls.owner_group)

        cls.product = Product.objects.create(
            name="Filter Coffee",
            category="Beverages",
            price=Decimal("25.00"),
            cost_price=Decimal("10.00"),
            stock=Decimal("30.00"),
            is_active=True,
        )
        cls.customer = Customer.objects.create(
            name="Ramesh Gupta",
            phone="9876501234",
            opening_balance=Decimal("0.00"),
        )

    def test_khata_sale_requires_customer_returns_400(self):
        """Khata payment without a customer must return 400."""
        self.client.login(username="khata_cashier", password="password123")
        payload = {
            "items": [{"id": self.product.id, "quantity": 2}],
            "payment_method": Bill.KHATA,
            "customer_id": None,
        }
        response = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)
        self.assertIn("customer", data["error"].lower())

    def test_khata_sale_creates_credit_and_updates_customer_balance(self):
        """Khata payment creates a Bill with created_by and a KhataTransaction(kind='CREDIT')."""
        self.client.login(username="khata_cashier", password="password123")
        payload = {
            "items": [{"id": self.product.id, "quantity": 3}],
            "payment_method": Bill.KHATA,
            "customer_id": self.customer.id,
            "discount": "5.00",
        }
        # 3 * 25.00 = 75.00 - 5.00 = 70.00
        response = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        bill_id = response.json()["bill_id"]
        bill = Bill.objects.get(id=bill_id)

        self.assertEqual(bill.payment_method, Bill.KHATA)
        self.assertEqual(bill.created_by, self.cashier)
        self.assertEqual(bill.grand_total, Decimal("70.00"))

        # Verify KhataTransaction created
        tx = KhataTransaction.objects.get(bill=bill)
        self.assertEqual(tx.customer, self.customer)
        self.assertEqual(tx.kind, KhataTransaction.CREDIT)
        self.assertEqual(tx.amount, Decimal("70.00"))

        # Verify Customer balance reflects credit
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("70.00"))

        # Verify product stock decremented
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal("27.00"))

    def test_deleting_khata_bill_restores_stock_and_zeroes_balance(self):
        """Deleting a KHATA bill restores stock, cascades the credit, and zeroes the balance."""
        self.client.login(username="khata_cashier", password="password123")
        payload = {
            "items": [{"id": self.product.id, "quantity": 4}],
            "payment_method": Bill.KHATA,
            "customer_id": self.customer.id,
        }
        # 4 * 25.00 = 100.00
        resp = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        bill_id = resp.json()["bill_id"]
        bill = Bill.objects.get(id=bill_id)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("100.00"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal("26.00"))

        # Delete the bill as Owner
        self.client.login(username="khata_owner", password="password123")
        delete_resp = self.client.post(reverse("bill_delete", kwargs={"bill_id": bill.id}))
        self.assertEqual(delete_resp.status_code, 302)

        # Verify Bill is deleted
        self.assertFalse(Bill.objects.filter(id=bill_id).exists())

        # Verify KhataTransaction was cascade deleted
        self.assertFalse(KhataTransaction.objects.filter(bill_id=bill_id).exists())

        # Verify customer balance is back to 0.00 (zeroed)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("0.00"))

        # Verify stock was restored to 30.00
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal("30.00"))

        # Verify audit log details contain khata_credit_reversed = True
        audit_entry = AuditLog.objects.filter(action="DELETE", model_name="Bill").latest("id")
        self.assertTrue(audit_entry.details.get("khata_credit_reversed"))


class CustomerSecurityAndEscapingTests(TestCase):
    """
    Tests ensuring HTML escaping and security:
    - Customer named '<script>alert(1)</script>' is escaped on /customers/
    - base.html logout form uses POST with CSRF token
    """

    @classmethod
    def setUpTestData(cls):
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")
        cls.cashier = User.objects.create_user(username="xss_cashier", password="password123")
        cls.cashier.groups.add(cls.cashier_group)

        cls.xss_customer = Customer.objects.create(
            name="<script>alert(1)</script>",
            phone="9123456780",
        )

    def test_customer_name_xss_escaped_on_customers_page(self):
        """A customer named <script>alert(1)</script> must be escaped on /customers/."""
        self.client.login(username="xss_cashier", password="password123")
        response = self.client.get(reverse("customers"))
        self.assertEqual(response.status_code, 200)

        content = response.content.decode("utf-8")
        # Escaped script tags must be present in the table
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", content)
        # Raw unescaped script tag must NOT be present
        self.assertNotIn("<script>alert(1)</script>", content)

    def test_base_template_logout_is_post_form(self):
        """base.html should render logout as a POST form with csrf_token instead of an <a> link."""
        self.client.login(username="xss_cashier", password="password123")
        response = self.client.get(reverse("billing"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertNotIn('href="/logout/"', content)
        self.assertIn('action="/logout/"', content)
        self.assertIn('method="post"', content)


class MenuItemFormTests(TestCase):
    """
    Tests covering MenuItemForm enhancements:
    - stock (min 0) added to form and menu_items.html table
    - price min 0.01, cost_price min 0
    - shortcut duplicate check moved to clean():
      - inactive item may reuse an active shortcut
      - active duplicate rejected case-insensitively
    - image upload:
      - whitelist extensions (.jpg .jpeg .png .webp .gif)
      - 'x.html' upload rejected
      - validate only NEW uploads (editing an item whose file is missing does not 500)
    """

    @classmethod
    def setUpTestData(cls):
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")
        cls.owner = User.objects.create_user(username="menu_owner", password="password123")
        cls.owner.groups.add(cls.owner_group)

        cls.active_product = Product.objects.create(
            name="Espresso",
            category="Beverages",
            shortcut_key="ESP",
            price=Decimal("30.00"),
            cost_price=Decimal("10.00"),
            stock=Decimal("20.00"),
            is_active=True,
        )

    def test_stock_field_present_and_saved(self):
        """Form includes stock field with min_value 0, and saves properly."""
        form_data = {
            "name": "Cappuccino",
            "category": "Beverages",
            "shortcut_key": "CAP",
            "price": "45.00",
            "cost_price": "15.00",
            "stock": "25.00",
            "is_active": True,
        }
        form = MenuItemForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        product = form.save()
        self.assertEqual(product.stock, Decimal("25.00"))

        # Negative stock rejected
        bad_form = MenuItemForm(data={**form_data, "stock": "-1.00"})
        self.assertFalse(bad_form.is_valid())
        self.assertIn("stock", bad_form.errors)

    def test_stock_column_rendered_in_menu_items_html(self):
        """menu_items.html should include a Stock column in header and body."""
        self.client.login(username="menu_owner", password="password123")
        response = self.client.get(reverse("menu_items"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("<th>Stock</th>", content)
        self.assertIn(f"<td>{self.active_product.stock:.2f}</td>", content)

    def test_price_min_0_01_and_cost_price_min_0(self):
        """price must be at least 0.01; cost_price must be at least 0."""
        base_data = {
            "name": "Latte",
            "category": "Beverages",
            "stock": "10.00",
            "is_active": True,
        }

        # Price 0 is rejected (must be at least 0.01)
        form_zero_price = MenuItemForm(data={**base_data, "price": "0.00", "cost_price": "5.00"})
        self.assertFalse(form_zero_price.is_valid())
        self.assertIn("price", form_zero_price.errors)

        # Price negative rejected
        form_neg_price = MenuItemForm(data={**base_data, "price": "-1.00", "cost_price": "5.00"})
        self.assertFalse(form_neg_price.is_valid())
        self.assertIn("price", form_neg_price.errors)

        # Cost price negative rejected
        form_neg_cost = MenuItemForm(data={**base_data, "price": "50.00", "cost_price": "-0.01"})
        self.assertFalse(form_neg_cost.is_valid())
        self.assertIn("cost_price", form_neg_cost.errors)

        # Cost price 0 is accepted
        form_zero_cost = MenuItemForm(data={**base_data, "price": "50.00", "cost_price": "0.00"})
        self.assertTrue(form_zero_cost.is_valid(), form_zero_cost.errors)

    def test_inactive_item_may_reuse_an_active_shortcut(self):
        """An inactive item may have the same shortcut key as an active item."""
        form_data = {
            "name": "Old Espresso Inactive",
            "category": "Beverages",
            "shortcut_key": "ESP",  # self.active_product has ESP and is_active=True
            "price": "30.00",
            "cost_price": "10.00",
            "stock": "0.00",
            "is_active": False,
        }
        form = MenuItemForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        product = form.save()
        self.assertEqual(product.shortcut_key, "ESP")
        self.assertFalse(product.is_active)

    def test_active_duplicate_shortcut_rejected_case_insensitively(self):
        """An active item with duplicate shortcut is rejected case-insensitively."""
        form_data = {
            "name": "Duplicate Espresso",
            "category": "Beverages",
            "shortcut_key": "esp",  # self.active_product has ESP and is active
            "price": "35.00",
            "cost_price": "12.00",
            "stock": "10.00",
            "is_active": True,
        }
        form = MenuItemForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn("shortcut_key", form.errors)
        self.assertIn("already assigned", form.errors["shortcut_key"][0])

    def test_image_upload_x_html_rejected(self):
        """Uploading a file named 'x.html' must be rejected."""
        fake_html = SimpleUploadedFile("x.html", b"<html><body>Malicious</body></html>", content_type="text/html")
        form_data = {
            "name": "Iced Tea",
            "category": "Beverages",
            "price": "25.00",
            "cost_price": "5.00",
            "stock": "10.00",
            "is_active": True,
        }
        form = MenuItemForm(data=form_data, files={"image": fake_html})
        self.assertFalse(form.is_valid())
        self.assertIn("image", form.errors)
        self.assertIn("Unsupported file extension", form.errors["image"][0])

    def test_editing_item_with_missing_image_file_does_not_500(self):
        """Editing an existing item whose image file is missing from disk must succeed without 500."""
        # Create product with a reference to an image file that does not exist on disk
        ghost_item = Product.objects.create(
            name="Ghost Item",
            category="Beverages",
            price=Decimal("20.00"),
            cost_price=Decimal("5.00"),
            stock=Decimal("10.00"),
            image="menu_items/non_existent_image_12345.jpg",
            is_active=True,
        )

        self.client.login(username="menu_owner", password="password123")
        url = reverse("menu_item_edit", kwargs={"item_id": ghost_item.id})

        # GET should succeed (200)
        get_resp = self.client.get(url)
        self.assertEqual(get_resp.status_code, 200)

        # POST to update without uploading a new image
        post_resp = self.client.post(url, {
            "name": "Ghost Item Renamed",
            "category": "Beverages",
            "shortcut_key": "GHO",
            "price": "22.00",
            "cost_price": "6.00",
            "stock": "15.00",
            "is_active": "on",
        })
        # Should redirect to menu_items, not crash with 500
        self.assertEqual(post_resp.status_code, 302)

        ghost_item.refresh_from_db()
        self.assertEqual(ghost_item.name, "Ghost Item Renamed")
        self.assertEqual(ghost_item.stock, Decimal("15.00"))


class DateValidationAndToastTests(TestCase):
    """
    Tests ensuring invalid date parameters return 200 with a toast message:
    - ?date=abc on /billing/history/ returns 200 with "Enter a valid date" toast
    - ?date=abc on /expenses/ returns 200 with "Enter a valid date" toast
    """

    @classmethod
    def setUpTestData(cls):
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")
        cls.owner = User.objects.create_user(username="date_owner", password="password123")
        cls.owner.groups.add(cls.owner_group)

    def test_invalid_date_on_billing_history_shows_toast_returns_200(self):
        self.client.login(username="date_owner", password="password123")
        response = self.client.get(reverse("billing_history"), {"date": "abc"})
        self.assertEqual(response.status_code, 200)
        messages_list = list(response.context["messages"])
        self.assertTrue(any("Enter a valid date" in str(m) for m in messages_list))

    def test_invalid_date_on_expenses_shows_toast_returns_200(self):
        self.client.login(username="date_owner", password="password123")
        response = self.client.get(reverse("expenses"), {"date": "abc"})
        self.assertEqual(response.status_code, 200)
        messages_list = list(response.context["messages"])
        self.assertTrue(any("Enter a valid date" in str(m) for m in messages_list))


class PaginationAndUrlReplaceTests(TestCase):
    """
    Tests for pagination (50/page) on:
    - billing history
    - customers
    - expenses
    - audit logs
    And ?page=99999 and ?page=abc returning 200.
    And url_replace preserving other query params.
    """

    @classmethod
    def setUpTestData(cls):
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")
        cls.owner = User.objects.create_user(username="page_owner", password="password123")
        cls.owner.groups.add(cls.owner_group)

        # Create dummy records to test pagination
        for i in range(55):
            Customer.objects.create(name=f"Customer {i:03d}", phone=f"900000{i:04d}")

    def test_page_out_of_range_and_invalid_return_200(self):
        self.client.login(username="page_owner", password="password123")
        endpoints = [
            reverse("billing_history"),
            reverse("customers"),
            reverse("expenses"),
            reverse("audit_logs"),
        ]
        for url in endpoints:
            with self.subTest(url=url, page="99999"):
                resp = self.client.get(url, {"page": "99999"})
                self.assertEqual(resp.status_code, 200)

            with self.subTest(url=url, page="abc"):
                resp = self.client.get(url, {"page": "abc"})
                self.assertEqual(resp.status_code, 200)

    def test_pagination_renders_controls_and_uses_url_replace(self):
        self.client.login(username="page_owner", password="password123")
        # 55 customers means 2 pages
        resp = self.client.get(reverse("customers"), {"q": "Customer"})
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn("Page <b>1</b> of <b>2</b>", content)
        # Verify url_replace preserved "q=Customer" and has page=2
        self.assertIn("q=Customer", content)
        self.assertIn("page=2", content)


class CustomerOutstandingQueryOptimizationTests(TestCase):
    """
    Tests ensuring CustomerQuerySet.with_outstanding():
    - query count stays constant when rendering 15 customers
    - ORDER BY name is maintained without UnorderedObjectListWarning
    - works in /customers/, /khata/ and dashboard
    """

    @classmethod
    def setUpTestData(cls):
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")
        cls.owner = User.objects.create_user(username="nplusone_owner", password="password123")
        cls.owner.groups.add(cls.owner_group)

    def test_assert_num_queries_stays_constant_with_15_customers(self):
        self.client.login(username="nplusone_owner", password="password123")

        # 1 customer with transactions
        c1 = Customer.objects.create(name="Customer 01", opening_balance=Decimal("10.00"))
        KhataTransaction.objects.create(customer=c1, kind=KhataTransaction.CREDIT, amount=Decimal("50.00"))
        KhataTransaction.objects.create(customer=c1, kind=KhataTransaction.PAYMENT, amount=Decimal("20.00"))

        # Warm session/auth cache
        self.client.get(reverse("customers"))

        # Measure baseline query count on /customers/ with 1 customer
        with CaptureQueriesContext(connection) as baseline_ctx:
            self.client.get(reverse("customers"))
        baseline_queries = len(baseline_ctx)

        # Now create 14 more customers (total 15 customers)
        for i in range(2, 16):
            c = Customer.objects.create(name=f"Customer {i:02d}", opening_balance=Decimal(f"{i * 5}.00"))
            KhataTransaction.objects.create(customer=c, kind=KhataTransaction.CREDIT, amount=Decimal("30.00"))
            KhataTransaction.objects.create(customer=c, kind=KhataTransaction.PAYMENT, amount=Decimal("10.00"))

        # Query count with 15 customers MUST STAY EXACTLY THE SAME as baseline (no per-row N+1 queries)
        with self.assertNumQueries(baseline_queries):
            resp = self.client.get(reverse("customers"))
            self.assertEqual(resp.status_code, 200)

        # Baseline and query count for /khata/
        self.client.get(reverse("khata"))
        with CaptureQueriesContext(connection) as khata_ctx:
            resp_khata = self.client.get(reverse("khata"))
            self.assertEqual(resp_khata.status_code, 200)
        # Verify khata query count is constant and does not perform per-row aggregate queries
        self.assertEqual(len(khata_ctx), 5)

    def test_dashboard_uses_with_outstanding_and_returns_200(self):
        self.client.login(username="nplusone_owner", password="password123")
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("total_outstanding", response.context)
        content = response.content.decode("utf-8")
        self.assertIn("Khata Outstanding", content)



