from decimal import Decimal
from io import BytesIO
import os
import re

from django.contrib.auth.models import Group, User
from django.db.models import Q
from django import forms
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import (
    BusinessSettings,
    Customer,
    Expense,
    ExpenseCategory,
    Modifier,
    Product,
    Purchase,
    PurchaseLine,
    RecurringExpense,
    Shift,
    StaffProfile,
    StockItem,
    Supplier,
    Wastage,
)


MAX_IMAGE_SIZE = 3 * 1024 * 1024
MAX_IMAGE_DIMENSION = 800
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class ModifierForm(forms.ModelForm):
    price_delta = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        initial=Decimal("0.00"),
        label="Price Delta (₹)",
        help_text="Additional price (positive) or discount (negative) applied to item.",
    )

    class Meta:
        model = Modifier
        fields = ["name", "price_delta", "is_active"]
        widgets = {
            "is_active": forms.CheckboxInput(attrs={"style": "width:20px;height:20px;min-height:20px;cursor:pointer"}),
        }

    def clean_name(self):
        name = self.cleaned_data.get("name", "").strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        return name

    def clean_price_delta(self):
        delta = self.cleaned_data.get("price_delta")
        if delta is None:
            return Decimal("0.00")
        return delta


class MenuItemForm(forms.ModelForm):
    price = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_digits=10,
        decimal_places=2,
        error_messages={"min_value": "Price must be at least 0.01."},
    )
    cost_price = forms.DecimalField(
        min_value=Decimal("0"),
        max_digits=10,
        decimal_places=2,
        required=False,
        initial=Decimal("0.00"),
        error_messages={"min_value": "Cost price must be greater than or equal to zero."},
    )
    stock = forms.DecimalField(
        min_value=Decimal("0"),
        max_digits=10,
        decimal_places=2,
        required=False,
        initial=Decimal("0.00"),
        error_messages={"min_value": "Stock must be greater than or equal to zero."},
    )

    stock_tracked = forms.BooleanField(
        required=False,
        initial=False,
        label="Track stock inventory",
    )

    modifiers = forms.ModelMultipleChoiceField(
        queryset=Modifier.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "modifier-checkboxes"}),
        label="Modifiers / Add-ons",
        help_text="Select modifiers/add-ons available for this item (optional).",
    )

    class Meta:
        model = Product
        fields = [
            "name",
            "name_ta",
            "category",
            "shortcut_key",
            "price",
            "cost_price",
            "stock",
            "stock_tracked",
            "modifiers",
            "description",
            "image",
            "is_favorite",
            "is_active",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "stock_tracked": forms.CheckboxInput(attrs={"style": "width:20px;height:20px;min-height:20px;cursor:pointer"}),
            "is_favorite": forms.CheckboxInput(attrs={"style": "width:20px;height:20px;min-height:20px;cursor:pointer"}),
            "is_active": forms.CheckboxInput(attrs={"style": "width:20px;height:20px;min-height:20px;cursor:pointer"}),
        }

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        return name

    def clean_cost_price(self):
        cost_price = self.cleaned_data.get("cost_price")
        if cost_price is None:
            return Decimal("0.00")
        return cost_price

    def clean_stock(self):
        stock = self.cleaned_data.get("stock")
        if stock is None:
            return Decimal("0.00")
        return stock

    def clean_shortcut_key(self):
        shortcut = self.cleaned_data.get("shortcut_key", "").strip().upper()
        if shortcut and not re.fullmatch(r"[A-Z0-9]+", shortcut):
            raise forms.ValidationError("Use only letters and numbers, such as T, 10, or A1.")
        return shortcut

    def clean_category(self):
        category = self.cleaned_data["category"].strip()
        return category

    def clean(self):
        cleaned_data = super().clean()
        shortcut = cleaned_data.get("shortcut_key", "")
        is_active = cleaned_data.get("is_active", True)
        if shortcut and is_active:
            duplicate = Product.objects.filter(is_active=True, shortcut_key__iexact=shortcut)
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                self.add_error(
                    "shortcut_key",
                    f"Shortcut key {shortcut} is already assigned to {duplicate.first().name}.",
                )
        return cleaned_data

    def clean_image(self):
        image = self.cleaned_data.get("image")
        if not image or not isinstance(image, UploadedFile):
            return image

        name = getattr(image, "name", "")
        ext = os.path.splitext(name)[1].lower()
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            raise forms.ValidationError(
                f"Unsupported file extension '{ext}'. Allowed extensions are: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}."
            )

        if image.size > MAX_IMAGE_SIZE:
            raise forms.ValidationError("Image files must be 3 MB or smaller.")

        try:
            image.file.seek(0)
            with Image.open(image.file) as opened_image:
                opened_image.verify()
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError):
            raise forms.ValidationError("Upload a valid image file.")
        finally:
            image.file.seek(0)
        return image

    def save(self, commit=True):
        instance = super().save(commit=False)
        uploaded_image = self.files.get("image")
        if uploaded_image:
            uploaded_image.file.seek(0)
            with Image.open(uploaded_image.file) as opened_image:
                image_format = opened_image.format or "PNG"
                processed_image = ImageOps.exif_transpose(opened_image)
                processed_image.thumbnail(
                    (MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION),
                    Image.Resampling.LANCZOS,
                )
                if image_format == "JPEG" and processed_image.mode not in {"RGB", "L"}:
                    processed_image = processed_image.convert("RGB")

                output = BytesIO()
                save_options = {"format": image_format}
                if image_format == "JPEG":
                    save_options.update(quality=90, optimize=True)
                processed_image.save(output, **save_options)

            instance.image.save(
                uploaded_image.name,
                ContentFile(output.getvalue()),
                save=False,
            )

        if commit:
            instance.save()
        return instance


class CustomerForm(forms.ModelForm):
    credit_limit = forms.DecimalField(
        min_value=Decimal("0.00"),
        max_digits=10,
        decimal_places=2,
        required=False,
        label="Credit Limit (₹)",
        help_text="Maximum allowed khata balance. Leave blank for no limit.",
    )

    class Meta:
        model = Customer
        fields = ["name", "phone", "email", "credit_limit", "address", "notes"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3}), "notes": forms.Textarea(attrs={"rows": 3})}

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        return name


class ExpenseCategoryForm(forms.ModelForm):
    class Meta:
        model = ExpenseCategory
        fields = ["name", "is_active"]

    def clean_name(self):
        name = self.cleaned_data.get("name", "").strip()
        if not name:
            raise forms.ValidationError("Category name is required.")
        return name


class ExpenseForm(forms.ModelForm):
    paid_from_drawer = forms.BooleanField(
        required=False,
        initial=True,
        label="Paid from cash drawer",
    )
    category = forms.ModelChoiceField(
        queryset=ExpenseCategory.objects.all(),
        required=False,
        label="Category",
    )
    new_category = forms.CharField(
        max_length=120,
        required=False,
        label="Or New Category",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Supplies, Utilities"}),
        help_text="Leave category blank to add a new category with this name.",
    )
    receipt_image = forms.FileField(
        required=False,
        label="Receipt Image (Optional)",
        help_text="Upload receipt image (JPG, PNG, WebP, GIF up to 3MB).",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.instance.title:
            self.initial["title"] = self.instance.description or (self.instance.category.name if self.instance.category else "")
        if self.instance and self.instance.pk and self.instance.category_id:
            self.fields["category"].queryset = ExpenseCategory.objects.filter(
                Q(is_active=True) | Q(pk=self.instance.category_id)
            ).order_by("name")
        else:
            self.fields["category"].queryset = ExpenseCategory.objects.filter(is_active=True).order_by("name")

    field_order = [
        "title",
        "category",
        "new_category",
        "amount",
        "expense_date",
        "payment_method",
        "paid_from_drawer",
        "receipt_image",
        "notes",
    ]

    class Meta:
        model = Expense
        fields = [
            "title",
            "category",
            "amount",
            "expense_date",
            "payment_method",
            "paid_from_drawer",
            "receipt_image",
            "notes",
        ]
        widgets = {
            "expense_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_title(self):
        title = self.cleaned_data.get("title", "").strip()
        if not title:
            raise forms.ValidationError("Title is required.")
        return title

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is None or amount <= 0:
            raise forms.ValidationError("Amount must be greater than 0.")
        return amount

    def clean_receipt_image(self):
        image = self.cleaned_data.get("receipt_image")
        if not image or not isinstance(image, UploadedFile):
            return image

        name = getattr(image, "name", "")
        ext = os.path.splitext(name)[1].lower()
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            raise forms.ValidationError(
                f"Unsupported file extension '{ext}'. Allowed extensions are: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}."
            )

        if image.size > MAX_IMAGE_SIZE:
            raise forms.ValidationError("Image files must be 3 MB or smaller.")

        try:
            image.file.seek(0)
            with Image.open(image.file) as opened_image:
                opened_image.verify()
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError):
            raise forms.ValidationError("Upload a valid image file.")
        finally:
            image.file.seek(0)
        return image

    def clean(self):
        cleaned_data = super().clean()
        category = cleaned_data.get("category")
        new_category = cleaned_data.get("new_category", "").strip()

        if not category and not new_category:
            self.add_error("category", "Category is required.")
        elif not category and new_category:
            cat_obj, _ = ExpenseCategory.objects.get_or_create(
                name=new_category,
                defaults={"is_active": True},
            )
            cleaned_data["category"] = cat_obj

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        uploaded_image = self.files.get("receipt_image")
        if uploaded_image and uploaded_image in self.cleaned_data.values():
            uploaded_image.file.seek(0)
            with Image.open(uploaded_image.file) as opened_image:
                image_format = opened_image.format or "PNG"
                processed_image = ImageOps.exif_transpose(opened_image)
                processed_image.thumbnail(
                    (MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION),
                    Image.Resampling.LANCZOS,
                )
                if image_format == "JPEG" and processed_image.mode not in {"RGB", "L"}:
                    processed_image = processed_image.convert("RGB")

                output = BytesIO()
                save_options = {"format": image_format}
                if image_format == "JPEG":
                    save_options.update(quality=90, optimize=True)
                processed_image.save(output, **save_options)

            instance.receipt_image.save(
                uploaded_image.name,
                ContentFile(output.getvalue()),
                save=False,
            )

        if commit:
            instance.save()
        return instance


class RecurringExpenseForm(forms.ModelForm):
    category = forms.ModelChoiceField(
        queryset=ExpenseCategory.objects.filter(is_active=True).order_by("name"),
        label="Category",
    )

    class Meta:
        model = RecurringExpense
        fields = ["title", "category", "amount", "method", "frequency", "next_due", "is_active"]
        widgets = {
            "next_due": forms.DateInput(attrs={"type": "date"}),
        }

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is None or amount <= 0:
            raise forms.ValidationError("Amount must be greater than 0.")
        return amount


class ShiftOpenForm(forms.ModelForm):
    opening_cash = forms.DecimalField(
        min_value=Decimal("0.00"),
        max_digits=10,
        decimal_places=2,
        initial=Decimal("0.00"),
        label="Opening Cash in Drawer",
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )

    class Meta:
        model = Shift
        fields = ["opening_cash"]

    def clean(self):
        cleaned_data = super().clean()
        if Shift.objects.filter(status=Shift.STATUS_OPEN).exists():
            raise forms.ValidationError("A shift is already open. Double-open is blocked.")
        return cleaned_data


class ShiftCloseForm(forms.Form):
    counted_cash = forms.DecimalField(
        min_value=Decimal("0.00"),
        max_digits=10,
        decimal_places=2,
        label="Counted Cash in Drawer",
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00", "id": "countedCashInput"}),
    )
    note = forms.CharField(
        required=False,
        label="Closing Notes / Variance explanation",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Optional explanation or note..."}),
    )


class BusinessSettingsForm(forms.ModelForm):
    gst_percent = forms.DecimalField(
        min_value=Decimal("0.00"),
        max_digits=5,
        decimal_places=2,
        required=False,
        initial=Decimal("0.00"),
        label="GST Percentage (%)",
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    low_stock_threshold = forms.IntegerField(
        min_value=0,
        initial=5,
        label="Low Stock Threshold",
        help_text="Items with stock at or below this threshold are flagged as low stock.",
        widget=forms.NumberInput(attrs={"min": 0, "placeholder": "5"}),
    )
    default_receipt_mode = forms.ChoiceField(
        choices=BusinessSettings.RECEIPT_MODE_CHOICES,
        required=False,
        initial=BusinessSettings.RECEIPT_MODE_A4,
        label="Default Receipt Mode",
    )
    printer_type = forms.ChoiceField(
        choices=BusinessSettings.PRINTER_TYPE_CHOICES,
        required=False,
        initial=BusinessSettings.PRINTER_TYPE_USB,
        label="Printer Connection Type",
    )
    printer_paper_width = forms.ChoiceField(
        choices=BusinessSettings.PRINTER_WIDTH_CHOICES,
        required=False,
        initial=BusinessSettings.PRINTER_WIDTH_80,
        label="Paper Width",
    )
    backup_directory = forms.CharField(
        max_length=255,
        required=False,
        label="Backup Directory",
        help_text="Custom directory for database backups (e.g. D:\\backups or USB drive path). Leave blank for default 'backups/'.",
        widget=forms.TextInput(attrs={"placeholder": "e.g. D:\\backups or /media/usb/backups"}),
    )

    def clean_default_receipt_mode(self):
        return self.cleaned_data.get("default_receipt_mode") or (self.instance.default_receipt_mode if self.instance else BusinessSettings.RECEIPT_MODE_A4)

    def clean_printer_type(self):
        return self.cleaned_data.get("printer_type") or (self.instance.printer_type if self.instance else BusinessSettings.PRINTER_TYPE_USB)

    def clean_printer_paper_width(self):
        return self.cleaned_data.get("printer_paper_width") or (self.instance.printer_paper_width if self.instance else BusinessSettings.PRINTER_WIDTH_80)

    class Meta:
        model = BusinessSettings
        fields = [
            "business_name",
            "address",
            "phone",
            "email",
            "tax_number",
            "fssai_number",
            "currency_symbol",
            "invoice_footer",
            "default_receipt_mode",
            "gst_enabled",
            "gst_percent",
            "prices_include_gst",
            "show_cashier",
            "low_stock_threshold",
            "backup_directory",
            "printer_enabled",
            "printer_type",
            "printer_address",
            "printer_paper_width",
            "printer_cut",
            "printer_drawer_kick",
            "printer_counter_slip",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "gst_enabled": forms.CheckboxInput(attrs={"style": "width:20px;height:20px;cursor:pointer"}),
            "prices_include_gst": forms.CheckboxInput(attrs={"style": "width:20px;height:20px;cursor:pointer"}),
            "show_cashier": forms.CheckboxInput(attrs={"style": "width:20px;height:20px;cursor:pointer"}),
            "printer_enabled": forms.CheckboxInput(attrs={"style": "width:20px;height:20px;cursor:pointer"}),
            "printer_cut": forms.CheckboxInput(attrs={"style": "width:20px;height:20px;cursor:pointer"}),
            "printer_drawer_kick": forms.CheckboxInput(attrs={"style": "width:20px;height:20px;cursor:pointer"}),
            "printer_counter_slip": forms.CheckboxInput(attrs={"style": "width:20px;height:20px;cursor:pointer"}),
            "printer_address": forms.TextInput(attrs={"placeholder": "e.g. 04b8:0e15 or 192.168.1.100:9100 or COM3"}),
        }

    def clean_gst_percent(self):
        val = self.cleaned_data.get("gst_percent")
        if val is None:
            return Decimal("0.00")
        if val < Decimal("0.00"):
            raise forms.ValidationError("GST percentage cannot be negative.")
        return val


class StaffCreateForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        label="Username",
        widget=forms.TextInput(attrs={"placeholder": "e.g. cashier_john"}),
    )
    first_name = forms.CharField(max_length=150, required=False, label="First Name")
    last_name = forms.CharField(max_length=150, required=False, label="Last Name")
    role = forms.ChoiceField(
        choices=[("Owner", "Owner"), ("Cashier", "Cashier")],
        initial="Cashier",
        label="Role",
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "••••••••"}),
        label="Password",
        help_text="Password for standard login.",
    )
    pin = forms.CharField(
        max_length=4,
        required=False,
        widget=forms.PasswordInput(attrs={"maxlength": 4, "pattern": "\\d{4}", "inputmode": "numeric", "placeholder": "4 digits"}),
        label="Quick PIN (Optional)",
        help_text="Optional 4-digit numeric PIN for quick login.",
    )
    is_active = forms.BooleanField(
        required=False,
        initial=True,
        label="Active account",
        widget=forms.CheckboxInput(attrs={"style": "width:20px;height:20px;cursor:pointer"}),
    )

    def clean_username(self):
        username = self.cleaned_data.get("username", "").strip()
        if not username:
            raise forms.ValidationError("Username is required.")
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("A user with this username already exists.")
        return username

    def clean_pin(self):
        pin = self.cleaned_data.get("pin", "").strip()
        if pin:
            if not pin.isdigit() or len(pin) != 4:
                raise forms.ValidationError("PIN must be exactly 4 numeric digits.")
        return pin

    def save_new_user(self):
        username = self.cleaned_data["username"]
        first_name = self.cleaned_data.get("first_name", "").strip()
        last_name = self.cleaned_data.get("last_name", "").strip()
        password = self.cleaned_data["password"]
        role = self.cleaned_data["role"]
        pin = self.cleaned_data.get("pin", "").strip()
        is_active = self.cleaned_data.get("is_active", True)

        user = User.objects.create_user(
            username=username,
            password=password,
            first_name=first_name,
            last_name=last_name,
            is_active=is_active,
        )
        group, _ = Group.objects.get_or_create(name=role)
        user.groups.add(group)

        profile, _ = StaffProfile.objects.get_or_create(user=user)
        if pin:
            profile.set_pin(pin)
            profile.save()

        return user


class StaffEditForm(forms.Form):
    first_name = forms.CharField(max_length=150, required=False, label="First Name")
    last_name = forms.CharField(max_length=150, required=False, label="Last Name")
    role = forms.ChoiceField(
        choices=[("Owner", "Owner"), ("Cashier", "Cashier")],
        label="Role",
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "Leave blank to keep current"}),
        required=False,
        label="New Password",
        help_text="Leave blank to keep existing password.",
    )
    pin = forms.CharField(
        max_length=4,
        required=False,
        widget=forms.PasswordInput(attrs={"maxlength": 4, "pattern": "\\d{4}", "inputmode": "numeric", "placeholder": "New 4-digit PIN"}),
        label="New PIN",
        help_text="Leave blank to keep existing PIN.",
    )
    clear_pin = forms.BooleanField(
        required=False,
        label="Clear existing PIN",
        widget=forms.CheckboxInput(attrs={"style": "width:20px;height:20px;cursor:pointer"}),
    )
    is_active = forms.BooleanField(
        required=False,
        label="Active account",
        widget=forms.CheckboxInput(attrs={"style": "width:20px;height:20px;cursor:pointer"}),
    )

    @property
    def current_role(self):
        if self.instance_user:
            if self.instance_user.groups.filter(name="Owner").exists() or self.instance_user.is_superuser:
                return "Owner"
            return "Cashier"
        return "No role"

    def __init__(self, *args, instance_user=None, **kwargs):
        self.instance_user = instance_user
        if instance_user:
            initial = kwargs.get("initial", {})
            initial["first_name"] = instance_user.first_name
            initial["last_name"] = instance_user.last_name
            initial["is_active"] = instance_user.is_active
            if instance_user.groups.filter(name="Owner").exists() or instance_user.is_superuser:
                initial["role"] = "Owner"
            else:
                initial["role"] = "Cashier"
            kwargs["initial"] = initial
        super().__init__(*args, **kwargs)

    def clean_pin(self):
        pin = self.cleaned_data.get("pin", "").strip()
        if pin:
            if not pin.isdigit() or len(pin) != 4:
                raise forms.ValidationError("PIN must be exactly 4 numeric digits.")
        return pin

    def clean(self):
        cleaned_data = super().clean()
        user = self.instance_user
        if not user:
            return cleaned_data

        role = cleaned_data.get("role")
        is_active = cleaned_data.get("is_active", True)

        # Check last-owner protection
        is_current_owner = (user.groups.filter(name="Owner").exists() or user.is_superuser) and user.is_active
        if is_current_owner:
            active_owners_count = User.objects.filter(
                Q(groups__name="Owner") | Q(is_superuser=True),
                is_active=True,
            ).distinct().count()
            if active_owners_count <= 1:
                if not is_active:
                    raise forms.ValidationError("Cannot disable the last remaining Owner.")
                if role != "Owner":
                    raise forms.ValidationError("Cannot demote the last remaining Owner.")

        return cleaned_data

    def save_user(self):
        user = self.instance_user
        user.first_name = self.cleaned_data.get("first_name", "").strip()
        user.last_name = self.cleaned_data.get("last_name", "").strip()
        user.is_active = self.cleaned_data.get("is_active", True)

        password_changed = False
        new_password = self.cleaned_data.get("password")
        if new_password:
            user.set_password(new_password)
            password_changed = True

        role = self.cleaned_data.get("role")
        owner_group, _ = Group.objects.get_or_create(name="Owner")
        cashier_group, _ = Group.objects.get_or_create(name="Cashier")
        if role == "Owner":
            user.groups.remove(cashier_group)
            user.groups.add(owner_group)
        else:
            user.groups.remove(owner_group)
            user.groups.add(cashier_group)

        user.save()

        pin_changed = False
        profile, _ = StaffProfile.objects.get_or_create(user=user)
        clear_pin = self.cleaned_data.get("clear_pin")
        new_pin = self.cleaned_data.get("pin", "").strip()

        if clear_pin:
            profile.set_pin("")
            profile.save()
            pin_changed = True
        elif new_pin:
            profile.set_pin(new_pin)
            profile.save()
            pin_changed = True

        return {
            "role": role,
            "password_changed": password_changed,
            "pin_changed": pin_changed,
        }


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "phone", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Metro Dairy, City Gas"}),
            "phone": forms.TextInput(attrs={"placeholder": "Contact phone"}),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Payment terms, address, notes..."}),
        }

    def clean_name(self):
        name = self.cleaned_data.get("name", "").strip()
        if not name:
            raise forms.ValidationError("Supplier name is required.")
        return name


class StockItemForm(forms.ModelForm):
    current_qty = forms.DecimalField(
        min_value=Decimal("0.00"),
        max_digits=10,
        decimal_places=2,
        initial=Decimal("0.00"),
        label="Current Quantity",
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    reorder_level = forms.DecimalField(
        min_value=Decimal("0.00"),
        max_digits=10,
        decimal_places=2,
        initial=Decimal("0.00"),
        label="Reorder Alert Level",
        help_text="Items at or below this level trigger a low-stock alert.",
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    cost_per_unit = forms.DecimalField(
        min_value=Decimal("0.00"),
        max_digits=10,
        decimal_places=2,
        initial=Decimal("0.00"),
        label="Cost Per Unit (₹)",
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )

    class Meta:
        model = StockItem
        fields = ["name", "unit", "current_qty", "reorder_level", "cost_per_unit"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Milk, Tea Powder, Sugar, Gas Cylinder"}),
            "unit": forms.TextInput(attrs={"placeholder": "e.g. L, kg, pcs, cylinder"}),
        }

    def clean_name(self):
        name = self.cleaned_data.get("name", "").strip()
        if not name:
            raise forms.ValidationError("Stock item name is required.")
        return name

    def clean_unit(self):
        unit = self.cleaned_data.get("unit", "").strip()
        if not unit:
            raise forms.ValidationError("Unit is required (e.g. L, kg, cylinder).")
        return unit


class PurchaseForm(forms.ModelForm):
    paid_amount = forms.DecimalField(
        min_value=Decimal("0.00"),
        max_digits=10,
        decimal_places=2,
        initial=Decimal("0.00"),
        label="Amount Paid Now",
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00", "id": "paidAmountInput"}),
    )
    post_to_drawer = forms.BooleanField(
        required=False,
        label="Post to Cash Drawer expense (deduct cash from current shift)",
        help_text="Only applicable when paid in Cash and an active shift is open.",
        widget=forms.CheckboxInput(attrs={"style": "width:20px;height:20px;cursor:pointer", "id": "postToDrawerCheckbox"}),
    )

    class Meta:
        model = Purchase
        fields = ["supplier", "date", "method", "paid_amount", "note"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "note": forms.Textarea(attrs={"rows": 2, "placeholder": "Invoice #, bill memo, delivery notes..."}),
        }


class PurchaseLineForm(forms.ModelForm):
    qty = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_digits=10,
        decimal_places=2,
        label="Quantity",
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "Quantity"}),
    )
    unit_cost = forms.DecimalField(
        min_value=Decimal("0.00"),
        max_digits=10,
        decimal_places=2,
        label="Unit Cost",
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "Unit Cost"}),
    )

    class Meta:
        model = PurchaseLine
        fields = ["stock_item", "qty", "unit_cost"]

    def clean_qty(self):
        qty = self.cleaned_data.get("qty")
        if qty is None or qty <= Decimal("0"):
            raise forms.ValidationError("Quantity must be greater than zero.")
        return qty

    def clean_unit_cost(self):
        cost = self.cleaned_data.get("unit_cost")
        if cost is None or cost < Decimal("0"):
            raise forms.ValidationError("Unit cost cannot be negative.")
        return cost


class WastageForm(forms.ModelForm):
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(is_active=True).order_by("name"),
        required=False,
        empty_label="-- Select Menu Item --",
    )
    stock_item = forms.ModelChoiceField(
        queryset=StockItem.objects.all().order_by("name"),
        required=False,
        empty_label="-- Select Raw Material --",
    )
    quantity = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=True,
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "e.g. 2.50"}),
    )
    reason = forms.ChoiceField(
        choices=Wastage.REASON_CHOICES,
        required=True,
    )
    note = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Optional note (e.g. dropped, spoiled, bad batch)"}),
    )

    class Meta:
        model = Wastage
        fields = ["product", "stock_item", "quantity", "reason", "note"]

    def __init__(self, *args, is_owner=False, **kwargs):
        self.is_owner = is_owner
        super().__init__(*args, **kwargs)
        if not self.is_owner:
            self.fields["reason"].choices = [
                (k, v) for k, v in Wastage.REASON_CHOICES if k != Wastage.REASON_CORRECTION
            ]

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get("product")
        stock_item = cleaned_data.get("stock_item")
        quantity = cleaned_data.get("quantity")
        reason = cleaned_data.get("reason")

        if not product and not stock_item:
            raise forms.ValidationError("Please select either a menu item or a raw material.")
        if product and stock_item:
            raise forms.ValidationError("Please select only one: either a menu item or a raw material, not both.")

        if not self.is_owner:
            if reason == Wastage.REASON_CORRECTION:
                raise forms.ValidationError("Only owners can log correction entries.")
            if quantity is not None and quantity <= Decimal("0"):
                raise forms.ValidationError("Quantity must be greater than zero.")
        else:
            if quantity is not None and quantity == Decimal("0"):
                raise forms.ValidationError("Quantity cannot be zero.")

        return cleaned_data
