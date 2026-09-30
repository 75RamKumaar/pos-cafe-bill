from decimal import Decimal
from io import BytesIO
import os
import re

from django import forms
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import BusinessSettings, Customer, Expense, Product


MAX_IMAGE_SIZE = 3 * 1024 * 1024
MAX_IMAGE_DIMENSION = 800
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


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

    class Meta:
        model = Product
        fields = ["name", "category", "shortcut_key", "price", "cost_price", "stock", "description", "image", "is_active"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "is_active": forms.CheckboxInput(),
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
    class Meta:
        model = Customer
        fields = ["name", "phone", "email", "address", "notes"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3}), "notes": forms.Textarea(attrs={"rows": 3})}

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        return name


class ExpenseForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.instance.title:
            self.initial["title"] = self.instance.description or self.instance.category

    class Meta:
        model = Expense
        fields = ["title", "category", "amount", "expense_date", "payment_method", "notes"]
        widgets = {"expense_date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 3})}

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if not title:
            raise forms.ValidationError("Title is required.")
        return title

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("Amount must be greater than 0.")
        return amount


class BusinessSettingsForm(forms.ModelForm):
    class Meta:
        model = BusinessSettings
        fields = ["business_name", "address", "phone", "email", "tax_number", "currency_symbol", "invoice_footer"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}