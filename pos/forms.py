from decimal import Decimal
import re

from django import forms

from .models import BusinessSettings, Customer, Expense, Product


class MenuItemForm(forms.ModelForm):
    price = forms.DecimalField(
        min_value=Decimal("0"),
        max_digits=10,
        decimal_places=2,
        error_messages={"min_value": "Price must be greater than or equal to zero."},
    )

    class Meta:
        model = Product
        fields = ["name", "category", "shortcut_key", "price", "cost_price", "description", "image", "is_active"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "is_active": forms.CheckboxInput(),
        }

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        return name

    def clean_shortcut_key(self):
        shortcut = self.cleaned_data.get("shortcut_key", "").strip().upper()
        if shortcut and not re.fullmatch(r"[A-Z0-9]+", shortcut):
            raise forms.ValidationError("Use only letters and numbers, such as T, 10, or A1.")
        if shortcut and self.cleaned_data.get("is_active", True):
            duplicate = Product.objects.filter(is_active=True, shortcut_key__iexact=shortcut)
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise forms.ValidationError(
                    f"Shortcut key {shortcut} is already assigned to {duplicate.first().name}."
                )
        return shortcut

    def clean_category(self):
        category = self.cleaned_data["category"].strip()
        return category

    def clean_image(self):
        image = self.cleaned_data.get("image")
        content_type = getattr(image, "content_type", None)
        if content_type and content_type.split("/")[0] != "image":
            raise forms.ValidationError("Upload a valid image file.")
        return image


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