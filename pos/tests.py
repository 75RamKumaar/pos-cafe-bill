import json
from pathlib import Path
import re
from datetime import timedelta
from decimal import Decimal
from unittest import mock
from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from pos.forms import MenuItemForm, ExpenseForm, PurchaseLineForm, PurchaseForm, StockItemForm, SupplierForm
from pos.models import (
    Bill,
    BillItem,
    BillPayment,
    BusinessSettings,
    Customer,
    Expense,
    ExpenseCategory,
    Product,
    KhataTransaction,
    AuditLog,
    HeldOrder,
    RecurringExpense,
    Shift,
    Modifier,
    StaffProfile,
    Supplier,
    StockItem,
    Purchase,
    PurchaseLine,
    RecipeLine,
    Wastage,
    process_recurring_expenses,
)
from pos.views import parse_decimal, TWO_PLACES, compute_customer_ageing
from django.apps import apps
import pos.urls


class POSRoleAccessTests(TestCase):
    """
    Test suite asserting access and HTTP status codes for every URL in pos/urls.py
    for Anonymous, Cashier, and Owner roles.
    """

    OWNER_ONLY_URLS = {
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
        "shift_reopen",
        "modifier_create",
        "modifier_edit",
        "modifier_delete",
        "menu_item_reorder",
        "menu_items_bulk_price",
        "menu_items_export_csv",
        "menu_items_import_csv",
        "category_rename",
        "menu_item_toggle_active",
        "menu_item_toggle_favorite",
        "menu_item_duplicate",
        "staff_list",
        "staff_edit",
        "stock_items",
        "stock_list",
        "stock_item_create",
        "stock_item_edit",
        "stock_item_delete",
        "purchases",
        "purchase_list",
        "purchase_create",
        "purchase_detail",
        "suppliers",
        "supplier_list",
        "supplier_create",
        "supplier_detail",
        "supplier_edit",
        "daily_summary",
        "test_print_endpoint",
    }

    EXPECTED_CASHIER_STATUS = {
        "pos": 302,
        "billing": 200,
        "bill_create": 405,
        "billing_history": 200,
        "shift_open": 200,
        "day_close": 200,
        "shift_close": 200,
        "customer_search": 200,
        "held_order_hold": 405,
        "held_order_list": 200,
        "held_order_recall": 405,
        "held_order_discard": 405,
        "bill_detail": 200,
        "print_bill_endpoint": 200,
        "bills_print_alias": 200,
        "bill_void": 302,
        "customers": 200,
        "customer_create": 200,
        "customer_detail": 200,
        "khata": 200,
        "khata_payment": 302,
        "khata_statement": 200,
        "wastage": 200,
        "wastage_list": 200,
        "wastage_create": 200,
        "set_language": 302,
        "service_worker": 200,
        "manifest": 200,
        "offline": 200,
    }

    EXPECTED_OWNER_STATUS = {
        "pos": 302,
        "billing": 200,
        "bill_create": 405,
        "billing_history": 200,
        "shift_open": 200,
        "day_close": 200,
        "shift_close": 200,
        "shift_reopen": 405,
        "customer_search": 200,
        "held_order_hold": 405,
        "held_order_list": 200,
        "held_order_recall": 405,
        "held_order_discard": 405,
        "bill_detail": 200,
        "print_bill_endpoint": 200,
        "bills_print_alias": 200,
        "bill_void": 302,
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
        "khata_statement": 200,
        "expenses": 200,
        "expense_create": 200,
        "expense_edit": 200,
        "expense_delete": 302,
        "dashboard": 200,
        "reports": 200,
        "export_sales_csv": 200,
        "settings": 200,
        "audit_logs": 200,
        "modifier_create": 200,
        "modifier_edit": 200,
        "modifier_delete": 302,
        "menu_item_reorder": 405,
        "menu_items_bulk_price": 405,
        "menu_items_export_csv": 200,
        "menu_items_import_csv": 405,
        "category_rename": 405,
        "menu_item_toggle_active": 405,
        "menu_item_toggle_favorite": 405,
        "menu_item_duplicate": 405,
        "staff_list": 200,
        "staff_edit": 200,
        "stock_items": 200,
        "stock_list": 200,
        "stock_item_create": 200,
        "stock_item_edit": 200,
        "stock_item_delete": 302,
        "purchases": 200,
        "purchase_list": 200,
        "purchase_create": 200,
        "purchase_detail": 200,
        "suppliers": 200,
        "supplier_list": 200,
        "supplier_create": 200,
        "supplier_detail": 200,
        "supplier_edit": 200,
        "wastage": 200,
        "wastage_list": 200,
        "wastage_create": 200,
        "set_language": 302,
        "service_worker": 200,
        "manifest": 200,
        "offline": 200,
        "daily_summary": 200,
        "test_print_endpoint": 200,
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
        cls.modifier = Modifier.objects.create(
            name="Extra Cheese",
            price_delta=Decimal("10.00"),
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
        cls.held_order = HeldOrder.objects.create(
            label="Test Held",
            items=[{"id": cls.product.id, "quantity": 1}],
        )
        cls.shift = Shift.objects.create(
            status=Shift.STATUS_CLOSED,
            opening_cash=Decimal("500.00"),
            opened_by=cls.owner_user,
            closed_by=cls.owner_user,
            closed_at=timezone.now(),
        )
        cls.stock_item = StockItem.objects.create(
            name="Raw Milk",
            unit="L",
            current_qty=Decimal("10.00"),
            reorder_level=Decimal("5.00"),
            cost_per_unit=Decimal("50.00"),
        )
        cls.supplier = Supplier.objects.create(
            name="Metro Dairy",
            phone="9876543210",
        )
        cls.purchase = Purchase.objects.create(
            supplier=cls.supplier,
            paid_amount=Decimal("100.00"),
            method="CASH",
        )
        PurchaseLine.objects.create(
            purchase=cls.purchase,
            stock_item=cls.stock_item,
            qty=Decimal("2.00"),
            unit_cost=Decimal("50.00"),
        )

    def get_url(self, name):
        kwargs = {}
        if name in {"bill_detail", "bill_void", "print_bill_endpoint", "bills_print_alias"}:
            kwargs = {"bill_id": self.bill.id}
        elif name in {"menu_item_edit", "menu_item_delete", "menu_item_toggle_active", "menu_item_toggle_favorite", "menu_item_duplicate"}:
            kwargs = {"item_id": self.product.id}
        elif name in {"modifier_edit", "modifier_delete"}:
            kwargs = {"modifier_id": self.modifier.id}
        elif name in {"customer_detail", "customer_edit", "customer_delete", "khata_payment", "khata_statement"}:
            kwargs = {"customer_id": self.customer.id}
        elif name in {"expense_edit", "expense_delete"}:
            kwargs = {"expense_id": self.expense.id}
        elif name in {"held_order_recall", "held_order_discard"}:
            kwargs = {"order_id": self.held_order.id}
        elif name in {"shift_reopen"}:
            kwargs = {"shift_id": self.shift.id}
        elif name in {"staff_edit"}:
            kwargs = {"user_id": self.cashier_user.id}
        elif name in {"stock_item_edit", "stock_item_delete"}:
            kwargs = {"item_id": self.stock_item.id}
        elif name in {"purchase_detail"}:
            kwargs = {"purchase_id": self.purchase.id}
        elif name in {"supplier_detail", "supplier_edit"}:
            kwargs = {"supplier_id": self.supplier.id}
        return reverse(name, kwargs=kwargs)

    PUBLIC_URLS = {"service_worker", "manifest", "offline"}

    def test_anonymous_role_access(self):
        """Anonymous users must be redirected (302) to login for protected pos/urls.py URLs, and 200 for public PWA URLs."""
        self.client.logout()
        for pattern in pos.urls.urlpatterns:
            url_name = pattern.name
            with self.subTest(role="anonymous", url_name=url_name):
                url = self.get_url(url_name)
                response = self.client.get(url)
                if url_name in self.PUBLIC_URLS:
                    self.assertEqual(
                        response.status_code,
                        200,
                        f"Anonymous user should receive 200 for public PWA route {url_name} ({url}), got {response.status_code}",
                    )
                else:
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
            stock_tracked=True,
            is_active=True,
        )
        cls.product2 = Product.objects.create(
            name="Bun Maska",
            category="Snacks",
            price=Decimal("35.00"),
            cost_price=Decimal("16.00"),
            stock=Decimal("5.00"),
            stock_tracked=True,
            is_active=True,
        )
        cls.customer = Customer.objects.create(
            name="Suresh Kumar",
            phone="9000000005",
        )

    def setUp(self):
        self.client.login(username="cashier_tester", password="password123")
        self.url = reverse("bill_create")
        Shift.objects.create(opened_by=self.user, opening_cash=Decimal("500.00"), status=Shift.STATUS_OPEN)

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
            stock_tracked=True,
            is_active=True,
        )
        cls.customer = Customer.objects.create(
            name="Ramesh Gupta",
            phone="9876501234",
            opening_balance=Decimal("0.00"),
        )

    def setUp(self):
        Shift.objects.create(opened_by=self.cashier, opening_cash=Decimal("500.00"), status=Shift.STATUS_OPEN)

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

    def test_voiding_khata_bill_restores_stock_and_zeroes_balance(self):
        """Voiding a KHATA bill restores stock, creates a VOID reversal, and zeroes the balance without deleting rows."""
        self.client.login(username="khata_cashier", password="password123")
        self.product.stock_tracked = True
        self.product.save(update_fields=["stock_tracked"])
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

        # Void the bill as Owner
        self.client.login(username="khata_owner", password="password123")
        void_resp = self.client.post(
            reverse("bill_void", kwargs={"bill_id": bill.id}),
            {"void_reason": "Order cancelled"},
        )
        self.assertEqual(void_resp.status_code, 302)

        # Verify Bill is NOT deleted, but marked VOID
        self.assertTrue(Bill.objects.filter(id=bill_id).exists())
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.STATUS_VOID)
        self.assertEqual(bill.void_reason, "Order cancelled")

        # Verify KhataTransaction of kind VOID was created
        self.assertTrue(KhataTransaction.objects.filter(bill_id=bill_id, kind=KhataTransaction.VOID).exists())

        # Verify customer balance is back to 0.00 (zeroed)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("0.00"))

        # Verify stock was restored to 30.00
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal("30.00"))

        # Verify audit log details contain khata_credit_reversed = True
        audit_entry = AuditLog.objects.filter(action="VOID", model_name="Bill").latest("id")
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
        self.assertEqual(len(khata_ctx), 8)

    def test_dashboard_uses_with_outstanding_and_returns_200(self):
        self.client.login(username="nplusone_owner", password="password123")
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("total_outstanding", response.context)
        content = response.content.decode("utf-8")
        self.assertIn("Khata Outstanding", content)


class KhataPaymentValidationTests(TestCase):
    """
    Tests covering khata_payment view:
    - GET request redirects to khata
    - non-finite amounts (NaN, Infinity) rejected with message, no 500
    - negative amounts rejected with message, no 500
    - zero amount rejected with message, no 500
    - overpayment (amount > current outstanding balance) rejected with message, no 500
    - exact balance payment succeeds, new balance 0.00, success message, audit log created
    - valid partial payment succeeds, new balance updated, success message, audit log created
    """

    @classmethod
    def setUpTestData(cls):
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")
        cls.cashier = User.objects.create_user(username="khata_pay_cashier", password="password123")
        cls.cashier.groups.add(cls.cashier_group)

    def setUp(self):
        self.client.login(username="khata_pay_cashier", password="password123")
        self.customer = Customer.objects.create(
            name="Aarav Sharma",
            phone="9876543210",
            opening_balance=Decimal("100.00"),
        )
        self.url = reverse("khata_payment", kwargs={"customer_id": self.customer.id})

    def test_get_request_redirects_to_khata(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("khata"))
        self.assertEqual(KhataTransaction.objects.filter(customer=self.customer).count(), 0)

    def test_reject_nan_amount(self):
        response = self.client.post(self.url, {"amount": "NaN"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse("khata"))
        messages_list = list(response.context["messages"])
        self.assertTrue(any("Non-finite decimal value is not allowed" in str(m) for m in messages_list))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("100.00"))
        self.assertEqual(KhataTransaction.objects.filter(customer=self.customer).count(), 0)

    def test_reject_infinity_amount(self):
        response = self.client.post(self.url, {"amount": "Infinity"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse("khata"))
        messages_list = list(response.context["messages"])
        self.assertTrue(any("Non-finite decimal value is not allowed" in str(m) for m in messages_list))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("100.00"))
        self.assertEqual(KhataTransaction.objects.filter(customer=self.customer).count(), 0)

    def test_reject_negative_amount(self):
        response = self.client.post(self.url, {"amount": "-25.00"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse("khata"))
        messages_list = list(response.context["messages"])
        self.assertTrue(any("Value cannot be less than 0.01" in str(m) for m in messages_list))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("100.00"))
        self.assertEqual(KhataTransaction.objects.filter(customer=self.customer).count(), 0)

    def test_reject_zero_amount(self):
        response = self.client.post(self.url, {"amount": "0.00"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse("khata"))
        messages_list = list(response.context["messages"])
        self.assertTrue(any("Value cannot be less than 0.01" in str(m) for m in messages_list))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("100.00"))
        self.assertEqual(KhataTransaction.objects.filter(customer=self.customer).count(), 0)

    def test_reject_overpayment(self):
        response = self.client.post(self.url, {"amount": "150.00"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse("khata"))
        messages_list = list(response.context["messages"])
        self.assertTrue(any("exceeds outstanding balance" in str(m) for m in messages_list))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("100.00"))
        self.assertEqual(KhataTransaction.objects.filter(customer=self.customer).count(), 0)

    def test_exact_balance_payment_success(self):
        response = self.client.post(
            self.url,
            {"amount": "100.00", "method": "CASH", "note": "Full settlement"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse("khata"))
        messages_list = list(response.context["messages"])
        self.assertTrue(any("100.00" in str(m) and "0.00" in str(m) for m in messages_list))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("0.00"))
        tx = KhataTransaction.objects.get(customer=self.customer)
        self.assertEqual(tx.kind, KhataTransaction.PAYMENT)
        self.assertEqual(tx.method, KhataTransaction.METHOD_CASH)
        self.assertEqual(tx.amount, Decimal("100.00"))
        self.assertEqual(tx.note, "Full settlement")
        audit = AuditLog.objects.filter(action="PAYMENT").latest("id")
        self.assertEqual(audit.details.get("customer"), self.customer.name)
        self.assertEqual(audit.details.get("amount"), "100.00")
        self.assertEqual(audit.details.get("note"), "Full settlement")

    def test_valid_partial_payment_success(self):
        response = self.client.post(
            self.url,
            {"amount": "40.00", "method": "CASH", "note": "Part payment"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse("khata"))
        messages_list = list(response.context["messages"])
        self.assertTrue(any("40.00" in str(m) and "60.00" in str(m) for m in messages_list))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("60.00"))
        tx = KhataTransaction.objects.get(customer=self.customer)
        self.assertEqual(tx.kind, KhataTransaction.PAYMENT)
        self.assertEqual(tx.amount, Decimal("40.00"))
        self.assertEqual(tx.note, "Part payment")
        audit = AuditLog.objects.filter(action="PAYMENT").latest("id")
        self.assertEqual(audit.details.get("customer"), self.customer.name)
        self.assertEqual(audit.details.get("amount"), "40.00")
        self.assertEqual(audit.details.get("note"), "Part payment")


class DashboardSalesAndProfitDiscountTests(TestCase):
    """
    Tests covering dashboard sales, profit, discount, expenses, net profit, and payment split:
    - creates discounted bill and asserts dashboard total_sales equals reports total_sales for same date
    - asserts profit accounts for the discount (item profit minus bill discount)
    - asserts expenses today and net profit are accurately calculated and rendered
    - asserts payment-split row matches payment methods (cash, upi, card, khata)
    - asserts Top 5 table is kept and all products table is collapsed behind toggle
    """

    @classmethod
    def setUpTestData(cls):
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")
        cls.owner = User.objects.create_user(username="dash_owner", password="password123")
        cls.owner.groups.add(cls.owner_group)

        cls.product1 = Product.objects.create(
            name="Masala Chai",
            category="Beverages",
            price=Decimal("50.00"),
            cost_price=Decimal("20.00"),
            stock=Decimal("100.00"),
        )
        cls.product2 = Product.objects.create(
            name="Samosa",
            category="Snacks",
            price=Decimal("30.00"),
            cost_price=Decimal("10.00"),
            stock=Decimal("100.00"),
        )

    def test_dashboard_sales_and_profit_with_discount_matches_reports(self):
        self.client.login(username="dash_owner", password="password123")
        today = timezone.localdate()

        bill = Bill.objects.create(
            bill_number=f"{today:%Y%m%d}-9001",
            subtotal=Decimal("260.00"),
            discount=Decimal("30.00"),
            grand_total=Decimal("230.00"),
            payment_method=Bill.UPI,
        )
        BillItem.objects.create(
            bill=bill,
            menu_item=self.product1,
            item_name=self.product1.name,
            price=self.product1.price,
            cost_price=self.product1.cost_price,
            quantity=Decimal("4.00"),
            total=Decimal("200.00"),
        )
        BillItem.objects.create(
            bill=bill,
            menu_item=self.product2,
            item_name=self.product2.name,
            price=self.product2.price,
            cost_price=self.product2.cost_price,
            quantity=Decimal("2.00"),
            total=Decimal("60.00"),
        )

        Expense.objects.create(
            title="Milk Supplies",
            category="Supplies",
            amount=Decimal("40.00"),
            expense_date=today,
        )

        dash_resp = self.client.get(reverse("dashboard"))
        self.assertEqual(dash_resp.status_code, 200)

        reports_resp = self.client.get(
            reverse("reports"),
            {"from_date": today.strftime("%Y-%m-%d"), "to_date": today.strftime("%Y-%m-%d")},
        )
        self.assertEqual(reports_resp.status_code, 200)

        # Assert dashboard total_sales equals reports total_sales for the same date (both 230.00)
        self.assertEqual(dash_resp.context["total_sales"], Decimal("230.00"))
        self.assertEqual(reports_resp.context["total_sales"], Decimal("230.00"))
        self.assertEqual(dash_resp.context["total_sales"], reports_resp.context["total_sales"])

        # Assert total_profit accounts for the discount: (120 + 40) - 30 = 130.00
        self.assertEqual(dash_resp.context["total_profit"], Decimal("130.00"))

        # Assert expenses today and net profit
        self.assertEqual(dash_resp.context["expenses_today"], Decimal("40.00"))
        self.assertEqual(dash_resp.context["net_profit"], Decimal("90.00"))

        # Assert payment split
        self.assertEqual(dash_resp.context["upi_sales"], Decimal("230.00"))
        self.assertEqual(dash_resp.context["cash_sales"], Decimal("0.00"))
        self.assertEqual(dash_resp.context["card_sales"], Decimal("0.00"))
        self.assertEqual(dash_resp.context["khata_sales"], Decimal("0.00"))

        # Assert template rendered elements
        content = dash_resp.content.decode("utf-8")
        self.assertIn("Today's Sales", content)
        self.assertIn("230.00", content)
        self.assertIn("Expenses Today", content)
        self.assertIn("Net Profit", content)
        self.assertIn("Top 5 Items Today", content)
        self.assertIn("All Products Sold Today", content)
        self.assertIn("<details", content)


class StockTrackedAndDecimalQuantityTests(TestCase):
    """
    Tests covering stock_tracked behavior and fractional quantity:
    - untracked sale at stock 0 succeeds (stock_tracked=False, stock=0)
    - tracked insufficient stock returns 400 (stock_tracked=True, stock < qty)
    - tracked deduction succeeds and decrements stock (stock_tracked=True)
    - decimal quantity 0.5 succeeds with correct line total and grand total
    - dashboard low-stock only includes tracked products
    - MenuItemForm includes stock_tracked toggle and saves it
    """

    @classmethod
    def setUpTestData(cls):
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")

        cls.cashier = User.objects.create_user(username="stock_cashier", password="password123")
        cls.cashier.groups.add(cls.cashier_group)

        cls.owner = User.objects.create_user(username="stock_owner", password="password123")
        cls.owner.groups.add(cls.owner_group)

    def setUp(self):
        self.client.login(username="stock_cashier", password="password123")
        Shift.objects.create(opened_by=self.cashier, opening_cash=Decimal("500.00"), status=Shift.STATUS_OPEN)

    def test_untracked_sale_at_stock_zero_succeeds(self):
        """Untracked product (stock_tracked=False) with stock 0 can be sold without error."""
        product = Product.objects.create(
            name="Cutting Chai",
            category="Beverages",
            price=Decimal("15.00"),
            cost_price=Decimal("5.00"),
            stock=Decimal("0.00"),
            stock_tracked=False,
            is_active=True,
        )
        payload = {
            "items": [{"id": product.id, "quantity": "2"}],
            "payment_method": Bill.CASH,
        }
        response = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        product.refresh_from_db()
        # Stock should not be decremented
        self.assertEqual(product.stock, Decimal("0.00"))

    def test_tracked_insufficient_stock_returns_400(self):
        """Tracked product (stock_tracked=True) returns 400 when requested quantity > stock."""
        product = Product.objects.create(
            name="Bun Maska",
            category="Snacks",
            price=Decimal("35.00"),
            cost_price=Decimal("15.00"),
            stock=Decimal("2.00"),
            stock_tracked=True,
            is_active=True,
        )
        payload = {
            "items": [{"id": product.id, "quantity": "3"}],
            "payment_method": Bill.CASH,
        }
        response = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)
        self.assertIn("Insufficient stock", data["error"])

    def test_tracked_deduction_decrements_stock(self):
        """Tracked product (stock_tracked=True) decrements stock upon successful sale."""
        product = Product.objects.create(
            name="Special Tea",
            category="Beverages",
            price=Decimal("25.00"),
            cost_price=Decimal("10.00"),
            stock=Decimal("10.00"),
            stock_tracked=True,
            is_active=True,
        )
        payload = {
            "items": [{"id": product.id, "quantity": "4"}],
            "payment_method": Bill.CASH,
        }
        response = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        product.refresh_from_db()
        self.assertEqual(product.stock, Decimal("6.00"))

    def test_decimal_quantity_half_succeeds(self):
        """Selling a decimal quantity 0.5 succeeds and calculates proper totals."""
        product = Product.objects.create(
            name="Filter Coffee Half",
            category="Beverages",
            price=Decimal("40.00"),
            cost_price=Decimal("15.00"),
            stock=Decimal("10.00"),
            stock_tracked=True,
            is_active=True,
        )
        payload = {
            "items": [{"id": product.id, "quantity": "0.5"}],
            "payment_method": Bill.CASH,
        }
        response = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total"], "20.00")
        bill = Bill.objects.get(pk=data["bill_id"])
        self.assertEqual(bill.grand_total, Decimal("20.00"))
        item = bill.items.first()
        self.assertEqual(item.quantity, Decimal("0.50"))
        self.assertEqual(item.total, Decimal("20.00"))
        product.refresh_from_db()
        self.assertEqual(product.stock, Decimal("9.50"))

    def test_dashboard_low_stock_shows_only_tracked_products(self):
        """Dashboard low stock warning should ignore untracked items with stock <= 5."""
        self.client.login(username="stock_owner", password="password123")
        # Untracked item with stock 0
        p_untracked = Product.objects.create(
            name="Untracked Chai",
            category="Beverages",
            price=Decimal("10.00"),
            stock=Decimal("0.00"),
            stock_tracked=False,
            is_active=True,
        )
        # Tracked item with stock 2
        p_tracked = Product.objects.create(
            name="Tracked Biscuit",
            category="Snacks",
            price=Decimal("5.00"),
            stock=Decimal("2.00"),
            stock_tracked=True,
            is_active=True,
        )
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        low_stock_list = list(response.context["low_stock_products"])
        self.assertIn(p_tracked, low_stock_list)
        self.assertNotIn(p_untracked, low_stock_list)

    def test_menu_item_form_saves_stock_tracked(self):
        """MenuItemForm should allow toggling stock_tracked."""
        form_data = {
            "name": "Ginger Tea",
            "category": "Beverages",
            "price": "20.00",
            "cost_price": "8.00",
            "stock": "0.00",
            "stock_tracked": True,
            "is_active": True,
        }
        form = MenuItemForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        product = form.save()
        self.assertTrue(product.stock_tracked)


class CustomerArchiveAndLedgerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")

        cls.cashier = User.objects.create_user(
            username="ledger_cashier",
            password="password123",
        )
        cls.cashier.groups.add(cls.cashier_group)

        cls.owner = User.objects.create_user(
            username="ledger_owner",
            password="password123",
        )
        cls.owner.groups.add(cls.owner_group)

    def setUp(self):
        self.client.login(username="ledger_cashier", password="password123")

    def test_ledger_running_balance(self):
        """Ledger calculates running balance starting from opening balance across credits and payments."""
        customer = Customer.objects.create(
            name="Ramesh",
            phone="9898989898",
            opening_balance=Decimal("100.00"),
        )
        KhataTransaction.objects.create(
            customer=customer,
            kind=KhataTransaction.CREDIT,
            amount=Decimal("50.00"),
            note="Tea and samosa",
        )
        KhataTransaction.objects.create(
            customer=customer,
            kind=KhataTransaction.PAYMENT,
            amount=Decimal("30.00"),
            note="UPI payment",
        )
        KhataTransaction.objects.create(
            customer=customer,
            kind=KhataTransaction.CREDIT,
            amount=Decimal("20.00"),
            note="Bun maska",
        )

        response = self.client.get(reverse("customer_detail", args=[customer.id]))
        self.assertEqual(response.status_code, 200)

        ledger = response.context["ledger"]
        self.assertEqual(len(ledger), 4)

        # 1. Opening balance
        self.assertEqual(ledger[0]["type"], "Opening Balance")
        self.assertEqual(ledger[0]["amount"], Decimal("100.00"))
        self.assertEqual(ledger[0]["running_balance"], Decimal("100.00"))

        # 2. Credit 50 -> 150
        self.assertEqual(ledger[1]["type"], "Credit Sale")
        self.assertEqual(ledger[1]["amount"], Decimal("50.00"))
        self.assertEqual(ledger[1]["running_balance"], Decimal("150.00"))

        # 3. Payment 30 -> 120
        self.assertEqual(ledger[2]["type"], "Payment Received")
        self.assertEqual(ledger[2]["amount"], Decimal("30.00"))
        self.assertEqual(ledger[2]["running_balance"], Decimal("120.00"))

        # 4. Credit 20 -> 140
        self.assertEqual(ledger[3]["type"], "Credit Sale")
        self.assertEqual(ledger[3]["amount"], Decimal("20.00"))
        self.assertEqual(ledger[3]["running_balance"], Decimal("140.00"))

        self.assertEqual(customer.balance, Decimal("140.00"))
        self.assertContains(response, "140.00")
        self.assertContains(response, "Receive payment")
        self.assertContains(response, 'name="next"')
        self.assertIsNotNone(response.context["last_visit"])

    def test_receive_payment_form_with_validated_next_redirect(self):
        """Receive payment respects allowed next URL and falls back if invalid."""
        customer = Customer.objects.create(
            name="Suresh",
            opening_balance=Decimal("100.00"),
        )
        detail_url = reverse("customer_detail", args=[customer.id])

        # Valid next redirect
        response = self.client.post(
            reverse("khata_payment", args=[customer.id]),
            {"amount": "25.00", "method": "CASH", "note": "Cash", "next": detail_url},
        )
        self.assertRedirects(response, detail_url)
        self.assertEqual(customer.balance, Decimal("75.00"))

        # Invalid/untrusted next redirect (should fall back to khata)
        response_unsafe = self.client.post(
            reverse("khata_payment", args=[customer.id]),
            {"amount": "10.00", "method": "CASH", "next": "http://evil.com/phishing"},
        )
        self.assertRedirects(response_unsafe, reverse("khata"))
        self.assertEqual(customer.balance, Decimal("65.00"))

    def test_archive_blocked_with_dues(self):
        """Customer cannot be archived if outstanding balance is not zero."""
        self.client.login(username="ledger_owner", password="password123")
        customer = Customer.objects.create(
            name="Dues Customer",
            opening_balance=Decimal("50.00"),
        )
        response = self.client.post(reverse("customer_delete", args=[customer.id]))
        self.assertRedirects(response, reverse("customers"))

        customer.refresh_from_db()
        self.assertTrue(customer.is_active)

        # Flash message explains why
        messages_list = list(response.wsgi_request._messages)
        self.assertTrue(any("Cannot archive customer" in str(m) for m in messages_list))

    def test_archive_allowed_at_zero(self):
        """Customer with zero balance can be archived successfully."""
        self.client.login(username="ledger_owner", password="password123")
        customer = Customer.objects.create(
            name="Zero Balance Customer",
            opening_balance=Decimal("0.00"),
        )
        response = self.client.post(reverse("customer_delete", args=[customer.id]))
        self.assertRedirects(response, reverse("customers"))

        customer.refresh_from_db()
        self.assertFalse(customer.is_active)

        # Audit log created
        log = AuditLog.objects.filter(action="ARCHIVE", object_repr=str(customer)).first()
        self.assertIsNotNone(log)

    def test_archived_customer_excluded_from_search_and_khata_and_customers(self):
        """Archived customers are hidden from search, khata, and default customers list."""
        active = Customer.objects.create(name="Active Anita", is_active=True, opening_balance=Decimal("10.00"))
        archived = Customer.objects.create(name="Archived Ajay", is_active=False, opening_balance=Decimal("0.00"))

        # 1. customer_search
        search_res = self.client.get(reverse("customer_search"), {"q": "Ajay"})
        self.assertEqual(search_res.status_code, 200)
        self.assertEqual(search_res.json()["customers"], [])

        search_active = self.client.get(reverse("customer_search"), {"q": "Anita"})
        self.assertEqual(len(search_active.json()["customers"]), 1)
        self.assertEqual(search_active.json()["customers"][0]["id"], active.id)

        # 2. khata
        khata_res = self.client.get(reverse("khata"))
        self.assertEqual(khata_res.status_code, 200)
        khata_custs = list(khata_res.context["customers"])
        self.assertIn(active, khata_custs)
        self.assertNotIn(archived, khata_custs)

        # 3. customers list (default)
        cust_res = self.client.get(reverse("customers"))
        self.assertEqual(cust_res.status_code, 200)
        custs = list(cust_res.context["customers"])
        self.assertIn(active, custs)
        self.assertNotIn(archived, custs)

        # 4. customers list with archived=1 filter
        archived_res = self.client.get(reverse("customers"), {"archived": "1"})
        self.assertEqual(archived_res.status_code, 200)
        archived_custs = list(archived_res.context["customers"])
        self.assertIn(archived, archived_custs)

    def test_ledger_survives_archive_and_foreign_key_protected(self):
        """Ledger transactions survive archive, and KhataTransaction is protected against cascade delete."""
        from django.db.models.deletion import ProtectedError

        self.client.login(username="ledger_owner", password="password123")
        customer = Customer.objects.create(name="Protected User", opening_balance=Decimal("0.00"))
        KhataTransaction.objects.create(
            customer=customer,
            kind=KhataTransaction.CREDIT,
            amount=Decimal("50.00"),
            note="Item",
        )
        KhataTransaction.objects.create(
            customer=customer,
            kind=KhataTransaction.PAYMENT,
            amount=Decimal("50.00"),
            note="Settled",
        )
        self.assertEqual(customer.balance, Decimal("0.00"))

        # Archive customer
        resp = self.client.post(reverse("customer_delete", args=[customer.id]))
        self.assertRedirects(resp, reverse("customers"))
        customer.refresh_from_db()
        self.assertFalse(customer.is_active)

        # Transactions survive
        self.assertEqual(customer.khata_transactions.count(), 2)
        self.assertEqual(KhataTransaction.objects.filter(customer=customer).count(), 2)

        # Direct ORM delete is blocked by PROTECT
        with self.assertRaises(ProtectedError):
            customer.delete()

    def test_customers_list_has_dues_and_sort_by_balance(self):
        """Customers list can filter by dues and sort by balance."""
        c_high = Customer.objects.create(name="Zack High", opening_balance=Decimal("200.00"))
        c_mid = Customer.objects.create(name="Mona Mid", opening_balance=Decimal("50.00"))
        c_zero = Customer.objects.create(name="Abby Zero", opening_balance=Decimal("0.00"))

        # Filter has_dues=1
        res_dues = self.client.get(reverse("customers"), {"has_dues": "1"})
        dues_custs = list(res_dues.context["customers"])
        self.assertIn(c_high, dues_custs)
        self.assertIn(c_mid, dues_custs)
        self.assertNotIn(c_zero, dues_custs)

        # Sort by balance descending
        res_sort = self.client.get(reverse("customers"), {"sort": "balance"})
        sort_custs = list(res_sort.context["customers"])
        self.assertEqual(sort_custs[0], c_high)
        self.assertEqual(sort_custs[1], c_mid)
        self.assertEqual(sort_custs[2], c_zero)

        # Sort by balance ascending
        res_sort_asc = self.client.get(reverse("customers"), {"sort": "balance_asc"})
        sort_asc_custs = list(res_sort_asc.context["customers"])
        self.assertEqual(sort_asc_custs[0], c_zero)
        self.assertEqual(sort_asc_custs[1], c_mid)
        self.assertEqual(sort_asc_custs[2], c_high)


class BillVoidFeatureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")

        cls.owner = User.objects.create_user(
            username="void_owner",
            password="ownerpass123",
        )
        cls.owner.groups.add(cls.owner_group)

        cls.cashier = User.objects.create_user(
            username="void_cashier",
            password="cashierpass123",
        )
        cls.cashier.groups.add(cls.cashier_group)

        cls.other_cashier = User.objects.create_user(
            username="other_cashier",
            password="otherpass123",
        )
        cls.other_cashier.groups.add(cls.cashier_group)

    def test_owner_void_direct_and_reason_required(self):
        """Owner can void directly with a reason; missing reason is rejected."""
        self.client.login(username="void_owner", password="ownerpass123")
        bill = Bill.objects.create(
            bill_number="VOID-TEST-001",
            subtotal=Decimal("50.00"),
            grand_total=Decimal("50.00"),
            payment_method=Bill.CASH,
            created_at=timezone.now(),
        )

        # 1. Missing reason rejected
        resp_no_reason = self.client.post(reverse("bill_void", args=[bill.id]), {"void_reason": "   "})
        self.assertEqual(resp_no_reason.status_code, 302)
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.STATUS_ACTIVE)

        # 2. Valid reason succeeds directly for Owner
        resp = self.client.post(reverse("bill_void", args=[bill.id]), {"void_reason": "Customer cancelled"})
        self.assertEqual(resp.status_code, 302)
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.STATUS_VOID)
        self.assertEqual(bill.void_reason, "Customer cancelled")
        self.assertEqual(bill.voided_by, self.owner)
        self.assertIsNotNone(bill.voided_at)

    def test_cashier_void_with_valid_and_invalid_owner_credentials(self):
        """Cashier requires valid owner credentials to void a bill."""
        self.client.login(username="void_cashier", password="cashierpass123")
        bill = Bill.objects.create(
            bill_number="VOID-TEST-002",
            subtotal=Decimal("80.00"),
            grand_total=Decimal("80.00"),
            payment_method=Bill.CASH,
            created_at=timezone.now(),
        )

        # 1. Missing credentials
        resp1 = self.client.post(reverse("bill_void", args=[bill.id]), {"void_reason": "Error"})
        self.assertEqual(resp1.status_code, 302)
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.STATUS_ACTIVE)

        # 2. Wrong password
        resp2 = self.client.post(
            reverse("bill_void", args=[bill.id]),
            {"void_reason": "Error", "owner_username": "void_owner", "owner_password": "wrongpassword"},
        )
        self.assertEqual(resp2.status_code, 302)
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.STATUS_ACTIVE)

        # 3. Non-owner credentials (another cashier)
        resp3 = self.client.post(
            reverse("bill_void", args=[bill.id]),
            {"void_reason": "Error", "owner_username": "other_cashier", "owner_password": "otherpass123"},
        )
        self.assertEqual(resp3.status_code, 302)
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.STATUS_ACTIVE)

        # 4. Valid owner credentials succeed
        resp4 = self.client.post(
            reverse("bill_void", args=[bill.id]),
            {"void_reason": "Wrong item keyed in", "owner_username": "void_owner", "owner_password": "ownerpass123"},
        )
        self.assertEqual(resp4.status_code, 302)
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.STATUS_VOID)
        self.assertEqual(bill.voided_by, self.cashier)

        # Verify audit log records both cashier and owner authorizer
        log = AuditLog.objects.filter(action="VOID", model_name="Bill").latest("id")
        self.assertEqual(log.details["cashier"], "void_cashier")
        self.assertEqual(log.details["owner_authorizer"], "void_owner")

    def test_stock_restore_only_for_tracked_items(self):
        """Voiding restores stock only for stock_tracked products."""
        self.client.login(username="void_owner", password="ownerpass123")
        p_tracked = Product.objects.create(
            name="Tracked Cola",
            price=Decimal("30.00"),
            stock=Decimal("10.00"),
            stock_tracked=True,
        )
        p_untracked = Product.objects.create(
            name="Untracked Samosa",
            price=Decimal("15.00"),
            stock=Decimal("5.00"),
            stock_tracked=False,
        )
        bill = Bill.objects.create(
            bill_number="VOID-TEST-003",
            subtotal=Decimal("75.00"),
            grand_total=Decimal("75.00"),
            payment_method=Bill.CASH,
            created_at=timezone.now(),
        )
        BillItem.objects.create(
            bill=bill,
            menu_item=p_tracked,
            item_name=p_tracked.name,
            price=p_tracked.price,
            quantity=Decimal("2.00"),
            total=Decimal("60.00"),
        )
        BillItem.objects.create(
            bill=bill,
            menu_item=p_untracked,
            item_name=p_untracked.name,
            price=p_untracked.price,
            quantity=Decimal("1.00"),
            total=Decimal("15.00"),
        )
        # Simulate initial stock deduction for tracked product
        p_tracked.stock = Decimal("8.00")
        p_tracked.save()

        # Void bill
        self.client.post(reverse("bill_void", args=[bill.id]), {"void_reason": "Cancel"})
        p_tracked.refresh_from_db()
        p_untracked.refresh_from_db()

        # Tracked restored: 8 + 2 = 10
        self.assertEqual(p_tracked.stock, Decimal("10.00"))
        # Untracked unchanged: 5
        self.assertEqual(p_untracked.stock, Decimal("5.00"))

    def test_khata_reversal_and_customer_balance(self):
        """Voiding a KHATA bill creates a VOID transaction that offsets customer balance."""
        self.client.login(username="void_owner", password="ownerpass123")
        customer = Customer.objects.create(name="Khata Void Customer", opening_balance=Decimal("0.00"))
        bill = Bill.objects.create(
            bill_number="VOID-TEST-004",
            customer=customer,
            subtotal=Decimal("120.00"),
            grand_total=Decimal("120.00"),
            payment_method=Bill.KHATA,
            created_at=timezone.now(),
        )
        KhataTransaction.objects.create(
            customer=customer,
            bill=bill,
            kind=KhataTransaction.CREDIT,
            amount=Decimal("120.00"),
            note="Credit sale",
        )
        self.assertEqual(customer.balance, Decimal("120.00"))

        # Void bill
        self.client.post(reverse("bill_void", args=[bill.id]), {"void_reason": "Order cancelled"})
        customer.refresh_from_db()

        # Balance restored to 0.00
        self.assertEqual(customer.balance, Decimal("0.00"))
        qs_cust = Customer.objects.with_outstanding().get(id=customer.id)
        self.assertEqual(qs_cust.outstanding, Decimal("0.00"))

        # Void transaction created
        void_tx = KhataTransaction.objects.filter(bill=bill, kind=KhataTransaction.VOID).first()
        self.assertIsNotNone(void_tx)
        self.assertEqual(void_tx.amount, Decimal("120.00"))

        # Customer detail total_spent excludes voided bill
        resp = self.client.get(reverse("customer_detail", args=[customer.id]))
        self.assertEqual(resp.context["total_spent"], Decimal("0.00"))

    def test_exclusion_from_reports_dashboard_and_csv(self):
        """VOID bills are excluded from dashboard, reports, and CSV exports."""
        self.client.login(username="void_owner", password="ownerpass123")
        p = Product.objects.create(name="Tea", price=Decimal("20.00"), stock_tracked=False)

        # Active bill
        b_active = Bill.objects.create(
            bill_number="BILL-ACTIVE-01",
            subtotal=Decimal("40.00"),
            grand_total=Decimal("40.00"),
            payment_method=Bill.CASH,
            status=Bill.STATUS_ACTIVE,
            created_at=timezone.now(),
        )
        BillItem.objects.create(
            bill=b_active,
            menu_item=p,
            item_name=p.name,
            price=p.price,
            quantity=Decimal("2.00"),
            total=Decimal("40.00"),
        )

        # Void bill
        b_void = Bill.objects.create(
            bill_number="BILL-VOID-02",
            subtotal=Decimal("60.00"),
            grand_total=Decimal("60.00"),
            payment_method=Bill.CASH,
            status=Bill.STATUS_VOID,
            void_reason="Test void",
            created_at=timezone.now(),
        )
        BillItem.objects.create(
            bill=b_void,
            menu_item=p,
            item_name=p.name,
            price=p.price,
            quantity=Decimal("3.00"),
            total=Decimal("60.00"),
        )

        # Dashboard check
        dash_resp = self.client.get(reverse("dashboard"))
        self.assertEqual(dash_resp.context["total_sales"], Decimal("40.00"))
        self.assertEqual(dash_resp.context["total_quantity"], Decimal("2.00"))

        # Reports check
        rep_resp = self.client.get(reverse("reports"))
        self.assertEqual(rep_resp.context["total_sales"], Decimal("40.00"))

        # CSV check
        csv_resp = self.client.get(reverse("export_sales_csv"))
        content = csv_resp.content.decode("utf-8")
        self.assertIn("BILL-ACTIVE-01", content)
        self.assertNotIn("BILL-VOID-02", content)

    def test_double_void_blocked(self):
        """Attempting to void an already-voided bill is blocked."""
        self.client.login(username="void_owner", password="ownerpass123")
        p = Product.objects.create(name="Tracked Snack", price=Decimal("10.00"), stock=Decimal("5.00"), stock_tracked=True)
        bill = Bill.objects.create(
            bill_number="VOID-TEST-DOUBLE",
            subtotal=Decimal("10.00"),
            grand_total=Decimal("10.00"),
            payment_method=Bill.CASH,
            created_at=timezone.now(),
        )
        BillItem.objects.create(
            bill=bill,
            menu_item=p,
            item_name=p.name,
            price=p.price,
            quantity=Decimal("1.00"),
            total=Decimal("10.00"),
        )
        # First void
        self.client.post(reverse("bill_void", args=[bill.id]), {"void_reason": "Reason 1"})
        p.refresh_from_db()
        self.assertEqual(p.stock, Decimal("6.00"))

        # Second void attempt
        resp2 = self.client.post(reverse("bill_void", args=[bill.id]), {"void_reason": "Reason 2"})
        self.assertEqual(resp2.status_code, 302)
        messages_list = list(resp2.wsgi_request._messages)
        self.assertTrue(any("already been voided" in str(m) for m in messages_list))
        # Stock should not be incremented again
        p.refresh_from_db()
        self.assertEqual(p.stock, Decimal("6.00"))

    def test_billing_history_shows_void_pill_filter_and_removes_delete_button(self):
        """Billing history displays VOID status pill, status filter, and no delete button."""
        self.client.login(username="void_owner", password="ownerpass123")
        b_act = Bill.objects.create(
            bill_number="HIST-ACT-01",
            subtotal=Decimal("10.00"),
            grand_total=Decimal("10.00"),
            payment_method=Bill.CASH,
            status=Bill.STATUS_ACTIVE,
            created_at=timezone.now(),
        )
        b_void = Bill.objects.create(
            bill_number="HIST-VOID-02",
            subtotal=Decimal("20.00"),
            grand_total=Decimal("20.00"),
            payment_method=Bill.CASH,
            status=Bill.STATUS_VOID,
            void_reason="Cancelled",
            created_at=timezone.now(),
        )

        resp = self.client.get(reverse("billing_history"))
        self.assertContains(resp, "HIST-ACT-01")
        self.assertContains(resp, "HIST-VOID-02")
        self.assertContains(resp, "VOID")
        self.assertNotContains(resp, 'btn-danger">Delete<')

        # Filter ACTIVE
        resp_act = self.client.get(reverse("billing_history"), {"status": "ACTIVE"})
        self.assertContains(resp_act, "HIST-ACT-01")
        self.assertNotContains(resp_act, "HIST-VOID-02")

        # Filter VOID
        resp_void = self.client.get(reverse("billing_history"), {"status": "VOID"})
        self.assertNotContains(resp_void, "HIST-ACT-01")
        self.assertContains(resp_void, "HIST-VOID-02")

    def test_bill_detail_receipt_shows_void_watermark(self):
        """Receipt displays VOID watermark when bill status is VOID."""
        self.client.login(username="void_owner", password="ownerpass123")
        b_act = Bill.objects.create(
            bill_number="REC-ACT-01",
            subtotal=Decimal("10.00"),
            grand_total=Decimal("10.00"),
            payment_method=Bill.CASH,
            status=Bill.STATUS_ACTIVE,
            created_at=timezone.now(),
        )
        b_void = Bill.objects.create(
            bill_number="REC-VOID-02",
            subtotal=Decimal("20.00"),
            grand_total=Decimal("20.00"),
            payment_method=Bill.CASH,
            status=Bill.STATUS_VOID,
            void_reason="Test cancellation",
            created_at=timezone.now(),
        )

        resp_act = self.client.get(reverse("bill_detail", args=[b_act.id]))
        self.assertNotContains(resp_act, 'class="void-watermark"')
        self.assertContains(resp_act, "Void Bill")

        resp_void = self.client.get(reverse("bill_detail", args=[b_void.id]))
        self.assertContains(resp_void, 'class="void-watermark"')
        self.assertNotContains(resp_void, "Void Bill")


class BillCashTenderedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")
        cls.cashier = User.objects.create_user(username="tender_cashier", password="password123")
        cls.cashier.groups.add(cls.cashier_group)

    def setUp(self):
        self.client.login(username="tender_cashier", password="password123")
        Shift.objects.create(opened_by=self.cashier, opening_cash=Decimal("500.00"), status=Shift.STATUS_OPEN)
        self.product = Product.objects.create(
            name="Masala Chai",
            category="Beverages",
            price=Decimal("20.00"),
            cost_price=Decimal("8.00"),
            stock=Decimal("100.00"),
            stock_tracked=True,
            is_active=True,
        )

    def test_tendered_below_total_rejected(self):
        """Cash bill with tendered amount less than grand total is rejected with 400."""
        payload = {
            "items": [{"id": self.product.id, "quantity": "2"}],
            "payment_method": Bill.CASH,
            "tendered": "35.00",
        }
        response = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)
        self.assertIn("cannot be less than grand total", data["error"])
        self.assertEqual(Bill.objects.count(), 0)

    def test_tendered_change_computed_and_stored(self):
        """Cash bill computes change_due correctly and stores both tendered and change_due."""
        payload = {
            "items": [{"id": self.product.id, "quantity": "3"}],
            "discount": "10.00",
            "payment_method": Bill.CASH,
            "tendered": "100.00",
        }
        response = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("total"), "50.00")
        self.assertEqual(data.get("tendered"), "100.00")
        self.assertEqual(data.get("change_due"), "50.00")

        bill = Bill.objects.get(id=data["bill_id"])
        self.assertEqual(bill.grand_total, Decimal("50.00"))
        self.assertEqual(bill.tendered, Decimal("100.00"))
        self.assertEqual(bill.change_due, Decimal("50.00"))

    def test_exact_tendered_change_is_zero(self):
        """Exact cash payment computes change_due of 0.00."""
        payload = {
            "items": [{"id": self.product.id, "quantity": "1"}],
            "payment_method": Bill.CASH,
            "tendered": "20.00",
        }
        response = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        bill = Bill.objects.latest("id")
        self.assertEqual(bill.tendered, Decimal("20.00"))
        self.assertEqual(bill.change_due, Decimal("0.00"))

    def test_json_response_includes_bill_id_and_detail_url(self):
        """Successful bill_create JSON response includes bill_id and detail_url with ?print=1."""
        payload = {
            "items": [{"id": self.product.id, "quantity": "1"}],
            "payment_method": Bill.CASH,
            "tendered": "50.00",
        }
        response = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("bill_id", data)
        self.assertIn("detail_url", data)

        bill = Bill.objects.get(id=data["bill_id"])
        expected_detail_url = f"{reverse('bill_detail', kwargs={'bill_id': bill.id})}?print=1"
        self.assertEqual(data["detail_url"], expected_detail_url)

        # Verify that detail_url is accessible and returns auto_print receipt for hidden iframe
        detail_resp = self.client.get(data["detail_url"])
        self.assertEqual(detail_resp.status_code, 200)
        self.assertTrue(detail_resp.context["auto_print"])

    def test_cash_without_tendered_succeeds_as_optional(self):
        """Tendered is optional for cash bills; fields remain null when not supplied."""
        payload = {
            "items": [{"id": self.product.id, "quantity": "1"}],
            "payment_method": Bill.CASH,
        }
        response = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        bill = Bill.objects.latest("id")
        self.assertIsNone(bill.tendered)
        self.assertIsNone(bill.change_due)

    def test_non_cash_ignores_tendered(self):
        """Non-cash payments do not populate tendered and change_due."""
        payload = {
            "items": [{"id": self.product.id, "quantity": "1"}],
            "payment_method": Bill.UPI,
            "tendered": "50.00",
        }
        response = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        bill = Bill.objects.latest("id")
        self.assertIsNone(bill.tendered)
        self.assertIsNone(bill.change_due)

    def test_billing_page_contains_tendered_and_iframe_elements(self):
        """Billing page renders cash tendered inputs, quick buttons, change due, toast, iframe, and reprint button."""
        response = self.client.get(reverse("billing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="cashTenderedSection"')
        self.assertContains(response, 'id="tenderedAmount"')
        self.assertContains(response, 'setQuickTendered(\'exact\')')
        self.assertContains(response, 'id="changeDueDisplay"')
        self.assertContains(response, 'id="receiptPrintFrame"')
        self.assertContains(response, 'id="billingToast"')
        self.assertContains(response, 'id="reprintLastBtn"')


class BillSplitPaymentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")
        cls.cashier = User.objects.create_user(username="split_cashier", password="password123")
        cls.cashier.groups.add(cls.cashier_group)
        cls.owner = User.objects.create_user(username="split_owner", password="password123")
        cls.owner.groups.add(cls.owner_group)

    def setUp(self):
        self.client.login(username="split_cashier", password="password123")
        Shift.objects.create(opened_by=self.cashier, opening_cash=Decimal("500.00"), status=Shift.STATUS_OPEN)
        self.product = Product.objects.create(
            name="Irani Chai",
            category="Beverages",
            price=Decimal("25.00"),
            cost_price=Decimal("10.00"),
            stock=Decimal("100.00"),
            stock_tracked=True,
            is_active=True,
        )

    def test_cash_and_upi_split_success(self):
        """Creating a bill with cash and UPI split stores BillPayment rows and sets payment_method=SPLIT."""
        payload = {
            "items": [{"id": self.product.id, "quantity": "2"}],  # total = 50.00
            "payment_method": Bill.SPLIT,
            "payments": [
                {"method": Bill.CASH, "amount": "30.00"},
                {"method": Bill.UPI, "amount": "20.00"},
            ],
        }
        resp = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        bill = Bill.objects.get(id=data["bill_id"])
        self.assertEqual(bill.payment_method, Bill.SPLIT)
        self.assertEqual(bill.payments.count(), 2)

        cash_p = bill.payments.get(method=Bill.CASH)
        upi_p = bill.payments.get(method=Bill.UPI)
        self.assertEqual(cash_p.amount, Decimal("30.00"))
        self.assertEqual(upi_p.amount, Decimal("20.00"))

    def test_cash_and_khata_split_creates_partial_khata_credit(self):
        """A split bill with a KHATA part creates a Khata credit only for that part."""
        customer = Customer.objects.create(name="Ramesh", phone="9876543210")
        payload = {
            "items": [{"id": self.product.id, "quantity": "2"}],  # total = 50.00
            "customer_id": customer.id,
            "payments": [
                {"method": Bill.CASH, "amount": "35.00"},
                {"method": Bill.KHATA, "amount": "15.00"},
            ],
        }
        resp = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        bill = Bill.objects.get(id=data["bill_id"])
        self.assertEqual(bill.payments.count(), 2)

        khata_txs = KhataTransaction.objects.filter(bill=bill)
        self.assertEqual(khata_txs.count(), 1)
        tx = khata_txs.first()
        self.assertEqual(tx.kind, KhataTransaction.CREDIT)
        self.assertEqual(tx.amount, Decimal("15.00"))
        self.assertEqual(customer.balance, Decimal("15.00"))

    def test_split_khata_without_customer_rejected(self):
        """A split payment including KHATA requires a customer; otherwise rejected with 400."""
        payload = {
            "items": [{"id": self.product.id, "quantity": "2"}],  # total = 50.00
            "payments": [
                {"method": Bill.CASH, "amount": "30.00"},
                {"method": Bill.KHATA, "amount": "20.00"},
            ],
        }
        resp = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("customer is required", resp.json().get("error", ""))
        self.assertEqual(Bill.objects.count(), 0)

    def test_split_sum_mismatch_rejected(self):
        """If sum of split payment amounts does not equal grand total, reject with 400."""
        payload = {
            "items": [{"id": self.product.id, "quantity": "2"}],  # total = 50.00
            "payments": [
                {"method": Bill.CASH, "amount": "30.00"},
                {"method": Bill.UPI, "amount": "15.00"},  # 45.00 != 50.00
            ],
        }
        resp = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("does not match grand total", resp.json().get("error", ""))
        self.assertEqual(Bill.objects.count(), 0)

    def test_migration_backfill_existing_bills(self):
        """Data migration backfill creates BillPayment rows equal to grand_total for existing bills."""
        import importlib
        migration_mod = importlib.import_module("pos.migrations.0019_backfill_bill_payments")
        backfill_bill_payments = migration_mod.backfill_bill_payments

        bill = Bill.objects.create(
            bill_number="MIG-TEST-01",
            subtotal=Decimal("75.00"),
            grand_total=Decimal("75.00"),
            payment_method=Bill.UPI,
            created_at=timezone.now(),
        )
        self.assertFalse(BillPayment.objects.filter(bill=bill).exists())

        backfill_bill_payments(apps, None)

        self.assertTrue(BillPayment.objects.filter(bill=bill).exists())
        payment = BillPayment.objects.get(bill=bill)
        self.assertEqual(payment.method, Bill.UPI)
        self.assertEqual(payment.amount, Decimal("75.00"))

    def test_report_totals_equal_grand_totals(self):
        """Reports, dashboard and CSV compute cash/upi/card/khata totals from BillPayment matching grand totals."""
        self.client.login(username="split_owner", password="password123")
        customer = Customer.objects.create(name="Suresh", phone="9123456780")

        # Bill 1: 60.00 total -> Cash 40.00 + UPI 20.00
        b1 = Bill.objects.create(
            bill_number="REP-SPLIT-01",
            subtotal=Decimal("60.00"),
            grand_total=Decimal("60.00"),
            payment_method=Bill.SPLIT,
            status=Bill.STATUS_ACTIVE,
            created_at=timezone.now(),
        )
        BillPayment.objects.create(bill=b1, method=Bill.CASH, amount=Decimal("40.00"))
        BillPayment.objects.create(bill=b1, method=Bill.UPI, amount=Decimal("20.00"))

        # Bill 2: 50.00 total -> Card 30.00 + Khata 20.00
        b2 = Bill.objects.create(
            bill_number="REP-SPLIT-02",
            customer=customer,
            subtotal=Decimal("50.00"),
            grand_total=Decimal("50.00"),
            payment_method=Bill.SPLIT,
            status=Bill.STATUS_ACTIVE,
            created_at=timezone.now(),
        )
        BillPayment.objects.create(bill=b2, method=Bill.CARD, amount=Decimal("30.00"))
        BillPayment.objects.create(bill=b2, method=Bill.KHATA, amount=Decimal("20.00"))

        # Check Dashboard
        dash_resp = self.client.get(reverse("dashboard"))
        self.assertEqual(dash_resp.status_code, 200)
        self.assertEqual(dash_resp.context["total_sales"], Decimal("110.00"))
        self.assertEqual(dash_resp.context["cash_sales"], Decimal("40.00"))
        self.assertEqual(dash_resp.context["upi_sales"], Decimal("20.00"))
        self.assertEqual(dash_resp.context["card_sales"], Decimal("30.00"))
        self.assertEqual(dash_resp.context["khata_sales"], Decimal("20.00"))
        computed_sum = (
            dash_resp.context["cash_sales"]
            + dash_resp.context["upi_sales"]
            + dash_resp.context["card_sales"]
            + dash_resp.context["khata_sales"]
        )
        self.assertEqual(computed_sum, dash_resp.context["total_sales"])

        # Check Reports
        rep_resp = self.client.get(reverse("reports"))
        self.assertEqual(rep_resp.status_code, 200)
        self.assertEqual(rep_resp.context["total_sales"], Decimal("110.00"))
        self.assertEqual(rep_resp.context["cash_sales"], Decimal("40.00"))
        self.assertEqual(rep_resp.context["upi_sales"], Decimal("20.00"))
        self.assertEqual(rep_resp.context["card_sales"], Decimal("30.00"))
        self.assertEqual(rep_resp.context["khata_sales"], Decimal("20.00"))
        rep_sum = (
            rep_resp.context["cash_sales"]
            + rep_resp.context["upi_sales"]
            + rep_resp.context["card_sales"]
            + rep_resp.context["khata_sales"]
        )
        self.assertEqual(rep_sum, rep_resp.context["total_sales"])

        # Check CSV
        csv_resp = self.client.get(reverse("export_sales_csv"))
        self.assertEqual(csv_resp.status_code, 200)
        content = csv_resp.content.decode("utf-8")
        self.assertIn("Payment Totals (BillPayment)", content)
        self.assertIn("Cash,40.00", content)
        self.assertIn("UPI,20.00", content)
        self.assertIn("Card,30.00", content)
        self.assertIn("Khata,20.00", content)

    def test_receipt_lists_each_payment_line(self):
        """Receipt details lists each payment line with its method and amount."""
        bill = Bill.objects.create(
            bill_number="REC-SPLIT-01",
            subtotal=Decimal("50.00"),
            grand_total=Decimal("50.00"),
            payment_method=Bill.SPLIT,
            status=Bill.STATUS_ACTIVE,
            created_at=timezone.now(),
        )
        BillPayment.objects.create(bill=bill, method=Bill.CASH, amount=Decimal("30.00"))
        BillPayment.objects.create(bill=bill, method=Bill.UPI, amount=Decimal("20.00"))

        resp = self.client.get(reverse("bill_detail", kwargs={"bill_id": bill.id}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cash")
        self.assertContains(resp, "30.00")
        self.assertContains(resp, "UPI")
        self.assertContains(resp, "20.00")

    def test_billing_ui_contains_split_toggle_and_elements(self):
        """Billing UI renders split payment toggle, rows container, and remaining indicator."""
        resp = self.client.get(reverse("billing"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="splitPaymentToggle"')
        self.assertContains(resp, 'id="splitPaymentSection"')
        self.assertContains(resp, 'id="splitRowsContainer"')
        self.assertContains(resp, 'id="splitRemaining"')


class BillingRedesignAndFavoritesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")
        cls.cashier = User.objects.create_user(username="redesign_cashier", password="password123")
        cls.cashier.groups.add(cls.cashier_group)

    def setUp(self):
        self.client.login(username="redesign_cashier", password="password123")

    def test_billing_computes_favorites_top_8_in_last_30_days(self):
        """Billing view computes top 8 best-selling products from the last 30 days."""
        now = timezone.now()
        old_date = now - timedelta(days=35)
        recent_date = now - timedelta(days=5)

        # Create 10 products
        products = []
        for i in range(10):
            p = Product.objects.create(
                name=f"Product {i+1}",
                category="General",
                price=Decimal("20.00"),
                cost_price=Decimal("10.00"),
                stock=Decimal("100.00"),
                is_active=True,
            )
            products.append(p)

        # Old bill (35 days ago) - should not count
        old_bill = Bill.objects.create(
            bill_number="OLD-001",
            subtotal=Decimal("2000.00"),
            grand_total=Decimal("2000.00"),
            created_at=old_date,
            status=Bill.STATUS_ACTIVE,
        )
        BillItem.objects.create(
            bill=old_bill,
            menu_item=products[9],
            item_name=products[9].name,
            price=products[9].price,
            quantity=Decimal("100.00"),
            total=Decimal("2000.00"),
        )

        # Recent bill with distinct quantities for products 0 to 8
        recent_bill = Bill.objects.create(
            bill_number="REC-001",
            subtotal=Decimal("1000.00"),
            grand_total=Decimal("1000.00"),
            created_at=recent_date,
            status=Bill.STATUS_ACTIVE,
        )
        for i in range(9):
            qty = Decimal(f"{i + 1}.00")  # Product 0 has 1, Product 1 has 2, ..., Product 8 has 9
            BillItem.objects.create(
                bill=recent_bill,
                menu_item=products[i],
                item_name=products[i].name,
                price=products[i].price,
                quantity=qty,
                total=qty * products[i].price,
            )

        resp = self.client.get(reverse("billing"))
        self.assertEqual(resp.status_code, 200)
        favs = resp.context["favorites"]
        self.assertEqual(len(favs), 8)
        # Most sold is product 8 (qty 9), then product 7 (qty 8), ..., down to product 1 (qty 2)
        expected_ids = [products[i].id for i in range(8, 0, -1)]
        actual_ids = [p.id for p in favs]
        self.assertEqual(actual_ids, expected_ids)
        # Product 0 (qty 1) was 9th so excluded from top 8; Product 9 only sold >30 days ago so excluded
        self.assertNotIn(products[0].id, actual_ids)
        self.assertNotIn(products[9].id, actual_ids)

    def test_no_inline_style_or_script_in_billing_template(self):
        """No inline <style> tag or inline <script> block remains in billing.html."""
        from pathlib import Path
        template_path = Path("templates/pos/billing.html")
        content = template_path.read_text(encoding="utf-8")

        # Must not contain <style>
        self.assertNotIn("<style", content)
        # Must contain link to static CSS and script tag with src
        self.assertIn("billing.css", content)
        self.assertIn("billing.js", content)
        # Any <script> tag must have a src attribute (no inline script blocks)
        script_tags = re.findall(r"<script\b([^>]*)>(.*?)</script>", content, re.DOTALL | re.IGNORECASE)
        self.assertTrue(len(script_tags) > 0)
        for attrs, body in script_tags:
            self.assertIn("src=", attrs)
            self.assertEqual(body.strip(), "")

    def test_all_thirty_required_element_ids_preserved(self):
        """All 30 preserved element IDs exist in billing.html."""
        resp = self.client.get(reverse("billing"))
        self.assertEqual(resp.status_code, 200)
        required_ids = [
            "shortcutInput",
            "shortcutFeedback",
            "reprintLastBtn",
            "productSearch",
            "categoryTabs",
            "productGrid",
            "cartPanel",
            "cartCount",
            "cart",
            "subtotal",
            "discount",
            "grandTotal",
            "customerSearch",
            "customer",
            "customerSuggestions",
            "selectedCustomer",
            "paymentGrid",
            "splitPaymentToggle",
            "splitPaymentSection",
            "splitRowsContainer",
            "splitRemaining",
            "cashTenderedSection",
            "tenderedAmount",
            "changeDueDisplay",
            "billingError",
            "generateBillButton",
            "billingToast",
            "receiptPrintFrame",
            "mobileDockCount",
            "mobileDockTotal",
        ]
        for elem_id in required_ids:
            self.assertContains(resp, f'id="{elem_id}"')

    def test_favorites_strip_rendered_when_favorites_exist(self):
        """Favorites strip is rendered above category tabs when favorites exist."""
        p = Product.objects.create(
            name="Irani Chai",
            category="Beverages",
            price=Decimal("20.00"),
            stock=Decimal("50.00"),
            is_active=True,
        )
        bill = Bill.objects.create(
            bill_number="FAV-001",
            subtotal=Decimal("20.00"),
            grand_total=Decimal("20.00"),
            status=Bill.STATUS_ACTIVE,
        )
        BillItem.objects.create(
            bill=bill,
            menu_item=p,
            item_name=p.name,
            price=p.price,
            quantity=Decimal("5.00"),
            total=Decimal("100.00"),
        )
        resp = self.client.get(reverse("billing"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="favoritesStrip"')
        self.assertContains(resp, "Irani Chai")


class LoginThrottlingAndSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.username = "throttle_test_user"
        self.password = "validpass123"
        self.user = User.objects.create_user(username=self.username, password=self.password)
        cashier_group, _ = Group.objects.get_or_create(name="Cashier")
        self.user.groups.add(cashier_group)

    def tearDown(self):
        cache.clear()

    def test_gen_secret_management_command(self):
        """gen_secret command generates a long cryptographically secure secret key."""
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command("gen_secret", stdout=out)
        key = out.getvalue().strip()
        self.assertTrue(len(key) >= 50)

    def test_security_settings_configured(self):
        """Settings match requested security headers and cookies."""
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertTrue(settings.SECURE_CONTENT_TYPE_NOSNIFF)
        self.assertEqual(settings.X_FRAME_OPTIONS, "DENY")

    def test_login_throttling_locks_after_5_failures_and_releases_after_expiry(self):
        """5 failed login attempts lock the user+IP for 5 minutes; unlocks after expiry using mocked clock."""
        from unittest import mock

        base_time = 1700000000.0

        with mock.patch("cafe_pos.urls.get_current_time", return_value=base_time):
            # 5 consecutive failed attempts
            for i in range(5):
                resp = self.client.post(
                    reverse("login"),
                    {"username": self.username, "password": "wrongpassword"},
                    REMOTE_ADDR="192.168.1.100",
                )
                self.assertEqual(resp.status_code, 200)
                if i < 4:
                    self.assertNotContains(resp, "locked for 5 minutes")

            # 6th attempt (even with the CORRECT password) is locked out!
            locked_resp = self.client.post(
                reverse("login"),
                {"username": self.username, "password": self.password},
                REMOTE_ADDR="192.168.1.100",
            )
            self.assertEqual(locked_resp.status_code, 200)
            self.assertContains(locked_resp, "locked for 5 minutes")
            # User should NOT be logged in
            self.assertNotIn("_auth_user_id", self.client.session)

            # Different IP with correct password is NOT locked
            diff_ip_resp = self.client.post(
                reverse("login"),
                {"username": self.username, "password": self.password},
                REMOTE_ADDR="192.168.1.101",
            )
            self.assertRedirects(diff_ip_resp, reverse("billing"))
            self.client.logout()

        # Advance mocked clock by 301 seconds (5 minutes and 1 second)
        with mock.patch("cafe_pos.urls.get_current_time", return_value=base_time + 301):
            success_resp = self.client.post(
                reverse("login"),
                {"username": self.username, "password": self.password},
                REMOTE_ADDR="192.168.1.100",
            )
            self.assertRedirects(success_resp, reverse("billing"))
            self.assertIn("_auth_user_id", self.client.session)


class CustomErrorPagesAndRoleBadgeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")
        cls.owner_user = User.objects.create_user(username="badge_owner", password="password123")
        cls.owner_user.groups.add(cls.owner_group)
        cls.cashier_user = User.objects.create_user(username="badge_cashier", password="password123")
        cls.cashier_user.groups.add(cls.cashier_group)
        cls.norole_user = User.objects.create_user(username="badge_norole", password="password123")

    def test_no_role_user_gets_styled_403_with_logout_form(self):
        """Logged in user with no group gets styled 403.html with username and logout form."""
        self.client.login(username="badge_norole", password="password123")
        resp = self.client.get(reverse("billing"))
        self.assertEqual(resp.status_code, 403)
        self.assertTemplateUsed(resp, "403.html")
        self.assertContains(resp, "badge_norole", status_code=403)
        self.assertContains(resp, "No role", status_code=403)
        self.assertContains(resp, "Access Denied", status_code=403)
        self.assertContains(resp, reverse("logout"), status_code=403)
        self.assertContains(resp, "Log out", status_code=403)

    def test_cashier_on_owner_page_gets_403_with_billing_home_link(self):
        """Cashier accessing Owner-only page gets 403 with link to billing counter."""
        self.client.login(username="badge_cashier", password="password123")
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 403)
        self.assertTemplateUsed(resp, "403.html")
        self.assertContains(resp, "badge_cashier", status_code=403)
        self.assertContains(resp, reverse("billing"), status_code=403)
        self.assertContains(resp, "Go to Billing Counter", status_code=403)

    def test_custom_404_page_rendered(self):
        """Requesting an invalid URL renders custom 404.html template."""
        self.client.login(username="badge_cashier", password="password123")
        resp = self.client.get("/non-existent-page-url-xyz/")
        self.assertEqual(resp.status_code, 404)
        self.assertTemplateUsed(resp, "404.html")
        self.assertContains(resp, "Page Not Found", status_code=404)

    def test_role_badge_text_for_owner_and_cashier(self):
        """Sidebar footer renders correct role badge text and username for Owner and Cashier."""
        # Test Owner
        self.client.login(username="badge_owner", password="password123")
        owner_resp = self.client.get(reverse("dashboard"))
        self.assertEqual(owner_resp.status_code, 200)
        self.assertContains(owner_resp, "badge_owner")
        self.assertContains(owner_resp, "Owner")

        # Test Cashier
        self.client.login(username="badge_cashier", password="password123")
        cashier_resp = self.client.get(reverse("billing"))
        self.assertEqual(cashier_resp.status_code, 200)
        self.assertContains(cashier_resp, "badge_cashier")
        self.assertContains(cashier_resp, "Cashier")

    def test_500_template_renders_without_db_or_context_processors(self):
        """500.html is standalone and renders cleanly with empty context."""
        from django.template.loader import render_to_string

        rendered = render_to_string("500.html", {})
        self.assertIn("Server Error (500)", rendered)
        self.assertIn("Return to Home", rendered)


class HeldOrderAndTokenCounterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")

        cls.cashier1 = User.objects.create_user(username="cashier_one", password="password123")
        cls.cashier1.groups.add(cls.cashier_group)

        cls.cashier2 = User.objects.create_user(username="cashier_two", password="password123")
        cls.cashier2.groups.add(cls.cashier_group)

        cls.product1 = Product.objects.create(
            name="Masala Chai",
            category="Tea",
            price=Decimal("15.00"),
            cost_price=Decimal("5.00"),
            stock=Decimal("100.00"),
            stock_tracked=True,
        )
        cls.product2 = Product.objects.create(
            name="Bun Maska",
            category="Snacks",
            price=Decimal("30.00"),
            cost_price=Decimal("12.00"),
            stock=Decimal("50.00"),
            stock_tracked=True,
        )
        cls.customer = Customer.objects.create(
            name="Rahul Sharma",
            phone="9988776655",
        )

    def test_hold_recall_roundtrip(self):
        """Hold an order, inspect list, and recall it back into an empty cart."""
        self.client.login(username="cashier_one", password="password123")
        hold_url = reverse("held_order_hold")
        payload = {
            "label": "Table 4",
            "items": [
                {"id": self.product1.id, "name": self.product1.name, "price": "15.00", "quantity": 2},
                {"id": self.product2.id, "name": self.product2.name, "price": "30.00", "quantity": 1},
            ],
            "discount": "5.00",
            "customer_id": self.customer.id,
        }
        hold_resp = self.client.post(hold_url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(hold_resp.status_code, 200)
        hold_data = hold_resp.json()
        self.assertTrue(hold_data.get("ok"))
        order_id = hold_data["id"]

        held_order = HeldOrder.objects.get(pk=order_id)
        self.assertEqual(held_order.label, "Table 4")
        self.assertEqual(held_order.cashier, self.cashier1)
        self.assertEqual(held_order.customer, self.customer)
        self.assertEqual(held_order.discount, Decimal("5.00"))

        # Verify listed in GET held_order_list
        list_url = reverse("held_order_list")
        list_resp = self.client.get(list_url)
        self.assertEqual(list_resp.status_code, 200)
        list_data = list_resp.json()
        self.assertEqual(list_data["count"], 1)
        listed_order = list_data["orders"][0]
        self.assertEqual(listed_order["id"], order_id)
        self.assertEqual(listed_order["label"], "Table 4")
        self.assertEqual(listed_order["total"], "55.00")  # (15*2 + 30*1) - 5 = 55.00
        self.assertEqual(listed_order["item_count"], 3)

        # Recall order into empty cart
        recall_url = reverse("held_order_recall", kwargs={"order_id": order_id})
        recall_resp = self.client.post(recall_url, data=json.dumps({"cart_has_items": False}), content_type="application/json")
        self.assertEqual(recall_resp.status_code, 200)
        recall_data = recall_resp.json()
        self.assertTrue(recall_data.get("ok"))
        self.assertEqual(recall_data["order"]["id"], order_id)
        self.assertEqual(recall_data["order"]["label"], "Table 4")
        self.assertEqual(len(recall_data["order"]["items"]), 2)
        self.assertEqual(recall_data["order"]["customer_id"], self.customer.id)

        # Ensure order was deleted after recall
        self.assertFalse(HeldOrder.objects.filter(pk=order_id).exists())

    def test_recall_into_non_empty_cart_rejected_without_replace_true(self):
        """Recall into a non-empty cart is rejected server-side with 400 unless replace=true."""
        self.client.login(username="cashier_one", password="password123")
        held = HeldOrder.objects.create(
            cashier=self.cashier1,
            label="Pending Customer",
            items=[{"id": self.product1.id, "quantity": 1}],
        )
        recall_url = reverse("held_order_recall", kwargs={"order_id": held.id})

        # Request with cart_has_items=True without replace=true should fail (400)
        resp_reject = self.client.post(
            recall_url,
            data=json.dumps({"cart_has_items": True, "replace": False}),
            content_type="application/json",
        )
        self.assertEqual(resp_reject.status_code, 400)
        self.assertIn("Cart is not empty", resp_reject.json().get("error", ""))
        self.assertTrue(HeldOrder.objects.filter(pk=held.id).exists())

        # Request with cart_has_items=True and replace=True should succeed (200)
        resp_accept = self.client.post(
            recall_url,
            data=json.dumps({"cart_has_items": True, "replace": True}),
            content_type="application/json",
        )
        self.assertEqual(resp_accept.status_code, 200)
        self.assertTrue(resp_accept.json().get("ok"))
        self.assertFalse(HeldOrder.objects.filter(pk=held.id).exists())

    def test_token_number_assigned_and_resets_daily(self):
        """Bill.token_number is assigned sequentially on save and resets daily."""
        day1 = timezone.now() - timedelta(days=2)
        bill1 = Bill.objects.create(
            bill_number="BILL-DAY1-001",
            subtotal=Decimal("15.00"),
            grand_total=Decimal("15.00"),
            payment_method=Bill.CASH,
            created_at=day1,
        )
        self.assertEqual(bill1.token_number, 1)

        bill2 = Bill.objects.create(
            bill_number="BILL-DAY1-002",
            subtotal=Decimal("30.00"),
            grand_total=Decimal("30.00"),
            payment_method=Bill.CASH,
            created_at=day1,
        )
        self.assertEqual(bill2.token_number, 2)

        # Day 2: token number must reset back to 1
        day2 = timezone.now() - timedelta(days=1)
        bill3 = Bill.objects.create(
            bill_number="BILL-DAY2-001",
            subtotal=Decimal("45.00"),
            grand_total=Decimal("45.00"),
            payment_method=Bill.CASH,
            created_at=day2,
        )
        self.assertEqual(bill3.token_number, 1)

        bill4 = Bill.objects.create(
            bill_number="BILL-DAY2-002",
            subtotal=Decimal("15.00"),
            grand_total=Decimal("15.00"),
            payment_method=Bill.CASH,
            created_at=day2,
        )
        self.assertEqual(bill4.token_number, 2)

    def test_bill_create_returns_token_number(self):
        """bill_create endpoint returns token_number in response."""
        Shift.objects.create(opened_by=self.cashier1, opening_cash=Decimal("500.00"), status=Shift.STATUS_OPEN)
        self.client.login(username="cashier_one", password="password123")
        create_url = reverse("bill_create")
        payload = {
            "items": [{"id": self.product1.id, "quantity": 1}],
            "payment_method": Bill.CASH,
            "discount": "0",
        }
        resp = self.client.post(create_url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("token_number", data)
        self.assertIsNotNone(data["token_number"])
        self.assertGreaterEqual(data["token_number"], 1)

    def test_held_orders_are_per_store_visible_to_any_cashier(self):
        """Held orders held by Cashier 1 are visible to Cashier 2."""
        held = HeldOrder.objects.create(
            cashier=self.cashier1,
            label="Cashier 1 hold",
            items=[{"id": self.product1.id, "quantity": 1, "price": "15.00"}],
        )

        # Cashier 2 logs in and checks held orders list
        self.client.login(username="cashier_two", password="password123")
        list_url = reverse("held_order_list")
        resp = self.client.get(list_url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        order_ids = [o["id"] for o in data["orders"]]
        self.assertIn(held.id, order_ids)

    def test_held_orders_older_than_24h_purged_on_list(self):
        """Orders older than 24h are deleted when list endpoint is accessed."""
        self.client.login(username="cashier_one", password="password123")
        old_time = timezone.now() - timedelta(hours=26)
        old_order = HeldOrder.objects.create(
            cashier=self.cashier1,
            label="Old Order",
            items=[{"id": self.product1.id, "quantity": 1, "price": "15.00"}],
            created_at=old_time,
        )
        recent_order = HeldOrder.objects.create(
            cashier=self.cashier1,
            label="Recent Order",
            items=[{"id": self.product2.id, "quantity": 1, "price": "30.00"}],
        )

        resp = self.client.get(reverse("held_order_list"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        order_ids = [o["id"] for o in data["orders"]]
        self.assertNotIn(old_order.id, order_ids)
        self.assertIn(recent_order.id, order_ids)
        self.assertFalse(HeldOrder.objects.filter(pk=old_order.id).exists())
        self.assertTrue(HeldOrder.objects.filter(pk=recent_order.id).exists())

    def test_held_order_discard(self):
        """Discard endpoint permanently deletes a held order."""
        self.client.login(username="cashier_one", password="password123")
        order = HeldOrder.objects.create(
            cashier=self.cashier1,
            label="To Discard",
            items=[{"id": self.product1.id, "quantity": 1}],
        )
        discard_url = reverse("held_order_discard", kwargs={"order_id": order.id})
        resp = self.client.post(discard_url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("ok"))
        self.assertFalse(HeldOrder.objects.filter(pk=order.id).exists())


class ShiftManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")

        cls.cashier = User.objects.create_user(username="shift_cashier", password="password123")
        cls.cashier.groups.add(cls.cashier_group)

        cls.owner = User.objects.create_user(username="shift_owner", password="password123")
        cls.owner.groups.add(cls.owner_group)

        cls.product = Product.objects.create(
            name="Espresso",
            category="Coffee",
            price=Decimal("50.00"),
            cost_price=Decimal("15.00"),
            stock=Decimal("100.00"),
            stock_tracked=True,
        )
        cls.customer = Customer.objects.create(
            name="Suresh Verma",
            phone="9123456789",
        )

    def setUp(self):
        Shift.objects.all().delete()

    def test_open_shift(self):
        """Cashier can open a shift with opening cash, recorded and audit-logged."""
        self.client.login(username="shift_cashier", password="password123")
        resp = self.client.post(reverse("shift_open"), {"opening_cash": "350.00"})
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse("billing"))

        open_shifts = Shift.objects.filter(status=Shift.STATUS_OPEN)
        self.assertEqual(open_shifts.count(), 1)
        shift = open_shifts.first()
        self.assertEqual(shift.opened_by, self.cashier)
        self.assertEqual(shift.opening_cash, Decimal("350.00"))

        log = AuditLog.objects.filter(action="SHIFT_OPEN").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.cashier)

    def test_bill_create_blocked_without_open_shift(self):
        """Creating a bill is blocked with a clear message and link if no shift is open."""
        self.client.login(username="shift_cashier", password="password123")
        self.assertIsNone(Shift.current_open())

        create_url = reverse("bill_create")
        payload = {
            "items": [{"id": self.product.id, "quantity": 1}],
            "payment_method": Bill.CASH,
            "discount": "0",
        }
        resp = self.client.post(create_url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn("error", data)
        self.assertIn("shift", data["error"].lower())
        self.assertIn(reverse("shift_open"), data["error"])
        self.assertTrue(data.get("shift_required"))
        self.assertEqual(data.get("open_shift_url"), reverse("shift_open"))
        self.assertEqual(Bill.objects.count(), 0)

    def test_expected_cash_math(self):
        """Expected cash accurately calculates: opening + cash sales + khata collections - drawer expenses."""
        self.client.login(username="shift_cashier", password="password123")
        shift = Shift.objects.create(
            opened_by=self.cashier,
            opening_cash=Decimal("500.00"),
            status=Shift.STATUS_OPEN,
        )

        bill1 = Bill.objects.create(
            shift=shift,
            bill_number="BILL-TEST-CASH-1",
            subtotal=Decimal("200.00"),
            grand_total=Decimal("200.00"),
            payment_method=Bill.CASH,
        )
        BillPayment.objects.create(bill=bill1, method=Bill.CASH, amount=Decimal("200.00"))

        bill2 = Bill.objects.create(
            shift=shift,
            bill_number="BILL-TEST-UPI-1",
            subtotal=Decimal("150.00"),
            grand_total=Decimal("150.00"),
            payment_method=Bill.UPI,
        )
        BillPayment.objects.create(bill=bill2, method=Bill.UPI, amount=Decimal("150.00"))

        bill3 = Bill.objects.create(
            shift=shift,
            bill_number="BILL-TEST-SPLIT-1",
            subtotal=Decimal("150.00"),
            grand_total=Decimal("150.00"),
            payment_method=Bill.SPLIT,
        )
        BillPayment.objects.create(bill=bill3, method=Bill.CASH, amount=Decimal("50.00"))
        BillPayment.objects.create(bill=bill3, method=Bill.UPI, amount=Decimal("100.00"))

        KhataTransaction.objects.create(
            customer=self.customer,
            kind=KhataTransaction.PAYMENT,
            amount=Decimal("100.00"),
            note="Payment received",
        )

        Expense.objects.create(
            shift=shift,
            title="Milk",
            category="Supplies",
            amount=Decimal("80.00"),
            paid_from_drawer=True,
        )

        Expense.objects.create(
            shift=shift,
            title="Internet",
            category="Utilities",
            amount=Decimal("60.00"),
            paid_from_drawer=False,
        )

        resp = self.client.get(reverse("day_close"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["opening_cash"], Decimal("500.00"))
        self.assertEqual(resp.context["cash_sales"], Decimal("250.00"))
        self.assertEqual(resp.context["cash_khata"], Decimal("100.00"))
        self.assertEqual(resp.context["drawer_expenses"], Decimal("80.00"))
        self.assertEqual(resp.context["expected_cash"], Decimal("770.00"))

    def test_short_and_excess_variance(self):
        """Closing shift records short (negative) and excess (positive) variance properly."""
        self.client.login(username="shift_cashier", password="password123")
        shift = Shift.objects.create(
            opened_by=self.cashier,
            opening_cash=Decimal("500.00"),
            status=Shift.STATUS_OPEN,
        )

        # Case 1: Short / Deficit (counted 470.00 -> variance -30.00)
        close_resp = self.client.post(
            reverse("day_close"),
            {"counted_cash": "470.00", "note": "Cash short due to loose change shortage"},
        )
        self.assertEqual(close_resp.status_code, 302)
        shift.refresh_from_db()
        self.assertEqual(shift.status, Shift.STATUS_CLOSED)
        self.assertEqual(shift.counted_cash, Decimal("470.00"))
        self.assertEqual(shift.expected_cash, Decimal("500.00"))
        self.assertEqual(shift.variance, Decimal("-30.00"))
        self.assertEqual(shift.closed_by, self.cashier)
        self.assertIsNotNone(shift.closed_at)

        # Case 2: Excess / Over (counted 525.00 -> variance +25.00)
        self.client.login(username="shift_owner", password="password123")
        self.client.post(reverse("shift_reopen", kwargs={"shift_id": shift.id}))
        shift.refresh_from_db()
        self.assertEqual(shift.status, Shift.STATUS_OPEN)

        close_resp2 = self.client.post(
            reverse("day_close"),
            {"counted_cash": "525.00", "note": "Extra tips left in drawer"},
        )
        self.assertEqual(close_resp2.status_code, 302)
        shift.refresh_from_db()
        self.assertEqual(shift.status, Shift.STATUS_CLOSED)
        self.assertEqual(shift.counted_cash, Decimal("525.00"))
        self.assertEqual(shift.variance, Decimal("25.00"))

    def test_double_open_blocked(self):
        """Double opening is blocked when a shift is already active."""
        self.client.login(username="shift_cashier", password="password123")
        Shift.objects.create(
            opened_by=self.cashier,
            opening_cash=Decimal("100.00"),
            status=Shift.STATUS_OPEN,
        )

        resp = self.client.post(reverse("shift_open"), {"opening_cash": "200.00"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Shift.objects.filter(status=Shift.STATUS_OPEN).count(), 1)

    def test_owner_only_reopen(self):
        """Closing is final; only an Owner can reopen, and the action is audit-logged."""
        shift = Shift.objects.create(
            opened_by=self.cashier,
            opening_cash=Decimal("500.00"),
            closed_by=self.cashier,
            closed_at=timezone.now(),
            status=Shift.STATUS_CLOSED,
            counted_cash=Decimal("500.00"),
            expected_cash=Decimal("500.00"),
            variance=Decimal("0.00"),
        )
        reopen_url = reverse("shift_reopen", kwargs={"shift_id": shift.id})

        # Cashier attempt is blocked with 403 Forbidden
        self.client.login(username="shift_cashier", password="password123")
        resp_cashier = self.client.post(reopen_url)
        self.assertEqual(resp_cashier.status_code, 403)
        shift.refresh_from_db()
        self.assertEqual(shift.status, Shift.STATUS_CLOSED)

        # Owner attempt succeeds
        self.client.login(username="shift_owner", password="password123")
        resp_owner = self.client.post(reopen_url)
        self.assertEqual(resp_owner.status_code, 302)
        shift.refresh_from_db()
        self.assertEqual(shift.status, Shift.STATUS_OPEN)
        self.assertIsNone(shift.closed_at)
        self.assertIsNone(shift.closed_by)

        log = AuditLog.objects.filter(action="SHIFT_REOPEN").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.owner)


class ModifierAndOrderNoteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")

        cls.owner = User.objects.create_user(username="mod_owner", password="password123")
        cls.owner.groups.add(cls.owner_group)

        cls.cashier = User.objects.create_user(username="mod_cashier", password="password123")
        cls.cashier.groups.add(cls.cashier_group)

        cls.product = Product.objects.create(
            name="Espresso",
            category="Beverages",
            price=Decimal("40.00"),
            cost_price=Decimal("15.00"),
            stock=Decimal("100.00"),
            stock_tracked=True,
            is_active=True,
        )
        cls.mod_caramel = Modifier.objects.create(
            name="Caramel Shot",
            price_delta=Decimal("15.00"),
            is_active=True,
        )
        cls.mod_vanilla = Modifier.objects.create(
            name="Vanilla Syrup",
            price_delta=Decimal("10.00"),
            is_active=True,
        )
        cls.product.modifiers.add(cls.mod_caramel, cls.mod_vanilla)

    def setUp(self):
        self.shift = Shift.objects.create(
            opened_by=self.cashier,
            opening_cash=Decimal("500.00"),
            status=Shift.STATUS_OPEN,
        )
        self.client.login(username="mod_cashier", password="password123")

    def test_price_delta_in_totals(self):
        """Line total and bill grand total include modifier price_delta and are recomputed server-side."""
        payload = {
            "items": [
                {
                    "id": self.product.id,
                    "quantity": "2",
                    "modifier_ids": [self.mod_caramel.id],
                }
            ],
            "payment_method": Bill.CASH,
            "discount": "0.00",
        }
        resp = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        bill = Bill.objects.get(pk=data["bill_id"])

        # Base price 40.00 + delta 15.00 = 55.00 unit price * 2 = 110.00
        self.assertEqual(bill.subtotal, Decimal("110.00"))
        self.assertEqual(bill.grand_total, Decimal("110.00"))

        item = bill.items.first()
        self.assertEqual(item.price, Decimal("55.00"))
        self.assertEqual(item.total, Decimal("110.00"))
        self.assertEqual(item.quantity, Decimal("2.00"))
        self.assertEqual(item.modifier_names, ["Caramel Shot"])

    def test_same_item_with_different_modifiers_stored_as_two_lines(self):
        """The same product with different modifier sets remains as separate BillItem rows."""
        payload = {
            "items": [
                {
                    "id": self.product.id,
                    "quantity": "1",
                    "modifier_ids": [self.mod_caramel.id],
                },
                {
                    "id": self.product.id,
                    "quantity": "1",
                    "modifier_ids": [self.mod_vanilla.id],
                },
            ],
            "payment_method": Bill.CASH,
            "discount": "0.00",
        }
        resp = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        bill = Bill.objects.get(pk=resp.json()["bill_id"])

        # 2 distinct lines for the same product
        self.assertEqual(bill.items.count(), 2)

        items = list(bill.items.all().order_by("price"))
        # Line 1: Vanilla (40 + 10 = 50)
        self.assertEqual(items[0].modifier_names, ["Vanilla Syrup"])
        self.assertEqual(items[0].price, Decimal("50.00"))
        self.assertEqual(items[0].total, Decimal("50.00"))

        # Line 2: Caramel (40 + 15 = 55)
        self.assertEqual(items[1].modifier_names, ["Caramel Shot"])
        self.assertEqual(items[1].price, Decimal("55.00"))
        self.assertEqual(items[1].total, Decimal("55.00"))

        self.assertEqual(bill.subtotal, Decimal("105.00"))

        # Stock deducted for 2 items
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal("98.00"))

    def test_invalid_modifier_id_rejected(self):
        """Invalid modifier ID (non-existent or not assigned to product) is rejected with 400."""
        # Non-existent modifier ID
        payload_nonexistent = {
            "items": [
                {
                    "id": self.product.id,
                    "quantity": 1,
                    "modifier_ids": [99999],
                }
            ],
            "payment_method": Bill.CASH,
        }
        resp1 = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload_nonexistent),
            content_type="application/json",
        )
        self.assertEqual(resp1.status_code, 400)
        self.assertIn("modifier", resp1.json().get("error", "").lower())

        # Modifier exists in DB but not allowed on this product
        unlinked_mod = Modifier.objects.create(name="Unlinked Topping", price_delta=Decimal("5.00"))
        payload_unlinked = {
            "items": [
                {
                    "id": self.product.id,
                    "quantity": 1,
                    "modifier_ids": [unlinked_mod.id],
                }
            ],
            "payment_method": Bill.CASH,
        }
        resp2 = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload_unlinked),
            content_type="application/json",
        )
        self.assertEqual(resp2.status_code, 400)
        self.assertIn("modifier", resp2.json().get("error", "").lower())

    def test_the_note_saved(self):
        """Free-text order note for the whole bill is saved on Bill.note and line note on BillItem.note."""
        payload = {
            "items": [
                {
                    "id": self.product.id,
                    "quantity": 1,
                    "modifier_ids": [self.mod_vanilla.id],
                    "note": "Extra hot please",
                }
            ],
            "payment_method": Bill.CASH,
            "note": "Customer wants takeaway packing and paper straws.",
        }
        resp = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        bill = Bill.objects.get(pk=resp.json()["bill_id"])

        self.assertEqual(bill.note, "Customer wants takeaway packing and paper straws.")
        item = bill.items.first()
        self.assertEqual(item.note, "Extra hot please")

        # Verify receipt prints modifiers and notes
        detail_resp = self.client.get(reverse("bill_detail", kwargs={"bill_id": bill.id}))
        self.assertEqual(detail_resp.status_code, 200)
        content = detail_resp.content.decode()
        self.assertIn("Vanilla Syrup", content)
        self.assertIn("Extra hot please", content)
        self.assertIn("Customer wants takeaway packing and paper straws.", content)

    def test_owner_menu_page_modifier_management(self):
        """Owner can view, create, edit, and delete modifiers on the menu catalog."""
        self.client.login(username="mod_owner", password="password123")

        # 1. View menu page
        menu_resp = self.client.get(reverse("menu_items"))
        self.assertEqual(menu_resp.status_code, 200)
        self.assertIn("Caramel Shot", menu_resp.content.decode())

        # 2. Create modifier
        create_resp = self.client.post(
            reverse("modifier_create"),
            data={"name": "Oat Milk", "price_delta": "20.00", "is_active": "on"},
        )
        self.assertEqual(create_resp.status_code, 302)
        new_mod = Modifier.objects.get(name="Oat Milk")
        self.assertEqual(new_mod.price_delta, Decimal("20.00"))

        # 3. Edit modifier
        edit_resp = self.client.post(
            reverse("modifier_edit", kwargs={"modifier_id": new_mod.id}),
            data={"name": "Almond Milk", "price_delta": "25.00", "is_active": "on"},
        )
        self.assertEqual(edit_resp.status_code, 302)
        new_mod.refresh_from_db()
        self.assertEqual(new_mod.name, "Almond Milk")
        self.assertEqual(new_mod.price_delta, Decimal("25.00"))

        # 4. Delete modifier
        del_resp = self.client.post(
            reverse("modifier_delete", kwargs={"modifier_id": new_mod.id})
        )
        self.assertEqual(del_resp.status_code, 302)
        self.assertFalse(Modifier.objects.filter(pk=new_mod.id).exists())


class ProductCatalogAndManagementTests(TestCase):
    """
    Tests covering Prompt 10:
    - Product.is_favorite and Product.sort_order persistence and billing ordering
    - Reorder persistence
    - Bulk price edit: preview vs apply
    - Delete button hidden and server-side blocked when sold
    - CSV import reporting exact line number on bad row
    - Shortcut uniqueness enforced on duplicate
    - Category rename across all items
    - CSV export and favorite toggle
    """

    @classmethod
    def setUpTestData(cls):
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")

        cls.owner = User.objects.create_user(username="catalog_owner", password="password123")
        cls.owner.groups.add(cls.owner_group)

        cls.cashier = User.objects.create_user(username="catalog_cashier", password="password123")
        cls.cashier.groups.add(cls.cashier_group)

    def setUp(self):
        self.client.login(username="catalog_owner", password="password123")

    def test_reorder_persistence(self):
        """Reordering menu items updates sort_order and persists in queries and billing."""
        p1 = Product.objects.create(name="Item A", price=Decimal("10.00"), sort_order=0)
        p2 = Product.objects.create(name="Item B", price=Decimal("20.00"), sort_order=1)
        p3 = Product.objects.create(name="Item C", price=Decimal("30.00"), sort_order=2)

        # 1. Reorder via JSON list of IDs
        resp = self.client.post(
            reverse("menu_item_reorder"),
            data=json.dumps({"order": [p3.id, p1.id, p2.id]}),
            content_type="application/json",
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("ok"))

        p1.refresh_from_db()
        p2.refresh_from_db()
        p3.refresh_from_db()

        self.assertEqual(p3.sort_order, 0)
        self.assertEqual(p1.sort_order, 1)
        self.assertEqual(p2.sort_order, 2)

        ordered_names = list(Product.objects.filter(id__in=[p1.id, p2.id, p3.id]).values_list("name", flat=True))
        self.assertEqual(ordered_names, ["Item C", "Item A", "Item B"])

        # 2. Reorder via direction up/down
        resp2 = self.client.post(
            reverse("menu_item_reorder"),
            data=json.dumps({"item_id": p2.id, "direction": "up"}),
            content_type="application/json",
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resp2.status_code, 200)
        p1.refresh_from_db()
        p2.refresh_from_db()
        self.assertLess(p2.sort_order, p1.sort_order)

    def test_bulk_price_preview_vs_apply(self):
        """Bulk price edit correctly previews changes without modifying DB, then applies them."""
        p1 = Product.objects.create(name="Espresso", price=Decimal("100.00"))
        p2 = Product.objects.create(name="Cappuccino", price=Decimal("50.00"))

        # 1. Preview percent increase (+10%)
        preview_resp = self.client.post(
            reverse("menu_items_bulk_price"),
            data=json.dumps({
                "action": "preview",
                "item_ids": [p1.id, p2.id],
                "change_type": "percent",
                "value": "10",
            }),
            content_type="application/json",
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(preview_resp.status_code, 200)
        preview_data = preview_resp.json()
        self.assertTrue(preview_data.get("ok"))
        self.assertEqual(preview_data.get("action"), "preview")

        previews = {item["id"]: item for item in preview_data["preview"]}
        self.assertEqual(previews[p1.id]["new_price"], "110.00")
        self.assertEqual(previews[p2.id]["new_price"], "55.00")

        # Verify DB is NOT modified after preview
        p1.refresh_from_db()
        p2.refresh_from_db()
        self.assertEqual(p1.price, Decimal("100.00"))
        self.assertEqual(p2.price, Decimal("50.00"))

        # 2. Preview flat decrease (-5)
        flat_preview_resp = self.client.post(
            reverse("menu_items_bulk_price"),
            data=json.dumps({
                "action": "preview",
                "item_ids": [p1.id],
                "change_type": "flat",
                "value": "-5",
            }),
            content_type="application/json",
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(flat_preview_resp.status_code, 200)
        self.assertEqual(flat_preview_resp.json()["preview"][0]["new_price"], "95.00")
        p1.refresh_from_db()
        self.assertEqual(p1.price, Decimal("100.00"))

        # 3. Apply changes (+10%)
        apply_resp = self.client.post(
            reverse("menu_items_bulk_price"),
            data=json.dumps({
                "action": "apply",
                "item_ids": [p1.id, p2.id],
                "change_type": "percent",
                "value": "10",
            }),
            content_type="application/json",
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(apply_resp.status_code, 200)
        self.assertTrue(apply_resp.json().get("applied"))

        # Verify DB IS modified after apply
        p1.refresh_from_db()
        p2.refresh_from_db()
        self.assertEqual(p1.price, Decimal("110.00"))
        self.assertEqual(p2.price, Decimal("55.00"))

    def test_delete_hidden_and_blocked_when_sold(self):
        """Delete button is hidden in UI and delete is blocked server-side when an item has BillItems."""
        unsold = Product.objects.create(name="Unsold Item", price=Decimal("40.00"))
        sold = Product.objects.create(name="Sold Item", price=Decimal("60.00"))

        bill = Bill.objects.create(
            bill_number="BILL-SOLD-001",
            subtotal=Decimal("60.00"),
            discount=Decimal("0.00"),
            grand_total=Decimal("60.00"),
            payment_method=Bill.CASH,
            created_by=self.owner,
        )
        BillItem.objects.create(
            bill=bill,
            menu_item=sold,
            item_name=sold.name,
            price=sold.price,
            quantity=Decimal("1.00"),
            total=Decimal("60.00"),
        )

        # 1. View menu items page - Delete should be visible for unsold, hidden for sold
        get_resp = self.client.get(reverse("menu_items"))
        self.assertEqual(get_resp.status_code, 200)
        content = get_resp.content.decode()

        self.assertIn(f'action="/menu-items/{unsold.id}/delete/"', content)
        self.assertNotIn(f'action="/menu-items/{sold.id}/delete/"', content)

        # 2. Server-side deletion of sold item must be blocked
        sold_del_resp = self.client.post(reverse("menu_item_delete", kwargs={"item_id": sold.id}))
        self.assertTrue(Product.objects.filter(pk=sold.id).exists())

        # With JSON / AJAX header, returns 400 error
        sold_ajax_del = self.client.post(
            reverse("menu_item_delete", kwargs={"item_id": sold.id}),
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(sold_ajax_del.status_code, 400)
        self.assertIn("cannot be deleted", sold_ajax_del.json().get("error", ""))
        self.assertTrue(Product.objects.filter(pk=sold.id).exists())

        # 3. Unsold item can be deleted
        unsold_del_resp = self.client.post(reverse("menu_item_delete", kwargs={"item_id": unsold.id}))
        self.assertEqual(unsold_del_resp.status_code, 302)
        self.assertFalse(Product.objects.filter(pk=unsold.id).exists())

    def test_import_with_bad_row_reports_line_number(self):
        """CSV import with an invalid row stops import, rolls back, and reports the exact line number."""
        initial_count = Product.objects.count()

        # Line 1: Header
        # Line 2: Valid item
        # Line 3: Invalid price 'xyz'
        csv_data = "name,category,price,cost,shortcut,active\nGood Tea,Beverages,25.00,10.00,GT,true\nBad Coffee,Beverages,xyz,15.00,BC,true\n"
        csv_file = SimpleUploadedFile("items.csv", csv_data.encode("utf-8"), content_type="text/csv")

        resp = self.client.post(
            reverse("menu_items_import_csv"),
            {"csv_file": csv_file},
        )
        self.assertEqual(resp.status_code, 400)
        content = resp.content.decode()
        self.assertIn("Line 3", content)
        self.assertEqual(resp.json().get("line"), 3)

        # Verify rollback: Good Tea from line 2 was not saved
        self.assertEqual(Product.objects.count(), initial_count)
        self.assertFalse(Product.objects.filter(name="Good Tea").exists())

        # Also test Line 4 missing name
        csv_data_bad_name = "name,category,price,cost,shortcut,active\nItem 1,Food,30.00,10.00,I1,true\nItem 2,Food,40.00,10.00,I2,true\n,Food,50.00,10.00,I3,true\n"
        csv_file_bad_name = SimpleUploadedFile("items_bad_name.csv", csv_data_bad_name.encode("utf-8"), content_type="text/csv")

        resp2 = self.client.post(
            reverse("menu_items_import_csv"),
            {"csv_file": csv_file_bad_name},
        )
        self.assertEqual(resp2.status_code, 400)
        self.assertIn("Line 4", resp2.content.decode())
        self.assertEqual(resp2.json().get("line"), 4)

        # Successful import test
        valid_csv = "name,category,price,cost,shortcut,active\nFresh Chai,Beverages,20.00,5.00,FC,true\nSamosa,Snacks,15.00,5.00,SM,true\n"
        valid_file = SimpleUploadedFile("valid.csv", valid_csv.encode("utf-8"), content_type="text/csv")
        resp3 = self.client.post(
            reverse("menu_items_import_csv"),
            {"csv_file": valid_file},
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resp3.status_code, 200)
        self.assertTrue(resp3.json().get("ok"))
        self.assertTrue(Product.objects.filter(name="Fresh Chai").exists())
        self.assertTrue(Product.objects.filter(name="Samosa").exists())

    def test_shortcut_uniqueness_still_enforced_on_duplicate(self):
        """Duplicating an active item enforces shortcut uniqueness and rejects colliding shortcuts."""
        orig = Product.objects.create(
            name="Espresso Double",
            price=Decimal("120.00"),
            shortcut_key="ED",
            is_active=True,
        )
        other = Product.objects.create(
            name="Green Tea",
            price=Decimal("50.00"),
            shortcut_key="GT",
            is_active=True,
        )

        initial_count = Product.objects.count()

        # 1. Duplicating orig without specifying shortcut defaults to orig's shortcut 'ED', colliding with active orig
        resp_colliding_orig = self.client.post(
            reverse("menu_item_duplicate", kwargs={"item_id": orig.id}),
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resp_colliding_orig.status_code, 400)
        self.assertIn("already in use", resp_colliding_orig.json().get("error", ""))
        self.assertEqual(Product.objects.count(), initial_count)

        # 2. Specifying another active product's shortcut 'GT' is also rejected
        resp_colliding_other = self.client.post(
            reverse("menu_item_duplicate", kwargs={"item_id": orig.id}),
            data={"shortcut_key": "GT"},
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resp_colliding_other.status_code, 400)
        self.assertIn("already in use", resp_colliding_other.json().get("error", ""))
        self.assertEqual(Product.objects.count(), initial_count)

        # 3. Duplicating with a unique shortcut 'ED2' or empty shortcut '' succeeds
        resp_success = self.client.post(
            reverse("menu_item_duplicate", kwargs={"item_id": orig.id}),
            data={"name": "Espresso Double Decaf", "shortcut_key": "ED2"},
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resp_success.status_code, 200)
        self.assertEqual(Product.objects.count(), initial_count + 1)
        cloned = Product.objects.get(name="Espresso Double Decaf")
        self.assertEqual(cloned.shortcut_key, "ED2")
        self.assertEqual(cloned.price, orig.price)

    def test_category_rename(self):
        """Category rename updates all items with the old category name in a single action."""
        p1 = Product.objects.create(name="Item 1", category="Hot Drinks", price=Decimal("30.00"))
        p2 = Product.objects.create(name="Item 2", category="Hot Drinks", price=Decimal("40.00"))
        p3 = Product.objects.create(name="Item 3", category="Cold Drinks", price=Decimal("50.00"))

        resp = self.client.post(
            reverse("category_rename"),
            data={"old_category": "Hot Drinks", "new_category": "Warm Beverages"},
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("updated"), 2)

        p1.refresh_from_db()
        p2.refresh_from_db()
        p3.refresh_from_db()
        self.assertEqual(p1.category, "Warm Beverages")
        self.assertEqual(p2.category, "Warm Beverages")
        self.assertEqual(p3.category, "Cold Drinks")

    def test_favorite_star_toggle_and_billing_favorites(self):
        """Toggling favorite updates is_favorite and appears in billing favorites strip."""
        p = Product.objects.create(name="Special Latte", price=Decimal("90.00"), is_favorite=False)

        # Toggle favorite to True
        resp = self.client.post(
            reverse("menu_item_toggle_favorite", kwargs={"item_id": p.id}),
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("is_favorite"))
        p.refresh_from_db()
        self.assertTrue(p.is_favorite)

        # Check billing view context
        billing_resp = self.client.get(reverse("billing"))
        self.assertEqual(billing_resp.status_code, 200)
        favs = billing_resp.context["favorites"]
        self.assertIn(p.id, [f.id for f in favs])

        # Toggle favorite back to False
        resp2 = self.client.post(
            reverse("menu_item_toggle_favorite", kwargs={"item_id": p.id}),
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resp2.status_code, 200)
        self.assertFalse(resp2.json().get("is_favorite"))
        p.refresh_from_db()
        self.assertFalse(p.is_favorite)

    def test_export_menu_items_csv(self):
        """Owner can export CSV containing name, category, price, cost, shortcut, active."""
        Product.objects.create(name="Filter Coffee", category="Coffee", price=Decimal("35.00"), cost_price=Decimal("12.00"), shortcut_key="FC", is_active=True)
        resp = self.client.get(reverse("menu_items_export_csv"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv; charset=utf-8")
        content = resp.content.decode("utf-8-sig")
        self.assertIn("name,category,price,cost,shortcut,active", content)
        self.assertIn("Filter Coffee,Coffee,35.00,12.00,FC,true", content)


class KhataAgeingAndCreditManagementTests(TestCase):
    """
    Tests covering:
    - FIFO ageing buckets (0-30, 31-60, 61-90, 90+ days)
    - Credit limit blocking and Owner override in bill_create
    - Payment method required (CASH, UPI, CARD)
    - Statement page totals matching balance
    - Khata search, dues filter, sorting, and pagination (50)
    """

    @classmethod
    def setUpTestData(cls):
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")

        cls.owner = User.objects.create_user(username="khata_owner", password="password123")
        cls.owner.groups.add(cls.owner_group)

        cls.cashier = User.objects.create_user(username="khata_cashier", password="password123")
        cls.cashier.groups.add(cls.cashier_group)

        cls.product = Product.objects.create(
            name="Espresso",
            price=Decimal("150.00"),
            cost_price=Decimal("50.00"),
            stock=Decimal("100.00"),
            is_active=True,
        )

    def setUp(self):
        self.shift = Shift.objects.create(
            status=Shift.STATUS_OPEN,
            opened_by=self.owner,
            opened_at=timezone.now(),
            opening_cash=Decimal("1000.00"),
        )
        self.client.login(username="khata_cashier", password="password123")

    def test_fifo_ageing_buckets(self):
        """FIFO allocates payments to opening balance and oldest credits first, matching balance."""
        now = timezone.now()
        cust = Customer.objects.create(
            name="Vikram Seth",
            phone="9876543210",
            opening_balance=Decimal("200.00"),
        )

        # 4 credit transactions at different ages
        c1 = KhataTransaction.objects.create(
            customer=cust,
            kind=KhataTransaction.CREDIT,
            amount=Decimal("300.00"),
            created_at=now - timedelta(days=100),
        )
        c2 = KhataTransaction.objects.create(
            customer=cust,
            kind=KhataTransaction.CREDIT,
            amount=Decimal("150.00"),
            created_at=now - timedelta(days=70),
        )
        c3 = KhataTransaction.objects.create(
            customer=cust,
            kind=KhataTransaction.CREDIT,
            amount=Decimal("250.00"),
            created_at=now - timedelta(days=45),
        )
        c4 = KhataTransaction.objects.create(
            customer=cust,
            kind=KhataTransaction.CREDIT,
            amount=Decimal("100.00"),
            created_at=now - timedelta(days=15),
        )

        # Total debt: 200 (opening) + 300 + 150 + 250 + 100 = 1000.00
        # Add 2 payments totaling 450.00 (350 + 100)
        KhataTransaction.objects.create(
            customer=cust,
            kind=KhataTransaction.PAYMENT,
            method="CASH",
            amount=Decimal("350.00"),
            created_at=now - timedelta(days=5),
        )
        KhataTransaction.objects.create(
            customer=cust,
            kind=KhataTransaction.PAYMENT,
            method="UPI",
            amount=Decimal("100.00"),
            created_at=now - timedelta(days=2),
        )

        # Outstanding balance = 1000.00 - 450.00 = 550.00
        cust.refresh_from_db()
        self.assertEqual(cust.balance, Decimal("550.00"))

        ageing = compute_customer_ageing(cust)

        # FIFO Breakdown:
        # Payments 450:
        # - Pays 200 opening balance (unpaid opening = 0)
        # - Pays 250 of Credit 1 (300 - 250 = 50 unpaid, age 100d -> 90+ bucket)
        # - Credit 2 (150, age 70d -> 61-90 bucket)
        # - Credit 3 (250, age 45d -> 31-60 bucket)
        # - Credit 4 (100, age 15d -> 0-30 bucket)
        self.assertEqual(ageing["b_0_30"], Decimal("100.00"))
        self.assertEqual(ageing["b_31_60"], Decimal("250.00"))
        self.assertEqual(ageing["b_61_90"], Decimal("150.00"))
        self.assertEqual(ageing["b_90_plus"], Decimal("50.00"))
        self.assertEqual(ageing["total"], Decimal("550.00"))
        self.assertEqual(ageing["total"], cust.balance)

        # Test customer with zero balance
        cust_zero = Customer.objects.create(name="Zero Dues", opening_balance=Decimal("0.00"))
        zero_ageing = compute_customer_ageing(cust_zero)
        self.assertEqual(zero_ageing["total"], Decimal("0.00"))
        self.assertEqual(zero_ageing["b_0_30"], Decimal("0.00"))

    def test_credit_limit_blocked_for_cashier_and_unauthorized_owner(self):
        """Khata sale exceeding credit limit is blocked with 400 for cashier and owner without override."""
        customer = Customer.objects.create(
            name="Limited Customer",
            opening_balance=Decimal("400.00"),
            credit_limit=Decimal("500.00"),
        )
        # Existing balance = 400, limit = 500. Sale of 150 brings total to 550 (> 500 limit).

        sale_data = {
            "items": [{"id": self.product.id, "quantity": 1}],
            "customer_id": customer.id,
            "payment_method": Bill.KHATA,
            "discount": "0",
        }

        # 1. Cashier attempts sale exceeding limit -> Blocked
        resp = self.client.post(
            reverse("bill_create"),
            data=json.dumps(sale_data),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(resp.json().get("credit_limit_exceeded"))
        self.assertIn("exceeded", resp.json().get("error", "").lower())
        self.assertEqual(customer.balance, Decimal("400.00"))

        # 2. Cashier with override flag -> Still Blocked (only Owners can override)
        sale_data["override_credit_limit"] = True
        resp_cashier_override = self.client.post(
            reverse("bill_create"),
            data=json.dumps(sale_data),
            content_type="application/json",
        )
        self.assertEqual(resp_cashier_override.status_code, 400)
        self.assertTrue(resp_cashier_override.json().get("credit_limit_exceeded"))

        # 3. Owner attempts sale WITHOUT override flag -> Blocked
        self.client.login(username="khata_owner", password="password123")
        sale_data["override_credit_limit"] = False
        resp_owner_no_override = self.client.post(
            reverse("bill_create"),
            data=json.dumps(sale_data),
            content_type="application/json",
        )
        self.assertEqual(resp_owner_no_override.status_code, 400)
        self.assertTrue(resp_owner_no_override.json().get("credit_limit_exceeded"))

    def test_credit_limit_owner_override_success(self):
        """Owner with override flag successfully creates a sale exceeding credit limit."""
        self.client.login(username="khata_owner", password="password123")
        customer = Customer.objects.create(
            name="Override Customer",
            opening_balance=Decimal("400.00"),
            credit_limit=Decimal("500.00"),
        )

        sale_data = {
            "items": [{"id": self.product.id, "quantity": 1}],
            "customer_id": customer.id,
            "payment_method": Bill.KHATA,
            "discount": "0",
            "override_credit_limit": True,
        }

        resp = self.client.post(
            reverse("bill_create"),
            data=json.dumps(sale_data),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("bill_id"))

        customer.refresh_from_db()
        self.assertEqual(customer.balance, Decimal("550.00"))
        self.assertTrue(
            KhataTransaction.objects.filter(customer=customer, kind=KhataTransaction.CREDIT, amount=Decimal("150.00")).exists()
        )

    def test_payment_method_required(self):
        """Khata payment requires a valid payment method (CASH, UPI, CARD)."""
        customer = Customer.objects.create(
            name="Priya Patel",
            opening_balance=Decimal("200.00"),
        )
        url = reverse("khata_payment", kwargs={"customer_id": customer.id})

        # 1. Missing method
        resp_no_method = self.client.post(
            url,
            data={"amount": "100.00"},
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resp_no_method.status_code, 400)
        self.assertIn("Payment method is required", resp_no_method.json().get("error", ""))
        self.assertEqual(customer.balance, Decimal("200.00"))

        # 2. Invalid method
        resp_bad_method = self.client.post(
            url,
            data={"amount": "100.00", "method": "BITCOIN"},
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resp_bad_method.status_code, 400)
        self.assertIn("Invalid payment method", resp_bad_method.json().get("error", ""))

        # 3. Valid method (UPI)
        resp_valid = self.client.post(
            url,
            data={"amount": "100.00", "method": "UPI", "note": "GPay payment"},
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resp_valid.status_code, 200)
        self.assertEqual(resp_valid.json().get("method"), "UPI")

        customer.refresh_from_db()
        self.assertEqual(customer.balance, Decimal("100.00"))
        tx = KhataTransaction.objects.filter(customer=customer, kind=KhataTransaction.PAYMENT).latest("id")
        self.assertEqual(tx.method, "UPI")
        self.assertEqual(tx.amount, Decimal("100.00"))

    def test_statement_totals_match_balance(self):
        """Printable statement page renders opening balance, ledger, and ageing matching total balance."""
        customer = Customer.objects.create(
            name="Meera Kapoor",
            phone="9123456789",
            opening_balance=Decimal("150.00"),
            credit_limit=Decimal("1000.00"),
        )
        # Credit sale
        KhataTransaction.objects.create(
            customer=customer,
            kind=KhataTransaction.CREDIT,
            amount=Decimal("350.00"),
            note="Evening coffee",
        )
        # Partial payment
        KhataTransaction.objects.create(
            customer=customer,
            kind=KhataTransaction.PAYMENT,
            method="CASH",
            amount=Decimal("200.00"),
            note="Cash received",
        )

        customer.refresh_from_db()
        # Balance = 150 + 350 - 200 = 300.00
        self.assertEqual(customer.balance, Decimal("300.00"))

        resp = self.client.get(reverse("khata_statement", kwargs={"customer_id": customer.id}))
        self.assertEqual(resp.status_code, 200)

        # Context assertions
        self.assertEqual(resp.context["final_balance"], Decimal("300.00"))
        self.assertEqual(resp.context["ageing"]["total"], Decimal("300.00"))
        self.assertEqual(resp.context["opening_balance"], Decimal("150.00"))

        content = resp.content.decode()
        self.assertIn("Meera Kapoor", content)
        self.assertIn("300.00", content)
        self.assertIn("FIFO Ageing Summary", content)
        self.assertIn("Transaction Ledger", content)

    def test_khata_search_filter_sort_and_pagination(self):
        """Khata page supports name/phone search, dues filter, sorting, and pagination (50)."""
        # Create 60 customers: 40 with dues, 20 with 0 dues
        for i in range(60):
            dues = Decimal(f"{(i + 1) * 10}.00") if i < 40 else Decimal("0.00")
            c = Customer.objects.create(
                name=f"Customer {i+1:02d}",
                phone=f"90000000{i:02d}",
                opening_balance=dues,
            )
            if i == 0:
                # Add an old credit to Customer 01 to test oldest due sorting
                KhataTransaction.objects.create(
                    customer=c,
                    kind=KhataTransaction.CREDIT,
                    amount=Decimal("10.00"),
                    created_at=timezone.now() - timedelta(days=95),
                )

        # 1. Search by name
        search_resp = self.client.get(reverse("khata"), {"q": "Customer 05"})
        self.assertEqual(search_resp.status_code, 200)
        names = [c.name for c in search_resp.context["customers"]]
        self.assertEqual(names, ["Customer 05"])

        # 2. Filter "only with dues"
        dues_resp = self.client.get(reverse("khata"), {"has_dues": "1"})
        self.assertEqual(dues_resp.status_code, 200)
        for c in dues_resp.context["customers"]:
            self.assertGreater(c.balance, Decimal("0.00"))

        # 3. Sort by amount
        sort_resp = self.client.get(reverse("khata"), {"sort": "amount"})
        self.assertEqual(sort_resp.status_code, 200)
        balances = [c.balance for c in sort_resp.context["customers"]]
        self.assertEqual(balances, sorted(balances, reverse=True))

        # 4. Sort by oldest due
        oldest_resp = self.client.get(reverse("khata"), {"sort": "oldest_due"})
        self.assertEqual(oldest_resp.status_code, 200)
        # First customer should be Customer 01 who has the 95-day-old credit
        self.assertEqual(oldest_resp.context["customers"][0].name, "Customer 01")

        # 5. Pagination: 50 items on page 1, 10 on page 2
        page1_resp = self.client.get(reverse("khata"), {"page": "1"})
        self.assertEqual(len(page1_resp.context["customers"]), 50)
        self.assertTrue(page1_resp.context["page_obj"].has_next())

        page2_resp = self.client.get(reverse("khata"), {"page": "2"})
        self.assertEqual(len(page2_resp.context["customers"]), 10)
        self.assertFalse(page2_resp.context["page_obj"].has_next())


class ReceiptSettingsAndTaxTests(TestCase):
    """
    Tests covering:
    - Tax-inclusive and tax-exclusive math
    - Snapshot immutability after a settings change
    - Duplicate label on the second print (print_count tracking on ?print=1)
    - Default receipt mode fallback (absent ?mode uses default_receipt_mode)
    - Cashier name, FSSAI number, and Khata outstanding on receipt
    """

    @classmethod
    def setUpTestData(cls):
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")

        cls.owner = User.objects.create_user(username="receipt_owner", password="password123")
        cls.owner.groups.add(cls.owner_group)

        cls.cashier = User.objects.create_user(
            username="receipt_cashier",
            first_name="Ramesh",
            last_name="Kumar",
            password="password123",
        )
        cls.cashier.groups.add(cls.cashier_group)

        cls.shift = Shift.objects.create(
            opened_by=cls.owner,
            status=Shift.STATUS_OPEN,
            opening_cash=Decimal("1000.00"),
        )

        cls.coffee = Product.objects.create(
            name="Espresso Coffee",
            category="Beverages",
            price=Decimal("100.00"),
            cost_price=Decimal("40.00"),
            stock=Decimal("50.00"),
            is_active=True,
        )

        cls.tea = Product.objects.create(
            name="Masala Tea",
            category="Beverages",
            price=Decimal("105.00"),
            cost_price=Decimal("30.00"),
            stock=Decimal("50.00"),
            is_active=True,
        )

        cls.customer = Customer.objects.create(
            name="Suresh Raina",
            phone="9876543210",
            opening_balance=Decimal("200.00"),
        )

    def setUp(self):
        self.settings = BusinessSettings.current()
        self.settings.business_name = "Cafe Aroma"
        self.settings.tax_number = "GSTIN29AAAAA0000A1Z5"
        self.settings.fssai_number = "10019043002685"
        self.settings.default_receipt_mode = BusinessSettings.RECEIPT_MODE_A4
        self.settings.gst_enabled = False
        self.settings.gst_percent = Decimal("0.00")
        self.settings.prices_include_gst = False
        self.settings.show_cashier = True
        self.settings.save()

    def test_tax_inclusive_math(self):
        """Tax-inclusive math: prices include GST, grand_total matches net, tax extracted correctly."""
        self.settings.gst_enabled = True
        self.settings.gst_percent = Decimal("5.00")
        self.settings.prices_include_gst = True
        self.settings.save()

        self.client.login(username="receipt_cashier", password="password123")
        # Masala tea is 105.00
        payload = {
            "items": [{"id": self.tea.id, "quantity": 1}],
            "payment_method": "CASH",
            "discount": "0.00",
        }
        resp = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        bill_id = resp.json()["bill_id"]
        bill = Bill.objects.get(pk=bill_id)

        # In tax-inclusive, 105.00 with 5% GST:
        # taxable_value = 105 / 1.05 = 100.00
        # tax_amount = 5.00
        # grand_total = 105.00
        self.assertEqual(bill.grand_total, Decimal("105.00"))
        self.assertEqual(bill.subtotal, Decimal("105.00"))
        self.assertEqual(bill.tax_amount, Decimal("5.00"))
        self.assertEqual(bill.gst_percent, Decimal("5.00"))
        self.assertEqual(bill.taxable_value, Decimal("100.00"))
        self.assertEqual(bill.cgst_percent, Decimal("2.50"))
        self.assertEqual(bill.sgst_percent, Decimal("2.50"))
        self.assertEqual(bill.cgst_amount, Decimal("2.50"))
        self.assertEqual(bill.sgst_amount, Decimal("2.50"))

        # Check receipt HTML output
        detail_resp = self.client.get(reverse("bill_detail", args=[bill.id]))
        self.assertEqual(detail_resp.status_code, 200)
        content = detail_resp.content.decode("utf-8")
        self.assertIn("Taxable Value", content)
        self.assertIn("100.00", content)
        self.assertIn("CGST (2.50%)", content)
        self.assertIn("SGST (2.50%)", content)
        self.assertIn("Total Tax", content)
        self.assertIn("5.00", content)

    def test_tax_exclusive_math(self):
        """Tax-exclusive math: GST added on top of net amount, grand_total = net + tax."""
        self.settings.gst_enabled = True
        self.settings.gst_percent = Decimal("5.00")
        self.settings.prices_include_gst = False
        self.settings.save()

        self.client.login(username="receipt_cashier", password="password123")
        # Espresso coffee is 100.00
        payload = {
            "items": [{"id": self.coffee.id, "quantity": 1}],
            "payment_method": "CASH",
            "discount": "0.00",
        }
        resp = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        bill_id = resp.json()["bill_id"]
        bill = Bill.objects.get(pk=bill_id)

        # In tax-exclusive, 100.00 with 5% GST:
        # taxable_value = 100.00
        # tax_amount = 5.00
        # grand_total = 105.00
        self.assertEqual(bill.subtotal, Decimal("100.00"))
        self.assertEqual(bill.tax_amount, Decimal("5.00"))
        self.assertEqual(bill.gst_percent, Decimal("5.00"))
        self.assertEqual(bill.grand_total, Decimal("105.00"))
        self.assertEqual(bill.taxable_value, Decimal("100.00"))
        self.assertEqual(bill.cgst_amount, Decimal("2.50"))
        self.assertEqual(bill.sgst_amount, Decimal("2.50"))

        # Receipt displays taxes and grand total
        detail_resp = self.client.get(reverse("bill_detail", args=[bill.id]))
        self.assertEqual(detail_resp.status_code, 200)
        content = detail_resp.content.decode("utf-8")
        self.assertIn("Taxable Value", content)
        self.assertIn("100.00", content)
        self.assertIn("CGST (2.50%)", content)
        self.assertIn("SGST (2.50%)", content)
        self.assertIn("105.00", content)

    def test_snapshot_immutability_after_settings_change(self):
        """Old bills preserve their tax snapshot when business settings change or GST is disabled."""
        # 1. Create bill with 5% GST
        self.settings.gst_enabled = True
        self.settings.gst_percent = Decimal("5.00")
        self.settings.prices_include_gst = True
        self.settings.save()

        self.client.login(username="receipt_cashier", password="password123")
        resp = self.client.post(
            reverse("bill_create"),
            data=json.dumps({"items": [{"id": self.tea.id, "quantity": 1}], "payment_method": "CASH"}),
            content_type="application/json",
        )
        bill_id = resp.json()["bill_id"]
        old_bill = Bill.objects.get(pk=bill_id)
        self.assertEqual(old_bill.tax_amount, Decimal("5.00"))
        self.assertEqual(old_bill.gst_percent, Decimal("5.00"))

        # 2. Change settings: disable GST and change rate to 18%
        self.settings.gst_enabled = False
        self.settings.gst_percent = Decimal("18.00")
        self.settings.save()

        # 3. Old bill still has 5% GST and shows tax on receipt
        old_bill.refresh_from_db()
        self.assertEqual(old_bill.tax_amount, Decimal("5.00"))
        self.assertEqual(old_bill.gst_percent, Decimal("5.00"))

        receipt_old = self.client.get(reverse("bill_detail", args=[old_bill.id]))
        content_old = receipt_old.content.decode("utf-8")
        self.assertIn("CGST (2.50%)", content_old)
        self.assertIn("SGST (2.50%)", content_old)
        self.assertIn("Total Tax", content_old)
        self.assertNotIn("18.00", content_old)

        # 4. New bill created under new settings has NO tax
        resp2 = self.client.post(
            reverse("bill_create"),
            data=json.dumps({"items": [{"id": self.coffee.id, "quantity": 1}], "payment_method": "CASH"}),
            content_type="application/json",
        )
        new_bill = Bill.objects.get(pk=resp2.json()["bill_id"])
        self.assertEqual(new_bill.tax_amount, Decimal("0.00"))
        self.assertEqual(new_bill.gst_percent, Decimal("0.00"))

        receipt_new = self.client.get(reverse("bill_detail", args=[new_bill.id]))
        content_new = receipt_new.content.decode("utf-8")
        self.assertNotIn("Taxable Value", content_new)
        self.assertNotIn("CGST", content_new)
        self.assertNotIn("SGST", content_new)

    def test_duplicate_label_on_second_print(self):
        """Bill tracks print_count on ?print=1; shows 'DUPLICATE COPY' on any print after the first."""
        self.client.login(username="receipt_cashier", password="password123")
        bill = Bill.objects.create(
            shift=self.shift,
            bill_number="TEST-PRINT-001",
            subtotal=Decimal("100.00"),
            discount=Decimal("0.00"),
            grand_total=Decimal("100.00"),
            payment_method=Bill.CASH,
            created_by=self.cashier,
        )
        self.assertEqual(bill.print_count, 0)
        url = reverse("bill_detail", args=[bill.id])

        # Access without ?print=1: does not increment print_count, no duplicate label
        view_resp = self.client.get(url)
        bill.refresh_from_db()
        self.assertEqual(bill.print_count, 0)
        self.assertNotIn("DUPLICATE COPY", view_resp.content.decode("utf-8"))

        # First print (?print=1): print_count increments to 1, no duplicate label
        print1_resp = self.client.get(f"{url}?print=1")
        bill.refresh_from_db()
        self.assertEqual(bill.print_count, 1)
        self.assertNotIn("DUPLICATE COPY", print1_resp.content.decode("utf-8"))

        # Second print (?print=1): print_count increments to 2, DUPLICATE COPY displayed!
        print2_resp = self.client.get(f"{url}?print=1")
        bill.refresh_from_db()
        self.assertEqual(bill.print_count, 2)
        self.assertIn("DUPLICATE COPY", print2_resp.content.decode("utf-8"))

        # Third print (?print=1): DUPLICATE COPY still displayed
        print3_resp = self.client.get(f"{url}?print=1")
        bill.refresh_from_db()
        self.assertEqual(bill.print_count, 3)
        self.assertIn("DUPLICATE COPY", print3_resp.content.decode("utf-8"))

    def test_default_mode_fallback(self):
        """When ?mode is absent, bill_detail falls back to default_receipt_mode."""
        self.client.login(username="receipt_cashier", password="password123")
        bill = Bill.objects.create(
            shift=self.shift,
            bill_number="TEST-MODE-001",
            subtotal=Decimal("100.00"),
            discount=Decimal("0.00"),
            grand_total=Decimal("100.00"),
            payment_method=Bill.CASH,
            created_by=self.cashier,
        )
        url = reverse("bill_detail", args=[bill.id])

        # 1. Default is A4
        self.settings.default_receipt_mode = BusinessSettings.RECEIPT_MODE_A4
        self.settings.save()
        resp_a4 = self.client.get(url)
        self.assertEqual(resp_a4.context["receipt_mode"], "a4")
        self.assertIn("a4-paper", resp_a4.content.decode("utf-8"))

        # 2. Change default to thermal (80mm)
        self.settings.default_receipt_mode = BusinessSettings.RECEIPT_MODE_THERMAL
        self.settings.save()
        resp_thermal = self.client.get(url)
        self.assertEqual(resp_thermal.context["receipt_mode"], "thermal")
        self.assertIn("thermal-paper", resp_thermal.content.decode("utf-8"))

        # 3. Change default to thermal58 (58mm)
        self.settings.default_receipt_mode = BusinessSettings.RECEIPT_MODE_THERMAL58
        self.settings.save()
        resp_58 = self.client.get(url)
        self.assertEqual(resp_58.context["receipt_mode"], "thermal58")
        self.assertIn("thermal58-paper", resp_58.content.decode("utf-8"))

        # 4. Explicit ?mode parameter overrides default
        resp_explicit = self.client.get(f"{url}?mode=a4")
        self.assertEqual(resp_explicit.context["receipt_mode"], "a4")

        # 5. Invalid mode falls back to default_receipt_mode
        resp_invalid = self.client.get(f"{url}?mode=unknown_mode")
        self.assertEqual(resp_invalid.context["receipt_mode"], "thermal58")

    def test_cashier_fssai_and_khata_outstanding_on_receipt(self):
        """Receipt shows cashier name (when show_cashier=True), FSSAI number, and khata outstanding after bill."""
        self.settings.show_cashier = True
        self.settings.fssai_number = "98765432109876"
        self.settings.save()

        self.client.login(username="receipt_cashier", password="password123")
        # Customer Suresh Raina has opening_balance 200.00
        # Create a KHATA bill of 100.00
        payload = {
            "items": [{"id": self.coffee.id, "quantity": 1}],
            "payment_method": "KHATA",
            "customer_id": self.customer.id,
            "discount": "0.00",
        }
        resp = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        bill_id = resp.json()["bill_id"]

        detail_resp = self.client.get(reverse("bill_detail", args=[bill_id]))
        content = detail_resp.content.decode("utf-8")

        # Cashier name shown
        self.assertIn("Ramesh Kumar", content)
        # FSSAI shown
        self.assertIn("FSSAI: 98765432109876", content)
        # Token number shown
        self.assertIn("TOKEN #", content)
        # Khata outstanding after this bill: 200 opening + 100 sale = 300
        self.assertIn("Khata Outstanding", content)
        self.assertIn("300.00", content)

        # Hide cashier in settings: cashier line hidden
        self.settings.show_cashier = False
        self.settings.save()
        detail_resp2 = self.client.get(reverse("bill_detail", args=[bill_id]))
        self.assertNotIn("cashier-line", detail_resp2.content.decode("utf-8"))


from datetime import datetime, time


class ReportTabsAndBillingHistoryTests(TestCase):
    def setUp(self):
        self.owner_group, _ = Group.objects.get_or_create(name="Owner")
        self.cashier_group, _ = Group.objects.get_or_create(name="Cashier")

        self.owner = User.objects.create_user(
            username="owner_carol", password="password123", first_name="Carol", last_name="Owner"
        )
        self.owner.groups.add(self.owner_group)

        self.cashier1 = User.objects.create_user(
            username="cashier_alice", password="password123", first_name="Alice", last_name="Smith"
        )
        self.cashier1.groups.add(self.cashier_group)

        self.cashier2 = User.objects.create_user(
            username="cashier_bob", password="password123", first_name="Bob", last_name="Jones"
        )
        self.cashier2.groups.add(self.cashier_group)

        self.client.login(username="owner_carol", password="password123")

        self.product_coffee = Product.objects.create(
            name="Coffee",
            category="Beverages",
            price=Decimal("50.00"),
            cost_price=Decimal("20.00"),
            shortcut_key="CF",
            is_active=True,
        )
        self.product_sandwich = Product.objects.create(
            name="Sandwich",
            category="Food",
            price=Decimal("100.00"),
            cost_price=Decimal("40.00"),
            shortcut_key="SW",
            is_active=True,
        )
        self.product_cake = Product.objects.create(
            name="Cake",
            category="Food",
            price=Decimal("80.00"),
            cost_price=Decimal("30.00"),
            shortcut_key="CK",
            is_active=True,
        )

        self.fixture_date = timezone.localdate()

    def _setup_fixed_fixture(self):
        # Bill 1: Alice, 10:30 AM, subtotal 150, discount 10, grand_total 140, CASH
        dt_10am = timezone.make_aware(datetime.combine(self.fixture_date, time(10, 30)))
        b1 = Bill.objects.create(
            bill_number=f"{self.fixture_date:%Y%m%d}-0001",
            subtotal=Decimal("150.00"),
            discount=Decimal("10.00"),
            grand_total=Decimal("140.00"),
            payment_method=Bill.CASH,
            created_by=self.cashier1,
            created_at=dt_10am,
            status=Bill.STATUS_ACTIVE,
            token_number=1,
        )
        BillPayment.objects.create(bill=b1, method=Bill.CASH, amount=Decimal("140.00"))
        BillItem.objects.create(
            bill=b1,
            menu_item=self.product_coffee,
            item_name="Coffee",
            price=Decimal("50.00"),
            cost_price=Decimal("20.00"),
            quantity=Decimal("1.00"),
            total=Decimal("50.00"),
        )
        BillItem.objects.create(
            bill=b1,
            menu_item=self.product_sandwich,
            item_name="Sandwich",
            price=Decimal("100.00"),
            cost_price=Decimal("40.00"),
            quantity=Decimal("1.00"),
            total=Decimal("100.00"),
        )

        # Bill 2: Alice, 2:15 PM, subtotal 160, discount 0, grand_total 160, UPI
        dt_2pm_1 = timezone.make_aware(datetime.combine(self.fixture_date, time(14, 15)))
        b2 = Bill.objects.create(
            bill_number=f"{self.fixture_date:%Y%m%d}-0002",
            subtotal=Decimal("160.00"),
            discount=Decimal("0.00"),
            grand_total=Decimal("160.00"),
            payment_method=Bill.UPI,
            created_by=self.cashier1,
            created_at=dt_2pm_1,
            status=Bill.STATUS_ACTIVE,
            token_number=2,
        )
        BillPayment.objects.create(bill=b2, method=Bill.UPI, amount=Decimal("160.00"))
        BillItem.objects.create(
            bill=b2,
            menu_item=self.product_cake,
            item_name="Cake",
            price=Decimal("80.00"),
            cost_price=Decimal("30.00"),
            quantity=Decimal("2.00"),
            total=Decimal("160.00"),
        )

        # Bill 3: Bob, 2:45 PM, subtotal 200, discount 20, grand_total 180, CARD
        dt_2pm_2 = timezone.make_aware(datetime.combine(self.fixture_date, time(14, 45)))
        b3 = Bill.objects.create(
            bill_number=f"{self.fixture_date:%Y%m%d}-0003",
            subtotal=Decimal("200.00"),
            discount=Decimal("20.00"),
            grand_total=Decimal("180.00"),
            payment_method=Bill.CARD,
            created_by=self.cashier2,
            created_at=dt_2pm_2,
            status=Bill.STATUS_ACTIVE,
            token_number=3,
        )
        BillPayment.objects.create(bill=b3, method=Bill.CARD, amount=Decimal("180.00"))
        BillItem.objects.create(
            bill=b3,
            menu_item=self.product_sandwich,
            item_name="Sandwich",
            price=Decimal("100.00"),
            cost_price=Decimal("40.00"),
            quantity=Decimal("2.00"),
            total=Decimal("200.00"),
        )

        # Bill 4: VOID, Bob, 11 AM, subtotal 100, grand_total 100, CASH
        dt_11am = timezone.make_aware(datetime.combine(self.fixture_date, time(11, 0)))
        b4 = Bill.objects.create(
            bill_number=f"{self.fixture_date:%Y%m%d}-0004",
            subtotal=Decimal("100.00"),
            discount=Decimal("0.00"),
            grand_total=Decimal("100.00"),
            payment_method=Bill.CASH,
            created_by=self.cashier2,
            created_at=dt_11am,
            status=Bill.STATUS_VOID,
            token_number=4,
        )
        BillPayment.objects.create(bill=b4, method=Bill.CASH, amount=Decimal("100.00"))
        BillItem.objects.create(
            bill=b4,
            menu_item=self.product_sandwich,
            item_name="Sandwich",
            price=Decimal("100.00"),
            cost_price=Decimal("40.00"),
            quantity=Decimal("1.00"),
            total=Decimal("100.00"),
        )

        # Expense: 50.00
        Expense.objects.create(
            title="Ingredients",
            category="Kitchen",
            amount=Decimal("50.00"),
            expense_date=self.fixture_date,
        )

    def test_sales_tab_math_and_void_exclusion(self):
        """Sales tab computes revenue, payments, bill counts, excluding void bills."""
        self._setup_fixed_fixture()
        resp = self.client.get(
            reverse("reports"),
            {"tab": "sales", "from_date": str(self.fixture_date), "to_date": str(self.fixture_date)},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["total_sales"], Decimal("480.00"))
        self.assertEqual(resp.context["total_bills"], 3)
        self.assertEqual(resp.context["total_items"], Decimal("6.00"))
        self.assertEqual(resp.context["cash_sales"], Decimal("140.00"))
        self.assertEqual(resp.context["upi_sales"], Decimal("160.00"))
        self.assertEqual(resp.context["card_sales"], Decimal("180.00"))
        self.assertEqual(resp.context["khata_sales"], Decimal("0.00"))

    def test_items_tab_math_and_void_exclusion(self):
        """Items tab: quantity, revenue, cost, profit, margin per item."""
        self._setup_fixed_fixture()
        resp = self.client.get(
            reverse("reports"),
            {"tab": "items", "from_date": str(self.fixture_date), "to_date": str(self.fixture_date)},
        )
        self.assertEqual(resp.status_code, 200)
        items_by_name = {row["name"]: row for row in resp.context["items_report"]}

        # Sandwich: 1 (bill 1) + 2 (bill 3) = 3 sold. Void bill 4's sandwich is excluded!
        self.assertIn("Sandwich", items_by_name)
        sand = items_by_name["Sandwich"]
        self.assertEqual(sand["quantity"], Decimal("3.00"))
        self.assertEqual(sand["revenue"], Decimal("300.00"))
        self.assertEqual(sand["cost"], Decimal("120.00"))
        self.assertEqual(sand["profit"], Decimal("180.00"))
        self.assertEqual(sand["margin"], Decimal("60.00"))

        # Cake: 2 sold
        self.assertIn("Cake", items_by_name)
        cake = items_by_name["Cake"]
        self.assertEqual(cake["quantity"], Decimal("2.00"))
        self.assertEqual(cake["revenue"], Decimal("160.00"))
        self.assertEqual(cake["cost"], Decimal("60.00"))
        self.assertEqual(cake["profit"], Decimal("100.00"))
        self.assertEqual(cake["margin"], Decimal("62.50"))

        # Coffee: 1 sold
        self.assertIn("Coffee", items_by_name)
        coffee = items_by_name["Coffee"]
        self.assertEqual(coffee["quantity"], Decimal("1.00"))
        self.assertEqual(coffee["revenue"], Decimal("50.00"))
        self.assertEqual(coffee["cost"], Decimal("20.00"))
        self.assertEqual(coffee["profit"], Decimal("30.00"))
        self.assertEqual(coffee["margin"], Decimal("60.00"))

        # Totals
        self.assertEqual(resp.context["items_totals"]["quantity"], Decimal("6.00"))
        self.assertEqual(resp.context["items_totals"]["revenue"], Decimal("510.00"))
        self.assertEqual(resp.context["items_totals"]["cost"], Decimal("200.00"))
        self.assertEqual(resp.context["items_totals"]["profit"], Decimal("310.00"))

    def test_categories_tab_math_and_void_exclusion(self):
        """Categories tab: quantity, revenue, cost, profit, margin grouped by category."""
        self._setup_fixed_fixture()
        resp = self.client.get(
            reverse("reports"),
            {"tab": "categories", "from_date": str(self.fixture_date), "to_date": str(self.fixture_date)},
        )
        self.assertEqual(resp.status_code, 200)
        cats_by_name = {row["category"]: row for row in resp.context["categories_report"]}

        # Food: Sandwich (3, rev 300, cost 120) + Cake (2, rev 160, cost 60) = 5 qty, 460 rev, 180 cost, 280 profit
        self.assertIn("Food", cats_by_name)
        food = cats_by_name["Food"]
        self.assertEqual(food["quantity"], Decimal("5.00"))
        self.assertEqual(food["revenue"], Decimal("460.00"))
        self.assertEqual(food["cost"], Decimal("180.00"))
        self.assertEqual(food["profit"], Decimal("280.00"))

        # Beverages: Coffee (1 qty, rev 50, cost 20, profit 30)
        self.assertIn("Beverages", cats_by_name)
        bev = cats_by_name["Beverages"]
        self.assertEqual(bev["quantity"], Decimal("1.00"))
        self.assertEqual(bev["revenue"], Decimal("50.00"))
        self.assertEqual(bev["cost"], Decimal("20.00"))
        self.assertEqual(bev["profit"], Decimal("30.00"))

    def test_cashiers_tab_math(self):
        """Cashiers tab: bills, revenue, voids, average bill."""
        self._setup_fixed_fixture()
        resp = self.client.get(
            reverse("reports"),
            {"tab": "cashiers", "from_date": str(self.fixture_date), "to_date": str(self.fixture_date)},
        )
        self.assertEqual(resp.status_code, 200)
        cashiers_by_user = {row["username"]: row for row in resp.context["cashiers_report"]}

        # Alice: 2 active bills (140 + 160 = 300), 0 voids, avg = 150
        self.assertIn("cashier_alice", cashiers_by_user)
        alice = cashiers_by_user["cashier_alice"]
        self.assertEqual(alice["bills"], 2)
        self.assertEqual(alice["revenue"], Decimal("300.00"))
        self.assertEqual(alice["voids"], 0)
        self.assertEqual(alice["average_bill"], Decimal("150.00"))

        # Bob: 1 active bill (180), 1 void bill, avg = 180
        self.assertIn("cashier_bob", cashiers_by_user)
        bob = cashiers_by_user["cashier_bob"]
        self.assertEqual(bob["bills"], 1)
        self.assertEqual(bob["revenue"], Decimal("180.00"))
        self.assertEqual(bob["voids"], 1)
        self.assertEqual(bob["average_bill"], Decimal("180.00"))

        # Totals
        self.assertEqual(resp.context["cashiers_totals"]["bills"], 3)
        self.assertEqual(resp.context["cashiers_totals"]["revenue"], Decimal("480.00"))
        self.assertEqual(resp.context["cashiers_totals"]["voids"], 1)
        self.assertEqual(resp.context["cashiers_totals"]["average_bill"], Decimal("160.00"))

    def test_hourly_tab_math_and_void_exclusion(self):
        """Hourly tab: bar chart distribution by hour, void bills excluded."""
        self._setup_fixed_fixture()
        resp = self.client.get(
            reverse("reports"),
            {"tab": "hourly", "from_date": str(self.fixture_date), "to_date": str(self.fixture_date)},
        )
        self.assertEqual(resp.status_code, 200)
        hourly_map = {row["hour_num"]: row for row in resp.context["hourly_table"]}

        # Hour 10 (10:30 AM): 1 bill, 140.00
        self.assertEqual(hourly_map[10]["bills"], 1)
        self.assertEqual(hourly_map[10]["revenue"], Decimal("140.00"))

        # Hour 11 (11:00 AM VOID): 0 active bills, 0.00 revenue
        self.assertEqual(hourly_map[11]["bills"], 0)
        self.assertEqual(hourly_map[11]["revenue"], Decimal("0.00"))

        # Hour 14 (2:15 PM & 2:45 PM): 2 bills, 160 + 180 = 340.00
        self.assertEqual(hourly_map[14]["bills"], 2)
        self.assertEqual(hourly_map[14]["revenue"], Decimal("340.00"))

    def test_profit_tab_math_and_daily_breakdown(self):
        """Profit tab: revenue minus cost minus discounts minus expenses in range, per day."""
        self._setup_fixed_fixture()
        resp = self.client.get(
            reverse("reports"),
            {"tab": "profit", "from_date": str(self.fixture_date), "to_date": str(self.fixture_date)},
        )
        self.assertEqual(resp.status_code, 200)
        profit_rows = resp.context["profit_report"]
        self.assertEqual(len(profit_rows), 1)

        row = profit_rows[0]
        self.assertEqual(row["date"], self.fixture_date)
        # Gross items revenue: 50 + 100 + 160 + 200 = 510.00
        self.assertEqual(row["revenue"], Decimal("510.00"))
        # Cost: 20 + 40 + 60 + 80 = 200.00
        self.assertEqual(row["cost"], Decimal("200.00"))
        # Discounts: 10 + 0 + 20 = 30.00
        self.assertEqual(row["discounts"], Decimal("30.00"))
        # Expenses: 50.00
        self.assertEqual(row["expenses"], Decimal("50.00"))
        # Net profit: 510 - 200 - 30 - 50 = 230.00
        self.assertEqual(row["profit"], Decimal("230.00"))

        self.assertEqual(resp.context["profit_totals"]["profit"], Decimal("230.00"))

    def test_sales_detail_pagination(self):
        """Sales detail table paginates 50 bills per page."""
        today = self.fixture_date
        bills_to_create = []
        for i in range(1, 66):
            bills_to_create.append(
                Bill(
                    bill_number=f"PAGINATE-{i:03d}",
                    subtotal=Decimal("10.00"),
                    discount=Decimal("0.00"),
                    grand_total=Decimal("10.00"),
                    payment_method=Bill.CASH,
                    created_by=self.cashier1,
                    status=Bill.STATUS_ACTIVE,
                    token_number=i,
                )
            )
        Bill.objects.bulk_create(bills_to_create)

        # Page 1
        resp_p1 = self.client.get(reverse("reports"), {"tab": "sales", "page": 1})
        self.assertEqual(resp_p1.status_code, 200)
        self.assertEqual(len(resp_p1.context["sales_page"]), 50)
        self.assertTrue(resp_p1.context["page_obj"].has_next())

        # Page 2
        resp_p2 = self.client.get(reverse("reports"), {"tab": "sales", "page": 2})
        self.assertEqual(resp_p2.status_code, 200)
        self.assertEqual(len(resp_p2.context["sales_page"]), 15)

    def test_csv_export_columns_and_tabs(self):
        """CSV export supports tabs and adds cost, profit, cashier, token columns."""
        self._setup_fixed_fixture()

        # 1. Sales CSV (default / tab=sales)
        resp_sales = self.client.get(
            reverse("export_sales_csv"),
            {"tab": "sales", "from_date": str(self.fixture_date), "to_date": str(self.fixture_date)},
        )
        self.assertEqual(resp_sales.status_code, 200)
        content_sales = resp_sales.content.decode("utf-8")
        # Check new columns present
        self.assertIn("Token", content_sales)
        self.assertIn("Cashier", content_sales)
        self.assertIn("Cost Price", content_sales)
        self.assertIn("Profit", content_sales)
        # Check active bills present, void bill excluded
        self.assertIn(f"{self.fixture_date:%Y%m%d}-0001", content_sales)
        self.assertIn(f"{self.fixture_date:%Y%m%d}-0002", content_sales)
        self.assertIn(f"{self.fixture_date:%Y%m%d}-0003", content_sales)
        self.assertNotIn(f"{self.fixture_date:%Y%m%d}-0004", content_sales)

        # 2. Items CSV
        resp_items = self.client.get(
            reverse("export_sales_csv"),
            {"tab": "items", "from_date": str(self.fixture_date), "to_date": str(self.fixture_date)},
        )
        self.assertEqual(resp_items.status_code, 200)
        content_items = resp_items.content.decode("utf-8")
        self.assertIn("Item Name", content_items)
        self.assertIn("Sandwich", content_items)
        self.assertIn("Cake", content_items)
        self.assertIn("Coffee", content_items)

        # 3. Cashiers CSV
        resp_cashiers = self.client.get(
            reverse("export_sales_csv"),
            {"tab": "cashiers", "from_date": str(self.fixture_date), "to_date": str(self.fixture_date)},
        )
        self.assertEqual(resp_cashiers.status_code, 200)
        content_cashiers = resp_cashiers.content.decode("utf-8")
        self.assertIn("Cashier", content_cashiers)
        self.assertIn("Bills", content_cashiers)
        self.assertIn("Alice Smith", content_cashiers)
        self.assertIn("Bob Jones", content_cashiers)

        # 4. Profit CSV
        resp_profit = self.client.get(
            reverse("export_sales_csv"),
            {"tab": "profit", "from_date": str(self.fixture_date), "to_date": str(self.fixture_date)},
        )
        self.assertEqual(resp_profit.status_code, 200)
        content_profit = resp_profit.content.decode("utf-8")
        self.assertIn("Net Profit", content_profit)
        self.assertIn("230.00", content_profit)

    def test_billing_history_filters_and_totals_strip(self):
        """Billing history filters by dates, payment method, cashier, status, today shortcut, and displays totals strip."""
        self._setup_fixed_fixture()

        # 1. No filter: all 4 bills (3 active + 1 void)
        resp_all = self.client.get(reverse("billing_history"))
        self.assertEqual(resp_all.status_code, 200)
        totals = resp_all.context["totals"]
        self.assertEqual(totals["bills_count"], 4)
        self.assertEqual(totals["active_count"], 3)
        self.assertEqual(totals["void_count"], 1)
        self.assertEqual(totals["revenue"], Decimal("480.00"))
        self.assertEqual(totals["discount"], Decimal("30.00"))
        self.assertEqual(totals["cash"], Decimal("140.00"))
        self.assertEqual(totals["upi"], Decimal("160.00"))
        self.assertEqual(totals["card"], Decimal("180.00"))

        # Verify Token and Cashier in HTML
        content = resp_all.content.decode("utf-8")
        self.assertIn("#1", content)
        self.assertIn("cashier_alice", content)

        # 2. Filter by status: VOID
        resp_void = self.client.get(reverse("billing_history"), {"status": "VOID"})
        self.assertEqual(resp_void.status_code, 200)
        self.assertEqual(resp_void.context["totals"]["bills_count"], 1)
        self.assertEqual(resp_void.context["totals"]["void_count"], 1)
        self.assertEqual(resp_void.context["totals"]["active_count"], 0)

        # 3. Filter by Cashier: cashier_alice
        resp_alice = self.client.get(reverse("billing_history"), {"cashier": "cashier_alice"})
        self.assertEqual(resp_alice.status_code, 200)
        self.assertEqual(resp_alice.context["totals"]["bills_count"], 2)
        self.assertEqual(resp_alice.context["totals"]["revenue"], Decimal("300.00"))

        # 4. Filter by Payment Method: CARD
        resp_card = self.client.get(reverse("billing_history"), {"payment_method": "CARD"})
        self.assertEqual(resp_card.status_code, 200)
        self.assertEqual(resp_card.context["totals"]["bills_count"], 1)
        self.assertEqual(resp_card.context["totals"]["revenue"], Decimal("180.00"))

        # 5. Filter by Today shortcut
        resp_today = self.client.get(reverse("billing_history"), {"today": "1"})
        self.assertEqual(resp_today.status_code, 200)
        self.assertEqual(resp_today.context["totals"]["bills_count"], 4)
        self.assertTrue(resp_today.context["is_today"])


class StaffAndQuickPinTests(TestCase):
    """
    Test suite covering:
    - Creating a cashier via staff page
    - PIN login success and failure
    - Shared login throttling across password and PIN attempts
    - Last-owner protection (cannot demote or disable last active owner)
    - Disabled user unable to log in (password and PIN)
    - Audit log details contain no password or PIN secrets
    - low_stock_threshold usage in dashboard and billing
    - Backup now button runs backup and records audit log
    """

    def setUp(self):
        cache.clear()
        self.owner_group, _ = Group.objects.get_or_create(name="Owner")
        self.cashier_group, _ = Group.objects.get_or_create(name="Cashier")

        self.owner = User.objects.create_user(
            username="main_owner",
            password="owner_password123",
            first_name="Main",
            last_name="Owner",
        )
        self.owner.groups.add(self.owner_group)
        self.owner_profile, _ = StaffProfile.objects.get_or_create(user=self.owner)
        self.owner_profile.set_pin("1111")
        self.owner_profile.save()

        self.cashier = User.objects.create_user(
            username="active_cashier",
            password="cashier_password123",
            first_name="Active",
            last_name="Cashier",
        )
        self.cashier.groups.add(self.cashier_group)
        self.cashier_profile, _ = StaffProfile.objects.get_or_create(user=self.cashier)
        self.cashier_profile.set_pin("2222")
        self.cashier_profile.save()

    def tearDown(self):
        cache.clear()

    def test_create_cashier_via_staff_page(self):
        """Owner can create a new cashier with username, password, role, and optional PIN."""
        self.client.login(username="main_owner", password="owner_password123")
        response = self.client.post(
            reverse("staff_list"),
            {
                "username": "new_hire",
                "first_name": "New",
                "last_name": "Hire",
                "role": "Cashier",
                "password": "secret_hire_pass",
                "pin": "3333",
                "is_active": True,
            },
        )
        self.assertRedirects(response, reverse("staff_list"))

        # Verify user created in DB
        new_user = User.objects.get(username="new_hire")
        self.assertTrue(new_user.groups.filter(name="Cashier").exists())
        self.assertFalse(new_user.groups.filter(name="Owner").exists())
        self.assertTrue(new_user.is_active)
        self.assertTrue(new_user.check_password("secret_hire_pass"))

        # Verify StaffProfile and PIN
        profile = new_user.staff_profile
        self.assertTrue(profile.has_pin())
        self.assertTrue(profile.check_pin("3333"))

        # Verify audit log details contain no password or PIN
        audit = AuditLog.objects.filter(action="STAFF_CREATE", object_repr=str(new_user)).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.details.get("username"), "new_hire")
        self.assertEqual(audit.details.get("role"), "Cashier")
        self.assertTrue(audit.details.get("pin_set"))
        self.assertNotIn("secret_hire_pass", json.dumps(audit.details))
        self.assertNotIn("3333", json.dumps(audit.details))
        self.assertNotIn("password", audit.details)

    def test_pin_login_success_and_failure(self):
        """Quick PIN login succeeds with correct PIN and fails with wrong PIN."""
        # Failed attempt with wrong PIN
        fail_resp = self.client.post(
            reverse("login"),
            {"username": "active_cashier", "pin": "9999", "auth_mode": "pin"},
            REMOTE_ADDR="192.168.1.50",
        )
        self.assertEqual(fail_resp.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

        # Successful attempt with correct PIN
        success_resp = self.client.post(
            reverse("login"),
            {"username": "active_cashier", "pin": "2222", "auth_mode": "pin"},
            REMOTE_ADDR="192.168.1.50",
        )
        self.assertRedirects(success_resp, reverse("billing"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.cashier.id)

    def test_shared_throttling_across_password_and_pin(self):
        """Failed attempts across password and PIN share the 5-attempt limit and lockout."""
        base_time = 1700000000.0
        client_ip = "192.168.1.75"

        with mock.patch("cafe_pos.urls.get_current_time", return_value=base_time):
            # 2 failed password attempts
            for _ in range(2):
                resp = self.client.post(
                    reverse("login"),
                    {"username": "active_cashier", "password": "wrong_password", "auth_mode": "password"},
                    REMOTE_ADDR=client_ip,
                )
                self.assertEqual(resp.status_code, 200)

            # 3 failed PIN attempts (total 5 failures!)
            for _ in range(3):
                resp = self.client.post(
                    reverse("login"),
                    {"username": "active_cashier", "pin": "0000", "auth_mode": "pin"},
                    REMOTE_ADDR=client_ip,
                )
                self.assertEqual(resp.status_code, 200)

            # 6th attempt with CORRECT PIN is locked out
            locked_pin_resp = self.client.post(
                reverse("login"),
                {"username": "active_cashier", "pin": "2222", "auth_mode": "pin"},
                REMOTE_ADDR=client_ip,
            )
            self.assertEqual(locked_pin_resp.status_code, 200)
            self.assertContains(locked_pin_resp, "locked for 5 minutes")
            self.assertNotIn("_auth_user_id", self.client.session)

            # 7th attempt with CORRECT password is also locked out
            locked_pw_resp = self.client.post(
                reverse("login"),
                {"username": "active_cashier", "password": "cashier_password123", "auth_mode": "password"},
                REMOTE_ADDR=client_ip,
            )
            self.assertEqual(locked_pw_resp.status_code, 200)
            self.assertContains(locked_pw_resp, "locked for 5 minutes")
            self.assertNotIn("_auth_user_id", self.client.session)

        # Advance mocked clock by 301 seconds -> Lock expires, PIN login succeeds!
        with mock.patch("cafe_pos.urls.get_current_time", return_value=base_time + 301):
            success_resp = self.client.post(
                reverse("login"),
                {"username": "active_cashier", "pin": "2222", "auth_mode": "pin"},
                REMOTE_ADDR=client_ip,
            )
            self.assertRedirects(success_resp, reverse("billing"))
            self.assertEqual(int(self.client.session["_auth_user_id"]), self.cashier.id)

    def test_last_owner_protection(self):
        """Owner cannot disable or demote themselves if they are the last active Owner."""
        self.client.login(username="main_owner", password="owner_password123")

        # 1. Attempt to disable the only active Owner
        resp_disable = self.client.post(
            reverse("staff_edit", kwargs={"user_id": self.owner.id}),
            {
                "first_name": "Main",
                "last_name": "Owner",
                "role": "Owner",
                "is_active": False,
            },
        )
        self.assertEqual(resp_disable.status_code, 200)
        self.assertContains(resp_disable, "Cannot disable the last remaining Owner")
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)

        # 2. Attempt to demote the only active Owner
        resp_demote = self.client.post(
            reverse("staff_edit", kwargs={"user_id": self.owner.id}),
            {
                "first_name": "Main",
                "last_name": "Owner",
                "role": "Cashier",
                "is_active": True,
            },
        )
        self.assertEqual(resp_demote.status_code, 200)
        self.assertContains(resp_demote, "Cannot demote the last remaining Owner")
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.groups.filter(name="Owner").exists())

        # 3. Create a second active owner -> Now editing the second owner is allowed
        second_owner = User.objects.create_user(
            username="second_owner",
            password="owner_password456",
        )
        second_owner.groups.add(self.owner_group)

        # Can now demote the second owner because main_owner remains
        resp_demote_second = self.client.post(
            reverse("staff_edit", kwargs={"user_id": second_owner.id}),
            {
                "first_name": "",
                "last_name": "",
                "role": "Cashier",
                "is_active": True,
            },
        )
        self.assertRedirects(resp_demote_second, reverse("staff_list"))
        second_owner.refresh_from_db()
        self.assertTrue(second_owner.groups.filter(name="Cashier").exists())
        self.assertFalse(second_owner.groups.filter(name="Owner").exists())

    def test_disabled_user_unable_to_login(self):
        """Disabled users cannot log in with either password or PIN."""
        self.cashier.is_active = False
        self.cashier.save()

        # Try password login
        pw_resp = self.client.post(
            reverse("login"),
            {"username": "active_cashier", "password": "cashier_password123", "auth_mode": "password"},
            REMOTE_ADDR="192.168.1.60",
        )
        self.assertEqual(pw_resp.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

        # Try PIN login
        pin_resp = self.client.post(
            reverse("login"),
            {"username": "active_cashier", "pin": "2222", "auth_mode": "pin"},
            REMOTE_ADDR="192.168.1.60",
        )
        self.assertEqual(pin_resp.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_audit_details_contain_no_pin_or_password(self):
        """Audit log records for staff create and edit must NEVER contain plaintext passwords or PINs."""
        self.client.login(username="main_owner", password="owner_password123")

        # Create staff
        self.client.post(
            reverse("staff_list"),
            {
                "username": "audit_test_user",
                "role": "Cashier",
                "password": "SuperSecretPass999!",
                "pin": "7777",
                "is_active": True,
            },
        )
        user = User.objects.get(username="audit_test_user")

        # Edit staff
        self.client.post(
            reverse("staff_edit", kwargs={"user_id": user.id}),
            {
                "role": "Owner",
                "password": "AnotherSecretPass888!",
                "pin": "8888",
                "is_active": True,
            },
        )

        logs = AuditLog.objects.filter(object_repr=str(user))
        self.assertGreaterEqual(logs.count(), 2)
        for log in logs:
            raw_details = json.dumps(log.details)
            self.assertNotIn("SuperSecretPass999!", raw_details)
            self.assertNotIn("AnotherSecretPass888!", raw_details)
            self.assertNotIn("7777", raw_details)
            self.assertNotIn("8888", raw_details)
            # Keys in details should not include secrets
            self.assertNotIn("password", log.details)
            self.assertNotIn("pin", log.details)

    def test_low_stock_threshold_in_dashboard_and_billing(self):
        """low_stock_threshold from BusinessSettings replaces hardcoded 5 in dashboard and billing."""
        settings = BusinessSettings.current()
        settings.low_stock_threshold = 15
        settings.save()

        # Product with stock 10 (less than 15, but greater than hardcoded 5)
        product = Product.objects.create(
            name="Tracked Juice",
            price=Decimal("50.00"),
            stock=Decimal("10.00"),
            stock_tracked=True,
            is_active=True,
        )

        self.client.login(username="main_owner", password="owner_password123")

        # Check dashboard
        dash_resp = self.client.get(reverse("dashboard"))
        self.assertEqual(dash_resp.status_code, 200)
        self.assertEqual(dash_resp.context["low_stock_threshold"], 15)
        self.assertIn(product, list(dash_resp.context["low_stock_products"]))
        self.assertEqual(dash_resp.context["low_stock_count"], 1)

        # Check billing
        bill_resp = self.client.get(reverse("billing"))
        self.assertEqual(bill_resp.status_code, 200)
        self.assertEqual(bill_resp.context["low_stock_threshold"], 15)
        self.assertContains(bill_resp, "Low: 10")

    def test_backup_now_button_creates_backup_and_audit(self):
        """Backup now button executes backup_db and creates an audit log entry."""
        self.client.login(username="main_owner", password="owner_password123")
        response = self.client.post(
            reverse("settings"),
            {"backup_now": "1"},
        )
        self.assertRedirects(response, reverse("settings"))

        # Verify audit log
        audit = AuditLog.objects.filter(action="BACKUP_DB").first()
        self.assertIsNotNone(audit)
        self.assertIn("filename", audit.details)
        self.assertIn("size_bytes", audit.details)

        # Verify backup directory contains backup file
        backup_dir = settings.BASE_DIR / "backups"
        self.assertTrue(backup_dir.is_dir())
        backups = list(backup_dir.glob("db_*.sqlite3"))
        self.assertGreaterEqual(len(backups), 1)

        # GET settings page shows last backup info
        get_resp = self.client.get(reverse("settings"))
        self.assertEqual(get_resp.status_code, 200)
        self.assertTrue(get_resp.context["last_backup"]["exists"])
        self.assertContains(get_resp, "Database Backup")


class ExpenseCategoryAndRecurringTests(TestCase):
    def setUp(self):
        self.owner_group, _ = Group.objects.get_or_create(name="Owner")
        self.owner_user = User.objects.create_user(
            username="exp_owner",
            password="password123",
            email="exp_owner@example.com",
        )
        self.owner_user.groups.add(self.owner_group)
        self.client.login(username="exp_owner", password="password123")

    def test_category_migration(self):
        """Data migration converts legacy free-text categories into ExpenseCategory objects."""
        import importlib
        migration_mod = importlib.import_module("pos.migrations.0027_expense_category_and_recurring")
        convert_expense_categories = migration_mod.convert_expense_categories

        # Verify that convert_expense_categories runs cleanly with historical apps
        convert_expense_categories(apps, None)

        # Verify conversion logic with dummy historical expense records
        class FakeModelMeta:
            def __init__(self, fields):
                self.fields = fields

        class FakeField:
            def __init__(self, name):
                self.name = name

        class FakeHistoricalExpense:
            def __init__(self, category_text):
                self.category = category_text
                self.category_fk = None
                self.saved = False

            def save(self, update_fields=None):
                self.saved = True

        class FakeApps:
            def __init__(self):
                self.categories = {}

            def get_model(self, app_label, model_name):
                if model_name == "ExpenseCategory":
                    return ExpenseCategory
                elif model_name == "Expense":
                    fake_exp = type(
                        "ExpenseModel",
                        (),
                        {
                            "_meta": FakeModelMeta([FakeField("category"), FakeField("category_fk")]),
                            "objects": type(
                                "Manager",
                                (),
                                {
                                    "all": lambda *args: [
                                        FakeHistoricalExpense("Raw Supplies"),
                                        FakeHistoricalExpense("  Pantry  "),
                                        FakeHistoricalExpense(""),
                                    ]
                                },
                            )(),
                        },
                    )
                    return fake_exp

        fake_apps = FakeApps()
        convert_expense_categories(fake_apps, None)

        # Verify categories were created
        self.assertTrue(ExpenseCategory.objects.filter(name="Raw Supplies", is_active=True).exists())
        self.assertTrue(ExpenseCategory.objects.filter(name="Pantry", is_active=True).exists())
        self.assertTrue(ExpenseCategory.objects.filter(name="General", is_active=True).exists())

    def test_recurring_creation_exactly_once_per_due_date(self):
        """Recurring expenses create expense records exactly once per due date and advance next_due."""
        cat = ExpenseCategory.objects.create(name="Rent & Utilities", is_active=True)
        from datetime import date
        start_date = date(2026, 9, 1)

        rec = RecurringExpense.objects.create(
            title="Shop Rent",
            category=cat,
            amount=Decimal("12000.00"),
            method=Expense.METHOD_BANK,
            frequency=RecurringExpense.FREQ_MONTHLY,
            next_due=start_date,
            is_active=True,
        )

        # Target date is 2026-10-01 (should generate for 2026-09-01 and 2026-10-01)
        created = process_recurring_expenses(target_date=date(2026, 10, 1))
        self.assertEqual(created, 2)

        expenses = Expense.objects.filter(recurring_expense=rec).order_by("expense_date")
        self.assertEqual(expenses.count(), 2)
        self.assertEqual(expenses[0].expense_date, date(2026, 9, 1))
        self.assertEqual(expenses[0].amount, Decimal("12000.00"))
        self.assertEqual(expenses[0].payment_method, Expense.METHOD_BANK)
        self.assertEqual(expenses[1].expense_date, date(2026, 10, 1))

        rec.refresh_from_db()
        self.assertEqual(rec.next_due, date(2026, 11, 1))

        # Re-running process_recurring_expenses on the exact same date creates 0 new expenses
        re_run_created = process_recurring_expenses(target_date=date(2026, 10, 1))
        self.assertEqual(re_run_created, 0)
        self.assertEqual(Expense.objects.filter(recurring_expense=rec).count(), 2)

        # Even if next_due is manually reset back to 2026-10-01, duplicate check prevents re-creation
        rec.next_due = date(2026, 10, 1)
        rec.save()
        duplicate_prevented = process_recurring_expenses(target_date=date(2026, 10, 1))
        self.assertEqual(duplicate_prevented, 0)
        self.assertEqual(Expense.objects.filter(recurring_expense=rec).count(), 2)

    def test_daily_recurring_and_inactive_skipped(self):
        """Daily recurring expenses process all past days and inactive recurring expenses are ignored."""
        cat = ExpenseCategory.objects.create(name="Daily Milk", is_active=True)
        from datetime import date

        active_daily = RecurringExpense.objects.create(
            title="Milk 5L",
            category=cat,
            amount=Decimal("250.00"),
            method=Expense.METHOD_CASH,
            frequency=RecurringExpense.FREQ_DAILY,
            next_due=date(2026, 10, 1),
            is_active=True,
        )

        inactive_daily = RecurringExpense.objects.create(
            title="Pastries",
            category=cat,
            amount=Decimal("500.00"),
            method=Expense.METHOD_CASH,
            frequency=RecurringExpense.FREQ_DAILY,
            next_due=date(2026, 10, 1),
            is_active=False,
        )

        created = process_recurring_expenses(target_date=date(2026, 10, 3))
        # Oct 1, Oct 2, Oct 3 = 3 expenses for active_daily
        self.assertEqual(created, 3)
        self.assertEqual(Expense.objects.filter(recurring_expense=active_daily).count(), 3)
        self.assertEqual(Expense.objects.filter(recurring_expense=inactive_daily).count(), 0)

        active_daily.refresh_from_db()
        self.assertEqual(active_daily.next_due, date(2026, 10, 4))

    def test_management_command_run_recurring(self):
        """Management command run_recurring processes due recurring expenses."""
        from django.core.management import call_command
        cat = ExpenseCategory.objects.create(name="Internet", is_active=True)
        from datetime import date

        rec = RecurringExpense.objects.create(
            title="Wifi Bill",
            category=cat,
            amount=Decimal("999.00"),
            method=Expense.METHOD_UPI,
            frequency=RecurringExpense.FREQ_MONTHLY,
            next_due=date(2026, 10, 5),
            is_active=True,
        )

        call_command("run_recurring", date="2026-10-05")
        self.assertEqual(Expense.objects.filter(recurring_expense=rec).count(), 1)

    def test_dashboard_lazy_trigger_runs_recurring(self):
        """Loading Owner dashboard lazily triggers processing of due recurring expenses."""
        cat = ExpenseCategory.objects.create(name="Cleaning", is_active=True)
        today = timezone.localdate()

        rec = RecurringExpense.objects.create(
            title="Cleaning Supplies",
            category=cat,
            amount=Decimal("450.00"),
            method=Expense.METHOD_CASH,
            frequency=RecurringExpense.FREQ_DAILY,
            next_due=today,
            is_active=True,
        )

        self.assertEqual(Expense.objects.filter(recurring_expense=rec).count(), 0)
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)

        self.assertEqual(Expense.objects.filter(recurring_expense=rec).count(), 1)
        rec.refresh_from_db()
        self.assertEqual(rec.next_due, today + timedelta(days=1))

    def test_filter_totals_and_category_breakdown(self):
        """Expenses page computes filtered_total, category_breakdown, and applies month/category/date filters."""
        from datetime import date
        cat_food = ExpenseCategory.objects.create(name="Food Raw Materials", is_active=True)
        cat_util = ExpenseCategory.objects.create(name="Electricity & Water", is_active=True)

        # October 2026
        Expense.objects.create(
            title="Flour & Sugar",
            category=cat_food,
            amount=Decimal("100.00"),
            expense_date=date(2026, 10, 5),
            payment_method=Expense.METHOD_CASH,
        )
        Expense.objects.create(
            title="Coffee Beans",
            category=cat_food,
            amount=Decimal("200.00"),
            expense_date=date(2026, 10, 15),
            payment_method=Expense.METHOD_UPI,
        )
        Expense.objects.create(
            title="Electricity Bill",
            category=cat_util,
            amount=Decimal("500.00"),
            expense_date=date(2026, 10, 20),
            payment_method=Expense.METHOD_BANK,
        )
        # September 2026
        Expense.objects.create(
            title="Water Tanker",
            category=cat_util,
            amount=Decimal("400.00"),
            expense_date=date(2026, 9, 10),
            payment_method=Expense.METHOD_CASH,
        )

        # 1. No filter: all expenses
        resp = self.client.get(reverse("expenses"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["filtered_total"], Decimal("1200.00"))
        self.assertEqual(resp.context["all_total"], Decimal("1200.00"))
        breakdown = {b["name"]: b for b in resp.context["category_breakdown"]}
        self.assertEqual(breakdown["Electricity & Water"]["total"], Decimal("900.00"))
        self.assertEqual(breakdown["Electricity & Water"]["count"], 2)
        self.assertEqual(breakdown["Food Raw Materials"]["total"], Decimal("300.00"))
        self.assertEqual(breakdown["Food Raw Materials"]["count"], 2)

        # 2. Month filter: 2026-10 (should be 100 + 200 + 500 = 800)
        resp_month = self.client.get(reverse("expenses"), {"month": "2026-10"})
        self.assertEqual(resp_month.status_code, 200)
        self.assertEqual(resp_month.context["filtered_total"], Decimal("800.00"))
        month_breakdown = {b["name"]: b for b in resp_month.context["category_breakdown"]}
        self.assertEqual(month_breakdown["Food Raw Materials"]["total"], Decimal("300.00"))
        self.assertEqual(month_breakdown["Electricity & Water"]["total"], Decimal("500.00"))
        self.assertNotIn("Water Tanker", [e.title for e in resp_month.context["expenses"]])

        # 3. Category filter: Food Raw Materials
        resp_cat = self.client.get(reverse("expenses"), {"category": "Food Raw Materials"})
        self.assertEqual(resp_cat.status_code, 200)
        self.assertEqual(resp_cat.context["filtered_total"], Decimal("300.00"))
        cat_breakdown = resp_cat.context["category_breakdown"]
        self.assertEqual(len(cat_breakdown), 1)
        self.assertEqual(cat_breakdown[0]["name"], "Food Raw Materials")
        self.assertEqual(cat_breakdown[0]["total"], Decimal("300.00"))

        # 4. Specific date filter: 2026-10-15
        resp_date = self.client.get(reverse("expenses"), {"date": "2026-10-15"})
        self.assertEqual(resp_date.status_code, 200)
        self.assertEqual(resp_date.context["filtered_total"], Decimal("200.00"))
        self.assertEqual(len(resp_date.context["expenses"]), 1)
        self.assertEqual(resp_date.context["expenses"][0].title, "Coffee Beans")

        # 5. Search query filter: "flour"
        resp_q = self.client.get(reverse("expenses"), {"q": "flour"})
        self.assertEqual(resp_q.status_code, 200)
        self.assertEqual(resp_date.context["filtered_total"], Decimal("200.00"))
        self.assertEqual(len(resp_q.context["expenses"]), 1)

    def test_rejection_of_bad_image_type(self):
        """Uploading an invalid file extension, corrupt image, or oversized image is rejected."""
        cat = ExpenseCategory.objects.create(name="Maintenance", is_active=True)

        # 1. Bad file extension (.txt)
        bad_txt = SimpleUploadedFile("receipt.txt", b"Payment note text", content_type="text/plain")
        form_data = {
            "title": "Plumbing repair",
            "category": str(cat.id),
            "amount": "350.00",
            "expense_date": "2026-10-09",
            "payment_method": "CASH",
        }
        form = ExpenseForm(data=form_data, files={"receipt_image": bad_txt})
        self.assertFalse(form.is_valid())
        self.assertIn("receipt_image", form.errors)
        self.assertIn("Unsupported file extension", form.errors["receipt_image"][0])

        # 2. Corrupted image file with .jpg extension
        corrupt_jpg = SimpleUploadedFile("receipt.jpg", b"this is not a valid jpeg image", content_type="image/jpeg")
        form_corrupt = ExpenseForm(data=form_data, files={"receipt_image": corrupt_jpg})
        self.assertFalse(form_corrupt.is_valid())
        self.assertIn("receipt_image", form_corrupt.errors)
        self.assertIn("Upload a valid image file", form_corrupt.errors["receipt_image"][0])

        # 3. Oversized file (> 3MB)
        from io import BytesIO
        from PIL import Image

        img = Image.new("RGB", (100, 100), color="red")
        buf = BytesIO()
        img.save(buf, format="JPEG")
        valid_img_bytes = buf.getvalue()

        oversized_data = b"0" * (3 * 1024 * 1024 + 100)
        oversized_file = SimpleUploadedFile("big.jpg", oversized_data, content_type="image/jpeg")
        form_oversized = ExpenseForm(data=form_data, files={"receipt_image": oversized_file})
        self.assertFalse(form_oversized.is_valid())
        self.assertIn("receipt_image", form_oversized.errors)
        self.assertIn("3 MB or smaller", form_oversized.errors["receipt_image"][0])

        # 4. POST to expense_create view with bad image rejects and renders form error
        resp = self.client.post(
            reverse("expense_create"),
            {**form_data, "receipt_image": corrupt_jpg},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFormError(resp.context["form"], "receipt_image", "Upload a valid image file.")

        # 5. Valid image succeeds
        valid_file = SimpleUploadedFile("valid.jpg", valid_img_bytes, content_type="image/jpeg")
        resp_ok = self.client.post(
            reverse("expense_create"),
            {**form_data, "receipt_image": valid_file},
        )
        self.assertRedirects(resp_ok, reverse("expenses"))
        created_exp = Expense.objects.filter(title="Plumbing repair").first()
        self.assertIsNotNone(created_exp)
        self.assertTrue(bool(created_exp.receipt_image))

    def test_expense_creation_with_new_category(self):
        """ExpenseForm supports creating a new category on the fly via new_category."""
        form_data = {
            "title": "Packaging Boxes",
            "new_category": "Logistics & Delivery",
            "amount": "1200.00",
            "expense_date": "2026-10-09",
            "payment_method": "UPI",
        }
        form = ExpenseForm(data=form_data)
        self.assertTrue(form.is_valid())
        expense = form.save()
        self.assertEqual(expense.category.name, "Logistics & Delivery")
        self.assertTrue(ExpenseCategory.objects.filter(name="Logistics & Delivery").exists())


class AuditLogSystemTests(TestCase):
    def setUp(self):
        self.owner_group, _ = Group.objects.get_or_create(name="Owner")
        self.cashier_group, _ = Group.objects.get_or_create(name="Cashier")
        self.owner = User.objects.create_user(
            username="audit_owner",
            password="OwnerPassword123!",
            email="audit_owner@example.com",
        )
        self.owner.groups.add(self.owner_group)

        self.cashier = User.objects.create_user(
            username="audit_cashier",
            password="CashierPassword123!",
            email="audit_cashier@example.com",
        )
        self.cashier.groups.add(self.cashier_group)
        from django.contrib.auth.hashers import make_password
        StaffProfile.objects.create(
            user=self.cashier,
            pin_hash=make_password("4444"),
        )
        self.client.login(username="audit_owner", password="OwnerPassword123!")

    def test_price_change_produces_old_new_diff(self):
        """Editing product price, cost_price, and active status logs old/new diff in AuditLog details."""
        prod = Product.objects.create(
            name="Vanilla Latte",
            category="Beverages",
            price=Decimal("40.00"),
            cost_price=Decimal("15.00"),
            stock=Decimal("20.00"),
            is_active=True,
        )

        resp = self.client.post(
            reverse("menu_item_edit", kwargs={"item_id": prod.id}),
            {
                "name": "Vanilla Latte",
                "category": "Beverages",
                "price": "60.00",
                "cost_price": "22.00",
                "stock": "20.00",
                "is_active": False,
            },
        )
        self.assertRedirects(resp, reverse("menu_items"))

        prod.refresh_from_db()
        self.assertEqual(prod.price, Decimal("60.00"))
        self.assertEqual(prod.cost_price, Decimal("22.00"))
        self.assertFalse(prod.is_active)

        log = AuditLog.objects.filter(
            action="UPDATE",
            model_name="Product",
            object_repr=str(prod),
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.owner)
        self.assertIn("price", log.details)
        self.assertEqual(log.details["price"], ["40.00", "60.00"])
        self.assertIn("cost_price", log.details)
        self.assertEqual(log.details["cost_price"], ["15.00", "22.00"])
        self.assertIn("is_active", log.details)
        self.assertEqual(log.details["is_active"], [True, False])

    def test_bulk_price_change_produces_old_new_diff(self):
        """Bulk price adjustment produces AuditLog entries with old/new price diffs."""
        p1 = Product.objects.create(name="Tea", price=Decimal("20.00"), cost_price=Decimal("5.00"))
        p2 = Product.objects.create(name="Coffee", price=Decimal("30.00"), cost_price=Decimal("10.00"))

        resp = self.client.post(
            reverse("menu_items_bulk_price"),
            json.dumps({
                "action": "apply",
                "item_ids": [p1.id, p2.id],
                "change_type": "flat",
                "amount": "5.00",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

        log_p1 = AuditLog.objects.filter(action="UPDATE", model_name="Product", object_repr=str(p1)).latest("id")
        self.assertEqual(log_p1.details["price"], ["20.00", "25.00"])
        log_p2 = AuditLog.objects.filter(action="UPDATE", model_name="Product", object_repr=str(p2)).latest("id")
        self.assertEqual(log_p2.details["price"], ["30.00", "35.00"])

    def test_failed_login_logged(self):
        """Failed login attempts (both password and PIN) log LOGIN_FAILED in AuditLog without storing secrets."""
        # 1. Failed password login
        self.client.logout()
        resp_fail_pw = self.client.post(
            reverse("login"),
            {"username": "audit_cashier", "password": "WrongPassword999!"},
        )
        self.assertEqual(resp_fail_pw.status_code, 200)

        log_pw = AuditLog.objects.filter(action="LOGIN_FAILED", model_name="User").first()
        self.assertIsNotNone(log_pw)
        self.assertEqual(log_pw.object_repr, "audit_cashier")
        self.assertEqual(log_pw.details.get("status"), [None, "FAILED"])
        self.assertEqual(log_pw.details.get("username"), [None, "audit_cashier"])
        self.assertNotIn("WrongPassword999!", json.dumps(log_pw.details))
        self.assertNotIn("password", log_pw.details)

        # 2. Failed PIN login
        resp_fail_pin = self.client.post(
            reverse("login"),
            {"username": "audit_cashier", "pin": "9999", "auth_mode": "pin"},
        )
        self.assertEqual(resp_fail_pin.status_code, 200)

        log_pin = AuditLog.objects.filter(action="LOGIN_FAILED", model_name="User").first()
        self.assertIsNotNone(log_pin)
        self.assertNotIn("9999", json.dumps(log_pin.details))
        self.assertNotIn("pin", log_pin.details)

        # 3. Successful login logged
        resp_ok = self.client.post(
            reverse("login"),
            {"username": "audit_owner", "password": "OwnerPassword123!"},
        )
        self.assertEqual(resp_ok.status_code, 302)
        log_login = AuditLog.objects.filter(action="LOGIN", actor=self.owner).first()
        self.assertIsNotNone(log_login)
        self.assertEqual(log_login.details.get("status"), [None, "SUCCESS"])

        # 4. Logout logged
        self.client.post(reverse("logout"))
        log_logout = AuditLog.objects.filter(action="LOGOUT").first()
        self.assertIsNotNone(log_logout)

    def test_secrets_absent(self):
        """Passwords, PINs, and secret keys are NEVER stored in AuditLog details."""
        from pos.views import log_audit
        from django.test import RequestFactory

        rf = RequestFactory()
        req = rf.post("/")
        req.user = self.owner

        prod = Product.objects.create(name="Test Item", price=Decimal("10.00"))
        log = log_audit(
            req,
            "TEST_EVENT",
            prod,
            {
                "password": "super_secret_password_123",
                "new_pin": "5555",
                "pin": "1234",
                "user_secret_token": "xyz_secret_token",
                "price": ["10.00", "20.00"],
            },
        )

        details_json = json.dumps(log.details)
        self.assertNotIn("super_secret_password_123", details_json)
        self.assertNotIn("5555", details_json)
        self.assertNotIn("1234", details_json)
        self.assertNotIn("xyz_secret_token", details_json)
        self.assertNotIn("password", log.details)
        self.assertNotIn("new_pin", log.details)
        self.assertNotIn("pin", log.details)
        self.assertNotIn("user_secret_token", log.details)
        self.assertEqual(log.details.get("price"), ["10.00", "20.00"])

    def test_audit_filters_and_readable_rendering(self):
        """Audit page filters by user, action, model, and date range; renders human-readable diffs."""
        self.client.login(username="audit_owner", password="OwnerPassword123!")
        today = timezone.localdate()

        log1 = AuditLog.objects.create(
            actor=self.owner,
            action="UPDATE",
            model_name="Product",
            object_repr="Americano",
            details={"price": ["30.00", "45.00"]},
        )
        log2 = AuditLog.objects.create(
            actor=self.cashier,
            action="VOID",
            model_name="Bill",
            object_repr="Bill #101",
            details={"status": ["ACTIVE", "VOID"], "void_reason": [None, "Customer cancelled"]},
        )
        log3 = AuditLog.objects.create(
            actor=None,
            action="LOGIN_FAILED",
            model_name="User",
            object_repr="stranger",
            details={"status": [None, "FAILED"]},
        )

        # 1. Filter by user
        resp_user = self.client.get(reverse("audit_logs"), {"user": "audit_owner"})
        self.assertEqual(resp_user.status_code, 200)
        user_ids = [l.id for l in resp_user.context["logs"]]
        self.assertIn(log1.id, user_ids)
        self.assertNotIn(log2.id, user_ids)

        # 2. Filter by action
        resp_act = self.client.get(reverse("audit_logs"), {"action": "VOID"})
        self.assertEqual(resp_act.status_code, 200)
        act_ids = [l.id for l in resp_act.context["logs"]]
        self.assertIn(log2.id, act_ids)
        self.assertNotIn(log1.id, act_ids)

        # 3. Filter by model
        resp_mod = self.client.get(reverse("audit_logs"), {"model": "Product"})
        self.assertEqual(resp_mod.status_code, 200)
        mod_ids = [l.id for l in resp_mod.context["logs"]]
        self.assertIn(log1.id, mod_ids)
        self.assertNotIn(log2.id, mod_ids)

        # 4. Filter by date range
        resp_date = self.client.get(reverse("audit_logs"), {
            "from_date": today.isoformat(),
            "to_date": today.isoformat(),
        })
        self.assertEqual(resp_date.status_code, 200)
        self.assertIn(log1.id, [l.id for l in resp_date.context["logs"]])

        # 5. Check readable key/value HTML rendering
        resp_all = self.client.get(reverse("audit_logs"))
        self.assertEqual(resp_all.status_code, 200)
        self.assertContains(resp_all, "price:")
        self.assertContains(resp_all, "30.00")
        self.assertContains(resp_all, "45.00")
        self.assertContains(resp_all, "→")

    def test_admin_read_only(self):
        """AuditLog in Django admin is strictly read-only and append-only."""
        from django.contrib import admin
        from pos.admin import AuditLogAdmin
        from django.test import RequestFactory

        rf = RequestFactory()
        req = rf.get("/admin/pos/auditlog/")
        req.user = self.owner

        admin_instance = AuditLogAdmin(AuditLog, admin.site)
        dummy_log = AuditLog.objects.create(
            actor=self.owner,
            action="TEST",
            model_name="Product",
            object_repr="Test",
            details={},
        )

        self.assertFalse(admin_instance.has_add_permission(req))
        self.assertFalse(admin_instance.has_change_permission(req))
        self.assertFalse(admin_instance.has_change_permission(req, dummy_log))
        self.assertFalse(admin_instance.has_delete_permission(req))
        self.assertFalse(admin_instance.has_delete_permission(req, dummy_log))

    def test_customer_expense_and_settings_audit_diffs(self):
        """Customer, Expense, and BusinessSettings edits log old/new diffs in AuditLog details."""
        # 1. Customer edit
        cust = Customer.objects.create(name="Alice", phone="9876543210")
        resp_c = self.client.post(
            reverse("customer_edit", kwargs={"customer_id": cust.id}),
            {"name": "Alice Wonderland", "phone": "9876543211", "email": "alice@example.com", "address": "Wonderland", "credit_limit": "500.00"},
        )
        self.assertRedirects(resp_c, reverse("customer_detail", kwargs={"customer_id": cust.id}))
        cust.refresh_from_db()
        cust_log = AuditLog.objects.filter(action="UPDATE", model_name="Customer", object_repr=str(cust)).first()
        self.assertIsNotNone(cust_log)
        self.assertEqual(cust_log.details["name"], ["Alice", "Alice Wonderland"])
        self.assertEqual(cust_log.details["phone"], ["9876543210", "9876543211"])

        # 2. Expense edit
        cat = ExpenseCategory.objects.create(name="Office Supplies")
        exp = Expense.objects.create(title="Notebooks", amount=Decimal("150.00"), category=cat, payment_method="CASH")
        resp_e = self.client.post(
            reverse("expense_edit", kwargs={"expense_id": exp.id}),
            {"title": "Notebooks & Pens", "amount": "220.00", "category": str(cat.id), "payment_method": "UPI", "expense_date": exp.expense_date.isoformat()},
        )
        self.assertRedirects(resp_e, reverse("expenses"))
        exp.refresh_from_db()
        exp_log = AuditLog.objects.filter(action="UPDATE", model_name="Expense", object_repr=str(exp)).first()
        self.assertIsNotNone(exp_log)
        self.assertEqual(exp_log.details["title"], ["Notebooks", "Notebooks & Pens"])
        self.assertEqual(exp_log.details["amount"], ["150.00", "220.00"])
        self.assertEqual(exp_log.details["payment_method"], ["CASH", "UPI"])

        # 3. Settings edit
        sett = BusinessSettings.current()
        resp_s = self.client.post(
            reverse("settings"),
            {
                "business_name": "Antigravity Cafe",
                "invoice_footer": sett.invoice_footer,
                "currency_symbol": "$",
                "default_receipt_mode": "thermal",
                "gst_percent": "18.00",
                "low_stock_threshold": 10,
            },
        )
        self.assertRedirects(resp_s, reverse("settings"))
        sett_log = AuditLog.objects.filter(action="UPDATE", model_name="BusinessSettings").first()
        self.assertIsNotNone(sett_log)
        self.assertIn("business_name", sett_log.details)
        self.assertEqual(sett_log.details["business_name"][1], "Antigravity Cafe")
        self.assertIn("low_stock_threshold", sett_log.details)
        self.assertEqual(sett_log.details["low_stock_threshold"][1], 10)


class InventoryAndPurchaseTests(TestCase):
    """
    Test suite covering:
    - StockItem qty increase on purchase
    - Cost per unit update on purchase
    - Supplier due math across purchases
    - Negative and zero qty rejection
    - Low-stock queryset, property, and UI list
    - Cash purchase drawer expense integration
    - Owner-only access enforcement
    """

    @classmethod
    def setUpTestData(cls):
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")

        cls.owner = User.objects.create_user(
            username="inv_owner",
            password="ownerpassword123",
        )
        cls.owner.groups.add(cls.owner_group)

        cls.cashier = User.objects.create_user(
            username="inv_cashier",
            password="cashierpassword123",
        )
        cls.cashier.groups.add(cls.cashier_group)

        cls.supplier = Supplier.objects.create(
            name="Metro Dairy",
            phone="9876543210",
            notes="Daily morning delivery",
        )

        cls.milk = StockItem.objects.create(
            name="Milk",
            unit="L",
            current_qty=Decimal("10.00"),
            reorder_level=Decimal("5.00"),
            cost_per_unit=Decimal("50.00"),
        )
        cls.tea = StockItem.objects.create(
            name="Tea Powder",
            unit="kg",
            current_qty=Decimal("2.00"),
            reorder_level=Decimal("4.00"),
            cost_per_unit=Decimal("250.00"),
        )
        cls.sugar = StockItem.objects.create(
            name="Sugar",
            unit="kg",
            current_qty=Decimal("15.00"),
            reorder_level=Decimal("5.00"),
            cost_per_unit=Decimal("40.00"),
        )

    def test_purchase_increases_stock_quantity(self):
        """Saving a purchase increases StockItem.current_qty."""
        initial_milk_qty = self.milk.current_qty
        initial_tea_qty = self.tea.current_qty

        purchase = Purchase.objects.create(
            supplier=self.supplier,
            paid_amount=Decimal("1500.00"),
            method="CASH",
        )
        # Line 1: 20 L of milk
        PurchaseLine.objects.create(
            purchase=purchase,
            stock_item=self.milk,
            qty=Decimal("20.00"),
            unit_cost=Decimal("52.00"),
        )
        # Line 2: 5 kg of tea powder
        PurchaseLine.objects.create(
            purchase=purchase,
            stock_item=self.tea,
            qty=Decimal("5.00"),
            unit_cost=Decimal("260.00"),
        )

        self.milk.refresh_from_db()
        self.tea.refresh_from_db()

        self.assertEqual(self.milk.current_qty, initial_milk_qty + Decimal("20.00"))
        self.assertEqual(self.tea.current_qty, initial_tea_qty + Decimal("5.00"))

        # Subsequent purchase further increases stock
        purchase2 = Purchase.objects.create(
            supplier=self.supplier,
            paid_amount=Decimal("500.00"),
            method="UPI",
        )
        PurchaseLine.objects.create(
            purchase=purchase2,
            stock_item=self.milk,
            qty=Decimal("10.50"),
            unit_cost=Decimal("53.00"),
        )
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.current_qty, Decimal("40.50"))

    def test_purchase_updates_cost_per_unit(self):
        """Saving a purchase updates StockItem.cost_per_unit to the latest purchase cost."""
        self.assertEqual(self.milk.cost_per_unit, Decimal("50.00"))

        purchase = Purchase.objects.create(
            supplier=self.supplier,
            paid_amount=Decimal("550.00"),
            method="CASH",
        )
        PurchaseLine.objects.create(
            purchase=purchase,
            stock_item=self.milk,
            qty=Decimal("10.00"),
            unit_cost=Decimal("55.00"),
        )

        self.milk.refresh_from_db()
        self.assertEqual(self.milk.cost_per_unit, Decimal("55.00"))

        # Next purchase at ₹58.50 updates cost again
        purchase2 = Purchase.objects.create(
            supplier=self.supplier,
            paid_amount=Decimal("585.00"),
            method="BANK",
        )
        PurchaseLine.objects.create(
            purchase=purchase2,
            stock_item=self.milk,
            qty=Decimal("10.00"),
            unit_cost=Decimal("58.50"),
        )

        self.milk.refresh_from_db()
        self.assertEqual(self.milk.cost_per_unit, Decimal("58.50"))

    def test_supplier_due_math(self):
        """Allow supplier dues (total minus paid) and calculate per-supplier outstanding."""
        supplier_dues_test = Supplier.objects.create(
            name="City Gas Agency",
            phone="9123456789",
        )
        gas_cylinder = StockItem.objects.create(
            name="LPG Cylinder",
            unit="cylinder",
            current_qty=Decimal("2.00"),
            reorder_level=Decimal("1.00"),
            cost_per_unit=Decimal("1000.00"),
        )
        # Purchase 1: Total = 2 * 1000 = 2000, Paid = 1200 -> Due = 800
        p1 = Purchase.objects.create(
            supplier=supplier_dues_test,
            paid_amount=Decimal("1200.00"),
            method="CASH",
        )
        PurchaseLine.objects.create(
            purchase=p1,
            stock_item=gas_cylinder,
            qty=Decimal("2.00"),
            unit_cost=Decimal("1000.00"),
        )
        self.assertEqual(p1.total_amount, Decimal("2000.00"))
        self.assertEqual(p1.due_amount, Decimal("800.00"))

        # Purchase 2: Total = 1 * 1050 = 1050, Paid = 1050 (Full) -> Due = 0
        p2 = Purchase.objects.create(
            supplier=supplier_dues_test,
            paid_amount=Decimal("1050.00"),
            method="UPI",
        )
        PurchaseLine.objects.create(
            purchase=p2,
            stock_item=gas_cylinder,
            qty=Decimal("1.00"),
            unit_cost=Decimal("1050.00"),
        )
        self.assertEqual(p2.total_amount, Decimal("1050.00"))
        self.assertEqual(p2.due_amount, Decimal("0.00"))

        # Purchase 3: Total = 3 * 1000 = 3000, Paid = 0 (Full Credit) -> Due = 3000
        p3 = Purchase.objects.create(
            supplier=supplier_dues_test,
            paid_amount=Decimal("0.00"),
            method="CREDIT",
        )
        PurchaseLine.objects.create(
            purchase=p3,
            stock_item=gas_cylinder,
            qty=Decimal("3.00"),
            unit_cost=Decimal("1000.00"),
        )
        self.assertEqual(p3.total_amount, Decimal("3000.00"))
        self.assertEqual(p3.due_amount, Decimal("3000.00"))

        # Total Purchases = 2000 + 1050 + 3000 = 6050
        # Total Paid = 1200 + 1050 + 0 = 2250
        # Outstanding Dues = 6050 - 2250 = 3800
        self.assertEqual(supplier_dues_test.total_purchases, Decimal("6050.00"))
        self.assertEqual(supplier_dues_test.total_paid, Decimal("2250.00"))
        self.assertEqual(supplier_dues_test.outstanding_dues, Decimal("3800.00"))
        self.assertEqual(supplier_dues_test.outstanding, Decimal("3800.00"))
        self.assertEqual(supplier_dues_test.dues, Decimal("3800.00"))

    def test_rejected_negative_quantity(self):
        """Reject negative and zero quantity on PurchaseLine and purchase creation."""
        purchase = Purchase.objects.create(
            supplier=self.supplier,
            paid_amount=Decimal("0.00"),
            method="CASH",
        )

        # 1. Negative quantity validation at model level
        neg_line = PurchaseLine(
            purchase=purchase,
            stock_item=self.milk,
            qty=Decimal("-5.00"),
            unit_cost=Decimal("50.00"),
        )
        with self.assertRaises((ValidationError, ValueError)):
            neg_line.full_clean()

        with self.assertRaises((ValidationError, ValueError)):
            neg_line.save()

        # 2. Zero quantity validation at model level
        zero_line = PurchaseLine(
            purchase=purchase,
            stock_item=self.milk,
            qty=Decimal("0.00"),
            unit_cost=Decimal("50.00"),
        )
        with self.assertRaises((ValidationError, ValueError)):
            zero_line.full_clean()

        with self.assertRaises((ValidationError, ValueError)):
            zero_line.save()

        # 3. Form validation
        form_neg = PurchaseLineForm(data={
            "stock_item": self.milk.id,
            "qty": "-3.00",
            "unit_cost": "50.00",
        })
        self.assertFalse(form_neg.is_valid())
        self.assertIn("qty", form_neg.errors)

        form_zero = PurchaseLineForm(data={
            "stock_item": self.milk.id,
            "qty": "0.00",
            "unit_cost": "50.00",
        })
        self.assertFalse(form_zero.is_valid())
        self.assertIn("qty", form_zero.errors)

        # Verify stock was not changed
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.current_qty, Decimal("10.00"))

        # 4. View POST rejection with negative qty
        self.client.login(username="inv_owner", password="ownerpassword123")
        post_data = {
            "supplier": self.supplier.id,
            "date": timezone.localdate().isoformat(),
            "method": "CASH",
            "paid_amount": "0.00",
            "stock_item": [self.milk.id],
            "qty": ["-10.00"],
            "unit_cost": ["50.00"],
        }
        resp = self.client.post(reverse("purchase_create"), post_data)
        # Should re-render form with error message or bad status, not redirect
        self.assertNotEqual(resp.status_code, 302)
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.current_qty, Decimal("10.00"))

    def test_low_stock_list_and_alerts(self):
        """Test low-stock queryset filter, property, and UI list view."""
        exact_item = StockItem.objects.create(
            name="Cardamom",
            unit="kg",
            current_qty=Decimal("5.00"),
            reorder_level=Decimal("5.00"),
            cost_per_unit=Decimal("1500.00"),
        )

        self.assertTrue(self.tea.is_low_stock)
        self.assertTrue(exact_item.is_low_stock)
        self.assertFalse(self.milk.is_low_stock)
        self.assertFalse(self.sugar.is_low_stock)

        # QuerySet manager low_stock()
        low_items = list(StockItem.objects.low_stock())
        self.assertIn(self.tea, low_items)
        self.assertIn(exact_item, low_items)
        self.assertNotIn(self.milk, low_items)
        self.assertNotIn(self.sugar, low_items)

        # UI view: Owner GET reverse("stock_items")?filter=low
        self.client.login(username="inv_owner", password="ownerpassword123")
        resp = self.client.get(reverse("stock_items"), {"filter": "low"})
        self.assertEqual(resp.status_code, 200)
        items_in_context = list(resp.context["items"])
        self.assertIn(self.tea, items_in_context)
        self.assertIn(exact_item, items_in_context)
        self.assertNotIn(self.milk, items_in_context)
        self.assertNotIn(self.sugar, items_in_context)

        # Without filter: All items returned
        resp_all = self.client.get(reverse("stock_items"))
        self.assertEqual(resp_all.status_code, 200)
        all_items = list(resp_all.context["items"])
        self.assertIn(self.tea, all_items)
        self.assertIn(exact_item, all_items)
        self.assertIn(self.milk, all_items)
        self.assertIn(self.sugar, all_items)

    def test_cash_purchase_optional_drawer_expense(self):
        """A cash purchase can optionally post a drawer expense when a shift is open."""
        shift = Shift.objects.create(
            status=Shift.STATUS_OPEN,
            opening_cash=Decimal("1000.00"),
            opened_by=self.owner,
        )

        self.client.login(username="inv_owner", password="ownerpassword123")
        post_data = {
            "supplier": self.supplier.id,
            "date": timezone.localdate().isoformat(),
            "method": "CASH",
            "paid_amount": "500.00",
            "post_to_drawer": "on",
            "stock_item": [self.milk.id],
            "qty": ["10.00"],
            "unit_cost": ["50.00"],
        }
        resp = self.client.post(reverse("purchase_create"), post_data)
        self.assertEqual(resp.status_code, 302)

        purchase = Purchase.objects.order_by("-id").first()
        self.assertIsNotNone(purchase)
        self.assertIsNotNone(purchase.expense)
        self.assertTrue(purchase.expense.paid_from_drawer)
        self.assertEqual(purchase.expense.shift, shift)
        self.assertEqual(purchase.expense.amount, Decimal("500.00"))
        self.assertEqual(purchase.expense.payment_method, "CASH")

    def test_owner_only_access_restrictions(self):
        """Cashiers receive 403 Forbidden for inventory views, Owners receive 200."""
        urls_to_test = [
            reverse("stock_items"),
            reverse("stock_item_create"),
            reverse("purchases"),
            reverse("purchase_create"),
            reverse("suppliers"),
            reverse("supplier_create"),
            reverse("supplier_detail", kwargs={"supplier_id": self.supplier.id}),
        ]

        self.client.login(username="inv_cashier", password="cashierpassword123")
        for u in urls_to_test:
            resp = self.client.get(u)
            self.assertEqual(resp.status_code, 403, f"Cashier should get 403 on {u}")

        self.client.login(username="inv_owner", password="ownerpassword123")
        for u in urls_to_test:
            resp = self.client.get(u)
            self.assertEqual(resp.status_code, 200, f"Owner should get 200 on {u}")


class RecipeAndInventoryTests(TestCase):
    """
    Test suite covering recipe requirements:
    - Deduction: On sale, deduct qty_per_unit * quantity from each StockItem inside transaction.
    - Snapshot cost: BillItem.cost_price stored as snapshot at sale time.
    - Void restore: Voided bills restore ingredient stock.
    - Negative flag: StockItem allowed to go negative and flagged with is_negative.
    - Recalculation: Purchase price change updates product derived cost without altering old bills.
    - Days of cover: Calculated based on 7-day usage.
    - Menu form: Inline recipe editor creates and updates RecipeLines.
    """

    @classmethod
    def setUpTestData(cls):
        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")

        cls.owner = User.objects.create_user(
            username="recipe_owner",
            password="ownerpassword123",
        )
        cls.owner.groups.add(cls.owner_group)

        cls.supplier = Supplier.objects.create(
            name="Dairy & Spice Supplies",
            phone="9876543210",
        )

        cls.milk = StockItem.objects.create(
            name="Whole Milk",
            unit="L",
            current_qty=Decimal("10.00"),
            reorder_level=Decimal("3.00"),
            cost_per_unit=Decimal("50.00"),
        )
        cls.tea = StockItem.objects.create(
            name="Assam Tea Powder",
            unit="kg",
            current_qty=Decimal("5.00"),
            reorder_level=Decimal("1.00"),
            cost_per_unit=Decimal("200.00"),
        )
        cls.sugar = StockItem.objects.create(
            name="White Sugar",
            unit="kg",
            current_qty=Decimal("10.00"),
            reorder_level=Decimal("2.00"),
            cost_per_unit=Decimal("40.00"),
        )

        cls.chai = Product.objects.create(
            name="Special Chai",
            category="Hot Drinks",
            price=Decimal("30.00"),
            cost_price=Decimal("10.00"),
        )
        # Recipe: 0.2000 L Milk (@ 50 = 10.00) + 0.0200 kg Tea (@ 200 = 4.00) + 0.0100 kg Sugar (@ 40 = 0.40)
        # Total derived cost = 14.40
        RecipeLine.objects.create(product=cls.chai, stock_item=cls.milk, qty_per_unit=Decimal("0.2000"))
        RecipeLine.objects.create(product=cls.chai, stock_item=cls.tea, qty_per_unit=Decimal("0.0200"))
        RecipeLine.objects.create(product=cls.chai, stock_item=cls.sugar, qty_per_unit=Decimal("0.0100"))

    def setUp(self):
        if not Shift.current_open():
            Shift.objects.create(
                status=Shift.STATUS_OPEN,
                opening_cash=Decimal("500.00"),
                opened_by=self.owner,
            )

    def test_derived_cost_price(self):
        """Product.cost_price becomes derived when a recipe exists: sum(qty * stock_item.cost_per_unit)."""
        self.assertEqual(self.chai.cost_price, Decimal("14.40"))
        self.assertTrue(self.chai.has_recipe)

        # Product without recipe keeps manual cost price
        coffee = Product.objects.create(
            name="Black Coffee",
            price=Decimal("40.00"),
            cost_price=Decimal("12.00"),
        )
        self.assertFalse(coffee.has_recipe)
        self.assertEqual(coffee.cost_price, Decimal("12.00"))

    def test_ingredient_deduction_on_sale(self):
        """On sale, deduct qty_per_unit times quantity from each StockItem inside transaction."""
        self.client.login(username="recipe_owner", password="ownerpassword123")
        payload = {
            "items": [
                {"id": self.chai.id, "quantity": 4, "price": "30.00"}
            ],
            "discount": "0.00",
            "payment_method": "CASH",
            "payments": [{"method": "CASH", "amount": "120.00"}],
            "tendered": "120.00",
        }
        resp = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

        self.milk.refresh_from_db()
        self.tea.refresh_from_db()
        self.sugar.refresh_from_db()

        # 4 cups * 0.2000 L Milk = 0.80 L deducted -> 10.00 - 0.80 = 9.20 L
        self.assertEqual(self.milk.current_qty, Decimal("9.20"))
        # 4 cups * 0.0200 kg Tea = 0.08 kg deducted -> 5.00 - 0.08 = 4.92 kg
        self.assertEqual(self.tea.current_qty, Decimal("4.92"))
        # 4 cups * 0.0100 kg Sugar = 0.04 kg deducted -> 10.00 - 0.04 = 9.96 kg
        self.assertEqual(self.sugar.current_qty, Decimal("9.96"))

    def test_snapshot_cost_price_on_bill_item(self):
        """Store a BillItem.cost_price snapshot as today."""
        self.client.login(username="recipe_owner", password="ownerpassword123")
        payload = {
            "items": [
                {"id": self.chai.id, "quantity": 2, "price": "30.00"}
            ],
            "discount": "0.00",
            "payment_method": "CASH",
            "payments": [{"method": "CASH", "amount": "60.00"}],
        }
        resp = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        bill_data = resp.json()
        bill = Bill.objects.get(pk=bill_data["bill_id"])
        item = bill.items.first()

        self.assertEqual(item.cost_price, Decimal("14.40"))

    def test_voided_bills_restore_ingredient_stock(self):
        """Voided bills restore ingredient stock."""
        self.client.login(username="recipe_owner", password="ownerpassword123")
        payload = {
            "items": [
                {"id": self.chai.id, "quantity": 5, "price": "30.00"}
            ],
            "discount": "0.00",
            "payment_method": "CASH",
            "payments": [{"method": "CASH", "amount": "150.00"}],
        }
        resp = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        bill_id = resp.json()["bill_id"]

        self.milk.refresh_from_db()
        self.assertEqual(self.milk.current_qty, Decimal("9.00"))

        # Void the bill
        void_resp = self.client.post(
            reverse("bill_void", kwargs={"bill_id": bill_id}),
            {"void_reason": "Customer cancelled order"},
        )
        self.assertIn(void_resp.status_code, (200, 302))

        # Check stock restored
        self.milk.refresh_from_db()
        self.tea.refresh_from_db()
        self.sugar.refresh_from_db()

        self.assertEqual(self.milk.current_qty, Decimal("10.00"))
        self.assertEqual(self.tea.current_qty, Decimal("5.00"))
        self.assertEqual(self.sugar.current_qty, Decimal("10.00"))

    def test_negative_stock_allowed_and_flagged(self):
        """Allow going negative on StockItem during sale, and flag it."""
        self.milk.current_qty = Decimal("1.00")
        self.milk.save()

        self.client.login(username="recipe_owner", password="ownerpassword123")
        payload = {
            "items": [
                {"id": self.chai.id, "quantity": 10, "price": "30.00"}
            ],
            "discount": "0.00",
            "payment_method": "CASH",
            "payments": [{"method": "CASH", "amount": "300.00"}],
        }
        resp = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

        self.milk.refresh_from_db()
        # 1.00 - (10 * 0.20) = -1.00 L
        self.assertEqual(self.milk.current_qty, Decimal("-1.00"))
        self.assertTrue(self.milk.is_negative)

        # Check queryset negative() manager filter
        negatives = list(StockItem.objects.negative())
        self.assertIn(self.milk, negatives)

        # Check stock list page contains negative flag
        stock_resp = self.client.get(reverse("stock_items"))
        self.assertEqual(stock_resp.status_code, 200)
        self.assertContains(stock_resp, "Negative")

    def test_cost_recalculation_after_purchase_price_change_not_altering_old_bills(self):
        """Cost recalculation after a purchase price change does not alter old bills."""
        self.client.login(username="recipe_owner", password="ownerpassword123")

        # 1. Sale at initial price (Milk @ ₹50.00 -> Chai cost ₹14.40)
        payload = {
            "items": [
                {"id": self.chai.id, "quantity": 1, "price": "30.00"}
            ],
            "discount": "0.00",
            "payment_method": "CASH",
            "payments": [{"method": "CASH", "amount": "30.00"}],
        }
        resp1 = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp1.status_code, 200)
        bill1_id = resp1.json()["bill_id"]
        bill1_item = BillItem.objects.get(bill_id=bill1_id)
        self.assertEqual(bill1_item.cost_price, Decimal("14.40"))

        # 2. Purchase arrives: Milk unit cost increases to ₹60.00
        purchase = Purchase.objects.create(
            supplier=self.supplier,
            paid_amount=Decimal("600.00"),
            method="CASH",
        )
        PurchaseLine.objects.create(
            purchase=purchase,
            stock_item=self.milk,
            qty=Decimal("10.00"),
            unit_cost=Decimal("60.00"),
        )
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.cost_per_unit, Decimal("60.00"))

        # 3. Product cost price is dynamically recalculated:
        # 0.2000 * 60 (12.00) + 0.0200 * 200 (4.00) + 0.0100 * 40 (0.40) = 16.40
        self.assertEqual(self.chai.cost_price, Decimal("16.40"))

        # 4. Old bill's cost_price MUST remain unchanged!
        bill1_item.refresh_from_db()
        self.assertEqual(bill1_item.cost_price, Decimal("14.40"))

        # 5. Subsequent new bill captures the new derived snapshot
        resp2 = self.client.post(
            reverse("bill_create"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp2.status_code, 200)
        bill2_id = resp2.json()["bill_id"]
        bill2_item = BillItem.objects.get(bill_id=bill2_id)
        self.assertEqual(bill2_item.cost_price, Decimal("16.40"))

    def test_stock_page_shows_days_of_cover(self):
        """Stock page shows days-of-cover estimated from the last 7 days of use."""
        self.milk.current_qty = Decimal("14.00")
        self.milk.save()

        # Bill selling 35 chai over last 7 days (35 * 0.20 = 7.0 L milk -> 1.0 L/day)
        bill = Bill.objects.create(
            bill_number="BILL-DOC-1",
            subtotal=Decimal("300.00"),
            discount=Decimal("0.00"),
            grand_total=Decimal("300.00"),
            payment_method=Bill.CASH,
            status=Bill.STATUS_ACTIVE,
            created_at=timezone.now() - timedelta(days=1),
        )
        BillItem.objects.create(
            bill=bill,
            menu_item=self.chai,
            item_name=self.chai.name,
            price=Decimal("30.00"),
            cost_price=self.chai.cost_price,
            quantity=Decimal("35.00"),
            total=Decimal("1050.00"),
        )

        doc = self.milk.get_days_of_cover(7)
        self.assertIsNotNone(doc)
        # 7.0 L in 7 days = 1.0 L/day. 14.0 L / 1.0 L/day = 14.0 days
        self.assertEqual(doc, Decimal("14.0"))

        # Stock list view renders days of cover
        self.client.login(username="recipe_owner", password="ownerpassword123")
        resp = self.client.get(reverse("stock_items"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "days")

    def test_menu_item_inline_recipe_submission(self):
        """Menu item form can create and update recipe lines."""
        self.client.login(username="recipe_owner", password="ownerpassword123")

        # 1. Create item with recipe lines
        post_data = {
            "name": "Cardamom Tea",
            "category": "Hot Drinks",
            "price": "35.00",
            "cost_price": "0.00",
            "stock": "0.00",
            "has_recipe_form": "1",
            "recipe_stock_item": [str(self.milk.id), str(self.sugar.id)],
            "recipe_qty": ["0.1500", "0.0100"],
        }
        resp = self.client.post(reverse("menu_item_create"), post_data)
        self.assertEqual(resp.status_code, 302)

        new_prod = Product.objects.get(name="Cardamom Tea")
        self.assertEqual(new_prod.recipe_lines.count(), 2)
        # 0.15 * 50 (7.50) + 0.01 * 40 (0.40) = 7.90
        self.assertEqual(new_prod.cost_price, Decimal("7.90"))

        # 2. Edit item: update recipe
        edit_data = {
            "name": "Cardamom Tea Special",
            "category": "Hot Drinks",
            "price": "40.00",
            "cost_price": "0.00",
            "stock": "0.00",
            "has_recipe_form": "1",
            "recipe_stock_item": [str(self.milk.id)],
            "recipe_qty": ["0.2000"],
        }
        resp_edit = self.client.post(reverse("menu_item_edit", kwargs={"item_id": new_prod.id}), edit_data)
        self.assertEqual(resp_edit.status_code, 302)

        new_prod.refresh_from_db()
        self.assertEqual(new_prod.recipe_lines.count(), 1)
        # 0.20 * 50 = 10.00
        self.assertEqual(new_prod.cost_price, Decimal("10.00"))



class WastageTests(TestCase):
    def setUp(self):
        self.owner_group, _ = Group.objects.get_or_create(name="Owner")
        self.cashier_group, _ = Group.objects.get_or_create(name="Cashier")

        self.owner = User.objects.create_user(username="owner_wastage", password="ownerpassword123")
        self.owner.groups.add(self.owner_group)

        self.cashier = User.objects.create_user(username="cashier_wastage", password="cashierpassword123")
        self.cashier.groups.add(self.cashier_group)

    def test_wastage_cost_snapshot(self):
        self.client.login(username="owner_wastage", password="ownerpassword123")

        prod = Product.objects.create(
            name="Croissant",
            price=Decimal("100.00"),
            cost_price=Decimal("45.00"),
            stock_tracked=True,
            stock=Decimal("20.00"),
        )
        resp = self.client.post(
            reverse("wastage_create"),
            {
                "product": prod.id,
                "quantity": "4.00",
                "reason": "SPILLED",
                "note": "Dropped pastry display tray",
            },
        )
        self.assertEqual(resp.status_code, 302)

        wastage = Wastage.objects.get(product=prod)
        self.assertEqual(wastage.cost, Decimal("180.00"))  # 4 * 45.00

        # Now change product cost_price in DB
        prod.cost_price = Decimal("90.00")
        prod.save(update_fields=["cost_price"])

        # Reload wastage from DB and verify cost snapshot remained unchanged
        wastage.refresh_from_db()
        self.assertEqual(wastage.cost, Decimal("180.00"))

        # Test cost snapshot on raw material StockItem
        stock_item = StockItem.objects.create(
            name="Coffee Beans",
            unit="kg",
            current_qty=Decimal("10.00"),
            cost_per_unit=Decimal("30.00"),
        )
        resp_si = self.client.post(
            reverse("wastage_create"),
            {
                "stock_item": stock_item.id,
                "quantity": "2.00",
                "reason": "EXPIRED",
                "note": "Expired bag",
            },
        )
        self.assertEqual(resp_si.status_code, 302)
        si_wastage = Wastage.objects.get(stock_item=stock_item)
        self.assertEqual(si_wastage.cost, Decimal("60.00"))  # 2 * 30.00

        stock_item.cost_per_unit = Decimal("75.00")
        stock_item.save(update_fields=["cost_per_unit"])

        si_wastage.refresh_from_db()
        self.assertEqual(si_wastage.cost, Decimal("60.00"))

    def test_stock_deduction_tracked_product(self):
        self.client.login(username="cashier_wastage", password="cashierpassword123")

        prod = Product.objects.create(
            name="Muffin",
            price=Decimal("60.00"),
            cost_price=Decimal("25.00"),
            stock_tracked=True,
            stock=Decimal("20.00"),
        )
        resp = self.client.post(
            reverse("wastage_create"),
            {
                "product": prod.id,
                "quantity": "5.00",
                "reason": "EXPIRED",
            },
        )
        self.assertEqual(resp.status_code, 302)

        prod.refresh_from_db()
        self.assertEqual(prod.stock, Decimal("15.00"))

        wastage = Wastage.objects.get(product=prod)
        self.assertEqual(wastage.quantity, Decimal("5.00"))
        self.assertEqual(wastage.cost, Decimal("125.00"))

    def test_stock_deduction_untracked_product_with_recipe(self):
        self.client.login(username="cashier_wastage", password="cashierpassword123")

        milk = StockItem.objects.create(
            name="Fresh Milk",
            unit="L",
            current_qty=Decimal("10.00"),
            cost_per_unit=Decimal("50.00"),
        )
        coffee_powder = StockItem.objects.create(
            name="Espresso Powder",
            unit="kg",
            current_qty=Decimal("5.00"),
            cost_per_unit=Decimal("200.00"),
        )
        latte = Product.objects.create(
            name="Cafe Latte",
            price=Decimal("120.00"),
            cost_price=Decimal("0.00"),
            stock_tracked=False,
            stock=Decimal("0.00"),
        )
        RecipeLine.objects.create(product=latte, stock_item=milk, qty_per_unit=Decimal("0.2500"))
        RecipeLine.objects.create(product=latte, stock_item=coffee_powder, qty_per_unit=Decimal("0.0200"))

        resp = self.client.post(
            reverse("wastage_create"),
            {
                "product": latte.id,
                "quantity": "4.00",
                "reason": "SPILLED",
                "note": "Barista spilled order tray",
            },
        )
        self.assertEqual(resp.status_code, 302)

        # Untracked finished product stock should remain 0
        latte.refresh_from_db()
        self.assertEqual(latte.stock, Decimal("0.00"))

        # Ingredients stock should be deducted
        milk.refresh_from_db()
        coffee_powder.refresh_from_db()
        self.assertEqual(milk.current_qty, Decimal("9.00"))  # 10.00 - (4 * 0.25)
        self.assertEqual(coffee_powder.current_qty, Decimal("4.92"))  # 5.00 - (4 * 0.02)

        wastage = Wastage.objects.get(product=latte)
        self.assertEqual(wastage.cost, Decimal("66.00"))  # 4 * 16.50

    def test_stock_deduction_untracked_product_without_recipe(self):
        self.client.login(username="cashier_wastage", password="cashierpassword123")

        cookie = Product.objects.create(
            name="Packaged Cookie",
            price=Decimal("30.00"),
            cost_price=Decimal("12.00"),
            stock_tracked=False,
            stock=Decimal("0.00"),
        )
        resp = self.client.post(
            reverse("wastage_create"),
            {
                "product": cookie.id,
                "quantity": "3.00",
                "reason": "RETURNED",
            },
        )
        self.assertEqual(resp.status_code, 302)

        cookie.refresh_from_db()
        self.assertEqual(cookie.stock, Decimal("0.00"))

        wastage = Wastage.objects.get(product=cookie)
        self.assertEqual(wastage.cost, Decimal("36.00"))  # 3 * 12.00

    def test_stock_deduction_stock_item(self):
        self.client.login(username="cashier_wastage", password="cashierpassword123")

        sugar = StockItem.objects.create(
            name="Sugar",
            unit="kg",
            current_qty=Decimal("25.00"),
            cost_per_unit=Decimal("40.00"),
        )
        resp = self.client.post(
            reverse("wastage_create"),
            {
                "stock_item": sugar.id,
                "quantity": "5.50",
                "reason": "SPILLED",
                "note": "Bag torn in storage",
            },
        )
        self.assertEqual(resp.status_code, 302)

        sugar.refresh_from_db()
        self.assertEqual(sugar.current_qty, Decimal("19.50"))

        wastage = Wastage.objects.get(stock_item=sugar)
        self.assertEqual(wastage.cost, Decimal("220.00"))  # 5.50 * 40.00

    def test_owner_only_correction(self):
        prod = Product.objects.create(
            name="Cheesecake",
            price=Decimal("150.00"),
            cost_price=Decimal("50.00"),
            stock_tracked=True,
            stock=Decimal("10.00"),
        )

        # 1. Cashier cannot log a correction (via reason=CORRECTION or negative quantity)
        self.client.login(username="cashier_wastage", password="cashierpassword123")

        resp_cashier_corr = self.client.post(
            reverse("wastage_create"),
            {
                "product": prod.id,
                "quantity": "2.00",
                "reason": "CORRECTION",
            },
        )
        self.assertEqual(resp_cashier_corr.status_code, 403)

        resp_cashier_neg = self.client.post(
            reverse("wastage_create"),
            {
                "product": prod.id,
                "quantity": "-2.00",
                "reason": "SPILLED",
            },
        )
        self.assertEqual(resp_cashier_neg.status_code, 403)

        prod.refresh_from_db()
        self.assertEqual(prod.stock, Decimal("10.00"))
        self.assertEqual(Wastage.objects.count(), 0)

        # 2. Owner logs regular wastage of 5 units
        self.client.login(username="owner_wastage", password="ownerpassword123")
        resp_waste = self.client.post(
            reverse("wastage_create"),
            {
                "product": prod.id,
                "quantity": "5.00",
                "reason": "SPILLED",
            },
        )
        self.assertEqual(resp_waste.status_code, 302)
        prod.refresh_from_db()
        self.assertEqual(prod.stock, Decimal("5.00"))

        # 3. Owner logs correction for 2 units mistakenly logged
        resp_corr = self.client.post(
            reverse("wastage_create"),
            {
                "product": prod.id,
                "quantity": "2.00",
                "reason": "CORRECTION",
                "note": "Corrected 2 accidentally logged cheesecakes",
            },
        )
        self.assertEqual(resp_corr.status_code, 302)

        # Stock should be restored by 2 units (5.00 + 2.00 = 7.00)
        prod.refresh_from_db()
        self.assertEqual(prod.stock, Decimal("7.00"))

        corr_entry = Wastage.objects.filter(reason="CORRECTION").latest("id")
        self.assertEqual(corr_entry.quantity, Decimal("-2.00"))
        self.assertEqual(corr_entry.cost, Decimal("-100.00"))

    def test_report_inclusion(self):
        self.client.login(username="owner_wastage", password="ownerpassword123")
        shift = Shift.objects.create(opening_cash=Decimal("500.00"), opened_by=self.owner)

        prod = Product.objects.create(name="Tea", price=Decimal("30.00"), cost_price=Decimal("10.00"))
        bill = Bill.objects.create(
            shift=shift,
            bill_number=f"{timezone.localdate():%Y%m%d}-9901",
            subtotal=Decimal("300.00"),
            discount=Decimal("20.00"),
            grand_total=Decimal("280.00"),
            payment_method=Bill.CASH,
            created_by=self.owner,
        )
        BillItem.objects.create(
            bill=bill,
            menu_item=prod,
            item_name=prod.name,
            price=prod.price,
            cost_price=prod.cost_price,
            quantity=Decimal("10.00"),
            total=Decimal("300.00"),
        )
        BillPayment.objects.create(bill=bill, method=Bill.CASH, amount=Decimal("280.00"))

        # Log Wastage:
        # 1. 2 Spilled Tea (cost 20.00)
        Wastage.objects.create(
            product=prod,
            quantity=Decimal("2.00"),
            cost=Decimal("20.00"),
            reason="SPILLED",
            recorded_by=self.owner,
        )
        # 2. 1 Expired Tea (cost 10.00)
        Wastage.objects.create(
            product=prod,
            quantity=Decimal("1.00"),
            cost=Decimal("10.00"),
            reason="EXPIRED",
            recorded_by=self.owner,
        )

        resp = self.client.get(reverse("reports") + "?tab=profit")
        self.assertEqual(resp.status_code, 200)

        # Revenue = 300, Cost = 100, Discount = 20, Expense = 0, Wastage = 30
        # Net profit = 300 - 100 - 20 - 0 - 30 = 150
        profit_totals = resp.context["profit_totals"]
        self.assertEqual(profit_totals["wastage"], Decimal("30.00"))
        self.assertEqual(profit_totals["profit"], Decimal("150.00"))

        # Verify wastage_reason_summary in context
        reason_summary = resp.context["wastage_reason_summary"]
        reasons_found = {r["reason"]: r["total_cost"] for r in reason_summary}
        self.assertEqual(reasons_found.get("SPILLED"), Decimal("20.00"))
        self.assertEqual(reasons_found.get("EXPIRED"), Decimal("10.00"))

        # Check rendered HTML
        self.assertContains(resp, "Wastage by Reason")
        self.assertContains(resp, "Spilled / Damaged")
        self.assertContains(resp, "Expired / Spoiled")

    def test_wastage_records_cannot_be_deleted(self):
        prod = Product.objects.create(name="Bread", price=Decimal("40.00"), cost_price=Decimal("20.00"))
        wastage = Wastage.objects.create(
            product=prod,
            quantity=Decimal("1.00"),
            cost=Decimal("20.00"),
            reason="EXPIRED",
        )
        with self.assertRaises(ValueError):
            wastage.delete()

        with self.assertRaises(ValueError):
            Wastage.objects.filter(pk=wastage.pk).delete()


class I18nTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner_i18n", password="ownerpassword123")
        owner_group, _ = Group.objects.get_or_create(name="Owner")
        self.owner.groups.add(owner_group)
        self.owner_profile = StaffProfile.objects.create(user=self.owner, role="Owner", language="en")

        self.cashier = User.objects.create_user(username="cashier_i18n", password="cashierpassword123")
        cashier_group, _ = Group.objects.get_or_create(name="Cashier")
        self.cashier.groups.add(cashier_group)
        self.cashier_profile = StaffProfile.objects.create(user=self.cashier, role="Cashier", language="en")

        self.shift = Shift.objects.create(opening_cash=Decimal("500.00"), opened_by=self.cashier)

    def test_switch_language_sets_session_cookie_and_profile(self):
        self.client.login(username="cashier_i18n", password="cashierpassword123")
        response = self.client.post(reverse("set_language"), {"language": "ta", "next": reverse("billing")})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("billing"), response.headers["Location"])

        # Check session
        session = self.client.session
        self.assertEqual(session.get("_language"), "ta")

        # Check cookie
        self.assertIn(settings.LANGUAGE_COOKIE_NAME, response.cookies)
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "ta")

        # Check user profile
        self.cashier_profile.refresh_from_db()
        self.assertEqual(self.cashier_profile.language, "ta")

    def test_switch_language_for_anonymous_session(self):
        response = self.client.post(reverse("set_language"), {"language": "ta", "next": reverse("billing")})
        self.assertEqual(response.status_code, 302)
        session = self.client.session
        self.assertEqual(session.get("_language"), "ta")

    def test_login_restores_user_language_preference(self):
        self.owner_profile.language = "ta"
        self.owner_profile.save()

        # Login should activate user language
        self.client.login(username="owner_i18n", password="ownerpassword123")
        session = self.client.session
        self.assertEqual(session.get("_language"), "ta")

    def test_tamil_product_name_fallback_on_counter_and_receipt(self):
        # Product 1: Has Tamil name
        prod_ta = Product.objects.create(
            name="Masala Chai",
            name_ta="மசாலா சாய்",
            price=Decimal("25.00"),
            is_active=True,
        )
        # Product 2: Blank Tamil name (should fallback to English name)
        prod_fallback = Product.objects.create(
            name="Filter Coffee",
            name_ta="",
            price=Decimal("30.00"),
            is_active=True,
        )

        from django.utils import translation
        with translation.override("en"):
            self.assertEqual(prod_ta.display_name, "Masala Chai")
            self.assertEqual(prod_fallback.display_name, "Filter Coffee")

        with translation.override("ta"):
            self.assertEqual(prod_ta.display_name, "மசாலா சாய்")
            self.assertEqual(prod_fallback.display_name, "Filter Coffee")

        # Create a bill with both items
        bill = Bill.objects.create(
            shift=self.shift,
            bill_number=f"{timezone.localdate():%Y%m%d}-8801",
            subtotal=Decimal("55.00"),
            grand_total=Decimal("55.00"),
            payment_method=Bill.CASH,
            created_by=self.cashier,
        )
        item1 = BillItem.objects.create(
            bill=bill,
            menu_item=prod_ta,
            item_name=prod_ta.name,
            price=prod_ta.price,
            quantity=Decimal("1.00"),
            total=Decimal("25.00"),
        )
        item2 = BillItem.objects.create(
            bill=bill,
            menu_item=prod_fallback,
            item_name=prod_fallback.name,
            price=prod_fallback.price,
            quantity=Decimal("1.00"),
            total=Decimal("30.00"),
        )

        with translation.override("en"):
            self.assertEqual(item1.display_name, "Masala Chai")
            self.assertEqual(item2.display_name, "Filter Coffee")

        with translation.override("ta"):
            self.assertEqual(item1.display_name, "மசாலா சாய்")
            self.assertEqual(item2.display_name, "Filter Coffee")

        # Test rendered counter page in Tamil:
        self.client.login(username="cashier_i18n", password="cashierpassword123")
        self.client.post(reverse("set_language"), {"language": "ta", "next": reverse("billing")})
        resp_billing = self.client.get(reverse("billing"))
        self.assertEqual(resp_billing.status_code, 200)
        self.assertContains(resp_billing, "மசாலா சாய்")
        self.assertContains(resp_billing, "Filter Coffee")
        self.assertContains(resp_billing, "புதிய பில்")
        self.assertContains(resp_billing, "கூட்டுத்தொகை")

        # Test rendered receipt page in Tamil:
        resp_receipt = self.client.get(reverse("bill_detail", args=[bill.id]))
        self.assertEqual(resp_receipt.status_code, 200)
        self.assertContains(resp_receipt, "மசாலா சாய்")
        self.assertContains(resp_receipt, "Filter Coffee")
        self.assertContains(resp_receipt, "பில்")
        self.assertContains(resp_receipt, "கூட்டுத்தொகை")

    def test_khata_page_translation(self):
        Customer.objects.create(name="Murugan", phone="9876543210")
        self.client.login(username="cashier_i18n", password="cashierpassword123")
        self.client.post(reverse("set_language"), {"language": "ta", "next": reverse("khata")})
        resp = self.client.get(reverse("khata"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "காதா கணக்குகள்")
        self.assertContains(resp, "மொத்த நிலுவை:")
        self.assertContains(resp, "வடிகட்டு")

    def test_compilemessages_command_succeeds(self):
        from pathlib import Path
        from django.core.management import call_command
        import io
        out = io.StringIO()
        call_command("compilemessages", stdout=out)
        output = out.getvalue()
        self.assertIn("Successfully compiled", output)

        mo_path = Path(settings.BASE_DIR) / "locale" / "ta" / "LC_MESSAGES" / "django.mo"
        self.assertTrue(mo_path.exists())
        self.assertGreater(mo_path.stat().st_size, 0)


class PWATests(TestCase):
    """
    Tests covering PWA implementation:
    - Web manifest route and content type
    - Service worker route, root scope, and content type
    - WhiteNoise cache control settings ensuring sw.js is not cached with long max-age
    - Offline fallback page
    - Base template PWA integration (manifest link, icons, install button, offline banner)
    """

    def test_manifest_route_and_content_type(self):
        url = reverse("manifest")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Content-Type"), "application/manifest+json")

        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(data.get("name"), "Cafe POS")
        self.assertEqual(data.get("short_name"), "Cafe POS")
        self.assertEqual(data.get("start_url"), "/billing/")
        self.assertEqual(data.get("display"), "standalone")
        self.assertEqual(data.get("theme_color"), "#20252b")
        self.assertEqual(data.get("background_color"), "#20252b")

        icons = data.get("icons", [])
        icon_sizes = [icon.get("sizes") for icon in icons]
        self.assertIn("192x192", icon_sizes)
        self.assertIn("512x512", icon_sizes)

    def test_service_worker_route_and_content_type(self):
        url = reverse("service_worker")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Content-Type"), "application/javascript")
        self.assertEqual(response.headers.get("Service-Worker-Allowed"), "/")

        cache_control = response.headers.get("Cache-Control", "")
        self.assertIn("no-cache", cache_control)
        self.assertNotIn("31536000", cache_control)
        self.assertNotIn("immutable", cache_control)

        content = response.content.decode("utf-8")
        self.assertIn("caches.open", content)
        self.assertIn("/offline/", content)
        self.assertIn("request.method !== 'GET'", content)

    def test_sw_not_cached_by_whitenoise_with_long_max_age(self):
        from cafe_pos.settings import whitenoise_custom_headers, whitenoise_immutable_test

        headers = {}
        whitenoise_custom_headers(headers, "pos/static/pos/sw.js", "/static/pos/sw.js")
        self.assertIn("Cache-Control", headers)
        self.assertEqual(headers["Cache-Control"], "no-cache, no-store, must-revalidate")
        self.assertNotIn("31536000", headers["Cache-Control"])
        self.assertNotIn("immutable", headers["Cache-Control"])

        is_immutable = whitenoise_immutable_test("pos/static/pos/sw.js", "/static/pos/sw.js")
        self.assertFalse(is_immutable)

        is_sw_root_immutable = whitenoise_immutable_test("pos/static/pos/sw.js", "/sw.js")
        self.assertFalse(is_sw_root_immutable)

    def test_offline_fallback_page(self):
        url = reverse("offline")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You are currently offline")
        self.assertContains(response, "Billing and payment generation do not work offline")

    def test_base_template_pwa_elements(self):
        owner_group, _ = Group.objects.get_or_create(name="Owner")
        user = User.objects.create_user(username="pwa_user", password="pwapassword123")
        user.groups.add(owner_group)
        self.client.login(username="pwa_user", password="pwapassword123")

        response = self.client.get(reverse("billing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'rel="manifest"')
        self.assertContains(response, 'manifest.webmanifest')
        self.assertContains(response, 'icon-192.png')
        self.assertContains(response, 'id="topbarInstallBtn"')
        self.assertContains(response, 'id="sidebarInstallBtn"')
        self.assertContains(response, 'id="globalOfflineBanner"')
        self.assertContains(response, 'id="billingOfflineWarning"')
        self.assertContains(response, 'navigator.serviceWorker.register')


class DatabaseBackupAndDailySummaryTests(TestCase):
    """
    Tests covering:
    - backup_db online backup integrity (PRAGMA integrity_check)
    - retention of 30 backups (cleanup of older backups)
    - restore_db requiring --confirm
    - restore_db with --confirm executing restore and making a safety backup
    - Owner Daily Summary page, math, summary text, and WhatsApp link
    """

    def setUp(self):
        self.owner_group, _ = Group.objects.get_or_create(name="Owner")
        self.owner_user = User.objects.create_user(
            username="summary_owner",
            password="ownerpassword123",
        )
        self.owner_user.groups.add(self.owner_group)

    def test_backup_db_integrity(self):
        import sqlite3
        import tempfile
        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as tmp_dir:
            backup_file = call_command("backup_db", backup_dir=tmp_dir)
            self.assertTrue(Path(backup_file).is_file())
            self.assertGreater(Path(backup_file).stat().st_size, 0)

            # Check SQLite integrity directly on the destination backup file
            conn = sqlite3.connect(backup_file)
            try:
                cur = conn.cursor()
                cur.execute("PRAGMA integrity_check;")
                res = cur.fetchone()
                self.assertIsNotNone(res)
                self.assertEqual(res[0].lower(), "ok")
            finally:
                conn.close()

    def test_backup_retention_of_30(self):
        import os
        import sqlite3
        import tempfile
        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            # Pre-populate directory with 35 dummy valid SQLite databases with distinct timestamps
            base_time = 1700000000
            for i in range(35):
                file_path = tmp_path / f"db_20260101_{i:04d}00.sqlite3"
                conn = sqlite3.connect(file_path)
                conn.execute("CREATE TABLE dummy (id INT);")
                conn.close()
                mtime = base_time + (i * 60)
                os.utime(file_path, (mtime, mtime))

            initial_files = list(tmp_path.glob("db_*.sqlite3"))
            self.assertEqual(len(initial_files), 35)

            # Running backup_db should add 1 new backup and prune the oldest to maintain exactly 30
            call_command("backup_db", backup_dir=tmp_dir)

            remaining_files = list(tmp_path.glob("db_*.sqlite3"))
            self.assertEqual(
                len(remaining_files),
                30,
                f"Expected exactly 30 backups retained, found {len(remaining_files)}",
            )

    def test_restore_db_without_confirm_fails(self):
        import tempfile
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with tempfile.TemporaryDirectory() as tmp_dir:
            backup_file = call_command("backup_db", backup_dir=tmp_dir)

            with self.assertRaises(CommandError) as ctx:
                call_command("restore_db", backup_file)

            self.assertIn("--confirm", str(ctx.exception))

    def test_restore_db_with_confirm_succeeds_and_safety_backup_created(self):
        import sqlite3
        import tempfile
        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            target_db = tmp_path / "app.sqlite3"

            # 1. Create a database with initial data
            conn = sqlite3.connect(target_db)
            conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT);")
            conn.execute("INSERT INTO products (name) VALUES ('Initial Item');")
            conn.commit()
            conn.close()

            # 2. Create a backup of this database
            backup_file = call_command("backup_db", backup_dir=tmp_dir, db_path=str(target_db))

            # 3. Modify target database (add a new record)
            conn = sqlite3.connect(target_db)
            conn.execute("INSERT INTO products (name) VALUES ('New Added Item');")
            conn.commit()
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM products;")
            self.assertEqual(cur.fetchone()[0], 2)
            conn.close()

            # 4. Restore the database using restore_db with --confirm
            call_command(
                "restore_db",
                backup_file,
                confirm=True,
                backup_dir=tmp_dir,
                db_path=str(target_db),
            )

            # 5. Verify safety backup (pre_restore_*.sqlite3) was created in backup_dir
            safety_backups = list(tmp_path.glob("pre_restore_*.sqlite3"))
            self.assertGreaterEqual(
                len(safety_backups),
                1,
                "Pre-restore safety backup must be generated before restoring",
            )
            self.assertGreater(safety_backups[0].stat().st_size, 0)

            # Verify safety backup has the 2 items
            safety_conn = sqlite3.connect(safety_backups[0])
            s_cur = safety_conn.cursor()
            s_cur.execute("SELECT count(*) FROM products;")
            self.assertEqual(s_cur.fetchone()[0], 2)
            safety_conn.close()

            # 6. Verify restored target database has been restored to the initial item
            res_conn = sqlite3.connect(target_db)
            r_cur = res_conn.cursor()
            r_cur.execute("SELECT name FROM products;")
            rows = [row[0] for row in r_cur.fetchall()]
            res_conn.close()

            self.assertEqual(rows, ["Initial Item"])

    def test_daily_summary_text_and_math(self):
        from datetime import time

        self.client.login(username="summary_owner", password="ownerpassword123")

        # Configure BusinessSettings
        bs = BusinessSettings.current()
        bs.phone = "+91 98765 43210"
        bs.save()

        summary_date = timezone.localdate()

        # 1. Customer with Khata opening balance
        Customer.objects.create(
            name="Suresh Trader",
            phone="9876500001",
            opening_balance=Decimal("75.00"),
        )

        # 2. Supplier with purchase due
        sup = Supplier.objects.create(name="Dairy Direct", phone="9876500002")
        pur = Purchase.objects.create(supplier=sup, paid_amount=Decimal("120.00"), method="CASH")
        stk = StockItem.objects.create(name="Fresh Milk", unit="L", current_qty=10, cost_per_unit=50)
        PurchaseLine.objects.create(purchase=pur, stock_item=stk, qty=Decimal("4.00"), unit_cost=Decimal("50.00"))
        # Purchase total = 200.00, paid = 120.00 => due = 80.00

        # 3. Products
        p1 = Product.objects.create(name="Masala Chai", price=Decimal("30.00"), cost_price=Decimal("10.00"))
        p2 = Product.objects.create(name="Filter Coffee", price=Decimal("40.00"), cost_price=Decimal("15.00"))
        p3 = Product.objects.create(name="Bun Maska", price=Decimal("50.00"), cost_price=Decimal("20.00"))

        # 4. Bill 1: ₹100 Cash
        b1 = Bill.objects.create(
            bill_number="BILL-SUMMARY-001",
            status=Bill.STATUS_ACTIVE,
            grand_total=Decimal("100.00"),
            subtotal=Decimal("100.00"),
            payment_method=Bill.CASH,
        )
        b1.created_at = timezone.make_aware(timezone.datetime.combine(summary_date, time(10, 0)))
        b1.save()
        BillPayment.objects.create(bill=b1, method=Bill.CASH, amount=Decimal("100.00"))
        BillItem.objects.create(
            bill=b1,
            menu_item=p1,
            item_name="Masala Chai",
            quantity=Decimal("2.00"),
            price=Decimal("30.00"),
            cost_price=Decimal("10.00"),
            total=Decimal("60.00"),
        )
        BillItem.objects.create(
            bill=b1,
            menu_item=p2,
            item_name="Filter Coffee",
            quantity=Decimal("1.00"),
            price=Decimal("40.00"),
            cost_price=Decimal("15.00"),
            total=Decimal("40.00"),
        )

        # 5. Bill 2: ₹50 UPI
        b2 = Bill.objects.create(
            bill_number="BILL-SUMMARY-002",
            status=Bill.STATUS_ACTIVE,
            grand_total=Decimal("50.00"),
            subtotal=Decimal("50.00"),
            payment_method=Bill.UPI,
        )
        b2.created_at = timezone.make_aware(timezone.datetime.combine(summary_date, time(15, 0)))
        b2.save()
        BillPayment.objects.create(bill=b2, method=Bill.UPI, amount=Decimal("50.00"))
        BillItem.objects.create(
            bill=b2,
            menu_item=p3,
            item_name="Bun Maska",
            quantity=Decimal("1.00"),
            price=Decimal("50.00"),
            cost_price=Decimal("20.00"),
            total=Decimal("50.00"),
        )

        # 6. Expenses on summary_date: ₹25.00
        cat = ExpenseCategory.objects.create(name="Daily Utilities")
        Expense.objects.create(
            title="Transport",
            amount=Decimal("25.00"),
            expense_date=summary_date,
            category=cat,
        )

        # Math breakdown:
        # Sales = ₹150.00
        # Bills = 2
        # Cash = ₹100.00, UPI = ₹50.00, Khata = ₹0.00
        # COGS = (2*10) + (1*15) + (1*20) = 55.00
        # Expenses = 25.00
        # Net Profit = 150.00 - 55.00 - 25.00 = 70.00
        # Dues: Khata = 75.00, Supplier = 80.00, Total = 155.00

        url = reverse("daily_summary") + f"?date={summary_date.strftime('%Y-%m-%d')}"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_sales"], Decimal("150.00"))
        self.assertEqual(response.context["total_bills"], 2)
        self.assertEqual(response.context["cash_sales"], Decimal("100.00"))
        self.assertEqual(response.context["upi_sales"], Decimal("50.00"))
        self.assertEqual(response.context["khata_sales"], Decimal("0.00"))
        self.assertEqual(response.context["total_expenses"], Decimal("25.00"))
        self.assertEqual(response.context["net_profit"], Decimal("70.00"))
        self.assertEqual(response.context["customer_dues"], Decimal("75.00"))
        self.assertEqual(response.context["supplier_dues"], Decimal("80.00"))
        self.assertEqual(response.context["total_dues"], Decimal("155.00"))

        summary_text = response.context["summary_text"]
        self.assertIn("Sales: ₹150.00 (2 bills)", summary_text)
        self.assertIn("Cash ₹100.00 | UPI ₹50.00 | Khata ₹0.00", summary_text)
        self.assertIn("Expenses: ₹25.00", summary_text)
        self.assertIn("Net Profit: ₹70.00", summary_text)
        self.assertIn("Masala Chai", summary_text)
        self.assertIn("Dues: Khata ₹75.00 | Supplier ₹80.00 (Total: ₹155.00)", summary_text)

        whatsapp_url = response.context["whatsapp_url"]
        self.assertTrue(whatsapp_url.startswith("https://wa.me/919876543210?text="))

        # Assert template rendered elements
        self.assertContains(response, "Copy summary")
        self.assertContains(response, "Share via WhatsApp")
        self.assertContains(response, "Linux / macOS (Cron)")
        self.assertContains(response, "Windows (Task Scheduler)")


class ThermalPrinterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from escpos.printer import Dummy
        from pos.printing import (
            CMD_CUT,
            CMD_DRAWER_KICK,
            build_receipt_bytes,
            build_counter_slip_bytes,
            build_test_receipt_bytes,
            print_raw_escpos_bytes,
            sanitize_for_escpos,
        )

        cls.owner_group, _ = Group.objects.get_or_create(name="Owner")
        cls.cashier_group, _ = Group.objects.get_or_create(name="Cashier")

        cls.owner = User.objects.create_user(username="prn_owner", password="password123")
        cls.owner.groups.add(cls.owner_group)

        cls.cashier = User.objects.create_user(username="prn_cashier", password="password123")
        cls.cashier.groups.add(cls.cashier_group)

        cls.settings = BusinessSettings.current()
        cls.settings.business_name = "Chai Shai Cafe"
        cls.settings.address = "123 Main Street\nCoimbatore"
        cls.settings.phone = "9876543210"
        cls.settings.tax_number = "33AAAAA0000A1Z5"
        cls.settings.invoice_footer = "Thank you! Visit again."
        cls.settings.printer_enabled = True
        cls.settings.printer_type = "dummy"
        cls.settings.printer_paper_width = "80"
        cls.settings.printer_cut = True
        cls.settings.printer_drawer_kick = True
        cls.settings.printer_counter_slip = False
        cls.settings.save()

        cls.tea = Product.objects.create(
            name="Masala Chai",
            name_ta="மசாலா சாய்",
            category="Hot Beverages",
            price=Decimal("20.00"),
            cost_price=Decimal("8.00"),
            stock=Decimal("100"),
        )
        cls.samosa = Product.objects.create(
            name="Samosa",
            name_ta="சமோசா",
            category="Snacks",
            price=Decimal("30.00"),
            cost_price=Decimal("12.00"),
            stock=Decimal("50"),
        )

        cls.bill = Bill.objects.create(
            bill_number="BILL-PRN-001",
            token_number=42,
            subtotal=Decimal("70.00"),
            discount=Decimal("5.00"),
            tax_amount=Decimal("3.25"),
            gst_percent=Decimal("5.00"),
            grand_total=Decimal("68.25"),
            payment_method=Bill.CASH,
            tendered=Decimal("100.00"),
            change_due=Decimal("31.75"),
            note="Pack parcel securely",
            created_by=cls.cashier,
        )
        BillItem.objects.create(
            bill=cls.bill,
            menu_item=cls.tea,
            item_name="Masala Chai",
            price=Decimal("20.00"),
            quantity=Decimal("2.00"),
            total=Decimal("40.00"),
            modifier_names=["Less Sugar", "Extra Cardamom"],
            note="Spicy",
        )
        BillItem.objects.create(
            bill=cls.bill,
            menu_item=cls.samosa,
            item_name="Samosa",
            price=Decimal("30.00"),
            quantity=Decimal("1.00"),
            total=Decimal("30.00"),
        )

    def test_receipt_byte_stream_contains_totals_cut_and_kick(self):
        from pos.printing import build_receipt_bytes, CMD_CUT, CMD_DRAWER_KICK

        bs = BusinessSettings.current()
        bs.printer_cut = True
        bs.printer_drawer_kick = True
        bs.printer_paper_width = "80"
        bs.save()

        raw_bytes = build_receipt_bytes(self.bill, bs, is_duplicate=False)

        # Assert totals
        self.assertIn(b"70.00", raw_bytes)       # Subtotal
        self.assertIn(b"-5.00", raw_bytes)       # Discount
        self.assertIn(b"3.25", raw_bytes)        # Tax / GST
        self.assertIn(b"TOTAL:", raw_bytes)      # TOTAL label
        self.assertIn(b"68.25", raw_bytes)       # Grand total
        self.assertIn(b"TOKEN: #42", raw_bytes)  # Token number
        self.assertIn(b"Chai Shai Cafe", raw_bytes) # Shop header
        self.assertIn(b"Masala Chai", raw_bytes) # Item line
        self.assertIn(b"Less Sugar", raw_bytes)  # Modifier
        self.assertIn(b"Thank you! Visit again.", raw_bytes) # Footer

        # Assert ESC/POS cut and drawer kick commands
        self.assertIn(CMD_CUT, raw_bytes, "Byte stream must contain ESC/POS auto-cut command")
        self.assertIn(CMD_DRAWER_KICK, raw_bytes, "Byte stream must contain ESC/POS drawer-kick command")

    def test_cut_and_kick_toggles(self):
        from pos.printing import build_receipt_bytes, CMD_CUT, CMD_DRAWER_KICK

        bs = BusinessSettings.current()
        bs.printer_cut = False
        bs.printer_drawer_kick = False
        bs.save()

        raw_bytes = build_receipt_bytes(self.bill, bs)

        self.assertNotIn(CMD_CUT, raw_bytes, "Auto-cut command should be omitted when disabled")
        self.assertNotIn(CMD_DRAWER_KICK, raw_bytes, "Drawer kick command should be omitted when disabled")

    def test_dummy_printer_backend_output(self):
        from escpos.printer import Dummy
        from pos.printing import build_receipt_bytes, print_raw_escpos_bytes, CMD_CUT, CMD_DRAWER_KICK

        bs = BusinessSettings.current()
        bs.printer_type = "dummy"
        bs.printer_cut = True
        bs.printer_drawer_kick = True
        bs.save()

        payload = build_receipt_bytes(self.bill, bs)
        success, err, out_bytes = print_raw_escpos_bytes(payload, bs, dummy=True)

        self.assertTrue(success)
        self.assertEqual(err, "")
        self.assertIn(b"TOTAL:", out_bytes)
        self.assertIn(b"68.25", out_bytes)
        self.assertIn(CMD_CUT, out_bytes)
        self.assertIn(CMD_DRAWER_KICK, out_bytes)

    def test_duplicate_label_in_receipt(self):
        from pos.printing import build_receipt_bytes

        bs = BusinessSettings.current()

        # Regular print: not duplicate
        original_bytes = build_receipt_bytes(self.bill, bs, is_duplicate=False)
        self.assertNotIn(b"*** DUPLICATE RECEIPT ***", original_bytes)

        # Duplicate print
        dup_bytes = build_receipt_bytes(self.bill, bs, is_duplicate=True)
        self.assertIn(b"*** DUPLICATE RECEIPT ***", dup_bytes)

        # Reprint when print_count > 1
        self.bill.print_count = 2
        self.bill.save(update_fields=["print_count"])
        reprint_bytes = build_receipt_bytes(self.bill, bs, is_duplicate=False)
        self.assertIn(b"*** DUPLICATE RECEIPT ***", reprint_bytes)

    def test_paper_width_58mm_formatting(self):
        from pos.printing import build_receipt_bytes

        bs = BusinessSettings.current()
        bs.printer_paper_width = "58"
        bs.save()

        raw_bytes = build_receipt_bytes(self.bill, bs)
        self.assertIn(b"TOTAL:", raw_bytes)
        self.assertIn(b"68.25", raw_bytes)

        # Test line width boundary: lines should not exceed 32 characters in 58mm
        for line in raw_bytes.split(b"\n"):
            # Exclude lines with ESC/POS binary command prefixes (ESC, GS)
            if not (b"\x1b" in line or b"\x1d" in line):
                self.assertLessEqual(len(line), 32)

    def test_tamil_transliteration_and_safe_ascii_skipping(self):
        from pos.printing import sanitize_for_escpos

        # Known Tamil phrases transliterated cleanly
        self.assertEqual(sanitize_for_escpos("மசாலா சாய்"), "Masala Chai")
        self.assertEqual(sanitize_for_escpos("தேநீர்"), "Tea")
        self.assertEqual(sanitize_for_escpos("காபி"), "Coffee")

        # Unknown Tamil characters stripped safely or fallback used
        sanitized = sanitize_for_escpos("சுவையான சிற்றுண்டி", fallback="Tasty Snack")
        self.assertEqual(sanitized, "Tasty Snack")

        # Mixed string with brackets
        mixed = sanitize_for_escpos("மசாலா தோசை (Masala Dosa)")
        self.assertEqual(mixed, "Masala Dosa")

        # Never outputs unhandled unicode outside ascii safe range
        raw_res = sanitize_for_escpos("தமிழ்")
        raw_res.encode("ascii")  # Must not raise UnicodeEncodeError

    def test_counter_slip_byte_stream_contains_only_token_and_items(self):
        from pos.printing import build_counter_slip_bytes, CMD_CUT

        bs = BusinessSettings.current()
        bs.printer_cut = True
        bs.save()

        slip_bytes = build_counter_slip_bytes(self.bill, bs)

        # Must include slip header, token, items, modifiers, order notes
        self.assertIn(b"*** KITCHEN / COUNTER SLIP ***", slip_bytes)
        self.assertIn(b"TOKEN: #42", slip_bytes)
        self.assertIn(b"2 x Masala Chai", slip_bytes)
        self.assertIn(b"Less Sugar", slip_bytes)
        self.assertIn(b"1 x Samosa", slip_bytes)
        self.assertIn(b"ORDER NOTE: Pack parcel securely", slip_bytes)
        self.assertIn(CMD_CUT, slip_bytes)

        # Must NOT include financial amounts or totals
        self.assertNotIn(b"Subtotal", slip_bytes)
        self.assertNotIn(b"Discount", slip_bytes)
        self.assertNotIn(b"TOTAL:", slip_bytes)
        self.assertNotIn(b"Grand Total", slip_bytes)
        self.assertNotIn(b"68.25", slip_bytes)
        self.assertNotIn(b"Tendered", slip_bytes)

    def test_print_bill_endpoint_success_dummy(self):
        self.client.login(username="prn_cashier", password="password123")
        bs = BusinessSettings.current()
        bs.printer_enabled = True
        bs.printer_type = "dummy"
        bs.save()

        initial_count = self.bill.print_count
        response = self.client.post(
            reverse("print_bill_endpoint", kwargs={"bill_id": self.bill.id}),
            data=json.dumps({"is_duplicate": False}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["error"], "")

        self.bill.refresh_from_db()
        self.assertEqual(self.bill.print_count, initial_count + 1)

    def test_print_bill_endpoint_counter_slip(self):
        self.client.login(username="prn_cashier", password="password123")
        bs = BusinessSettings.current()
        bs.printer_enabled = True
        bs.printer_type = "dummy"
        bs.save()

        initial_count = self.bill.print_count
        response = self.client.post(
            reverse("print_bill_endpoint", kwargs={"bill_id": self.bill.id}),
            data=json.dumps({"slip_type": "counter"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

        # Counter slip printing should not increment customer bill print_count
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.print_count, initial_count)

    def test_print_bill_endpoint_when_disabled(self):
        self.client.login(username="prn_cashier", password="password123")
        bs = BusinessSettings.current()
        bs.printer_enabled = False
        bs.save()

        response = self.client.post(
            reverse("print_bill_endpoint", kwargs={"bill_id": self.bill.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["success"])
        self.assertIn("disabled", data["error"].lower())
        self.assertIn("fallback_url", data)

    def test_print_bill_endpoint_hardware_failure_fallback(self):
        self.client.login(username="prn_cashier", password="password123")
        bs = BusinessSettings.current()
        bs.printer_enabled = True
        bs.printer_type = "usb"
        bs.printer_address = "invalid_usb_hex"
        bs.save()

        response = self.client.post(
            reverse("print_bill_endpoint", kwargs={"bill_id": self.bill.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["success"])
        self.assertIn("fallback_url", data)

    def test_test_print_endpoint_owner_and_cashier(self):
        bs = BusinessSettings.current()
        bs.printer_enabled = True
        bs.printer_type = "dummy"
        bs.save()

        # Cashier is forbidden from triggering test prints
        self.client.login(username="prn_cashier", password="password123")
        cashier_resp = self.client.post(reverse("test_print_endpoint"), content_type="application/json")
        self.assertEqual(cashier_resp.status_code, 403)

        # Owner can trigger test print
        self.client.login(username="prn_owner", password="password123")
        owner_resp = self.client.post(reverse("test_print_endpoint"), content_type="application/json")
        self.assertEqual(owner_resp.status_code, 200)
        data = owner_resp.json()
        self.assertTrue(data["success"])

    def test_settings_page_test_print_button_post(self):
        self.client.login(username="prn_owner", password="password123")
        bs = BusinessSettings.current()
        bs.printer_enabled = True
        bs.printer_type = "dummy"
        bs.save()

        response = self.client.post(reverse("settings"), data={"test_print": "1"})
        self.assertRedirects(response, reverse("settings"))




