import calendar
import json
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.db.models.functions import Coalesce, Lower
from django.utils import timezone

TWO_PLACES = Decimal("0.01")


class Modifier(models.Model):
    name = models.CharField(max_length=120)
    price_delta = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        sign = "+" if self.price_delta >= 0 else ""
        return f"{self.name} ({sign}₹{self.price_delta:.2f})"


class CustomerQuerySet(models.QuerySet):
    def with_outstanding(self):
        credits = Coalesce(
            models.Sum(
                "khata_transactions__amount",
                filter=models.Q(khata_transactions__kind="CREDIT"),
            ),
            Decimal("0.00"),
        )
        payments = Coalesce(
            models.Sum(
                "khata_transactions__amount",
                filter=models.Q(khata_transactions__kind__in=["PAYMENT", "VOID"]),
            ),
            Decimal("0.00"),
        )
        return self.annotate(
            outstanding=Coalesce(
                models.F("opening_balance") + credits - payments,
                Decimal("0.00"),
                output_field=models.DecimalField(max_digits=10, decimal_places=2),
            )
        ).order_by("name")


class Product(models.Model):
    name = models.CharField(max_length=120)
    name_ta = models.CharField(max_length=120, blank=True, default="")
    category = models.CharField(max_length=80, blank=True)
    shortcut_key = models.CharField(max_length=12, blank=True, default="")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.TextField(blank=True)
    image = models.FileField(upload_to="menu_items/", blank=True, null=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    stock = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    stock_tracked = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_favorite = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)
    modifiers = models.ManyToManyField(Modifier, blank=True, related_name="products")

    def get_active_modifiers_json(self):
        mods = [
            {"id": m.id, "name": m.name, "price_delta": str(m.price_delta)}
            for m in self.modifiers.filter(is_active=True)
        ]
        return json.dumps(mods)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                Lower("shortcut_key"),
                condition=~models.Q(shortcut_key="") & models.Q(is_active=True),
                name="unique_active_product_shortcut_key",
            ),
        ]

    def save(self, *args, **kwargs):
        self.shortcut_key = (self.shortcut_key or "").strip().upper()
        super().save(*args, **kwargs)

    @property
    def display_name(self):
        from django.utils.translation import get_language
        lang = (get_language() or "")[:2]
        if lang == "ta" and self.name_ta:
            return self.name_ta
        return self.name

    @property
    def has_recipe(self):
        if not self.pk:
            return False
        return self.recipe_lines.exists()

    def get_derived_cost_price(self):
        if self.pk:
            lines = list(self.recipe_lines.select_related("stock_item").all())
            if lines:
                return sum(
                    (l.qty_per_unit * l.stock_item.cost_per_unit for l in lines),
                    Decimal("0.00"),
                ).quantize(TWO_PLACES)
        return None

    @property
    def manual_cost_price(self):
        return super().__getattribute__("cost_price")

    def __getattribute__(self, name):
        if name == "cost_price":
            try:
                state = super().__getattribute__("_state")
                pk = super().__getattribute__("pk")
                if state.db is not None and pk:
                    recipe_mgr = super().__getattribute__("recipe_lines")
                    lines = list(recipe_mgr.select_related("stock_item").all())
                    if lines:
                        total = sum(
                            (line.qty_per_unit * line.stock_item.cost_per_unit for line in lines),
                            Decimal("0.00"),
                        )
                        return total.quantize(TWO_PLACES)
            except Exception:
                pass
        return super().__getattribute__(name)

    def __str__(self):
        return self.name


class Customer(models.Model):
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    opening_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    credit_limit = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    objects = CustomerQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    @property
    def balance(self):
        if hasattr(self, "outstanding"):
            return self.outstanding
        credits = self.khata_transactions.filter(kind="CREDIT").aggregate(
            total=models.Sum("amount")
        )["total"] or Decimal("0")
        payments = self.khata_transactions.filter(kind__in=["PAYMENT", "VOID"]).aggregate(
            total=models.Sum("amount")
        )["total"] or Decimal("0")
        return Decimal(self.opening_balance) + credits - payments

    def __str__(self):
        return self.name


class Shift(models.Model):
    STATUS_OPEN = "OPEN"
    STATUS_CLOSED = "CLOSED"

    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_CLOSED, "Closed"),
    ]

    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="opened_shifts",
    )
    opened_at = models.DateTimeField(default=timezone.now)
    opening_cash = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="closed_shifts",
    )
    closed_at = models.DateTimeField(null=True, blank=True)

    counted_cash = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    expected_cash = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    variance = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    note = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN)

    class Meta:
        ordering = ["-opened_at"]

    def __str__(self):
        return f"Shift #{self.pk} ({self.get_status_display()}) - {self.opened_at.strftime('%Y-%m-%d %H:%M')}"

    @classmethod
    def current_open(cls):
        return cls.objects.filter(status=cls.STATUS_OPEN).first()


class Bill(models.Model):
    CASH = "CASH"
    UPI = "UPI"
    CARD = "CARD"
    KHATA = "KHATA"
    SPLIT = "SPLIT"

    PAYMENT_CHOICES = [
        (CASH, "Cash"),
        (UPI, "UPI"),
        (CARD, "Card"),
        (KHATA, "Khata (credit)"),
        (SPLIT, "Split"),
    ]

    STATUS_ACTIVE = "ACTIVE"
    STATUS_VOID = "VOID"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_VOID, "Void"),
    ]

    shift = models.ForeignKey(
        Shift,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bills",
    )
    bill_number = models.CharField(max_length=40, unique=True)
    customer = models.ForeignKey(
        Customer, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="bills",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_bills",
    )
    created_at = models.DateTimeField(default=timezone.now)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=10, choices=PAYMENT_CHOICES)
    tendered = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    change_due = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    void_reason = models.TextField(blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="voided_bills",
    )

    token_number = models.PositiveIntegerField(null=True, blank=True)
    note = models.TextField(blank=True, default="")
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    gst_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    print_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.bill_number

    @property
    def taxable_value(self):
        return (self.grand_total - self.tax_amount).quantize(Decimal("0.01"))

    @property
    def cgst_percent(self):
        return (self.gst_percent / Decimal("2")).quantize(Decimal("0.01"))

    @property
    def sgst_percent(self):
        return (self.gst_percent / Decimal("2")).quantize(Decimal("0.01"))

    @property
    def cgst_amount(self):
        return (self.tax_amount / Decimal("2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def sgst_amount(self):
        return (self.tax_amount - self.cgst_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def save(self, *args, **kwargs):
        if not self.token_number:
            created_dt = self.created_at or timezone.now()
            bill_date = timezone.localdate(created_dt) if timezone.is_aware(created_dt) else created_dt.date()
            max_token = (
                Bill.objects.filter(created_at__date=bill_date)
                .aggregate(m=models.Max("token_number"))["m"]
                or 0
            )
            self.token_number = max_token + 1
        super().save(*args, **kwargs)


class HeldOrder(models.Model):
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="held_orders",
    )
    created_at = models.DateTimeField(default=timezone.now)
    label = models.CharField(max_length=100, blank=True)
    items = models.JSONField(default=list)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    customer = models.ForeignKey(
        Customer,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="held_orders",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"HeldOrder #{self.pk} - {self.label or 'Untitled'}"


class BillPayment(models.Model):
    bill = models.ForeignKey(Bill, related_name="payments", on_delete=models.CASCADE)
    method = models.CharField(max_length=10, choices=Bill.PAYMENT_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.bill.bill_number} - {self.get_method_display()}: {self.amount}"


class BillItem(models.Model):
    bill = models.ForeignKey(Bill, related_name="items", on_delete=models.CASCADE)
    menu_item = models.ForeignKey(Product, on_delete=models.PROTECT)
    item_name = models.CharField(max_length=120)
    shortcut_key = models.CharField(max_length=12, blank=True, default="")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    note = models.CharField(max_length=255, blank=True, default="")
    modifier_names = models.JSONField(default=list, blank=True)

    def __str__(self):
        return f"{self.item_name} x {self.quantity}"

    @property
    def display_name(self):
        from django.utils.translation import get_language
        lang = (get_language() or "")[:2]
        if lang == "ta" and self.menu_item and getattr(self.menu_item, "name_ta", ""):
            return self.menu_item.name_ta
        return self.item_name


class KhataTransaction(models.Model):
    CREDIT = "CREDIT"
    PAYMENT = "PAYMENT"
    VOID = "VOID"

    KIND_CHOICES = [
        (CREDIT, "Credit Sale"),
        (PAYMENT, "Payment Received"),
        (VOID, "Void Reversal"),
    ]

    METHOD_CASH = "CASH"
    METHOD_UPI = "UPI"
    METHOD_CARD = "CARD"
    METHOD_CHOICES = [
        (METHOD_CASH, "Cash"),
        (METHOD_UPI, "UPI"),
        (METHOD_CARD, "Card"),
    ]

    customer = models.ForeignKey(
        Customer, related_name="khata_transactions", on_delete=models.PROTECT
    )
    bill = models.ForeignKey(
        Bill,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="khata_entries",
    )
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    method = models.CharField(max_length=10, choices=METHOD_CHOICES, blank=True, default="")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.customer} - {self.get_kind_display()} - {self.amount}"


class ExpenseCategory(models.Model):
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Expense categories"

    def __str__(self):
        return self.name


class ExpenseQuerySet(models.QuerySet):
    def filter(self, *args, **kwargs):
        if "category" in kwargs and isinstance(kwargs["category"], str):
            kwargs["category__name"] = kwargs.pop("category")
        return super().filter(*args, **kwargs)


class Expense(models.Model):
    METHOD_CASH = "CASH"
    METHOD_UPI = "UPI"
    METHOD_CARD = "CARD"
    METHOD_BANK = "BANK"
    PAYMENT_METHOD_CHOICES = [
        (METHOD_CASH, "Cash"),
        (METHOD_UPI, "UPI"),
        (METHOD_CARD, "Card"),
        (METHOD_BANK, "Bank Transfer"),
    ]

    title = models.CharField(max_length=120, blank=True)
    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.PROTECT,
        related_name="expenses",
    )
    description = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    expense_date = models.DateField(default=timezone.localdate)
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default=METHOD_CASH,
    )
    paid_from_drawer = models.BooleanField(null=True, blank=True, default=False)
    shift = models.ForeignKey(
        Shift,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="expenses",
    )
    receipt_image = models.FileField(upload_to="receipts/", blank=True, null=True)
    recurring_expense = models.ForeignKey(
        "RecurringExpense",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generated_expenses",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    objects = ExpenseQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __init__(self, *args, **kwargs):
        if "category" in kwargs and isinstance(kwargs["category"], str):
            val = kwargs["category"].strip()
            if val:
                cat_obj, _ = ExpenseCategory.objects.get_or_create(name=val)
                kwargs["category"] = cat_obj
            else:
                kwargs["category"] = None
        super().__init__(*args, **kwargs)

    def __setattr__(self, name, value):
        if name == "category" and isinstance(value, str):
            if value.strip():
                value, _ = ExpenseCategory.objects.get_or_create(name=value.strip())
            else:
                value = None
        super().__setattr__(name, value)

    @property
    def display_title(self):
        cat_str = self.category.name if self.category else ""
        return self.title or self.description or cat_str

    def __str__(self):
        return self.display_title


class RecurringExpense(models.Model):
    FREQ_DAILY = "DAILY"
    FREQ_WEEKLY = "WEEKLY"
    FREQ_MONTHLY = "MONTHLY"
    FREQUENCY_CHOICES = [
        (FREQ_DAILY, "Daily"),
        (FREQ_WEEKLY, "Weekly"),
        (FREQ_MONTHLY, "Monthly"),
    ]

    title = models.CharField(max_length=120)
    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.PROTECT,
        related_name="recurring_expenses",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(
        max_length=20,
        choices=Expense.PAYMENT_METHOD_CHOICES,
        default=Expense.METHOD_CASH,
    )
    frequency = models.CharField(
        max_length=10,
        choices=FREQUENCY_CHOICES,
        default=FREQ_MONTHLY,
    )
    next_due = models.DateField(default=timezone.localdate)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["next_due", "title"]

    def __str__(self):
        return f"{self.title} (₹{self.amount} {self.get_frequency_display()})"


def advance_next_due(current_date, frequency):
    if frequency == RecurringExpense.FREQ_DAILY:
        return current_date + timedelta(days=1)
    elif frequency == RecurringExpense.FREQ_WEEKLY:
        return current_date + timedelta(days=7)
    elif frequency == RecurringExpense.FREQ_MONTHLY:
        year = current_date.year + (current_date.month // 12)
        month = (current_date.month % 12) + 1
        max_days = calendar.monthrange(year, month)[1]
        day = min(current_date.day, max_days)
        return current_date.replace(year=year, month=month, day=day)
    return current_date + timedelta(days=1)


def process_recurring_expenses(target_date=None):
    if target_date is None:
        target_date = timezone.localdate()
    created_count = 0
    recurring_list = RecurringExpense.objects.filter(is_active=True, next_due__lte=target_date).order_by("id")
    for rec in recurring_list:
        while rec.next_due <= target_date:
            existing = Expense.objects.filter(
                recurring_expense=rec,
                expense_date=rec.next_due,
            ).exists()
            if not existing:
                Expense.objects.create(
                    title=rec.title,
                    category=rec.category,
                    amount=rec.amount,
                    expense_date=rec.next_due,
                    payment_method=rec.method,
                    recurring_expense=rec,
                    notes=f"Recurring: {rec.title} ({rec.get_frequency_display()})",
                )
                created_count += 1
            rec.next_due = advance_next_due(rec.next_due, rec.frequency)
        rec.save()
    return created_count


class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pos_audit_logs",
    )
    action = models.CharField(max_length=50)
    model_name = models.CharField(max_length=100)
    object_repr = models.CharField(max_length=255)
    timestamp = models.DateTimeField(auto_now_add=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.action} {self.model_name} {self.object_repr}"

    @property
    def formatted_details(self):
        if not isinstance(self.details, dict):
            return []
        items = []
        for k, v in self.details.items():
            if isinstance(v, (list, tuple)) and len(v) == 2:
                items.append({
                    "field": k,
                    "old": v[0],
                    "new": v[1],
                    "is_diff": v[0] is not None and v[1] is not None,
                })
            else:
                items.append({
                    "field": k,
                    "old": None,
                    "new": v,
                    "is_diff": False,
                })
        return items


class BusinessSettings(models.Model):
    RECEIPT_MODE_A4 = "a4"
    RECEIPT_MODE_THERMAL = "thermal"
    RECEIPT_MODE_THERMAL58 = "thermal58"
    RECEIPT_MODE_CHOICES = [
        (RECEIPT_MODE_A4, "A4 Invoice"),
        (RECEIPT_MODE_THERMAL, "Thermal (80mm)"),
        (RECEIPT_MODE_THERMAL58, "Thermal (58mm)"),
    ]

    business_name = models.CharField(max_length=160, default="Cafe POS")
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    tax_number = models.CharField(max_length=80, blank=True)
    currency_symbol = models.CharField(max_length=5, default="₹")
    invoice_footer = models.CharField(max_length=255, default="Thank you for visiting!")
    default_receipt_mode = models.CharField(
        max_length=15,
        choices=RECEIPT_MODE_CHOICES,
        default=RECEIPT_MODE_A4,
    )
    gst_enabled = models.BooleanField(default=False)
    gst_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    prices_include_gst = models.BooleanField(default=False)
    fssai_number = models.CharField(max_length=50, blank=True, default="")
    show_cashier = models.BooleanField(default=True)
    low_stock_threshold = models.PositiveIntegerField(default=5)
    backup_directory = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Custom directory for database backups (e.g. USB drive path or external folder). Leave blank for default 'backups/'.",
    )

    PRINTER_TYPE_USB = "usb"
    PRINTER_TYPE_NETWORK = "network"
    PRINTER_TYPE_SERIAL = "serial"
    PRINTER_TYPE_DUMMY = "dummy"
    PRINTER_TYPE_CHOICES = [
        (PRINTER_TYPE_USB, "USB (Vendor & Product ID)"),
        (PRINTER_TYPE_NETWORK, "Network (TCP/IP:Port)"),
        (PRINTER_TYPE_SERIAL, "Serial / COM Port"),
        (PRINTER_TYPE_DUMMY, "Virtual / Dummy (Testing)"),
    ]

    PRINTER_WIDTH_80 = "80"
    PRINTER_WIDTH_58 = "58"
    PRINTER_WIDTH_CHOICES = [
        (PRINTER_WIDTH_80, "80mm (~48 columns)"),
        (PRINTER_WIDTH_58, "58mm (~32 columns)"),
    ]

    printer_enabled = models.BooleanField(default=False, help_text="Enable direct hardware ESC/POS thermal printing.")
    printer_type = models.CharField(max_length=20, choices=PRINTER_TYPE_CHOICES, default=PRINTER_TYPE_USB)
    printer_address = models.CharField(max_length=120, blank=True, default="04b8:0e15", help_text="USB hex IDs (e.g. 04b8:0e15), IP:Port (e.g. 192.168.1.100:9100), or Serial port (e.g. COM3).")
    printer_paper_width = models.CharField(max_length=10, choices=PRINTER_WIDTH_CHOICES, default=PRINTER_WIDTH_80)
    printer_cut = models.BooleanField(default=True, help_text="Send paper auto-cut command.")
    printer_drawer_kick = models.BooleanField(default=False, help_text="Send drawer-kick pulse signal.")
    printer_counter_slip = models.BooleanField(default=False, help_text="Print a compact counter slip (token & items only).")

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.business_name

    @classmethod
    def current(cls):
        settings, _ = cls.objects.get_or_create(pk=1)
        return settings


class StaffProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="staff_profile",
    )
    pin_hash = models.CharField(max_length=128, blank=True, default="")
    language = models.CharField(max_length=10, blank=True, default="en", choices=[("en", "English"), ("ta", "Tamil")])

    class Meta:
        ordering = ["user__username"]

    @property
    def preferred_language(self):
        return self.language or "en"

    @preferred_language.setter
    def preferred_language(self, val):
        self.language = val

    def __init__(self, *args, **kwargs):
        kwargs.pop("role", None)
        super().__init__(*args, **kwargs)

    def set_pin(self, raw_pin):
        if raw_pin:
            self.pin_hash = make_password(str(raw_pin).strip())
        else:
            self.pin_hash = ""

    def check_pin(self, raw_pin):
        if not self.pin_hash or not raw_pin:
            return False
        return check_password(str(raw_pin).strip(), self.pin_hash)

    def has_pin(self):
        return bool(self.pin_hash)

    def __str__(self):
        return f"StaffProfile({self.user.username})"


class WastageQuerySet(models.QuerySet):
    def delete(self):
        raise ValueError("Wastage records cannot be deleted. Log a correction entry instead.")


class Wastage(models.Model):
    REASON_SPILLED = "SPILLED"
    REASON_EXPIRED = "EXPIRED"
    REASON_RETURNED = "RETURNED"
    REASON_STAFF = "STAFF"
    REASON_OTHER = "OTHER"
    REASON_CORRECTION = "CORRECTION"

    REASON_CHOICES = [
        (REASON_SPILLED, "Spilled / Damaged"),
        (REASON_EXPIRED, "Expired / Spoiled"),
        (REASON_RETURNED, "Returned by Customer"),
        (REASON_STAFF, "Staff Consumption"),
        (REASON_OTHER, "Other"),
        (REASON_CORRECTION, "Correction / Adjustment"),
    ]

    product = models.ForeignKey(
        Product,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="wastages",
    )
    stock_item = models.ForeignKey(
        "StockItem",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="wastages",
    )
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default=REASON_OTHER)
    note = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recorded_wastages",
    )
    created_at = models.DateTimeField(default=timezone.now)

    objects = WastageQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Wastage #{self.pk}: {self.item_name} x {self.quantity} ({self.reason})"

    @property
    def item_name(self):
        if self.product:
            return self.product.name
        if self.stock_item:
            return self.stock_item.name
        return "Unknown"

    @property
    def item_type(self):
        if self.product:
            return "Product"
        if self.stock_item:
            return "Stock Item"
        return "Unknown"

    @property
    def unit(self):
        if self.product:
            return "units"
        if self.stock_item:
            return self.stock_item.unit
        return ""

    @property
    def is_correction(self):
        return self.reason == self.REASON_CORRECTION or self.quantity < Decimal("0.00")

    def clean(self):
        super().clean()
        if not self.product and not self.stock_item:
            from django.core.exceptions import ValidationError
            raise ValidationError("Please select either a product or a raw material stock item.")
        if self.product and self.stock_item:
            from django.core.exceptions import ValidationError
            raise ValidationError("Please select only one: either a product or a stock item, not both.")

    def delete(self, *args, **kwargs):
        raise ValueError("Wastage records cannot be deleted. Log a correction entry instead.")


class Supplier(models.Model):
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def total_purchases(self):
        total = Decimal("0.00")
        for p in self.purchases.all():
            total += p.total_amount
        return total

    @property
    def total_paid(self):
        res = self.purchases.aggregate(total=models.Sum("paid_amount"))["total"]
        return res if res is not None else Decimal("0.00")

    @property
    def outstanding_dues(self):
        return self.total_purchases - self.total_paid

    @property
    def outstanding(self):
        return self.outstanding_dues

    @property
    def dues(self):
        return self.outstanding_dues


class StockItemQuerySet(models.QuerySet):
    def low_stock(self):
        return self.filter(current_qty__lte=models.F("reorder_level"))

    def negative(self):
        return self.filter(current_qty__lt=Decimal("0.00"))


class StockItem(models.Model):
    name = models.CharField(max_length=150)
    unit = models.CharField(max_length=30)
    current_qty = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    reorder_level = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    cost_per_unit = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    created_at = models.DateTimeField(default=timezone.now)

    objects = StockItemQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.unit})"

    @property
    def is_low_stock(self):
        return self.current_qty <= self.reorder_level

    @property
    def is_negative(self):
        return self.current_qty < Decimal("0.00")

    @property
    def total_value(self):
        return (self.current_qty * self.cost_per_unit).quantize(TWO_PLACES)

    def get_days_of_cover(self, days=7):
        if not self.pk:
            return None
        start_date = timezone.now() - timedelta(days=days)
        from pos.models import BillItem
        sold_map = dict(
            BillItem.objects.filter(
                bill__created_at__gte=start_date,
                bill__status="ACTIVE",
            )
            .values("menu_item_id")
            .annotate(t=models.Sum("quantity"))
            .values_list("menu_item_id", "t")
        )
        if not sold_map:
            return None
        total_used = Decimal("0.00")
        for rl in self.recipe_lines.filter(product_id__in=sold_map.keys()):
            total_used += rl.qty_per_unit * sold_map.get(rl.product_id, Decimal("0.00"))
        if total_used <= Decimal("0.00"):
            return None
        daily_use = total_used / Decimal(str(days))
        if self.current_qty <= Decimal("0.00"):
            return Decimal("0.0")
        return (self.current_qty / daily_use).quantize(Decimal("0.1"))

    @property
    def days_of_cover(self):
        return self.get_days_of_cover(7)


class Purchase(models.Model):
    METHOD_CASH = "CASH"
    METHOD_UPI = "UPI"
    METHOD_CARD = "CARD"
    METHOD_BANK = "BANK"
    METHOD_CREDIT = "CREDIT"
    PAYMENT_METHOD_CHOICES = [
        (METHOD_CASH, "Cash"),
        (METHOD_UPI, "UPI"),
        (METHOD_CARD, "Card"),
        (METHOD_BANK, "Bank Transfer"),
        (METHOD_CREDIT, "Credit / Due"),
    ]

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="purchases",
    )
    date = models.DateField(default=timezone.localdate)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default=METHOD_CASH,
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    expense = models.ForeignKey(
        "Expense",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="purchases",
    )

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"Purchase #{self.id} - {self.supplier.name} ({self.date})"

    @property
    def total_amount(self):
        total = Decimal("0.00")
        for line in self.lines.all():
            total += line.line_total
        return total

    @property
    def total(self):
        return self.total_amount

    @property
    def due_amount(self):
        return max(Decimal("0.00"), self.total_amount - self.paid_amount)

    @property
    def due(self):
        return self.due_amount

    def add_line(self, stock_item, qty, unit_cost):
        return PurchaseLine.objects.create(
            purchase=self,
            stock_item=stock_item,
            qty=qty,
            unit_cost=unit_cost,
        )

    def post_drawer_expense_if_eligible(self):
        if self.method == self.METHOD_CASH and self.paid_amount > Decimal("0.00") and not self.expense:
            open_shift = Shift.current_open()
            if open_shift:
                cat, _ = ExpenseCategory.objects.get_or_create(name="Raw Materials")
                expense = Expense.objects.create(
                    title=f"Purchase #{self.id} - {self.supplier.name}",
                    amount=self.paid_amount,
                    category=cat,
                    payment_method=Expense.METHOD_CASH,
                    paid_from_drawer=True,
                    shift=open_shift,
                    expense_date=self.date,
                    notes=f"Cash payment for purchase #{self.id} from {self.supplier.name}",
                )
                self.expense = expense
                self.save(update_fields=["expense"])
                return expense
        return None


class PurchaseLine(models.Model):
    purchase = models.ForeignKey(
        Purchase,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    stock_item = models.ForeignKey(
        StockItem,
        on_delete=models.PROTECT,
        related_name="purchase_lines",
    )
    qty = models.DecimalField(max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.stock_item.name} x {self.qty} @ {self.unit_cost}"

    @property
    def line_total(self):
        return (self.qty * self.unit_cost).quantize(TWO_PLACES)

    def clean(self):
        super().clean()
        if self.qty is not None and self.qty <= Decimal("0"):
            from django.core.exceptions import ValidationError
            raise ValidationError("Quantity must be greater than zero.")
        if self.unit_cost is not None and self.unit_cost < Decimal("0"):
            from django.core.exceptions import ValidationError
            raise ValidationError("Unit cost cannot be negative.")

    def save(self, *args, **kwargs):
        if self.qty is not None and self.qty <= Decimal("0"):
            raise ValueError("Quantity must be greater than zero.")
        if self.unit_cost is not None and self.unit_cost < Decimal("0"):
            raise ValueError("Unit cost cannot be negative.")
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new:
            self.apply_to_stock()

    def apply_to_stock(self):
        stock_item = self.stock_item
        stock_item.current_qty = (stock_item.current_qty + self.qty).quantize(TWO_PLACES)
        stock_item.cost_per_unit = self.unit_cost.quantize(TWO_PLACES)
        stock_item.save(update_fields=["current_qty", "cost_per_unit"])


class RecipeLine(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="recipe_lines",
    )
    stock_item = models.ForeignKey(
        StockItem,
        on_delete=models.PROTECT,
        related_name="recipe_lines",
    )
    qty_per_unit = models.DecimalField(
        max_digits=10,
        decimal_places=4,
    )

    class Meta:
        ordering = ["id"]
        unique_together = ("product", "stock_item")

    def __str__(self):
        return f"{self.product.name} -> {self.qty_per_unit} {self.stock_item.unit} {self.stock_item.name}"

    @property
    def line_cost(self):
        return (self.qty_per_unit * self.stock_item.cost_per_unit).quantize(TWO_PLACES)

    def clean(self):
        super().clean()
        if self.qty_per_unit is not None and self.qty_per_unit <= Decimal("0"):
            from django.core.exceptions import ValidationError
            raise ValidationError("Quantity per unit must be greater than zero.")

    def save(self, *args, **kwargs):
        if self.qty_per_unit is not None and self.qty_per_unit <= Decimal("0"):
            raise ValueError("Quantity per unit must be greater than zero.")
        super().save(*args, **kwargs)


