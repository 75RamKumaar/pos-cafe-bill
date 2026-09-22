from decimal import Decimal
from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class Product(models.Model):
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=80, blank=True)
    shortcut_key = models.CharField(max_length=12, blank=True, default="")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.TextField(blank=True)
    image = models.FileField(upload_to="menu_items/", blank=True, null=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    stock = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "name"]
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

    def __str__(self):
        return self.name


class Customer(models.Model):
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    opening_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ["name"]

    @property
    def balance(self):
        credits = self.khata_transactions.filter(kind="CREDIT").aggregate(
            total=models.Sum("amount")
        )["total"] or Decimal("0")
        payments = self.khata_transactions.filter(kind="PAYMENT").aggregate(
            total=models.Sum("amount")
        )["total"] or Decimal("0")
        return Decimal(self.opening_balance) + credits - payments

    def __str__(self):
        return self.name


class Bill(models.Model):
    CASH = "CASH"
    UPI = "UPI"
    CARD = "CARD"

    PAYMENT_CHOICES = [
        (CASH, "Cash"),
        (UPI, "UPI"),
        (CARD, "Card"),
    ]

    bill_number = models.CharField(max_length=40, unique=True)
    customer = models.ForeignKey(
        Customer, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="bills",
    )
    created_at = models.DateTimeField(default=timezone.now)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=10, choices=PAYMENT_CHOICES)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.bill_number


class BillItem(models.Model):
    bill = models.ForeignKey(Bill, related_name="items", on_delete=models.CASCADE)
    menu_item = models.ForeignKey(Product, on_delete=models.PROTECT)
    item_name = models.CharField(max_length=120)
    shortcut_key = models.CharField(max_length=12, blank=True, default="")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.item_name} x {self.quantity}"


class KhataTransaction(models.Model):
    CREDIT = "CREDIT"
    PAYMENT = "PAYMENT"

    KIND_CHOICES = [
        (CREDIT, "Credit Sale"),
        (PAYMENT, "Payment Received"),
    ]

    customer = models.ForeignKey(
        Customer, related_name="khata_transactions", on_delete=models.CASCADE
    )
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.customer} - {self.get_kind_display()} - {self.amount}"


class Expense(models.Model):
    title = models.CharField(max_length=120, blank=True)
    category = models.CharField(max_length=80)
    description = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    expense_date = models.DateField(default=timezone.localdate)
    payment_method = models.CharField(max_length=20, default="CASH")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    @property
    def display_title(self):
        return self.title or self.description or self.category

    def __str__(self):
        return self.display_title


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


class BusinessSettings(models.Model):
    business_name = models.CharField(max_length=160, default="Cafe POS")
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    tax_number = models.CharField(max_length=80, blank=True)
    currency_symbol = models.CharField(max_length=5, default="₹")
    invoice_footer = models.CharField(max_length=255, default="Thank you for visiting!")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.business_name

    @classmethod
    def current(cls):
        settings, _ = cls.objects.get_or_create(pk=1)
        return settings


class Wastage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
