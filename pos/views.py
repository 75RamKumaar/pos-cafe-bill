import csv
import io
import json
import re
import urllib.parse
from functools import wraps
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

TWO_PLACES = Decimal("0.01")
MAX_DECIMAL_LIMIT = Decimal("99999999.99")


def parse_decimal(value, min_value=None, max_value=None):
    if value is None or value == "":
        raise ValueError("Value is required.")
    try:
        val_str = str(value).strip()
        d = Decimal(val_str)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid decimal value: {value}") from exc

    if not d.is_finite():
        raise ValueError(f"Non-finite decimal value is not allowed: {value}")

    range_limit = max_value if max_value is not None else MAX_DECIMAL_LIMIT
    if abs(d) > range_limit:
        raise ValueError(f"Value {value} out of range.")

    if min_value is not None and d < min_value:
        raise ValueError(f"Value cannot be less than {min_value}.")
    if max_value is not None and d > max_value:
        raise ValueError(f"Value cannot exceed {max_value}.")

    return d.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Min, Q, Sum
from django.db.models.functions import TruncDate
from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseNotAllowed, JsonResponse
from django.contrib.auth import authenticate
from django.conf import settings as django_settings
from django.core.management import call_command
from django.contrib.auth.models import Group, User
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.db.models.deletion import ProtectedError

def parse_date_param(request, param_name="date"):
    date_str = request.GET.get(param_name, "").strip()
    if not date_str:
        return None
    try:
        parsed = parse_date(date_str)
        if parsed is None:
            messages.error(request, "Enter a valid date")
            return None
        return parsed
    except (ValueError, TypeError):
        messages.error(request, "Enter a valid date")
        return None

from .forms import (
    BusinessSettingsForm,
    CustomerForm,
    ExpenseCategoryForm,
    ExpenseForm,
    MenuItemForm,
    ModifierForm,
    PurchaseForm,
    PurchaseLineForm,
    RecurringExpenseForm,
    ShiftOpenForm,
    ShiftCloseForm,
    StaffCreateForm,
    StaffEditForm,
    StockItemForm,
    SupplierForm,
    WastageForm,
)
from .models import (
    Bill,
    BillItem,
    BillPayment,
    Customer,
    Expense,
    ExpenseCategory,
    KhataTransaction,
    AuditLog,
    Modifier,
    Product,
    Purchase,
    PurchaseLine,
    RecipeLine,
    BusinessSettings,
    HeldOrder,
    RecurringExpense,
    Shift,
    StaffProfile,
    StockItem,
    Supplier,
    Wastage,
    process_recurring_expenses,
)
from .printing import (
    build_receipt_bytes,
    build_counter_slip_bytes,
    build_test_receipt_bytes,
    print_raw_escpos_bytes,
)


def role_required(*group_names):
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.user.is_superuser or request.user.groups.filter(
                name__in=group_names
            ).exists():
                return view(request, *args, **kwargs)
            return render(
                request,
                "403.html",
                {
                    "required_roles": group_names,
                },
                status=403,
            )

        return wrapped

    return decorator


@role_required("Cashier", "Owner")
def pos(request):
    return redirect("billing")


@role_required("Cashier", "Owner")
def billing(request):
    products = Product.objects.filter(is_active=True).prefetch_related("modifiers").order_by("sort_order", "name")
    categories = products.values_list("category", flat=True).distinct().order_by("category")
    favorites = list(Product.objects.filter(is_active=True, is_favorite=True).prefetch_related("modifiers").order_by("sort_order", "name"))
    if not favorites:
        thirty_days_ago = timezone.now() - timedelta(days=30)
        top_items = (
            BillItem.objects.filter(
                bill__created_at__gte=thirty_days_ago,
                bill__status=Bill.STATUS_ACTIVE,
                menu_item__is_active=True,
            )
            .values("menu_item_id")
            .annotate(total_qty=Sum("quantity"))
            .order_by("-total_qty")[:8]
        )
        fav_ids = [item["menu_item_id"] for item in top_items]
        fav_map = {p.id: p for p in Product.objects.filter(id__in=fav_ids, is_active=True).prefetch_related("modifiers")}
        favorites = [fav_map[fid] for fid in fav_ids if fid in fav_map]

    current_settings = BusinessSettings.current()
    low_stock_threshold = getattr(current_settings, "low_stock_threshold", 5)
    if low_stock_threshold is None:
        low_stock_threshold = 5

    return render(request, "pos/billing.html", {
        "products": products,
        "categories": categories,
        "favorites": favorites,
        "customers": Customer.objects.all(),
        "payment_methods": [m for m in Bill.PAYMENT_CHOICES if m[0] != Bill.SPLIT],
        "business_settings": current_settings,
        "low_stock_threshold": low_stock_threshold,
        "current_shift": Shift.current_open(),
    })


@role_required("Cashier", "Owner")
def customer_search(request):
    query = request.GET.get("q", "").strip()
    customers = Customer.objects.filter(is_active=True)
    if query:
        customers = customers.filter(Q(name__icontains=query) | Q(phone__icontains=query))
    return JsonResponse({"customers": [{"id": c.id, "name": c.name, "phone": c.phone} for c in customers[:10]]})


@role_required("Cashier", "Owner")
def bill_create(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    open_shift = Shift.current_open()
    if not open_shift:
        open_shift_url = reverse("shift_open")
        return JsonResponse(
            {
                "error": f"No shift is open. You must open a shift before creating bills: {open_shift_url}",
                "shift_required": True,
                "open_shift_url": open_shift_url,
            },
            status=400,
        )

    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            return JsonResponse({"error": "Invalid bill data."}, status=400)
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid bill data."}, status=400)

    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return JsonResponse({"error": "Add at least one item."}, status=400)

    if len(raw_items) > 100:
        return JsonResponse({"error": "Cannot exceed 100 items per bill."}, status=400)

    customer_id = data.get("customer_id")
    customer = None
    if customer_id not in (None, ""):
        try:
            customer_id_int = int(customer_id)
        except (ValueError, TypeError):
            return JsonResponse({"error": "Invalid customer ID."}, status=400)
        customer = Customer.objects.filter(pk=customer_id_int).first()
        if customer is None:
            return JsonResponse({"error": "Customer not found."}, status=400)

    raw_payments = data.get("payments")
    validated_payments = []
    payment_method = data.get("payment_method")

    if raw_payments is not None:
        if not isinstance(raw_payments, list) or len(raw_payments) == 0:
            return JsonResponse({"error": "Select at least one payment method."}, status=400)
        valid_methods = [Bill.CASH, Bill.UPI, Bill.CARD, Bill.KHATA]
        for p in raw_payments:
            if not isinstance(p, dict):
                return JsonResponse({"error": "Invalid payment details."}, status=400)
            m = p.get("method")
            if m not in valid_methods:
                return JsonResponse({"error": f"Invalid payment method: {m}"}, status=400)
            try:
                amt = parse_decimal(p.get("amount"), min_value=Decimal("0.01"))
            except ValueError:
                return JsonResponse({"error": "Invalid payment amount."}, status=400)
            validated_payments.append({"method": m, "amount": amt})
    else:
        if payment_method not in dict(Bill.PAYMENT_CHOICES) or payment_method == Bill.SPLIT:
            return JsonResponse({"error": "Select a valid payment method."}, status=400)
        if payment_method == Bill.KHATA and customer is None:
            return JsonResponse({"error": "A customer is required for Khata credit checkout."}, status=400)

    raw_discount = data.get("discount", "0")
    if raw_discount is None or raw_discount == "":
        raw_discount = "0"
    try:
        discount = parse_decimal(raw_discount, min_value=Decimal("0.00"), max_value=Decimal("99999999.99"))
    except ValueError:
        return JsonResponse({"error": "Invalid discount."}, status=400)

    parsed_items = []
    product_requested_quantities = {}
    all_requested_modifier_ids = set()

    for raw in raw_items:
        if not isinstance(raw, dict):
            return JsonResponse({"error": "Invalid item or quantity."}, status=400)
        try:
            product_id = int(raw.get("id"))
        except (KeyError, TypeError, ValueError):
            return JsonResponse({"error": "Invalid item or quantity."}, status=400)

        if product_id <= 0:
            return JsonResponse({"error": "Invalid item or quantity."}, status=400)

        raw_qty = raw.get("quantity")
        try:
            qty = parse_decimal(raw_qty, min_value=Decimal("0.01"), max_value=Decimal("9999.00"))
        except ValueError:
            return JsonResponse({"error": "Invalid item or quantity."}, status=400)

        if qty <= Decimal("0.00") or qty > Decimal("9999.00"):
            return JsonResponse({"error": "Quantities must be positive."}, status=400)

        raw_mods = raw.get("modifier_ids")
        if raw_mods is None:
            raw_mods = raw.get("modifiers", [])
        if not isinstance(raw_mods, list):
            return JsonResponse({"error": "Invalid modifier data."}, status=400)

        item_mod_ids = []
        for m in raw_mods:
            m_id = m.get("id") if isinstance(m, dict) else m
            try:
                m_int = int(m_id)
                if m_int <= 0:
                    return JsonResponse({"error": "Invalid modifier ID."}, status=400)
                item_mod_ids.append(m_int)
            except (ValueError, TypeError):
                return JsonResponse({"error": "Invalid modifier ID."}, status=400)

        item_mod_ids = sorted(list(set(item_mod_ids)))
        for m_int in item_mod_ids:
            all_requested_modifier_ids.add(m_int)

        item_note = str(raw.get("note", "") or "").strip()[:255]

        parsed_items.append({
            "product_id": product_id,
            "quantity": qty,
            "modifier_ids": item_mod_ids,
            "note": item_note,
        })

        product_requested_quantities[product_id] = (
            product_requested_quantities.get(product_id, Decimal("0.00")) + qty
        )
        if product_requested_quantities[product_id] > Decimal("9999.00"):
            return JsonResponse({"error": "Quantity cannot exceed 9999."}, status=400)

    # Merge items with identical (product_id, modifier_ids, note)
    merged_lines = {}
    for itm in parsed_items:
        key = (itm["product_id"], tuple(itm["modifier_ids"]), itm["note"])
        if key in merged_lines:
            merged_lines[key]["quantity"] += itm["quantity"]
            if merged_lines[key]["quantity"] > Decimal("9999.00"):
                return JsonResponse({"error": "Quantity cannot exceed 9999."}, status=400)
        else:
            merged_lines[key] = {
                "product_id": itm["product_id"],
                "quantity": itm["quantity"],
                "modifier_ids": itm["modifier_ids"],
                "note": itm["note"],
            }

    if len(merged_lines) > 100:
        return JsonResponse({"error": "Cannot exceed 100 items per bill."}, status=400)

    sorted_product_ids = sorted(product_requested_quantities.keys())
    with transaction.atomic():
        products_query = Product.objects.select_for_update().prefetch_related("modifiers").filter(
            pk__in=sorted_product_ids,
            is_active=True,
        )
        products_by_id = {p.pk: p for p in products_query}

        for p_id in sorted_product_ids:
            if p_id not in products_by_id:
                return JsonResponse({"error": "An item is unavailable and was not added."}, status=400)

        for p_id in sorted_product_ids:
            product = products_by_id[p_id]
            req_qty = product_requested_quantities[p_id]
            if product.stock_tracked and product.stock < req_qty:
                return JsonResponse(
                    {
                        "error": f"Insufficient stock for {product.name}. Available: {product.stock}"
                    },
                    status=400,
                )

        # Validate modifiers
        if all_requested_modifier_ids:
            active_modifiers = {
                m.id: m for m in Modifier.objects.filter(id__in=all_requested_modifier_ids, is_active=True)
            }
            for m_id in all_requested_modifier_ids:
                if m_id not in active_modifiers:
                    return JsonResponse({"error": f"Invalid modifier ID: {m_id}"}, status=400)

            for line in merged_lines.values():
                product = products_by_id[line["product_id"]]
                product_mod_ids = set(product.modifiers.filter(is_active=True).values_list("id", flat=True))
                for m_id in line["modifier_ids"]:
                    if m_id not in product_mod_ids:
                        return JsonResponse({"error": f"Invalid modifier ID: {m_id} for {product.name}."}, status=400)
        else:
            active_modifiers = {}

        validated_items = []
        subtotal = Decimal("0.00")
        for line in merged_lines.values():
            product = products_by_id[line["product_id"]]
            quantity = line["quantity"]
            item_modifiers = [active_modifiers[m_id] for m_id in line["modifier_ids"]]
            price_delta_sum = sum((m.price_delta for m in item_modifiers), Decimal("0.00"))
            unit_price = (product.price + price_delta_sum).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            line_total = (unit_price * quantity).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            subtotal += line_total
            validated_items.append({
                "product": product,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": line_total,
                "modifier_names": [m.name for m in item_modifiers],
                "note": line["note"],
            })

        if discount > subtotal:
            return JsonResponse({"error": "Discount cannot exceed the subtotal."}, status=400)

        business_settings = BusinessSettings.current()
        gst_percent = Decimal("0.00")
        tax_amount = Decimal("0.00")

        if business_settings.gst_enabled and business_settings.gst_percent and business_settings.gst_percent > 0:
            gst_percent = Decimal(business_settings.gst_percent).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            net_amount = subtotal - discount
            if business_settings.prices_include_gst:
                grand_total = net_amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
                taxable_val = (grand_total / (Decimal("1") + (gst_percent / Decimal("100")))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
                tax_amount = (grand_total - taxable_val).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            else:
                taxable_val = net_amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
                tax_amount = (taxable_val * (gst_percent / Decimal("100"))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
                grand_total = (taxable_val + tax_amount).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        else:
            grand_total = (subtotal - discount).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        if raw_payments is not None:
            payment_sum = sum((p["amount"] for p in validated_payments), Decimal("0.00"))
            if payment_sum != grand_total:
                return JsonResponse(
                    {
                        "error": f"Payment total (₹{payment_sum:.2f}) does not match grand total (₹{grand_total:.2f})."
                    },
                    status=400,
                )
            khata_part = sum((p["amount"] for p in validated_payments if p["method"] == Bill.KHATA), Decimal("0.00"))
            if khata_part > Decimal("0.00") and customer is None:
                return JsonResponse({"error": "A customer is required for Khata credit checkout."}, status=400)

            if len(validated_payments) > 1 or payment_method == Bill.SPLIT:
                bill_payment_method = Bill.SPLIT
            else:
                bill_payment_method = validated_payments[0]["method"]
        else:
            bill_payment_method = payment_method
            validated_payments = [{"method": payment_method, "amount": grand_total}]

        khata_amount = sum((p["amount"] for p in validated_payments if p["method"] == Bill.KHATA), Decimal("0.00"))
        credit_limit_overridden = False
        if khata_amount > Decimal("0.00"):
            if customer is None:
                return JsonResponse({"error": "A customer is required for Khata credit checkout."}, status=400)
            if customer.credit_limit is not None:
                current_outstanding = customer.balance
                new_outstanding = current_outstanding + khata_amount
                if new_outstanding > customer.credit_limit:
                    is_owner = request.user.is_superuser or request.user.groups.filter(name="Owner").exists()
                    has_override = bool(
                        data.get("override_credit_limit")
                        or data.get("credit_limit_override")
                        or data.get("owner_override")
                    )
                    if not (is_owner and has_override):
                        return JsonResponse(
                            {
                                "error": f"Credit limit of ₹{customer.credit_limit:.2f} exceeded for {customer.name}. Outstanding: ₹{current_outstanding:.2f}, sale: ₹{khata_amount:.2f}, new total: ₹{new_outstanding:.2f}. Owner authorization required.",
                                "credit_limit_exceeded": True,
                            },
                            status=400,
                        )
                    credit_limit_overridden = True

        cash_amount = sum((p["amount"] for p in validated_payments if p["method"] == Bill.CASH), Decimal("0.00"))
        tendered = None
        change_due = None
        raw_tendered = data.get("tendered")
        if cash_amount > Decimal("0.00") and raw_tendered not in (None, ""):
            try:
                tendered = parse_decimal(raw_tendered, min_value=Decimal("0.00"))
            except ValueError:
                return JsonResponse({"error": "Invalid tendered amount."}, status=400)
            if tendered < cash_amount:
                return JsonResponse(
                    {
                        "error": f"Tendered amount (₹{tendered:.2f}) cannot be less than grand total (₹{cash_amount:.2f})."
                    },
                    status=400,
                )
            change_due = (tendered - cash_amount).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        today_prefix = f"{timezone.localdate():%Y%m%d}"
        prefix_pattern = f"{today_prefix}-"
        bills_today = Bill.objects.filter(
            bill_number__startswith=prefix_pattern
        ).values_list("bill_number", flat=True)
        max_seq = 0
        for b_num in bills_today:
            parts = b_num.split("-")
            if len(parts) == 2 and parts[1].isdigit():
                max_seq = max(max_seq, int(parts[1]))
        next_seq = max_seq + 1
        bill_number = f"{today_prefix}-{next_seq:04d}"

        bill_note = str(data.get("note", "") or "").strip()
        bill = Bill.objects.create(
            shift=open_shift,
            bill_number=bill_number,
            customer=customer,
            created_by=request.user if request.user.is_authenticated else None,
            subtotal=subtotal,
            discount=discount,
            grand_total=grand_total,
            tax_amount=tax_amount,
            gst_percent=gst_percent,
            payment_method=bill_payment_method,
            tendered=tendered,
            change_due=change_due,
            note=bill_note,
        )

        for p in validated_payments:
            BillPayment.objects.create(
                bill=bill,
                method=p["method"],
                amount=p["amount"],
            )

        for itm in validated_items:
            product = itm["product"]
            BillItem.objects.create(
                bill=bill,
                menu_item=product,
                item_name=product.name,
                shortcut_key=product.shortcut_key,
                price=itm["unit_price"],
                cost_price=product.cost_price,
                quantity=itm["quantity"],
                total=itm["line_total"],
                note=itm["note"],
                modifier_names=itm["modifier_names"],
            )

        for p_id, total_qty in product_requested_quantities.items():
            product = products_by_id[p_id]
            if product.stock_tracked:
                Product.objects.filter(pk=p_id).update(stock=F("stock") - total_qty)

        # Deduct ingredient stock based on product recipes
        ingredient_deductions = defaultdict(Decimal)
        for itm in validated_items:
            product = itm["product"]
            item_qty = itm["quantity"]
            for rline in product.recipe_lines.all():
                ingredient_deductions[rline.stock_item_id] += rline.qty_per_unit * item_qty

        if ingredient_deductions:
            sorted_stock_item_ids = sorted(ingredient_deductions.keys())
            locked_stock_items = {
                si.id: si
                for si in StockItem.objects.select_for_update().filter(id__in=sorted_stock_item_ids)
            }
            for sid in sorted_stock_item_ids:
                si = locked_stock_items.get(sid)
                if si:
                    si.current_qty = (si.current_qty - ingredient_deductions[sid]).quantize(TWO_PLACES)
                    si.save(update_fields=["current_qty"])

        khata_total = sum((p["amount"] for p in validated_payments if p["method"] == Bill.KHATA), Decimal("0.00"))
        if khata_total > Decimal("0.00") and customer is not None:
            KhataTransaction.objects.create(
                customer=customer,
                bill=bill,
                kind=KhataTransaction.CREDIT,
                amount=khata_total,
                note=f"Credit sale: {bill.bill_number}",
            )
            if credit_limit_overridden and customer.credit_limit is not None:
                log_audit(
                    request,
                    "OVERRIDE",
                    customer,
                    {
                        "credit_limit": [str(customer.credit_limit), str(new_outstanding)],
                        "bill_number": [None, bill.bill_number],
                        "override_by": [None, request.user.username],
                    },
                )

    return JsonResponse({
        "ok": True,
        "bill_id": bill.id,
        "bill_number": bill.bill_number,
        "token_number": bill.token_number,
        "total": f"{bill.grand_total:.2f}",
        "detail_url": f"{reverse('bill_detail', kwargs={'bill_id': bill.id})}?print=1",
        "tendered": f"{bill.tendered:.2f}" if bill.tendered is not None else None,
        "change_due": f"{bill.change_due:.2f}" if bill.change_due is not None else None,
    })


@role_required("Cashier", "Owner")
def held_order_hold(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        data = json.loads(request.body) if request.body else request.POST.dict()
        if not isinstance(data, dict):
            return JsonResponse({"error": "Invalid order data."}, status=400)
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        data = request.POST.dict()

    items = data.get("items")
    if not isinstance(items, list) or not items:
        return JsonResponse({"error": "Add at least one item to hold order."}, status=400)

    customer_id = data.get("customer_id")
    customer = None
    if customer_id not in (None, ""):
        try:
            customer = Customer.objects.filter(pk=int(customer_id)).first()
        except (ValueError, TypeError):
            pass

    raw_discount = data.get("discount", "0")
    try:
        discount = parse_decimal(raw_discount, min_value=Decimal("0.00"), max_value=Decimal("99999999.99"))
    except ValueError:
        discount = Decimal("0.00")

    label = str(data.get("label", "")).strip()[:100]

    held_order = HeldOrder.objects.create(
        cashier=request.user if request.user.is_authenticated else None,
        label=label,
        items=items,
        discount=discount,
        customer=customer,
    )

    return JsonResponse({
        "ok": True,
        "id": held_order.id,
        "message": "Order held successfully.",
    })


@role_required("Cashier", "Owner")
def held_order_list(request):
    if request.method != "GET":
        return JsonResponse({"error": "GET required"}, status=405)

    cutoff = timezone.now() - timedelta(hours=24)
    HeldOrder.objects.filter(created_at__lt=cutoff).delete()

    orders = HeldOrder.objects.all().select_related("customer", "cashier").order_by("-created_at")
    orders_data = []
    for o in orders:
        total_items = Decimal("0.00")
        subtotal = Decimal("0.00")
        for itm in o.items:
            if isinstance(itm, dict):
                try:
                    qty = Decimal(str(itm.get("quantity", 1)))
                except (InvalidOperation, TypeError, ValueError):
                    qty = Decimal("1.00")
                total_items += qty
                try:
                    price = Decimal(str(itm.get("price", 0)))
                except (InvalidOperation, TypeError, ValueError):
                    price = Decimal("0.00")
                subtotal += price * qty
        grand_total = max(Decimal("0.00"), subtotal - o.discount)

        display_count = int(total_items) if total_items % 1 == 0 else float(total_items)

        orders_data.append({
            "id": o.id,
            "label": o.label or f"Order #{o.id}",
            "created_at": timezone.localtime(o.created_at).strftime("%I:%M %p"),
            "customer_id": o.customer.id if o.customer else None,
            "customer_name": o.customer.name if o.customer else None,
            "item_count": display_count or len(o.items),
            "discount": f"{o.discount:.2f}",
            "total": f"{grand_total:.2f}",
            "cashier": o.cashier.username if o.cashier else "",
            "items": o.items,
        })

    return JsonResponse({
        "ok": True,
        "orders": orders_data,
        "count": len(orders_data),
    })


@role_required("Cashier", "Owner")
def held_order_recall(request, order_id):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    order = get_object_or_404(HeldOrder, pk=order_id)

    try:
        data = json.loads(request.body) if request.body else request.POST.dict()
        if not isinstance(data, dict):
            data = {}
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        data = request.POST.dict()

    cart_has_items = bool(
        data.get("cart_has_items")
        or data.get("has_items")
        or data.get("cart_not_empty")
        or data.get("items")
        or data.get("cart_items")
        or (isinstance(data.get("cart"), (list, dict)) and len(data.get("cart")) > 0)
    )
    replace = data.get("replace") in (True, "true", "True", "1", 1)

    if cart_has_items and not replace:
        return JsonResponse(
            {"error": "Cart is not empty. Confirmation required.", "requires_confirm": True},
            status=400,
        )

    order_data = {
        "id": order.id,
        "label": order.label,
        "items": order.items,
        "discount": f"{order.discount:.2f}",
        "customer_id": order.customer_id,
        "customer_name": order.customer.name if order.customer else None,
    }
    order.delete()

    return JsonResponse({
        "ok": True,
        "order": order_data,
    })


@role_required("Cashier", "Owner")
def held_order_discard(request, order_id):
    if request.method not in ("POST", "DELETE"):
        return JsonResponse({"error": "POST required"}, status=405)

    order = get_object_or_404(HeldOrder, pk=order_id)
    order.delete()

    return JsonResponse({
        "ok": True,
        "message": "Held order discarded.",
    })


@role_required("Cashier", "Owner")
def shift_open(request):
    open_shift = Shift.current_open()
    if open_shift and request.method == "GET":
        messages.warning(request, f"Shift #{open_shift.id} is already open.")
        return redirect("day_close")

    if request.method == "POST":
        if open_shift:
            form = ShiftOpenForm(request.POST)
            form.add_error(None, "A shift is already open. Double-open is blocked.")
            messages.error(request, "A shift is already open. Double-open is blocked.")
            return render(request, "pos/shift_open.html", {"form": form}, status=400)

        form = ShiftOpenForm(request.POST)
        if form.is_valid():
            shift = form.save(commit=False)
            shift.opened_by = request.user
            shift.opened_at = timezone.now()
            shift.status = Shift.STATUS_OPEN
            shift.save()
            log_audit(
                request,
                "SHIFT_OPEN",
                shift,
                {
                    "status": [None, Shift.STATUS_OPEN],
                    "opening_cash": [None, str(shift.opening_cash)],
                },
            )
            messages.success(
                request,
                f"Shift #{shift.id} opened successfully with ₹{shift.opening_cash:.2f} opening cash.",
            )
            return redirect("billing")
        return render(request, "pos/shift_open.html", {"form": form}, status=400)

    form = ShiftOpenForm()
    return render(request, "pos/shift_open.html", {"form": form})


@role_required("Cashier", "Owner")
def day_close(request):
    shift_id = request.GET.get("shift_id")
    if shift_id:
        shift = get_object_or_404(Shift, pk=shift_id)
    else:
        shift = Shift.current_open()
        if not shift:
            shift = Shift.objects.order_by("-opened_at").first()

    if not shift:
        messages.info(request, "No shifts recorded yet. Please open a shift.")
        return redirect("shift_open")

    opening_cash = shift.opening_cash
    cash_sales = (
        BillPayment.objects.filter(
            bill__shift=shift,
            bill__status=Bill.STATUS_ACTIVE,
            method=Bill.CASH,
        ).aggregate(s=Sum("amount"))["s"]
        or Decimal("0.00")
    )

    khata_qs = KhataTransaction.objects.filter(
        kind=KhataTransaction.PAYMENT,
        created_at__gte=shift.opened_at,
    )
    if shift.closed_at:
        khata_qs = khata_qs.filter(created_at__lte=shift.closed_at)
    cash_khata = (
        khata_qs.filter(Q(method=KhataTransaction.METHOD_CASH) | Q(method=""))
        .aggregate(s=Sum("amount"))["s"]
        or Decimal("0.00")
    )

    drawer_expenses = (
        Expense.objects.filter(
            shift=shift,
            paid_from_drawer=True,
        ).aggregate(s=Sum("amount"))["s"]
        or Decimal("0.00")
    )

    expected_cash = opening_cash + cash_sales + cash_khata - drawer_expenses

    if shift.status == Shift.STATUS_OPEN:
        if request.method == "POST":
            form = ShiftCloseForm(request.POST)
            if form.is_valid():
                counted_cash = form.cleaned_data["counted_cash"]
                variance = (counted_cash - expected_cash).quantize(TWO_PLACES)
                note = form.cleaned_data["note"]

                shift.counted_cash = counted_cash
                shift.expected_cash = expected_cash
                shift.variance = variance
                shift.note = note
                shift.closed_by = request.user
                shift.closed_at = timezone.now()
                shift.status = Shift.STATUS_CLOSED
                shift.save()

                log_audit(
                    request,
                    "SHIFT_CLOSE",
                    shift,
                    {
                        "status": [Shift.STATUS_OPEN, Shift.STATUS_CLOSED],
                        "expected_cash": [None, str(expected_cash)],
                        "counted_cash": [None, str(counted_cash)],
                        "variance": [None, str(variance)],
                        "note": [None, note],
                    },
                )
                messages.success(request, f"Shift #{shift.id} closed successfully.")
                return redirect(f"{reverse('day_close')}?shift_id={shift.id}")
        else:
            form = ShiftCloseForm()

        return render(request, "pos/day_close.html", {
            "shift": shift,
            "opening_cash": opening_cash,
            "cash_sales": cash_sales,
            "cash_khata": cash_khata,
            "drawer_expenses": drawer_expenses,
            "expected_cash": expected_cash,
            "form": form,
            "is_open": True,
            "business_settings": BusinessSettings.current(),
        })
    else:
        # Shift is CLOSED - read-only / printable report
        return render(request, "pos/day_close.html", {
            "shift": shift,
            "opening_cash": shift.opening_cash,
            "cash_sales": cash_sales,
            "cash_khata": cash_khata,
            "drawer_expenses": drawer_expenses,
            "expected_cash": shift.expected_cash if shift.expected_cash is not None else expected_cash,
            "counted_cash": shift.counted_cash,
            "variance": shift.variance,
            "note": shift.note,
            "is_open": False,
            "is_owner": request.user.groups.filter(name="Owner").exists() or request.user.is_superuser,
            "business_settings": BusinessSettings.current(),
        })


@role_required("Owner")
def shift_reopen(request, shift_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    shift = get_object_or_404(Shift, pk=shift_id)

    active_shift = Shift.objects.filter(status=Shift.STATUS_OPEN).exclude(pk=shift.pk).first()
    if active_shift:
        messages.error(
            request,
            f"Cannot reopen shift #{shift.id}. Shift #{active_shift.id} is already open. Close it first.",
        )
        return redirect(f"{reverse('day_close')}?shift_id={shift.id}")

    shift.status = Shift.STATUS_OPEN
    shift.closed_by = None
    shift.closed_at = None
    shift.counted_cash = None
    shift.expected_cash = None
    shift.variance = None
    shift.save()

    log_audit(
        request,
        "SHIFT_REOPEN",
        shift,
        {
            "status": [Shift.STATUS_CLOSED, Shift.STATUS_OPEN],
            "reopened_by": [None, request.user.username],
        },
    )
    messages.success(request, f"Shift #{shift.id} has been reopened.")
    return redirect(f"{reverse('day_close')}?shift_id={shift.id}")


@role_required("Cashier", "Owner")
def billing_history(request):
    today = timezone.localdate()
    query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip().upper()
    payment_method_filter = request.GET.get("payment_method", "").strip().upper()
    cashier_filter = request.GET.get("cashier", "").strip()
    is_today_shortcut = request.GET.get("today", "").strip().lower() in ("1", "true", "yes", "today")

    raw_from = request.GET.get("from_date", "").strip()
    raw_to = request.GET.get("to_date", "").strip()
    raw_date = request.GET.get("date", "").strip()

    from_date = None
    to_date = None

    if is_today_shortcut:
        from_date = today
        to_date = today
    elif raw_date:
        if raw_date.lower() == "today":
            from_date = today
            to_date = today
        else:
            parsed = parse_date_param(request, "date")
            if parsed:
                from_date = parsed
                to_date = parsed
    else:
        if raw_from:
            from_date = parse_date(raw_from)
        if raw_to:
            to_date = parse_date(raw_to)

    bills = Bill.objects.select_related("customer", "created_by").prefetch_related("payments").order_by("-created_at", "-id")

    if query:
        bills = bills.filter(
            Q(bill_number__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(customer__phone__icontains=query)
        )

    if from_date:
        bills = bills.filter(created_at__date__gte=from_date)
    if to_date:
        bills = bills.filter(created_at__date__lte=to_date)

    if status_filter in dict(Bill.STATUS_CHOICES):
        bills = bills.filter(status=status_filter)

    if payment_method_filter in dict(Bill.PAYMENT_CHOICES):
        bills = bills.filter(
            Q(payment_method=payment_method_filter) | Q(payments__method=payment_method_filter)
        ).distinct()

    if cashier_filter:
        if cashier_filter.isdigit():
            bills = bills.filter(created_by_id=int(cashier_filter))
        else:
            bills = bills.filter(created_by__username=cashier_filter)

    # Compute Totals Strip for the filtered set (BEFORE pagination)
    total_bills = bills.count()
    active_bills = bills.filter(status=Bill.STATUS_ACTIVE)
    void_bills_count = bills.filter(status=Bill.STATUS_VOID).count()
    total_revenue = active_bills.aggregate(s=Sum("grand_total"))["s"] or Decimal("0.00")
    total_discount = active_bills.aggregate(s=Sum("discount"))["s"] or Decimal("0.00")

    payment_breakdown = BillPayment.objects.filter(
        bill__in=active_bills
    ).aggregate(
        cash=Sum("amount", filter=Q(method=Bill.CASH)),
        upi=Sum("amount", filter=Q(method=Bill.UPI)),
        card=Sum("amount", filter=Q(method=Bill.CARD)),
        khata=Sum("amount", filter=Q(method=Bill.KHATA)),
    )
    cash_sum = payment_breakdown["cash"] or Decimal("0.00")
    upi_sum = payment_breakdown["upi"] or Decimal("0.00")
    card_sum = payment_breakdown["card"] or Decimal("0.00")
    khata_sum = payment_breakdown["khata"] or Decimal("0.00")

    unpaid_active = active_bills.filter(payments__isnull=True)
    if unpaid_active.exists():
        legacy = unpaid_active.aggregate(
            cash=Sum("grand_total", filter=Q(payment_method=Bill.CASH)),
            upi=Sum("grand_total", filter=Q(payment_method=Bill.UPI)),
            card=Sum("grand_total", filter=Q(payment_method=Bill.CARD)),
            khata=Sum("grand_total", filter=Q(payment_method=Bill.KHATA)),
        )
        cash_sum += legacy["cash"] or Decimal("0.00")
        upi_sum += legacy["upi"] or Decimal("0.00")
        card_sum += legacy["card"] or Decimal("0.00")
        khata_sum += legacy["khata"] or Decimal("0.00")

    totals_strip = {
        "bills_count": total_bills,
        "active_count": total_bills - void_bills_count,
        "void_count": void_bills_count,
        "revenue": total_revenue,
        "discount": total_discount,
        "cash": cash_sum,
        "upi": upi_sum,
        "card": card_sum,
        "khata": khata_sum,
    }

    paginator = Paginator(bills, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    cashiers = User.objects.filter(
        Q(groups__name__in=["Cashier", "Owner"]) | Q(created_bills__isnull=False)
    ).distinct().order_by("first_name", "username")

    business_settings = BusinessSettings.current()

    return render(request, "pos/billing_history.html", {
        "bills": page_obj,
        "page_obj": page_obj,
        "query": query,
        "from_date": from_date.strftime("%Y-%m-%d") if from_date else "",
        "to_date": to_date.strftime("%Y-%m-%d") if to_date else "",
        "selected_date": raw_date,
        "selected_status": status_filter,
        "selected_payment_method": payment_method_filter,
        "selected_cashier": cashier_filter,
        "is_today": is_today_shortcut or (from_date == today and to_date == today and not raw_to),
        "totals": totals_strip,
        "cashiers": cashiers,
        "payment_methods": Bill.PAYMENT_CHOICES,
        "status_choices": Bill.STATUS_CHOICES,
        "business_settings": business_settings,
    })


@role_required("Cashier", "Owner")
def bill_detail(request, bill_id):
    bill = get_object_or_404(
        Bill.objects.select_related("customer", "created_by").prefetch_related("items", "payments", "khata_entries"),
        pk=bill_id,
    )
    business_settings = BusinessSettings.current()

    auto_print = request.GET.get("print") == "1"
    if auto_print:
        bill.print_count += 1
        bill.save(update_fields=["print_count"])

    is_duplicate = bill.print_count > 1

    req_mode = request.GET.get("mode")
    if not req_mode:
        mode = getattr(business_settings, "default_receipt_mode", "a4") or "a4"
    else:
        mode = req_mode.lower()

    if mode not in {"a4", "thermal", "thermal58"}:
        mode = getattr(business_settings, "default_receipt_mode", "a4") or "a4"
    if mode not in {"a4", "thermal", "thermal58"}:
        mode = "a4"

    cashier_name = None
    if getattr(business_settings, "show_cashier", True):
        if bill.created_by:
            cashier_name = bill.created_by.get_full_name() or bill.created_by.username
        else:
            cashier_name = "Cashier"

    khata_outstanding = None
    has_khata_payment = (
        bill.payment_method == Bill.KHATA
        or bill.payments.filter(method=Bill.KHATA).exists()
    )
    if bill.customer and has_khata_payment:
        khata_entry = bill.khata_entries.first()
        if khata_entry:
            tx_up_to_bill = bill.customer.khata_transactions.filter(id__lte=khata_entry.id)
        else:
            tx_up_to_bill = bill.customer.khata_transactions.filter(created_at__lte=bill.created_at)
        credits = tx_up_to_bill.filter(kind=KhataTransaction.CREDIT).aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
        payments = tx_up_to_bill.filter(kind__in=[KhataTransaction.PAYMENT, KhataTransaction.VOID]).aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
        khata_outstanding = (Decimal(bill.customer.opening_balance or 0) + credits - payments).quantize(TWO_PLACES)

    show_tax = bill.gst_percent > 0 and bill.tax_amount > 0
    is_owner = request.user.groups.filter(name="Owner").exists() or request.user.is_superuser

    return render(request, "pos/bill_detail.html", {
        "bill": bill,
        "business_settings": business_settings,
        "auto_print": auto_print,
        "receipt_mode": mode,
        "is_owner": is_owner,
        "is_duplicate": is_duplicate,
        "cashier_name": cashier_name,
        "khata_outstanding": khata_outstanding,
        "show_tax": show_tax,
    })


@role_required("Cashier", "Owner")
def print_bill_endpoint(request, bill_id):
    bill = get_object_or_404(
        Bill.objects.select_related("customer", "created_by").prefetch_related("items__menu_item", "payments"),
        pk=bill_id,
    )
    settings = BusinessSettings.current()
    fallback_url = f"{reverse('bill_detail', kwargs={'bill_id': bill.id})}?print=1"

    if not getattr(settings, "printer_enabled", False):
        return JsonResponse({
            "success": False,
            "error": "Thermal printer is disabled in settings.",
            "printer_enabled": False,
            "fallback_url": fallback_url,
        })

    is_duplicate = False
    slip_type = "receipt"
    if request.method == "POST":
        try:
            body_data = json.loads(request.body) if request.body else {}
        except Exception:
            body_data = {}
        is_duplicate = bool(body_data.get("is_duplicate", False))
        slip_type = body_data.get("slip_type", "receipt")
    else:
        is_duplicate = request.GET.get("is_duplicate") in ("1", "true", "True")
        slip_type = request.GET.get("slip_type", "receipt")

    try:
        if slip_type == "counter":
            payload = build_counter_slip_bytes(bill, settings)
        else:
            payload = build_receipt_bytes(bill, settings, is_duplicate=is_duplicate)
            if getattr(settings, "printer_counter_slip", False):
                counter_payload = build_counter_slip_bytes(bill, settings)
                payload += counter_payload

        success, err, out_bytes = print_raw_escpos_bytes(payload, settings)
        if success:
            if slip_type != "counter":
                bill.print_count += 1
                bill.save(update_fields=["print_count"])
            return JsonResponse({
                "success": True,
                "error": "",
                "message": "Receipt sent to printer successfully.",
                "print_count": bill.print_count,
            })
        else:
            return JsonResponse({
                "success": False,
                "error": err or "Failed to communicate with printer.",
                "fallback_url": fallback_url,
            })
    except Exception as exc:
        return JsonResponse({
            "success": False,
            "error": str(exc),
            "fallback_url": fallback_url,
        })


@role_required("Cashier", "Owner")
def bill_void(request, bill_id):
    if request.method != "POST":
        return redirect("billing_history")

    bill = get_object_or_404(Bill.objects.prefetch_related("items__menu_item"), pk=bill_id)

    next_url = request.POST.get("next", "").strip()
    redirect_target = (
        next_url
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()})
        else reverse("bill_detail", kwargs={"bill_id": bill.id})
    )

    if bill.status == Bill.STATUS_VOID:
        messages.error(request, "This bill has already been voided.")
        return redirect(redirect_target)

    void_reason = request.POST.get("void_reason", "").strip() or request.POST.get("reason", "").strip()
    if not void_reason:
        messages.error(request, "A reason is required to void a bill.")
        return redirect(redirect_target)

    is_owner = request.user.groups.filter(name="Owner").exists() or request.user.is_superuser
    authorizer = None
    if is_owner:
        authorizer = request.user
    else:
        owner_username = request.POST.get("owner_username", "").strip()
        owner_password = request.POST.get("owner_password", "")
        if not owner_username or not owner_password:
            messages.error(request, "Owner authorization is required. Please enter owner username and password.")
            return redirect(redirect_target)

        auth_user = authenticate(request, username=owner_username, password=owner_password)
        if auth_user is None or not (auth_user.groups.filter(name="Owner").exists() or auth_user.is_superuser):
            messages.error(request, "Invalid owner credentials. Voiding requires authorization from an owner.")
            return redirect(redirect_target)

        authorizer = auth_user

    with transaction.atomic():
        restored_count = 0
        for item in bill.items.all():
            if item.menu_item.stock_tracked:
                Product.objects.filter(pk=item.menu_item_id).update(stock=F("stock") + item.quantity)
                restored_count += 1

        # Restore ingredient stock for products with recipes
        ingredient_additions = defaultdict(Decimal)
        for item in bill.items.select_related("menu_item").prefetch_related("menu_item__recipe_lines"):
            for rline in item.menu_item.recipe_lines.all():
                ingredient_additions[rline.stock_item_id] += rline.qty_per_unit * item.quantity

        if ingredient_additions:
            sorted_add_ids = sorted(ingredient_additions.keys())
            locked_add_items = {
                si.id: si
                for si in StockItem.objects.select_for_update().filter(id__in=sorted_add_ids)
            }
            for sid in sorted_add_ids:
                si = locked_add_items.get(sid)
                if si:
                    si.current_qty = (si.current_qty + ingredient_additions[sid]).quantize(TWO_PLACES)
                    si.save(update_fields=["current_qty"])

        khata_amount = bill.payments.filter(method=Bill.KHATA).aggregate(total=Sum("amount"))["total"]
        if not khata_amount and bill.payment_method == Bill.KHATA:
            khata_amount = bill.grand_total
        is_khata = bool(khata_amount and khata_amount > Decimal("0") and bill.customer is not None)
        if is_khata:
            KhataTransaction.objects.create(
                customer=bill.customer,
                bill=bill,
                kind=KhataTransaction.VOID,
                amount=khata_amount,
                note=f"Void bill: {bill.bill_number}",
            )

        bill.status = Bill.STATUS_VOID
        bill.void_reason = void_reason
        bill.voided_at = timezone.now()
        bill.voided_by = request.user
        bill.save(update_fields=["status", "void_reason", "voided_at", "voided_by"])

        audit_details = {
            "status": [Bill.STATUS_ACTIVE, Bill.STATUS_VOID],
            "void_reason": [None, void_reason],
            "bill_number": bill.bill_number,
            "grand_total": str(bill.grand_total),
            "reason": void_reason,
            "voided_by": request.user.username,
            "authorized_by": authorizer.username,
            "restored_items": restored_count,
            "khata_credit_reversed": is_khata,
        }
        if authorizer != request.user:
            audit_details["cashier"] = request.user.username
            audit_details["owner_authorizer"] = authorizer.username

        log_audit(
            request,
            "VOID",
            bill,
            audit_details,
        )

    messages.success(request, f"Bill {bill.bill_number} has been voided.")
    return redirect(redirect_target)


@role_required("Owner")
def menu_items(request):
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    items = (
        Product.objects.all()
        .prefetch_related("modifiers")
        .annotate(sold_count=Count("billitem"))
        .order_by("sort_order", "name")
    )
    if query:
        items = items.filter(name__icontains=query)
    if category:
        items = items.filter(category=category)

    categories = (
        Product.objects.exclude(category="")
        .values_list("category", flat=True)
        .distinct()
        .order_by("category")
    )
    modifiers = Modifier.objects.all().prefetch_related("products").order_by("name")
    return render(request, "pos/menu_items.html", {
        "items": items,
        "categories": categories,
        "modifiers": modifiers,
        "query": query,
        "selected_category": category,
    })


@role_required("Owner")
def modifier_create(request):
    form = ModifierForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        modifier = form.save()
        messages.success(request, f"Modifier '{modifier.name}' added successfully.")
        return redirect("menu_items")
    return render(request, "pos/modifier_form.html", {
        "form": form,
        "page_title": "Add Modifier",
        "submit_label": "Add Modifier",
    })


@role_required("Owner")
def modifier_edit(request, modifier_id):
    modifier = get_object_or_404(Modifier, pk=modifier_id)
    form = ModifierForm(request.POST or None, instance=modifier)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Modifier '{modifier.name}' updated successfully.")
        return redirect("menu_items")
    return render(request, "pos/modifier_form.html", {
        "form": form,
        "modifier": modifier,
        "page_title": "Edit Modifier",
        "submit_label": "Save Changes",
    })


@role_required("Owner")
def modifier_delete(request, modifier_id):
    if request.method != "POST":
        return redirect("menu_items")
    modifier = get_object_or_404(Modifier, pk=modifier_id)
    name = modifier.name
    modifier.delete()
    messages.success(request, f"Modifier '{name}' deleted successfully.")
    return redirect("menu_items")


def save_product_recipe_lines(product, request):
    stock_item_ids = request.POST.getlist("recipe_stock_item")
    qtys = request.POST.getlist("recipe_qty")
    lines_to_create = []

    if stock_item_ids and qtys:
        for s_id_raw, q_raw in zip(stock_item_ids, qtys):
            if not s_id_raw or not q_raw:
                continue
            try:
                s_id = int(str(s_id_raw).strip())
                qty = Decimal(str(q_raw).strip())
                if qty > Decimal("0"):
                    lines_to_create.append((s_id, qty))
            except (ValueError, TypeError, InvalidOperation):
                continue
    elif "recipe_json" in request.POST:
        try:
            rdata = json.loads(request.POST.get("recipe_json", "[]"))
            for r in rdata:
                s_id = int(r.get("stock_item_id") or r.get("stock_item"))
                qty = Decimal(str(r.get("qty_per_unit") or r.get("qty")))
                if qty > Decimal("0"):
                    lines_to_create.append((s_id, qty))
        except (ValueError, TypeError, InvalidOperation, json.JSONDecodeError):
            pass

    has_recipe_submission = (
        "recipe_stock_item" in request.POST
        or "recipe_json" in request.POST
        or "has_recipe_form" in request.POST
    )
    if has_recipe_submission:
        with transaction.atomic():
            RecipeLine.objects.filter(product=product).delete()
            for s_id, qty in lines_to_create:
                RecipeLine.objects.create(
                    product=product,
                    stock_item_id=s_id,
                    qty_per_unit=qty,
                )
            derived = product.get_derived_cost_price()
            if derived is not None:
                Product.objects.filter(pk=product.pk).update(cost_price=derived)


@role_required("Owner")
def menu_item_create(request):
    form = MenuItemForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        item = form.save()
        save_product_recipe_lines(item, request)
        item.refresh_from_db()
        log_audit(
            request,
            "CREATE",
            item,
            {
                "name": [None, item.name],
                "price": [None, str(item.price)],
                "cost_price": [None, str(item.cost_price)],
                "is_active": [None, item.is_active],
            },
        )
        messages.success(request, "Menu item added successfully.")
        return redirect("menu_items")

    stock_items = StockItem.objects.all().order_by("name")
    return render(request, "pos/menu_item_form.html", {
        "form": form,
        "stock_items": stock_items,
        "recipe_lines": [],
        "page_title": "Add Menu Item",
        "submit_label": "Add Menu Item",
    })


@role_required("Owner")
def menu_item_edit(request, item_id):
    item = get_object_or_404(Product, pk=item_id)
    old_price = str(item.price)
    old_cost_price = str(item.cost_price)
    old_is_active = item.is_active
    old_name = item.name
    old_stock = str(item.stock)
    old_category = item.category

    form = MenuItemForm(request.POST or None, request.FILES or None, instance=item)
    if request.method == "POST" and form.is_valid():
        form.save()
        save_product_recipe_lines(item, request)
        item.refresh_from_db()

        changes = {}
        if str(item.price) != old_price:
            changes["price"] = [old_price, str(item.price)]
        if str(item.cost_price) != old_cost_price:
            changes["cost_price"] = [old_cost_price, str(item.cost_price)]
        if item.is_active != old_is_active:
            changes["is_active"] = [old_is_active, item.is_active]
        if item.name != old_name:
            changes["name"] = [old_name, item.name]
        if str(item.stock) != old_stock:
            changes["stock"] = [old_stock, str(item.stock)]
        if item.category != old_category:
            changes["category"] = [old_category, item.category]

        if changes:
            log_audit(request, "UPDATE", item, changes)

        messages.success(request, "Menu item updated successfully.")
        return redirect("menu_items")

    stock_items = StockItem.objects.all().order_by("name")
    existing_recipe_lines = item.recipe_lines.select_related("stock_item").all()
    return render(request, "pos/menu_item_form.html", {
        "form": form,
        "item": item,
        "stock_items": stock_items,
        "recipe_lines": existing_recipe_lines,
        "page_title": "Edit Menu Item",
        "submit_label": "Save Changes",
    })


@role_required("Owner")
def menu_item_delete(request, item_id):
    if request.method != "POST":
        return redirect("menu_items")
    item = get_object_or_404(Product, pk=item_id)
    if BillItem.objects.filter(menu_item=item).exists():
        err = "This item is used in existing sales and cannot be deleted. Disable it instead."
        messages.error(request, err)
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
            return JsonResponse({"error": err}, status=400)
        return redirect("menu_items")
    try:
        name = item.name
        item.delete()
    except ProtectedError:
        err = "This item is used in existing sales and cannot be deleted. Disable it instead."
        messages.error(request, err)
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
            return JsonResponse({"error": err}, status=400)
    else:
        log_audit(request, "DELETE", item, {"name": [name, None]})
        messages.success(request, "Menu item deleted successfully.")
    return redirect("menu_items")


@role_required("Owner")
def menu_item_toggle_active(request, item_id):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    item = get_object_or_404(Product, pk=item_id)
    old_active = item.is_active
    item.is_active = not item.is_active
    item.save(update_fields=["is_active"])
    log_audit(request, "UPDATE", item, {"is_active": [old_active, item.is_active]})
    status_str = "enabled" if item.is_active else "disabled"
    messages.success(request, f"Menu item '{item.name}' {status_str}.")
    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
        return JsonResponse({"ok": True, "is_active": item.is_active})
    return redirect("menu_items")


@role_required("Owner")
def menu_item_toggle_favorite(request, item_id):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    item = get_object_or_404(Product, pk=item_id)
    item.is_favorite = not item.is_favorite
    item.save(update_fields=["is_favorite"])
    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
        return JsonResponse({"ok": True, "is_favorite": item.is_favorite})
    msg = f"'{item.name}' {'marked as favorite' if item.is_favorite else 'removed from favorites'}."
    messages.success(request, msg)
    return redirect("menu_items")


@role_required("Owner")
def menu_item_reorder(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body) if request.body else request.POST.dict()
        if not isinstance(data, dict):
            data = {}
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        data = request.POST.dict()

    order = data.get("order") or request.POST.getlist("order[]") or request.POST.getlist("order")
    if order:
        if isinstance(order, str):
            order = [int(x.strip()) for x in order.split(",") if x.strip()]
        for idx, item_id in enumerate(order):
            try:
                Product.objects.filter(pk=int(item_id)).update(sort_order=idx)
            except (ValueError, TypeError):
                continue
        return JsonResponse({"ok": True, "message": "Reordered successfully."})

    item_id = data.get("item_id") or request.POST.get("item_id")
    direction = data.get("direction") or request.POST.get("direction")
    if item_id and direction in ("up", "down"):
        target = get_object_or_404(Product, pk=item_id)
        all_items = list(Product.objects.all().order_by("sort_order", "name"))
        try:
            curr_idx = [i.id for i in all_items].index(target.id)
            if direction == "up" and curr_idx > 0:
                swap_item = all_items[curr_idx - 1]
                target_order, swap_order = target.sort_order, swap_item.sort_order
                if target_order == swap_order:
                    target_order = curr_idx
                    swap_order = curr_idx - 1
                target.sort_order = swap_order
                swap_item.sort_order = target_order
                target.save(update_fields=["sort_order"])
                swap_item.save(update_fields=["sort_order"])
            elif direction == "down" and curr_idx < len(all_items) - 1:
                swap_item = all_items[curr_idx + 1]
                target_order, swap_order = target.sort_order, swap_item.sort_order
                if target_order == swap_order:
                    target_order = curr_idx
                    swap_order = curr_idx + 1
                target.sort_order = swap_order
                swap_item.sort_order = target_order
                target.save(update_fields=["sort_order"])
                swap_item.save(update_fields=["sort_order"])
        except ValueError:
            pass
        return JsonResponse({"ok": True, "message": "Reordered successfully."})

    return JsonResponse({"error": "Invalid reorder request."}, status=400)


@role_required("Owner")
def menu_items_bulk_price(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body) if request.body else request.POST.dict()
        if not isinstance(data, dict):
            data = {}
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        data = request.POST.dict()

    action = (data.get("action") or request.POST.get("action") or "preview").lower()
    item_ids = data.get("item_ids") or request.POST.getlist("item_ids[]") or request.POST.getlist("item_ids")
    if isinstance(item_ids, str):
        item_ids = [int(x.strip()) for x in item_ids.split(",") if x.strip()]
    elif isinstance(item_ids, list):
        item_ids = [int(x) for x in item_ids if str(x).isdigit()]
    else:
        item_ids = []

    if not item_ids:
        return JsonResponse({"error": "No items selected."}, status=400)

    raw_val = str(data.get("value") or data.get("amount") or request.POST.get("value") or request.POST.get("amount") or "").strip()
    change_type = (data.get("change_type") or request.POST.get("change_type") or "").strip().lower()

    if "%" in raw_val:
        change_type = "percent"
        raw_val = raw_val.replace("%", "").strip()
    elif not change_type:
        change_type = "flat"

    try:
        val_dec = Decimal(raw_val)
    except (InvalidOperation, TypeError, ValueError):
        return JsonResponse({"error": "Invalid price adjustment value."}, status=400)

    items = Product.objects.filter(id__in=item_ids).order_by("sort_order", "name")
    preview = []
    for item in items:
        old_price = item.price
        if change_type == "percent":
            delta = (old_price * (val_dec / Decimal("100"))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            new_price = max(TWO_PLACES, (old_price + delta).quantize(TWO_PLACES, rounding=ROUND_HALF_UP))
        else:
            new_price = max(TWO_PLACES, (old_price + val_dec).quantize(TWO_PLACES, rounding=ROUND_HALF_UP))

        preview.append({
            "id": item.id,
            "name": item.name,
            "old_price": str(old_price),
            "new_price": str(new_price),
        })

    if action in ("apply", "confirm"):
        for p_info in preview:
            Product.objects.filter(pk=p_info["id"]).update(price=Decimal(p_info["new_price"]))
            p_obj = Product.objects.get(pk=p_info["id"])
            log_audit(request, "UPDATE", p_obj, {"price": [p_info["old_price"], p_info["new_price"]]})
        messages.success(request, f"Updated prices for {len(preview)} items.")
        return JsonResponse({"ok": True, "applied": True, "count": len(preview), "preview": preview})

    return JsonResponse({"ok": True, "action": "preview", "preview": preview})


@role_required("Owner")
def menu_item_duplicate(request, item_id):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body) if request.body else request.POST.dict()
        if not isinstance(data, dict):
            data = {}
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        data = request.POST.dict()

    original = get_object_or_404(Product, pk=item_id)
    new_name = str(data.get("name") or request.POST.get("name") or f"{original.name} (Copy)").strip()

    if "shortcut_key" in data or "shortcut_key" in request.POST:
        raw_sc = data.get("shortcut_key") if "shortcut_key" in data else request.POST.get("shortcut_key", "")
        new_shortcut = str(raw_sc).strip().upper()
    else:
        new_shortcut = original.shortcut_key

    if new_shortcut and Product.objects.filter(is_active=True, shortcut_key__iexact=new_shortcut).exists():
        err = f"Shortcut key '{new_shortcut}' is already in use by another active item."
        messages.error(request, err)
        return JsonResponse({"error": err}, status=400)

    duplicate = Product.objects.create(
        name=new_name,
        category=original.category,
        shortcut_key=new_shortcut,
        price=original.price,
        cost_price=original.cost_price,
        stock=original.stock,
        stock_tracked=original.stock_tracked,
        description=original.description,
        is_active=original.is_active,
        is_favorite=original.is_favorite,
        sort_order=original.sort_order + 1,
    )
    duplicate.modifiers.set(original.modifiers.all())
    for rline in original.recipe_lines.all():
        RecipeLine.objects.create(
            product=duplicate,
            stock_item=rline.stock_item,
            qty_per_unit=rline.qty_per_unit,
        )

    messages.success(request, f"Item duplicated as '{duplicate.name}'.")
    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
        return JsonResponse({"ok": True, "id": duplicate.id, "name": duplicate.name})
    return redirect("menu_items")


@role_required("Owner")
def menu_items_export_csv(request):
    response = render_csv_response("menu_items")
    writer = csv.writer(response)
    writer.writerow(["name", "category", "price", "cost", "shortcut", "active"])
    for item in Product.objects.all().order_by("sort_order", "name"):
        writer.writerow([
            item.name,
            item.category,
            f"{item.price:.2f}",
            f"{item.cost_price:.2f}",
            item.shortcut_key,
            "true" if item.is_active else "false",
        ])
    return response


@role_required("Owner")
def menu_items_import_csv(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    csv_file = request.FILES.get("csv_file") or request.FILES.get("file")
    if csv_file:
        try:
            content = csv_file.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            return JsonResponse({"error": "Invalid file encoding. UTF-8 required."}, status=400)
    elif request.body and request.content_type.startswith("text/"):
        try:
            content = request.body.decode("utf-8-sig")
        except UnicodeDecodeError:
            return JsonResponse({"error": "Invalid encoding."}, status=400)
    elif request.POST.get("csv_data"):
        content = request.POST.get("csv_data")
    else:
        return JsonResponse({"error": "No CSV file or data provided."}, status=400)

    reader = csv.reader(io.StringIO(content))
    try:
        header = next(reader)
    except StopIteration:
        return JsonResponse({"error": "Empty CSV file."}, status=400)

    header_cols = [c.strip().lower() for c in header]

    def get_col_idx(col_name, default=None):
        try:
            return header_cols.index(col_name)
        except ValueError:
            return default

    name_idx = get_col_idx("name")
    cat_idx = get_col_idx("category")
    price_idx = get_col_idx("price")
    cost_idx = get_col_idx("cost", get_col_idx("cost_price"))
    shortcut_idx = get_col_idx("shortcut", get_col_idx("shortcut_key"))
    active_idx = get_col_idx("active", get_col_idx("is_active"))

    if name_idx is None or price_idx is None:
        return JsonResponse({"error": "CSV must contain at least 'name' and 'price' headers."}, status=400)

    parsed_rows = []
    seen_shortcuts = set()
    line_num = 1

    for row in reader:
        line_num += 1
        if not row or not any(field.strip() for field in row):
            continue

        name = row[name_idx].strip() if name_idx < len(row) else ""
        if not name:
            err = f"Line {line_num}: Name is required."
            messages.error(request, err)
            return JsonResponse({"error": err, "line": line_num}, status=400)

        raw_price = row[price_idx].strip() if price_idx < len(row) else ""
        try:
            price = Decimal(raw_price)
            if price <= Decimal("0"):
                raise ValueError("Price must be positive.")
        except (InvalidOperation, ValueError, TypeError):
            err = f"Line {line_num}: Invalid price '{raw_price}'."
            messages.error(request, err)
            return JsonResponse({"error": err, "line": line_num}, status=400)

        cost = Decimal("0.00")
        if cost_idx is not None and cost_idx < len(row):
            raw_cost = row[cost_idx].strip()
            if raw_cost:
                try:
                    cost = Decimal(raw_cost)
                    if cost < Decimal("0"):
                        raise ValueError("Cost cannot be negative.")
                except (InvalidOperation, ValueError, TypeError):
                    err = f"Line {line_num}: Invalid cost price '{raw_cost}'."
                    messages.error(request, err)
                    return JsonResponse({"error": err, "line": line_num}, status=400)

        category = row[cat_idx].strip() if cat_idx is not None and cat_idx < len(row) else ""
        shortcut = row[shortcut_idx].strip().upper() if shortcut_idx is not None and shortcut_idx < len(row) else ""

        is_active = True
        if active_idx is not None and active_idx < len(row):
            raw_active = row[active_idx].strip().lower()
            if raw_active in ("false", "0", "no", "inactive"):
                is_active = False

        if shortcut and is_active:
            if shortcut in seen_shortcuts:
                err = f"Line {line_num}: Duplicate shortcut key '{shortcut}' in CSV."
                messages.error(request, err)
                return JsonResponse({"error": err, "line": line_num}, status=400)
            collision = Product.objects.filter(is_active=True, shortcut_key__iexact=shortcut).exclude(name__iexact=name)
            if collision.exists():
                err = f"Line {line_num}: Shortcut key '{shortcut}' is already in use by another active item."
                messages.error(request, err)
                return JsonResponse({"error": err, "line": line_num}, status=400)
            seen_shortcuts.add(shortcut)

        parsed_rows.append({
            "name": name,
            "category": category,
            "price": price,
            "cost_price": cost,
            "shortcut_key": shortcut,
            "is_active": is_active,
        })

    with transaction.atomic():
        imported_count = 0
        for item_data in parsed_rows:
            Product.objects.update_or_create(
                name=item_data["name"],
                defaults={
                    "category": item_data["category"],
                    "price": item_data["price"],
                    "cost_price": item_data["cost_price"],
                    "shortcut_key": item_data["shortcut_key"],
                    "is_active": item_data["is_active"],
                },
            )
            imported_count += 1

    messages.success(request, f"Successfully imported {imported_count} menu items.")
    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
        return JsonResponse({"ok": True, "count": imported_count})
    return redirect("menu_items")


@role_required("Owner")
def category_rename(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body) if request.body else request.POST.dict()
        if not isinstance(data, dict):
            data = {}
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        data = request.POST.dict()

    old_category = str(data.get("old_category") or data.get("old_name") or request.POST.get("old_category", "")).strip()
    new_category = str(data.get("new_category") or data.get("new_name") or request.POST.get("new_category", "")).strip()

    if not old_category or not new_category:
        err = "Both current and new category names are required."
        messages.error(request, err)
        return JsonResponse({"error": err}, status=400)

    updated_count = Product.objects.filter(category=old_category).update(category=new_category)
    messages.success(request, f"Renamed category '{old_category}' to '{new_category}' across {updated_count} items.")
    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
        return JsonResponse({"ok": True, "updated": updated_count, "new_category": new_category})
    return redirect("menu_items")


def compute_customer_ageing(customer, transactions=None):
    """
    Computes FIFO ageing buckets (0-30, 31-60, 61-90, 90+ days) using FIFO allocation
    of payments to oldest credits and opening balance.
    The sum of all buckets matches customer.balance.
    """
    outstanding = getattr(customer, "outstanding", None)
    if outstanding is None:
        outstanding = customer.balance
    zero = Decimal("0.00")
    if outstanding <= zero:
        return {
            "b_0_30": zero,
            "b_31_60": zero,
            "b_61_90": zero,
            "b_90_plus": zero,
            "total": zero,
        }

    if transactions is not None:
        tx_list = list(transactions)
        total_payments = sum(
            (tx.amount for tx in tx_list if tx.kind in (KhataTransaction.PAYMENT, KhataTransaction.VOID)),
            zero,
        )
        credits = sorted(
            [tx for tx in tx_list if tx.kind == KhataTransaction.CREDIT],
            key=lambda tx: (tx.created_at, tx.id),
        )
    else:
        total_payments = (
            customer.khata_transactions.filter(
                kind__in=[KhataTransaction.PAYMENT, KhataTransaction.VOID]
            ).aggregate(s=Sum("amount"))["s"]
            or zero
        )
        credits = customer.khata_transactions.filter(
            kind=KhataTransaction.CREDIT
        ).order_by("created_at", "id")

    remaining_payment = total_payments
    b_0_30 = zero
    b_31_60 = zero
    b_61_90 = zero
    b_90_plus = zero

    # 1. Opening balance is the oldest historical debt (90+ bucket)
    opening_bal = Decimal(customer.opening_balance or 0)
    if opening_bal > zero:
        if remaining_payment >= opening_bal:
            remaining_payment -= opening_bal
        else:
            unpaid_opening = opening_bal - remaining_payment
            remaining_payment = zero
            b_90_plus += unpaid_opening

    # 2. Credits in chronological order (FIFO)
    today = timezone.localdate()

    for cred in credits:
        cred_amt = cred.amount
        if remaining_payment >= cred_amt:
            remaining_payment -= cred_amt
            continue
        elif remaining_payment > zero:
            unpaid_amt = cred_amt - remaining_payment
            remaining_payment = zero
        else:
            unpaid_amt = cred_amt

        cred_dt = timezone.localdate(cred.created_at) if timezone.is_aware(cred.created_at) else cred.created_at.date()
        age_days = (today - cred_dt).days

        if age_days <= 30:
            b_0_30 += unpaid_amt
        elif age_days <= 60:
            b_31_60 += unpaid_amt
        elif age_days <= 90:
            b_61_90 += unpaid_amt
        else:
            b_90_plus += unpaid_amt

    total = (b_0_30 + b_31_60 + b_61_90 + b_90_plus).quantize(TWO_PLACES)
    return {
        "b_0_30": b_0_30.quantize(TWO_PLACES),
        "b_31_60": b_31_60.quantize(TWO_PLACES),
        "b_61_90": b_61_90.quantize(TWO_PLACES),
        "b_90_plus": b_90_plus.quantize(TWO_PLACES),
        "total": total,
    }


@role_required("Cashier", "Owner")
def khata(request):
    query = request.GET.get("q", "").strip()
    only_dues = (
        request.GET.get("has_dues", "").strip()
        or request.GET.get("dues_only", "").strip()
        or request.GET.get("only_dues", "").strip()
    )
    sort_param = request.GET.get("sort", "").strip().lower()

    customer_list = Customer.objects.filter(is_active=True).with_outstanding()

    if only_dues in ("1", "true", "yes", "on"):
        customer_list = customer_list.filter(outstanding__gt=Decimal("0.00"))

    if query:
        customer_list = customer_list.filter(Q(name__icontains=query) | Q(phone__icontains=query))

    if sort_param in ("oldest_due", "oldest", "oldest_credit"):
        customer_list = customer_list.annotate(
            oldest_due_date=Min(
                "khata_transactions__created_at",
                filter=Q(khata_transactions__kind=KhataTransaction.CREDIT),
            )
        ).order_by(F("oldest_due_date").asc(nulls_last=True), "-outstanding", "name")
    elif sort_param in ("amount_asc", "+amount", "balance_asc", "asc"):
        customer_list = customer_list.order_by("outstanding", "name")
    elif sort_param in ("name", "name_asc"):
        customer_list = customer_list.order_by("name")
    else:
        # Default: sort by amount descending (highest outstanding dues first)
        customer_list = customer_list.order_by("-outstanding", "name")

    paginator = Paginator(customer_list, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    page_customers = list(page_obj)
    customer_ids = [c.id for c in page_customers]
    if customer_ids:
        page_transactions = KhataTransaction.objects.filter(
            customer_id__in=customer_ids
        ).order_by("created_at", "id")
        tx_by_customer = defaultdict(list)
        for tx in page_transactions:
            tx_by_customer[tx.customer_id].append(tx)
        for c in page_customers:
            c.ageing = compute_customer_ageing(c, transactions=tx_by_customer.get(c.id, []))
    else:
        for c in page_customers:
            c.ageing = compute_customer_ageing(c)

    business_settings = BusinessSettings.current()

    return render(
        request,
        "pos/khata.html",
        {
            "customers": page_obj,
            "page_obj": page_obj,
            "query": query,
            "has_dues": only_dues in ("1", "true", "yes", "on"),
            "selected_sort": sort_param,
            "payment_methods": KhataTransaction.METHOD_CHOICES,
            "business_settings": business_settings,
        },
    )


@role_required("Cashier", "Owner")
def khata_statement(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)

    ledger = []
    opening_bal = Decimal(customer.opening_balance or 0)
    running = opening_bal
    if opening_bal != Decimal("0.00"):
        ledger.append({
            "date": None,
            "type": "Opening Balance",
            "method": "",
            "note": "Opening balance",
            "debit": opening_bal if opening_bal > 0 else Decimal("0.00"),
            "credit": abs(opening_bal) if opening_bal < 0 else Decimal("0.00"),
            "amount": opening_bal,
            "running_balance": running,
        })

    txs = customer.khata_transactions.select_related("bill").order_by("created_at", "id")
    for tx in txs:
        debit = Decimal("0.00")
        credit = Decimal("0.00")
        if tx.kind == KhataTransaction.CREDIT:
            running += tx.amount
            debit = tx.amount
        elif tx.kind in (KhataTransaction.PAYMENT, KhataTransaction.VOID):
            running -= tx.amount
            credit = tx.amount

        ledger.append({
            "date": tx.created_at,
            "type": tx.get_kind_display(),
            "method": tx.get_method_display() if tx.method else "",
            "note": tx.note or (f"Bill #{tx.bill.bill_number}" if tx.bill else ""),
            "debit": debit,
            "credit": credit,
            "amount": tx.amount,
            "running_balance": running,
        })

    ageing = compute_customer_ageing(customer)
    business_settings = BusinessSettings.current()

    return render(
        request,
        "pos/khata_statement.html",
        {
            "customer": customer,
            "opening_balance": opening_bal,
            "ledger": ledger,
            "final_balance": customer.balance,
            "ageing": ageing,
            "business_settings": business_settings,
            "today": timezone.localdate(),
        },
    )


@role_required("Cashier", "Owner")
def customers(request):
    query = request.GET.get("q", "").strip()
    show_archived = request.GET.get("archived", "").strip()
    has_dues = request.GET.get("has_dues", "").strip()
    sort_param = request.GET.get("sort", "").strip()

    customer_list = Customer.objects.with_outstanding()

    if show_archived == "only":
        customer_list = customer_list.filter(is_active=False)
    elif show_archived in ("1", "true", "yes"):
        pass
    else:
        customer_list = customer_list.filter(is_active=True)

    if has_dues in ("1", "true", "yes"):
        customer_list = customer_list.filter(outstanding__gt=0)

    if query:
        customer_list = customer_list.filter(Q(name__icontains=query) | Q(phone__icontains=query))

    if sort_param in ("balance", "balance_desc", "-balance", "desc"):
        customer_list = customer_list.order_by("-outstanding", "name")
    elif sort_param in ("balance_asc", "+balance", "asc"):
        customer_list = customer_list.order_by("outstanding", "name")
    else:
        customer_list = customer_list.order_by("name")

    paginator = Paginator(customer_list, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "pos/customers.html",
        {
            "customers": page_obj,
            "page_obj": page_obj,
            "query": query,
            "show_archived": show_archived in ("1", "true", "yes", "only"),
            "has_dues": has_dues in ("1", "true", "yes"),
            "selected_sort": sort_param,
        },
    )


@role_required("Cashier", "Owner")
def customer_create(request):
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        customer = form.save()
        log_audit(
            request,
            "CREATE",
            customer,
            {
                "name": [None, customer.name],
                "phone": [None, customer.phone],
            },
        )
        messages.success(request, "Customer added successfully.")
        return redirect("customer_detail", customer.id)
    return render(request, "pos/customer_form.html", {"form": form, "page_title": "Add Customer", "submit_label": "Add Customer"})


@role_required("Cashier", "Owner")
def customer_detail(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)
    bills = customer.bills.all()

    last_bill = customer.bills.order_by("-created_at").values_list("created_at", flat=True).first()
    last_tx = customer.khata_transactions.order_by("-created_at").values_list("created_at", flat=True).first()
    visits = [d for d in (last_bill, last_tx) if d is not None]
    last_visit = max(visits) if visits else None

    ledger = []
    running = Decimal(customer.opening_balance or 0)
    if running != Decimal("0.00"):
        ledger.append({
            "date": None,
            "type": "Opening Balance",
            "note": "Opening balance",
            "amount": running,
            "running_balance": running,
        })

    txs = customer.khata_transactions.all().order_by("created_at", "id")
    for tx in txs:
        if tx.kind == KhataTransaction.CREDIT:
            running += tx.amount
        elif tx.kind in (KhataTransaction.PAYMENT, KhataTransaction.VOID):
            running -= tx.amount
        ledger.append({
            "date": tx.created_at,
            "type": tx.get_kind_display(),
            "note": tx.note,
            "amount": tx.amount,
            "running_balance": running,
        })

    return render(request, "pos/customer_detail.html", {
        "customer": customer,
        "bills": bills,
        "bill_count": bills.count(),
        "total_spent": bills.filter(status=Bill.STATUS_ACTIVE).aggregate(total=Sum("grand_total"))["total"] or Decimal("0"),
        "last_visit": last_visit,
        "ledger": ledger,
    })


@role_required("Owner")
def customer_edit(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)
    old_name = customer.name
    old_phone = customer.phone
    old_email = customer.email
    old_address = customer.address
    old_limit = str(customer.credit_limit) if customer.credit_limit is not None else None

    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == "POST" and form.is_valid():
        form.save()

        changes = {}
        if customer.name != old_name:
            changes["name"] = [old_name, customer.name]
        if customer.phone != old_phone:
            changes["phone"] = [old_phone, customer.phone]
        if customer.email != old_email:
            changes["email"] = [old_email, customer.email]
        if customer.address != old_address:
            changes["address"] = [old_address, customer.address]
        new_limit = str(customer.credit_limit) if customer.credit_limit is not None else None
        if new_limit != old_limit:
            changes["credit_limit"] = [old_limit, new_limit]

        if changes:
            log_audit(request, "UPDATE", customer, changes)

        messages.success(request, "Customer updated successfully.")
        return redirect("customer_detail", customer.id)
    return render(request, "pos/customer_form.html", {"form": form, "customer": customer, "page_title": "Edit Customer", "submit_label": "Save Changes"})


@role_required("Owner")
def customer_delete(request, customer_id):
    if request.method == "POST":
        customer = get_object_or_404(Customer, pk=customer_id)
        if customer.balance != Decimal("0.00"):
            messages.error(
                request,
                f"Cannot archive customer {customer.name} with outstanding balance of ₹{customer.balance:.2f}. Balance must be settled first.",
            )
            return redirect("customers")
        customer.is_active = False
        customer.save(update_fields=["is_active"])
        log_audit(request, "ARCHIVE", customer, {"is_active": [True, False], "phone": [customer.phone, None]})
        messages.success(request, f"Customer {customer.name} archived successfully.")
    return redirect("customers")


@role_required("Cashier", "Owner")
@transaction.atomic
def khata_payment(request, customer_id):
    if request.method != "POST":
        return redirect("khata")

    customer = get_object_or_404(Customer, pk=customer_id)

    next_url = request.POST.get("next", "").strip()
    redirect_target = (
        next_url
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()})
        else "khata"
    )

    raw_amount = request.POST.get("amount")
    try:
        amount = parse_decimal(raw_amount, min_value=TWO_PLACES)
    except (ValueError, InvalidOperation) as exc:
        messages.error(request, str(exc))
        return redirect(redirect_target)

    outstanding = customer.balance
    if amount > outstanding:
        messages.error(
            request,
            f"Payment amount of ₹{amount:.2f} exceeds outstanding balance of ₹{outstanding:.2f}.",
        )
        return redirect(redirect_target)

    raw_method = (request.POST.get("method") or "").strip().upper()
    if not raw_method:
        err = "Payment method is required."
        messages.error(request, err)
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
            return JsonResponse({"error": err}, status=400)
        return redirect(redirect_target)

    if raw_method not in dict(KhataTransaction.METHOD_CHOICES):
        err = f"Invalid payment method: {raw_method}. Must be CASH, UPI, or CARD."
        messages.error(request, err)
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
            return JsonResponse({"error": err}, status=400)
        return redirect(redirect_target)

    note = request.POST.get("note", "").strip() or "Payment received"
    payment = KhataTransaction.objects.create(
        customer=customer,
        kind=KhataTransaction.PAYMENT,
        method=raw_method,
        amount=amount,
        note=note,
    )
    new_balance = (outstanding - amount).quantize(TWO_PLACES)
    log_audit(
        request,
        "PAYMENT",
        payment,
        {"customer": customer.name, "amount": str(amount), "method": payment.method, "note": payment.note},
    )
    messages.success(
        request,
        f"Payment of ₹{amount:.2f} ({payment.get_method_display()}) recorded for {customer.name}. New balance: ₹{new_balance:.2f}.",
    )
    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
        return JsonResponse({
            "ok": True,
            "amount": str(amount),
            "method": payment.method,
            "new_balance": str(new_balance),
        })
    return redirect(redirect_target)


@role_required("Owner")
def expenses(request):
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    raw_date = request.GET.get("date", "").strip()
    selected_month = request.GET.get("month", "").strip()
    parsed_date = parse_date_param(request, "date")
    expense_list = Expense.objects.select_related("category").all().order_by("-expense_date", "-id")

    if query:
        expense_list = expense_list.filter(
            Q(title__icontains=query)
            | Q(category__name__icontains=query)
            | Q(notes__icontains=query)
            | Q(description__icontains=query)
        )
    if category:
        if category.isdigit():
            expense_list = expense_list.filter(Q(category_id=int(category)) | Q(category__name=category))
        else:
            expense_list = expense_list.filter(category__name=category)
    if parsed_date:
        expense_list = expense_list.filter(expense_date=parsed_date)
    elif selected_month:
        try:
            m_parts = selected_month.split("-")
            m_year = int(m_parts[0])
            m_month = int(m_parts[1])
            expense_list = expense_list.filter(expense_date__year=m_year, expense_date__month=m_month)
        except (ValueError, IndexError):
            pass

    today = timezone.localdate()
    filtered_total = expense_list.aggregate(v=Sum("amount"))["v"] or Decimal("0")

    breakdown_qs = (
        expense_list.values("category__name")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")
    )
    category_breakdown = []
    for item in breakdown_qs:
        cat_total = item["total"] or Decimal("0")
        pct = (cat_total / filtered_total * Decimal("100")).quantize(Decimal("0.1")) if filtered_total > 0 else Decimal("0.0")
        category_breakdown.append({
            "name": item["category__name"] or "General",
            "total": cat_total,
            "count": item["count"],
            "percentage": pct,
        })

    paginator = Paginator(expense_list, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    all_categories = ExpenseCategory.objects.all().order_by("name")

    return render(request, "pos/expenses.html", {
        "expenses": page_obj,
        "page_obj": page_obj,
        "categories": all_categories,
        "query": query,
        "selected_category": category,
        "selected_date": raw_date if parsed_date else "",
        "selected_month": selected_month,
        "filtered_total": filtered_total,
        "category_breakdown": category_breakdown,
        "today_total": Expense.objects.filter(expense_date=today).aggregate(v=Sum("amount"))["v"] or Decimal("0"),
        "month_total": Expense.objects.filter(expense_date__year=today.year, expense_date__month=today.month).aggregate(v=Sum("amount"))["v"] or Decimal("0"),
        "all_total": Expense.objects.aggregate(v=Sum("amount"))["v"] or Decimal("0"),
    })


@role_required("Owner")
def expense_create(request):
    form = ExpenseForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        expense = form.save(commit=False)
        if not expense.shift_id:
            open_shift = Shift.current_open()
            if open_shift:
                expense.shift = open_shift
        expense.save()
        log_audit(
            request,
            "CREATE",
            expense,
            {
                "title": [None, expense.title],
                "amount": [None, str(expense.amount)],
                "category": [None, expense.category.name if expense.category else ""],
                "payment_method": [None, expense.payment_method],
            },
        )
        messages.success(request, "Expense added successfully.")
        return redirect("expenses")
    return render(request, "pos/expense_form.html", {"form": form, "page_title": "Add Expense", "submit_label": "Add Expense"})


@role_required("Owner")
def expense_edit(request, expense_id):
    expense = get_object_or_404(Expense, pk=expense_id)
    old_title = expense.title
    old_amount = str(expense.amount)
    old_category = expense.category.name if expense.category else ""
    old_method = expense.payment_method
    old_notes = expense.notes

    form = ExpenseForm(request.POST or None, request.FILES or None, instance=expense)
    if request.method == "POST" and form.is_valid():
        form.save()

        changes = {}
        if expense.title != old_title:
            changes["title"] = [old_title, expense.title]
        if str(expense.amount) != old_amount:
            changes["amount"] = [old_amount, str(expense.amount)]
        new_cat = expense.category.name if expense.category else ""
        if new_cat != old_category:
            changes["category"] = [old_category, new_cat]
        if expense.payment_method != old_method:
            changes["payment_method"] = [old_method, expense.payment_method]
        if expense.notes != old_notes:
            changes["notes"] = [old_notes, expense.notes]

        if changes:
            log_audit(request, "UPDATE", expense, changes)

        messages.success(request, "Expense updated successfully.")
        return redirect("expenses")
    return render(request, "pos/expense_form.html", {"form": form, "expense": expense, "page_title": "Edit Expense", "submit_label": "Save Changes"})


@role_required("Owner")
def expense_delete(request, expense_id):
    if request.method == "POST":
        expense = get_object_or_404(Expense, pk=expense_id)
        log_audit(request, "DELETE", expense, {"amount": [str(expense.amount), None]})
        expense.delete()
        messages.success(request, "Expense deleted.")
    return redirect("expenses")


@role_required("Owner")
def dashboard(request):
    process_recurring_expenses()
    today = timezone.localdate()
    today_bills = Bill.objects.filter(created_at__date=today, status=Bill.STATUS_ACTIVE)
    bill_totals = today_bills.aggregate(
        sales=Sum("grand_total"),
        discounts=Sum("discount"),
    )
    total_sales = bill_totals["sales"] or Decimal("0")
    total_discounts = bill_totals["discounts"] or Decimal("0")

    payment_totals = BillPayment.objects.filter(
        bill__created_at__date=today,
        bill__status=Bill.STATUS_ACTIVE,
    ).aggregate(
        cash=Sum("amount", filter=Q(method=Bill.CASH)),
        upi=Sum("amount", filter=Q(method=Bill.UPI)),
        card=Sum("amount", filter=Q(method=Bill.CARD)),
        khata=Sum("amount", filter=Q(method=Bill.KHATA)),
    )
    cash_sales = payment_totals["cash"] or Decimal("0")
    upi_sales = payment_totals["upi"] or Decimal("0")
    card_sales = payment_totals["card"] or Decimal("0")
    khata_sales = payment_totals["khata"] or Decimal("0")

    unpaid_bills = today_bills.filter(payments__isnull=True)
    if unpaid_bills.exists():
        legacy_totals = unpaid_bills.aggregate(
            cash=Sum("grand_total", filter=Q(payment_method=Bill.CASH)),
            upi=Sum("grand_total", filter=Q(payment_method=Bill.UPI)),
            card=Sum("grand_total", filter=Q(payment_method=Bill.CARD)),
            khata=Sum("grand_total", filter=Q(payment_method=Bill.KHATA)),
        )
        cash_sales += legacy_totals["cash"] or Decimal("0")
        upi_sales += legacy_totals["upi"] or Decimal("0")
        card_sales += legacy_totals["card"] or Decimal("0")
        khata_sales += legacy_totals["khata"] or Decimal("0")

    products_sold = (
        BillItem.objects.filter(bill__created_at__date=today, bill__status=Bill.STATUS_ACTIVE)
        .values("item_name", "menu_item__category")
        .annotate(
            quantity_sold=Sum("quantity"),
            selling_amount=Sum("total"),
            profit=Sum((F("price") - F("cost_price")) * F("quantity")),
        )
        .order_by("item_name")
    )
    total_quantity = sum((row["quantity_sold"] for row in products_sold), Decimal("0"))
    item_profit = sum((row["profit"] for row in products_sold), Decimal("0"))
    total_profit = item_profit - total_discounts

    expenses_today = (
        Expense.objects.filter(expense_date=today).aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )
    net_profit = total_profit - expenses_today

    bs_settings = BusinessSettings.current()
    low_stock_threshold = getattr(bs_settings, "low_stock_threshold", 5)
    if low_stock_threshold is None:
        low_stock_threshold = 5

    top_products = sorted(products_sold, key=lambda row: row["selling_amount"], reverse=True)[:5]
    low_stock = Product.objects.filter(is_active=True, stock_tracked=True, stock__lte=low_stock_threshold)
    customers_with_outstanding = Customer.objects.with_outstanding()
    total_outstanding = customers_with_outstanding.aggregate(total=Sum("outstanding"))["total"] or Decimal("0")
    return render(request, "pos/dashboard.html", {
        "products_sold": products_sold,
        "top_products": top_products,
        "low_stock_products": low_stock.order_by("stock", "name"),
        "low_stock_count": low_stock.count(),
        "low_stock_threshold": low_stock_threshold,
        "business_settings": bs_settings,
        "total_sales": total_sales,
        "total_quantity": total_quantity,
        "total_profit": total_profit,
        "expenses_today": expenses_today,
        "today_expenses": expenses_today,
        "net_profit": net_profit,
        "cash_sales": cash_sales,
        "upi_sales": upi_sales,
        "card_sales": card_sales,
        "khata_sales": khata_sales,
        "payment_split": {
            "cash": cash_sales,
            "upi": upi_sales,
            "card": card_sales,
            "khata": khata_sales,
        },
        "total_outstanding": total_outstanding,
        "outstanding_customers": customers_with_outstanding.filter(outstanding__gt=0),
    })


@role_required("Owner")
def reports(request):
    from_date, to_date, date_error = report_date_range(request)
    bills = report_bills(from_date, to_date)
    context = report_context(bills, from_date, to_date, request=request)
    context["date_error"] = date_error
    return render(request, "pos/reports.html", context)


def report_date_range(request):
    today = timezone.localdate()
    from_value = request.GET.get("from_date", "").strip()
    to_value = request.GET.get("to_date", "").strip()
    date_error = ""
    try:
        from_date = datetime.strptime(from_value, "%Y-%m-%d").date() if from_value else today
        to_date = datetime.strptime(to_value, "%Y-%m-%d").date() if to_value else from_date
    except ValueError:
        from_date, to_date = today, today
        date_error = "Enter valid dates using the date fields."
    if from_date > to_date:
        from_date, to_date = today, today
        date_error = "From date cannot be after To date."
    return from_date, to_date, date_error


def report_bills(from_date, to_date):
    return Bill.objects.filter(
        created_at__date__gte=from_date,
        created_at__date__lte=to_date,
        status=Bill.STATUS_ACTIVE,
    ).select_related("customer", "created_by").prefetch_related("items__menu_item", "payments").order_by("created_at", "bill_number")


def report_context(bills, from_date, to_date, request=None):
    selected_tab = "sales"
    if request:
        tab_param = request.GET.get("tab", "").strip().lower()
        if tab_param in {"sales", "items", "categories", "payments", "cashiers", "hourly", "profit"}:
            selected_tab = tab_param

    payment_totals = BillPayment.objects.filter(
        bill__in=bills
    ).aggregate(
        cash=Sum("amount", filter=Q(method=Bill.CASH)),
        upi=Sum("amount", filter=Q(method=Bill.UPI)),
        card=Sum("amount", filter=Q(method=Bill.CARD)),
        khata=Sum("amount", filter=Q(method=Bill.KHATA)),
    )
    total_sales = bills.aggregate(sales=Sum("grand_total"))["sales"] or Decimal("0.00")
    cash_sales = payment_totals["cash"] or Decimal("0.00")
    upi_sales = payment_totals["upi"] or Decimal("0.00")
    card_sales = payment_totals["card"] or Decimal("0.00")
    khata_sales = payment_totals["khata"] or Decimal("0.00")

    unpaid_bills = bills.filter(payments__isnull=True)
    if unpaid_bills.exists():
        legacy_totals = unpaid_bills.aggregate(
            cash=Sum("grand_total", filter=Q(payment_method=Bill.CASH)),
            upi=Sum("grand_total", filter=Q(payment_method=Bill.UPI)),
            card=Sum("grand_total", filter=Q(payment_method=Bill.CARD)),
            khata=Sum("grand_total", filter=Q(payment_method=Bill.KHATA)),
        )
        cash_sales += legacy_totals["cash"] or Decimal("0.00")
        upi_sales += legacy_totals["upi"] or Decimal("0.00")
        card_sales += legacy_totals["card"] or Decimal("0.00")
        khata_sales += legacy_totals["khata"] or Decimal("0.00")

    total_payments = cash_sales + upi_sales + card_sales + khata_sales
    method_totals = {
        Bill.CASH: cash_sales,
        Bill.UPI: upi_sales,
        Bill.CARD: card_sales,
        Bill.KHATA: khata_sales,
    }

    # Items & Categories calculation
    bill_items_qs = BillItem.objects.filter(bill__in=bills).annotate(
        line_cost=ExpressionWrapper(F("cost_price") * F("quantity"), output_field=DecimalField(max_digits=12, decimal_places=2)),
    )

    items_grouped = (
        bill_items_qs.values("item_name", "menu_item__category")
        .annotate(
            quantity=Sum("quantity"),
            revenue=Sum("total"),
            cost=Sum("line_cost"),
        )
        .order_by("-revenue", "item_name")
    )

    items_report = []
    total_items_qty = Decimal("0.00")
    total_items_rev = Decimal("0.00")
    total_items_cost = Decimal("0.00")
    total_items_profit = Decimal("0.00")

    for row in items_grouped:
        qty = row["quantity"] or Decimal("0.00")
        rev = row["revenue"] or Decimal("0.00")
        cost = row["cost"] or Decimal("0.00")
        profit = rev - cost
        margin = (profit / rev * Decimal("100")) if rev > Decimal("0.00") else Decimal("0.00")

        total_items_qty += qty
        total_items_rev += rev
        total_items_cost += cost
        total_items_profit += profit

        items_report.append({
            "name": row["item_name"],
            "category": row["menu_item__category"] or "Uncategorized",
            "quantity": qty,
            "revenue": rev,
            "cost": cost,
            "profit": profit,
            "margin": margin,
        })

    items_totals = {
        "quantity": total_items_qty,
        "revenue": total_items_rev,
        "cost": total_items_cost,
        "profit": total_items_profit,
        "margin": (total_items_profit / total_items_rev * Decimal("100")) if total_items_rev > Decimal("0.00") else Decimal("0.00"),
    }

    categories_grouped = (
        bill_items_qs.values("menu_item__category")
        .annotate(
            quantity=Sum("quantity"),
            revenue=Sum("total"),
            cost=Sum("line_cost"),
        )
        .order_by("-revenue")
    )

    categories_report = []
    total_cats_qty = Decimal("0.00")
    total_cats_rev = Decimal("0.00")
    total_cats_cost = Decimal("0.00")
    total_cats_profit = Decimal("0.00")

    for row in categories_grouped:
        cat_name = row["menu_item__category"] or "Uncategorized"
        qty = row["quantity"] or Decimal("0.00")
        rev = row["revenue"] or Decimal("0.00")
        cost = row["cost"] or Decimal("0.00")
        profit = rev - cost
        margin = (profit / rev * Decimal("100")) if rev > Decimal("0.00") else Decimal("0.00")

        total_cats_qty += qty
        total_cats_rev += rev
        total_cats_cost += cost
        total_cats_profit += profit

        categories_report.append({
            "category": cat_name,
            "quantity": qty,
            "revenue": rev,
            "cost": cost,
            "profit": profit,
            "margin": margin,
        })

    categories_totals = {
        "quantity": total_cats_qty,
        "revenue": total_cats_rev,
        "cost": total_cats_cost,
        "profit": total_cats_profit,
        "margin": (total_cats_profit / total_cats_rev * Decimal("100")) if total_cats_rev > Decimal("0.00") else Decimal("0.00"),
    }

    # Payments breakdown
    payments_report = []
    for code, label in Bill.PAYMENT_CHOICES:
        if code == Bill.SPLIT:
            continue
        amt = method_totals.get(code, Decimal("0.00"))
        share = (amt / total_payments * Decimal("100")) if total_payments > Decimal("0.00") else Decimal("0.00")
        payments_report.append({
            "code": code,
            "label": label,
            "amount": amt,
            "share": share,
        })

    # Cashiers calculation
    void_bills = Bill.objects.filter(
        status=Bill.STATUS_VOID,
        created_at__date__gte=from_date,
        created_at__date__lte=to_date,
    )
    active_cashier_ids = set(bills.values_list("created_by", flat=True))
    void_cashier_ids = set(void_bills.values_list("created_by", flat=True))
    all_cashier_ids = active_cashier_ids.union(void_cashier_ids)

    user_ids = [cid for cid in all_cashier_ids if cid is not None]
    staff_users = User.objects.filter(
        Q(id__in=user_ids) | Q(groups__name__in=["Cashier", "Owner"])
    ).distinct().order_by("username")

    cashiers_report = []
    total_cashier_bills = 0
    total_cashier_rev = Decimal("0.00")
    total_cashier_voids = 0

    seen_user_ids = set()
    for u in staff_users:
        seen_user_ids.add(u.id)
        user_active = bills.filter(created_by=u)
        b_count = user_active.count()
        b_rev = user_active.aggregate(s=Sum("grand_total"))["s"] or Decimal("0.00")
        b_voids = void_bills.filter(created_by=u).count()
        avg_bill = (b_rev / Decimal(str(b_count))) if b_count > 0 else Decimal("0.00")

        cashiers_report.append({
            "cashier": u,
            "cashier_name": u.get_full_name() or u.username,
            "username": u.username,
            "bills": b_count,
            "revenue": b_rev,
            "voids": b_voids,
            "average_bill": avg_bill,
        })
        total_cashier_bills += b_count
        total_cashier_rev += b_rev
        total_cashier_voids += b_voids

    if None in all_cashier_ids:
        unassigned_active = bills.filter(created_by__isnull=True)
        u_count = unassigned_active.count()
        u_rev = unassigned_active.aggregate(s=Sum("grand_total"))["s"] or Decimal("0.00")
        u_voids = void_bills.filter(created_by__isnull=True).count()
        cashiers_report.append({
            "cashier": None,
            "cashier_name": "Unassigned",
            "username": "unassigned",
            "bills": u_count,
            "revenue": u_rev,
            "voids": u_voids,
            "average_bill": (u_rev / Decimal(str(u_count))) if u_count > 0 else Decimal("0.00"),
        })
        total_cashier_bills += u_count
        total_cashier_rev += u_rev
        total_cashier_voids += u_voids

    cashiers_totals = {
        "bills": total_cashier_bills,
        "revenue": total_cashier_rev,
        "voids": total_cashier_voids,
        "average_bill": (total_cashier_rev / Decimal(str(total_cashier_bills))) if total_cashier_bills > 0 else Decimal("0.00"),
    }

    # Hourly (0-23)
    hourly_counts = {h: 0 for h in range(24)}
    hourly_revenues = {h: Decimal("0.00") for h in range(24)}

    for b in bills.values("created_at", "grand_total"):
        local_dt = timezone.localtime(b["created_at"])
        h = local_dt.hour
        hourly_counts[h] += 1
        hourly_revenues[h] += (b["grand_total"] or Decimal("0.00"))

    hourly_labels = [
        f"{12 if h % 12 == 0 else h % 12} {'AM' if h < 12 else 'PM'}"
        for h in range(24)
    ]
    hourly_values = [float(hourly_revenues[h]) for h in range(24)]
    hourly_table = [
        {
            "hour_num": h,
            "hour_label": hourly_labels[h],
            "bills": hourly_counts[h],
            "revenue": hourly_revenues[h],
        }
        for h in range(24)
    ]
    peak_hour = max(range(24), key=lambda h: (hourly_revenues[h], hourly_counts[h]))

    # Profit per day (revenue minus cost minus discounts minus expenses)
    daily_bills_agg = (
        bills.annotate(sale_day=TruncDate("created_at"))
        .values("sale_day")
        .annotate(discounts=Sum("discount"))
        .order_by("sale_day")
    )
    discounts_by_day = {row["sale_day"]: row["discounts"] or Decimal("0.00") for row in daily_bills_agg}

    daily_items_agg = (
        bill_items_qs.annotate(sale_day=TruncDate("bill__created_at"))
        .values("sale_day")
        .annotate(
            revenue=Sum("total"),
            cost=Sum("line_cost"),
        )
        .order_by("sale_day")
    )
    items_by_day = {
        row["sale_day"]: {
            "revenue": row["revenue"] or Decimal("0.00"),
            "cost": row["cost"] or Decimal("0.00"),
        }
        for row in daily_items_agg
    }

    expenses_qs = (
        Expense.objects.filter(
            expense_date__gte=from_date,
            expense_date__lte=to_date,
        )
        .values("expense_date")
        .annotate(total=Sum("amount"))
    )
    expenses_by_day = {row["expense_date"]: row["total"] or Decimal("0.00") for row in expenses_qs}

    wastage_qs = (
        Wastage.objects.filter(
            created_at__date__gte=from_date,
            created_at__date__lte=to_date,
        )
        .annotate(w_day=TruncDate("created_at"))
        .values("w_day")
        .annotate(total=Sum("cost"))
    )
    wastage_by_day = {row["w_day"]: row["total"] or Decimal("0.00") for row in wastage_qs}

    all_days = sorted(
        set(discounts_by_day.keys())
        .union(set(items_by_day.keys()))
        .union(set(expenses_by_day.keys()))
        .union(set(wastage_by_day.keys()))
    )
    if not all_days:
        all_days = [from_date]

    profit_report = []
    tot_p_rev = Decimal("0.00")
    tot_p_cost = Decimal("0.00")
    tot_p_disc = Decimal("0.00")
    tot_p_exp = Decimal("0.00")
    tot_p_wastage = Decimal("0.00")
    tot_p_net = Decimal("0.00")

    for day in all_days:
        d_rev = items_by_day.get(day, {}).get("revenue", Decimal("0.00"))
        d_cost = items_by_day.get(day, {}).get("cost", Decimal("0.00"))
        d_disc = discounts_by_day.get(day, Decimal("0.00"))
        d_exp = expenses_by_day.get(day, Decimal("0.00"))
        d_wastage = wastage_by_day.get(day, Decimal("0.00"))
        d_profit = d_rev - d_cost - d_disc - d_exp - d_wastage
        d_margin = (d_profit / d_rev * Decimal("100")) if d_rev > Decimal("0.00") else Decimal("0.00")

        tot_p_rev += d_rev
        tot_p_cost += d_cost
        tot_p_disc += d_disc
        tot_p_exp += d_exp
        tot_p_wastage += d_wastage
        tot_p_net += d_profit

        profit_report.append({
            "date": day,
            "revenue": d_rev,
            "cost": d_cost,
            "discounts": d_disc,
            "expenses": d_exp,
            "wastage": d_wastage,
            "profit": d_profit,
            "margin": d_margin,
        })

    profit_totals = {
        "revenue": tot_p_rev,
        "cost": tot_p_cost,
        "discounts": tot_p_disc,
        "expenses": tot_p_exp,
        "wastage": tot_p_wastage,
        "profit": tot_p_net,
        "margin": (tot_p_net / tot_p_rev * Decimal("100")) if tot_p_rev > Decimal("0.00") else Decimal("0.00"),
    }

    # Wastage breakdown by reason for the date range
    wastage_by_reason_qs = (
        Wastage.objects.filter(
            created_at__date__gte=from_date,
            created_at__date__lte=to_date,
        )
        .values("reason")
        .annotate(
            count=Count("id"),
            total_qty=Sum("quantity"),
            total_cost=Sum("cost"),
        )
        .order_by("-total_cost")
    )
    reason_map = dict(Wastage.REASON_CHOICES)
    wastage_reason_summary = []
    for r in wastage_by_reason_qs:
        r_code = r["reason"]
        r_cost = r["total_cost"] or Decimal("0.00")
        r_qty = r["total_qty"] or Decimal("0.00")
        r_count = r["count"]
        r_share = (r_cost / tot_p_wastage * Decimal("100")) if tot_p_wastage > Decimal("0.00") else Decimal("0.00")
        wastage_reason_summary.append({
            "reason": r_code,
            "label": reason_map.get(r_code, r_code),
            "count": r_count,
            "total_qty": r_qty,
            "total_cost": r_cost,
            "share": r_share,
        })

    # Daily sales for Sales chart
    daily_sales = list(
        bills.annotate(sale_day=TruncDate("created_at"))
        .values("sale_day")
        .annotate(sales=Sum("grand_total"))
        .order_by("sale_day")
    )
    chart_methods = [m for m in Bill.PAYMENT_CHOICES if m[0] != Bill.SPLIT]

    # Paginate sales-detail table (50 per page)
    sales_paginator = Paginator(bills, 50)
    sales_page = sales_paginator.get_page(request.GET.get("page") if request else 1)

    return {
        "bills": bills,
        "sales_page": sales_page,
        "page_obj": sales_page,
        "selected_tab": selected_tab,
        "from_date": from_date,
        "to_date": to_date,
        "total_sales": total_sales,
        "total_bills": bills.count(),
        "total_items": total_items_qty,
        "cash_sales": cash_sales,
        "upi_sales": upi_sales,
        "card_sales": card_sales,
        "khata_sales": khata_sales,
        "total_payments": total_payments,
        "items_report": items_report,
        "items_totals": items_totals,
        "categories_report": categories_report,
        "categories_totals": categories_totals,
        "payments_report": payments_report,
        "cashiers_report": cashiers_report,
        "cashiers_totals": cashiers_totals,
        "hourly_labels": json.dumps(hourly_labels),
        "hourly_values": json.dumps(hourly_values),
        "hourly_table": hourly_table,
        "peak_hour": hourly_labels[peak_hour],
        "profit_report": profit_report,
        "profit_totals": profit_totals,
        "wastage_reason_summary": wastage_reason_summary,
        "total_wastage": tot_p_wastage,
        "sales_chart_labels": json.dumps([row["sale_day"].strftime("%d %b %Y") for row in daily_sales]),
        "sales_chart_values": json.dumps([float(row["sales"] or 0) for row in daily_sales]),
        "payment_chart_labels": json.dumps([label for _, label in chart_methods]),
        "payment_chart_values": json.dumps([
            float(method_totals.get(method, 0)) for method, _ in chart_methods
        ]),
        "business_settings": BusinessSettings.current(),
    }


@role_required("Owner")
def export_sales_csv(request):
    from_date, to_date, date_error = report_date_range(request)
    if date_error:
        return JsonResponse({"error": date_error}, status=400)

    tab = request.GET.get("tab", "sales").strip().lower()
    bills = report_bills(from_date, to_date).prefetch_related("payments", "items__menu_item", "created_by")

    date_suffix = f"{from_date:%Y-%m-%d}"
    if from_date != to_date:
        date_suffix += f"_to_{to_date:%Y-%m-%d}"

    context = report_context(bills, from_date, to_date, request=request)

    if tab == "items":
        response = render_csv_response(f"cafe_items_{date_suffix}")
        writer = csv.writer(response)
        writer.writerow(["Item Name", "Category", "Quantity Sold", "Revenue", "Cost", "Profit", "Margin %"])
        for itm in context["items_report"]:
            writer.writerow([
                itm["name"],
                itm["category"],
                f"{itm['quantity']:.2f}",
                f"{itm['revenue']:.2f}",
                f"{itm['cost']:.2f}",
                f"{itm['profit']:.2f}",
                f"{itm['margin']:.2f}%",
            ])
        writer.writerow([])
        writer.writerow([
            "Total",
            "",
            f"{context['items_totals']['quantity']:.2f}",
            f"{context['items_totals']['revenue']:.2f}",
            f"{context['items_totals']['cost']:.2f}",
            f"{context['items_totals']['profit']:.2f}",
            f"{context['items_totals']['margin']:.2f}%",
        ])
        return response

    elif tab == "categories":
        response = render_csv_response(f"cafe_categories_{date_suffix}")
        writer = csv.writer(response)
        writer.writerow(["Category", "Quantity Sold", "Revenue", "Cost", "Profit", "Margin %"])
        for cat in context["categories_report"]:
            writer.writerow([
                cat["category"],
                f"{cat['quantity']:.2f}",
                f"{cat['revenue']:.2f}",
                f"{cat['cost']:.2f}",
                f"{cat['profit']:.2f}",
                f"{cat['margin']:.2f}%",
            ])
        writer.writerow([])
        writer.writerow([
            "Total",
            f"{context['categories_totals']['quantity']:.2f}",
            f"{context['categories_totals']['revenue']:.2f}",
            f"{context['categories_totals']['cost']:.2f}",
            f"{context['categories_totals']['profit']:.2f}",
            f"{context['categories_totals']['margin']:.2f}%",
        ])
        return response

    elif tab == "cashiers":
        response = render_csv_response(f"cafe_cashiers_{date_suffix}")
        writer = csv.writer(response)
        writer.writerow(["Cashier", "Bills", "Revenue", "Voids", "Average Bill"])
        for c in context["cashiers_report"]:
            writer.writerow([
                c["cashier_name"],
                c["bills"],
                f"{c['revenue']:.2f}",
                c["voids"],
                f"{c['average_bill']:.2f}",
            ])
        writer.writerow([])
        writer.writerow([
            "Total",
            context["cashiers_totals"]["bills"],
            f"{context['cashiers_totals']['revenue']:.2f}",
            context["cashiers_totals"]["voids"],
            f"{context['cashiers_totals']['average_bill']:.2f}",
        ])
        return response

    elif tab == "hourly":
        response = render_csv_response(f"cafe_hourly_{date_suffix}")
        writer = csv.writer(response)
        writer.writerow(["Hour", "Bills", "Revenue"])
        for h in context["hourly_table"]:
            writer.writerow([
                h["hour_label"],
                h["bills"],
                f"{h['revenue']:.2f}",
            ])
        writer.writerow([])
        writer.writerow([
            "Total",
            sum(h["bills"] for h in context["hourly_table"]),
            f"{sum(h['revenue'] for h in context['hourly_table']):.2f}",
        ])
        return response

    elif tab == "profit":
        response = render_csv_response(f"cafe_profit_{date_suffix}")
        writer = csv.writer(response)
        writer.writerow(["Date", "Revenue", "Cost", "Discounts", "Expenses", "Net Profit", "Margin %"])
        for p in context["profit_report"]:
            writer.writerow([
                p["date"].strftime("%d-%m-%Y"),
                f"{p['revenue']:.2f}",
                f"{p['cost']:.2f}",
                f"{p['discounts']:.2f}",
                f"{p['expenses']:.2f}",
                f"{p['profit']:.2f}",
                f"{p['margin']:.2f}%",
            ])
        writer.writerow([])
        writer.writerow([
            "Total",
            f"{context['profit_totals']['revenue']:.2f}",
            f"{context['profit_totals']['cost']:.2f}",
            f"{context['profit_totals']['discounts']:.2f}",
            f"{context['profit_totals']['expenses']:.2f}",
            f"{context['profit_totals']['profit']:.2f}",
            f"{context['profit_totals']['margin']:.2f}%",
        ])
        return response

    elif tab == "payments":
        response = render_csv_response(f"cafe_payments_{date_suffix}")
        writer = csv.writer(response)
        writer.writerow(["Payment Method", "Amount", "Share %"])
        for p in context["payments_report"]:
            writer.writerow([
                p["label"],
                f"{p['amount']:.2f}",
                f"{p['share']:.2f}%",
            ])
        writer.writerow([])
        writer.writerow(["Total", f"{context['total_payments']:.2f}", "100.00%"])
        return response

    else:
        # Default tab == 'sales'
        response = render_csv_response(f"cafe_sales_{date_suffix}")
        writer = csv.writer(response)
        writer.writerow([
            "Bill Number", "Token", "Date", "Time", "Cashier", "Customer Name", "Item Name", "Category",
            "Quantity", "Unit Price", "Cost Price", "Profit", "Item Total", "Subtotal", "Discount", "Grand Total",
            "Payment Method",
        ])
        for bill in bills:
            customer_name = bill.customer.name if bill.customer else "Walk-in Customer"
            cashier_name = (bill.created_by.get_full_name() or bill.created_by.username) if bill.created_by else "Cashier"
            token_str = str(bill.token_number) if bill.token_number else ""
            payments_list = list(bill.payments.all())
            if payments_list:
                if len(payments_list) > 1 or bill.payment_method == Bill.SPLIT:
                    payment_str = "Split: " + ", ".join(f"{p.get_method_display()} {p.amount:.2f}" for p in payments_list)
                else:
                    payment_str = payments_list[0].get_method_display()
            else:
                payment_str = bill.get_payment_method_display()

            for item in bill.items.all():
                item_profit = (item.price - item.cost_price) * item.quantity
                writer.writerow([
                    bill.bill_number,
                    token_str,
                    timezone.localtime(bill.created_at).strftime("%d-%m-%Y"),
                    timezone.localtime(bill.created_at).strftime("%I:%M %p"),
                    cashier_name,
                    customer_name,
                    item.item_name,
                    item.menu_item.category,
                    item.quantity,
                    item.price,
                    item.cost_price,
                    item_profit,
                    item.total,
                    bill.subtotal,
                    bill.discount,
                    bill.grand_total,
                    payment_str,
                ])

        payment_totals = BillPayment.objects.filter(bill__in=bills).aggregate(
            cash=Sum("amount", filter=Q(method=Bill.CASH)),
            upi=Sum("amount", filter=Q(method=Bill.UPI)),
            card=Sum("amount", filter=Q(method=Bill.CARD)),
            khata=Sum("amount", filter=Q(method=Bill.KHATA)),
        )
        cash_total = payment_totals["cash"] or Decimal("0")
        upi_total = payment_totals["upi"] or Decimal("0")
        card_total = payment_totals["card"] or Decimal("0")
        khata_total = payment_totals["khata"] or Decimal("0")

        unpaid_bills = bills.filter(payments__isnull=True)
        if unpaid_bills.exists():
            legacy_totals = unpaid_bills.aggregate(
                cash=Sum("grand_total", filter=Q(payment_method=Bill.CASH)),
                upi=Sum("grand_total", filter=Q(payment_method=Bill.UPI)),
                card=Sum("grand_total", filter=Q(payment_method=Bill.CARD)),
                khata=Sum("grand_total", filter=Q(payment_method=Bill.KHATA)),
            )
            cash_total += legacy_totals["cash"] or Decimal("0")
            upi_total += legacy_totals["upi"] or Decimal("0")
            card_total += legacy_totals["card"] or Decimal("0")
            khata_total += legacy_totals["khata"] or Decimal("0")

        writer.writerow([])
        writer.writerow(["Payment Totals (BillPayment)"])
        writer.writerow(["Cash", f"{cash_total:.2f}"])
        writer.writerow(["UPI", f"{upi_total:.2f}"])
        writer.writerow(["Card", f"{card_total:.2f}"])
        writer.writerow(["Khata", f"{khata_total:.2f}"])
        writer.writerow(["Grand Total", f"{bills.aggregate(sales=Sum('grand_total'))['sales'] or Decimal('0'):.2f}"])
        return response


def render_csv_response(filename):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    response.write("\ufeff")
    return response


def get_backup_dir():
    try:
        bs = BusinessSettings.current()
        if bs.backup_directory and bs.backup_directory.strip():
            return Path(bs.backup_directory.strip())
    except Exception:
        pass
    return django_settings.BASE_DIR / "backups"


def get_backup_info():
    backup_dir = get_backup_dir()
    if not backup_dir.is_dir():
        return {
            "exists": False,
            "dir": str(backup_dir),
            "dir_exists": False,
        }
    backups = list(backup_dir.glob("db_*.sqlite3"))
    if not backups:
        return {
            "exists": False,
            "dir": str(backup_dir),
            "dir_exists": True,
        }
    latest = max(backups, key=lambda f: (f.stat().st_mtime, f.name))
    stat = latest.stat()
    mtime = timezone.make_aware(
        datetime.fromtimestamp(stat.st_mtime),
        timezone.get_current_timezone(),
    )
    size_bytes = stat.st_size
    if size_bytes < 1024:
        size_str = f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        size_str = f"{size_bytes / 1024:.1f} KB"
    else:
        size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
    return {
        "exists": True,
        "name": latest.name,
        "time": mtime,
        "size_bytes": size_bytes,
        "size_str": size_str,
        "dir": str(backup_dir),
        "dir_exists": True,
        "count": len(backups),
    }


@role_required("Owner")
def settings_page(request):
    current_settings = BusinessSettings.current()
    if request.method == "POST":
        if "backup_now" in request.POST or request.POST.get("action") == "backup_now":
            try:
                call_command("backup_db")
                b_info = get_backup_info()
                log_audit(
                    request,
                    "BACKUP_DB",
                    current_settings,
                    {
                        "filename": b_info.get("name"),
                        "size_bytes": b_info.get("size_bytes"),
                    },
                )
                messages.success(request, f"Database backup created successfully ({b_info.get('size_str', '')}).")
            except Exception as exc:
                messages.error(request, f"Backup failed: {exc}")
            return redirect("settings")

        if "test_print" in request.POST or request.POST.get("action") == "test_print":
            payload = build_test_receipt_bytes(current_settings)
            success, err, out_bytes = print_raw_escpos_bytes(payload, current_settings)
            if success:
                messages.success(request, "Test print sent successfully to thermal printer.")
            else:
                messages.error(request, f"Test print failed: {err}")
            return redirect("settings")

        fields_to_track = [
            "business_name",
            "address",
            "phone",
            "email",
            "tax_number",
            "currency_symbol",
            "invoice_footer",
            "default_receipt_mode",
            "gst_enabled",
            "gst_percent",
            "prices_include_gst",
            "fssai_number",
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
        old_settings = {}
        for f in fields_to_track:
            val = getattr(current_settings, f, None)
            old_settings[f] = str(val) if isinstance(val, Decimal) else val

        form = BusinessSettingsForm(request.POST, instance=current_settings)
        if form.is_valid():
            form.save()
            changes = {}
            for f, old_val in old_settings.items():
                new_val = getattr(current_settings, f, None)
                new_str = str(new_val) if isinstance(new_val, Decimal) else new_val
                if old_val != new_str:
                    changes[f] = [old_val, new_str]
            if changes:
                log_audit(request, "UPDATE", current_settings, changes)
            messages.success(request, "Business settings saved successfully.")
            return redirect("settings")
    else:
        form = BusinessSettingsForm(instance=current_settings)

    last_backup = get_backup_info()
    return render(
        request,
        "pos/settings.html",
        {
            "form": form,
            "business_settings": current_settings,
            "last_backup": last_backup,
            "backup_directory": str(get_backup_dir()),
        },
    )


@role_required("Owner")
def test_print_endpoint(request):
    settings = BusinessSettings.current()
    if not getattr(settings, "printer_enabled", False):
        return JsonResponse({
            "success": False,
            "error": "Thermal printer is disabled in settings.",
            "printer_enabled": False,
        })

    try:
        payload = build_test_receipt_bytes(settings)
        success, err, out_bytes = print_raw_escpos_bytes(payload, settings)
        if success:
            return JsonResponse({
                "success": True,
                "message": "Test receipt sent to printer successfully.",
            })
        else:
            return JsonResponse({
                "success": False,
                "error": err or "Failed to communicate with printer.",
            })
    except Exception as exc:
        return JsonResponse({
            "success": False,
            "error": str(exc),
        })


@role_required("Owner")
def staff_list(request):
    form = StaffCreateForm()
    if request.method == "POST":
        form = StaffCreateForm(request.POST)
        if form.is_valid():
            new_user = form.save_new_user()
            log_audit(
                request,
                "STAFF_CREATE",
                new_user,
                {
                    "username": new_user.username,
                    "role": form.cleaned_data["role"],
                    "is_active": new_user.is_active,
                    "pin_set": bool(form.cleaned_data.get("pin")),
                },
            )
            messages.success(request, f"Staff member '{new_user.username}' created successfully.")
            return redirect("staff_list")

    users = User.objects.prefetch_related("groups", "staff_profile").order_by("username")
    staff_data = []
    for u in users:
        is_owner = u.groups.filter(name="Owner").exists() or u.is_superuser
        is_cashier = u.groups.filter(name="Cashier").exists()
        role = "Owner" if is_owner else ("Cashier" if is_cashier else "No role")
        has_pin = False
        try:
            if hasattr(u, "staff_profile") and u.staff_profile.has_pin():
                has_pin = True
        except Exception:
            has_pin = False
        staff_data.append({
            "user": u,
            "role": role,
            "has_pin": has_pin,
            "is_self": request.user.id == u.id,
        })

    return render(
        request,
        "pos/staff.html",
        {
            "form": form,
            "staff_members": staff_data,
        },
    )


@role_required("Owner")
def staff_edit(request, user_id):
    staff_user = get_object_or_404(User, pk=user_id)
    if request.method == "POST":
        form = StaffEditForm(request.POST, instance_user=staff_user)
        if form.is_valid():
            old_role = form.current_role
            old_is_active = staff_user.is_active
            res = form.save_user()
            log_audit(
                request,
                "STAFF_UPDATE",
                staff_user,
                {
                    "username": [staff_user.username, staff_user.username],
                    "role": [old_role, res["role"]],
                    "is_active": [old_is_active, staff_user.is_active],
                    "password_changed": [False, res["password_changed"]],
                    "pin_changed": [False, res["pin_changed"]],
                },
            )
            messages.success(request, f"Staff member '{staff_user.username}' updated successfully.")
            return redirect("staff_list")
    else:
        form = StaffEditForm(instance_user=staff_user)

    has_pin = False
    try:
        if hasattr(staff_user, "staff_profile") and staff_user.staff_profile.has_pin():
            has_pin = True
    except Exception:
        has_pin = False

    return render(
        request,
        "pos/staff_edit.html",
        {
            "form": form,
            "staff_user": staff_user,
            "has_pin": has_pin,
        },
    )


def sanitize_audit_details(details):
    if not isinstance(details, dict):
        return {}
    sanitized = {}
    for k, v in details.items():
        k_str = str(k)
        k_lower = k_str.lower()
        if k_lower.endswith(("_set", "_changed")) or k_lower.startswith("is_"):
            pass
        elif any(secret in k_lower for secret in ("password", "pin", "secret", "token", "credential")):
            continue
        if isinstance(v, (list, tuple)) and len(v) == 2:
            old_val = str(v[0]) if isinstance(v[0], Decimal) else v[0]
            new_val = str(v[1]) if isinstance(v[1], Decimal) else v[1]
            sanitized[k_str] = [old_val, new_val]
        else:
            val = str(v) if isinstance(v, Decimal) else v
            sanitized[k_str] = val
    return sanitized


def log_audit(request, action, instance, details=None):
    actor = None
    if request and hasattr(request, "user") and request.user.is_authenticated:
        actor = request.user

    sanitized = sanitize_audit_details(details)

    if isinstance(instance, str):
        model_name = instance
        object_repr = instance
    else:
        model_name = instance.__class__.__name__
        object_repr = str(instance)

    return AuditLog.objects.create(
        actor=actor,
        action=action,
        model_name=model_name,
        object_repr=object_repr,
        details=sanitized,
    )


@role_required("Owner")
def audit_logs(request):
    logs = AuditLog.objects.select_related("actor").order_by("-timestamp", "-id")

    user_param = (request.GET.get("user") or request.GET.get("actor") or "").strip()
    action_param = (request.GET.get("action") or "").strip()
    model_param = (request.GET.get("model") or request.GET.get("model_name") or "").strip()
    from_date_str = (request.GET.get("from_date") or request.GET.get("date_from") or "").strip()
    to_date_str = (request.GET.get("to_date") or request.GET.get("date_to") or "").strip()

    if user_param:
        if user_param.isdigit():
            logs = logs.filter(Q(actor_id=int(user_param)) | Q(actor__username__iexact=user_param))
        else:
            logs = logs.filter(actor__username__iexact=user_param)

    if action_param:
        logs = logs.filter(action__iexact=action_param)

    if model_param:
        logs = logs.filter(model_name__iexact=model_param)

    if from_date_str:
        try:
            from_d = parse_date(from_date_str)
            if from_d:
                logs = logs.filter(timestamp__date__gte=from_d)
        except (ValueError, TypeError):
            pass

    if to_date_str:
        try:
            to_d = parse_date(to_date_str)
            if to_d:
                logs = logs.filter(timestamp__date__lte=to_d)
        except (ValueError, TypeError):
            pass

    all_users = User.objects.filter(pos_audit_logs__isnull=False).distinct().order_by("username")
    all_actions = (
        AuditLog.objects.order_by("action")
        .values_list("action", flat=True)
        .distinct()
    )
    all_models = (
        AuditLog.objects.order_by("model_name")
        .values_list("model_name", flat=True)
        .distinct()
    )

    paginator = Paginator(logs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "pos/audit_logs.html",
        {
            "logs": page_obj,
            "page_obj": page_obj,
            "users": all_users,
            "actions": all_actions,
            "models": all_models,
            "selected_user": user_param,
            "selected_action": action_param,
            "selected_model": model_param,
            "from_date": from_date_str,
            "to_date": to_date_str,
        },
    )


# ==========================================
# INVENTORY: Stock Items, Purchases, Suppliers
# ==========================================


@role_required("Owner")
def stock_items(request):
    items = StockItem.objects.all().order_by("name")
    low_stock_only = request.GET.get("filter") == "low" or request.GET.get("low_stock") in ("1", "true")
    negative_only = request.GET.get("filter") == "negative" or request.GET.get("negative") in ("1", "true")

    if low_stock_only:
        items = StockItem.objects.low_stock().order_by("name")
    elif negative_only:
        items = StockItem.objects.negative().order_by("name")

    query = request.GET.get("q", "").strip()
    if query:
        items = items.filter(Q(name__icontains=query) | Q(unit__icontains=query))

    all_items = StockItem.objects.all()
    total_items_count = all_items.count()
    low_stock_items = StockItem.objects.low_stock()
    low_stock_count = low_stock_items.count()
    negative_stock_items = StockItem.objects.negative()
    negative_stock_count = negative_stock_items.count()
    total_inventory_value = sum((item.total_value for item in all_items), Decimal("0.00"))

    # Compute 7-day usage and days-of-cover
    seven_days_ago = timezone.now() - timedelta(days=7)
    product_sold_qtys = dict(
        BillItem.objects.filter(
            bill__created_at__gte=seven_days_ago,
            bill__status=Bill.STATUS_ACTIVE,
        )
        .values("menu_item_id")
        .annotate(total_qty=Sum("quantity"))
        .values_list("menu_item_id", "total_qty")
    )
    usage_7d = defaultdict(Decimal)
    if product_sold_qtys:
        recipe_lines = RecipeLine.objects.filter(
            product_id__in=product_sold_qtys.keys()
        ).values("stock_item_id", "product_id", "qty_per_unit")
        for rl in recipe_lines:
            p_qty = product_sold_qtys.get(rl["product_id"], Decimal("0.00"))
            usage_7d[rl["stock_item_id"]] += rl["qty_per_unit"] * p_qty

    paginator = Paginator(items, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    for item in page_obj:
        used = usage_7d.get(item.id, Decimal("0.00"))
        daily_use = (used / Decimal("7")).quantize(Decimal("0.0001"))
        item.usage_7d = used
        item.daily_use = daily_use
        if daily_use > Decimal("0.00"):
            if item.current_qty <= Decimal("0.00"):
                item.days_cover = Decimal("0.0")
            else:
                item.days_cover = (item.current_qty / daily_use).quantize(Decimal("0.1"))
        else:
            item.days_cover = None

    form = StockItemForm()
    return render(
        request,
        "pos/stock_list.html",
        {
            "items": page_obj,
            "page_obj": page_obj,
            "stock_items": page_obj,
            "low_stock_items": low_stock_items,
            "low_stock_only": low_stock_only,
            "low_stock_count": low_stock_count,
            "negative_only": negative_only,
            "negative_stock_count": negative_stock_count,
            "total_items_count": total_items_count,
            "total_inventory_value": total_inventory_value,
            "query": query,
            "form": form,
        },
    )


@role_required("Owner")
def stock_item_create(request):
    form = StockItemForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        item = form.save()
        log_audit(
            request,
            "CREATE",
            item,
            {
                "name": [None, item.name],
                "unit": [None, item.unit],
                "current_qty": [None, str(item.current_qty)],
                "reorder_level": [None, str(item.reorder_level)],
                "cost_per_unit": [None, str(item.cost_per_unit)],
            },
        )
        messages.success(request, f"Stock item '{item.name}' added successfully.")
        return redirect("stock_items")
    return render(
        request,
        "pos/stock_item_form.html",
        {
            "form": form,
            "page_title": "Add Stock Item",
            "submit_label": "Add Stock Item",
        },
    )


@role_required("Owner")
def stock_item_edit(request, item_id):
    item = get_object_or_404(StockItem, pk=item_id)
    old_name = item.name
    old_unit = item.unit
    old_qty = str(item.current_qty)
    old_reorder = str(item.reorder_level)
    old_cost = str(item.cost_per_unit)

    form = StockItemForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        form.save()
        changes = {}
        if item.name != old_name:
            changes["name"] = [old_name, item.name]
        if item.unit != old_unit:
            changes["unit"] = [old_unit, item.unit]
        if str(item.current_qty) != old_qty:
            changes["current_qty"] = [old_qty, str(item.current_qty)]
        if str(item.reorder_level) != old_reorder:
            changes["reorder_level"] = [old_reorder, str(item.reorder_level)]
        if str(item.cost_per_unit) != old_cost:
            changes["cost_per_unit"] = [old_cost, str(item.cost_per_unit)]

        if changes:
            log_audit(request, "UPDATE", item, changes)
        messages.success(request, f"Stock item '{item.name}' updated successfully.")
        return redirect("stock_items")
    return render(
        request,
        "pos/stock_item_form.html",
        {
            "form": form,
            "item": item,
            "page_title": "Edit Stock Item",
            "submit_label": "Save Changes",
        },
    )


@role_required("Owner")
def stock_item_delete(request, item_id):
    item = get_object_or_404(StockItem, pk=item_id)
    if request.method == "POST":
        if item.purchase_lines.exists():
            messages.error(
                request,
                f"Cannot delete stock item '{item.name}' because it has recorded purchase transactions.",
            )
            return redirect("stock_items")
        name = item.name
        item.delete()
        log_audit(request, "DELETE", item, {"name": [name, None]})
        messages.success(request, f"Stock item '{name}' deleted.")
    return redirect("stock_items")


@role_required("Owner")
def purchase_list(request):
    purchases = Purchase.objects.select_related("supplier", "expense").prefetch_related("lines__stock_item").order_by("-date", "-id")

    supplier_filter = request.GET.get("supplier", "").strip()
    if supplier_filter and supplier_filter.isdigit():
        purchases = purchases.filter(supplier_id=int(supplier_filter))

    from_date = parse_date_param(request, "from_date") or parse_date_param(request, "date_from")
    if from_date:
        purchases = purchases.filter(date__gte=from_date)

    to_date = parse_date_param(request, "to_date") or parse_date_param(request, "date_to")
    if to_date:
        purchases = purchases.filter(date__lte=to_date)

    method_filter = request.GET.get("method", "").strip()
    if method_filter:
        purchases = purchases.filter(method=method_filter)

    due_only = request.GET.get("due_only") in ("1", "true")
    if due_only:
        p_ids = [p.id for p in purchases if p.due_amount > Decimal("0.00")]
        purchases = purchases.filter(id__in=p_ids)

    all_filtered = list(purchases)
    total_purchase_amount = sum((p.total_amount for p in all_filtered), Decimal("0.00"))
    total_paid_amount = sum((p.paid_amount for p in all_filtered), Decimal("0.00"))
    total_due_amount = sum((p.due_amount for p in all_filtered), Decimal("0.00"))

    paginator = Paginator(purchases, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    suppliers = Supplier.objects.all().order_by("name")
    return render(
        request,
        "pos/purchase_list.html",
        {
            "purchases": page_obj,
            "page_obj": page_obj,
            "suppliers": suppliers,
            "selected_supplier": supplier_filter,
            "from_date": request.GET.get("from_date", ""),
            "to_date": request.GET.get("to_date", ""),
            "selected_method": method_filter,
            "due_only": due_only,
            "total_purchase_amount": total_purchase_amount,
            "total_paid_amount": total_paid_amount,
            "total_due_amount": total_due_amount,
        },
    )


@role_required("Owner")
def purchase_create(request):
    open_shift = Shift.current_open()
    stock_items = StockItem.objects.all().order_by("name")
    suppliers = Supplier.objects.all().order_by("name")

    if request.method == "POST":
        form = PurchaseForm(request.POST)

        raw_lines = []
        if request.content_type == "application/json" and request.body:
            try:
                body_data = json.loads(request.body)
                raw_lines = body_data.get("lines", [])
                if not form.is_bound and isinstance(body_data, dict):
                    form = PurchaseForm(body_data)
            except Exception:
                pass

        if not raw_lines:
            lines_json = request.POST.get("lines_json", "").strip()
            if lines_json:
                try:
                    raw_lines = json.loads(lines_json)
                except Exception:
                    pass

        if not raw_lines:
            item_ids = request.POST.getlist("item_id[]") or request.POST.getlist("item_id") or request.POST.getlist("stock_item[]") or request.POST.getlist("stock_item")
            qtys = request.POST.getlist("qty[]") or request.POST.getlist("qty")
            costs = request.POST.getlist("unit_cost[]") or request.POST.getlist("unit_cost")
            for i in range(len(item_ids)):
                if i < len(qtys) and i < len(costs):
                    raw_lines.append({
                        "stock_item_id": item_ids[i],
                        "qty": qtys[i],
                        "unit_cost": costs[i],
                    })

        parsed_lines = []
        line_errors = []

        if not raw_lines:
            line_errors.append("At least one purchase line item is required.")

        for idx, row in enumerate(raw_lines, 1):
            s_id = row.get("stock_item_id") or row.get("stock_item") or row.get("item_id")
            if not s_id:
                line_errors.append(f"Line {idx}: Stock item is required.")
                continue
            try:
                stock_item_obj = StockItem.objects.get(pk=int(s_id))
            except (StockItem.DoesNotExist, ValueError, TypeError):
                line_errors.append(f"Line {idx}: Invalid stock item.")
                continue

            try:
                qty = Decimal(str(row.get("qty", "")))
                if qty <= Decimal("0"):
                    line_errors.append(f"Line {idx}: Quantity must be greater than zero.")
                    continue
            except (InvalidOperation, ValueError, TypeError):
                line_errors.append(f"Line {idx}: Invalid quantity.")
                continue

            try:
                unit_cost = Decimal(str(row.get("unit_cost", "")))
                if unit_cost < Decimal("0"):
                    line_errors.append(f"Line {idx}: Unit cost cannot be negative.")
                    continue
            except (InvalidOperation, ValueError, TypeError):
                line_errors.append(f"Line {idx}: Invalid unit cost.")
                continue

            parsed_lines.append({
                "stock_item": stock_item_obj,
                "qty": qty,
                "unit_cost": unit_cost,
            })

        if line_errors:
            for err in line_errors:
                messages.error(request, err)
            if request.content_type == "application/json" or request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"error": line_errors[0], "errors": line_errors}, status=400)
            return render(
                request,
                "pos/purchase_form.html",
                {
                    "form": form,
                    "stock_items": stock_items,
                    "suppliers": suppliers,
                    "open_shift": open_shift,
                    "page_title": "New Purchase",
                },
            )

        if form.is_valid():
            with transaction.atomic():
                purchase = form.save()
                for pline in parsed_lines:
                    PurchaseLine.objects.create(
                        purchase=purchase,
                        stock_item=pline["stock_item"],
                        qty=pline["qty"],
                        unit_cost=pline["unit_cost"],
                    )

                post_to_drawer = form.cleaned_data.get("post_to_drawer") or request.POST.get("post_to_drawer") in ("1", "true", "on")
                if post_to_drawer:
                    purchase.post_drawer_expense_if_eligible()

                log_audit(
                    request,
                    "PURCHASE_CREATE",
                    purchase,
                    {
                        "supplier": [None, purchase.supplier.name],
                        "total_amount": [None, str(purchase.total_amount)],
                        "paid_amount": [None, str(purchase.paid_amount)],
                        "due_amount": [None, str(purchase.due_amount)],
                        "method": [None, purchase.method],
                        "lines_count": [None, len(parsed_lines)],
                    },
                )

            messages.success(request, f"Purchase #{purchase.id} recorded successfully.")
            if request.content_type == "application/json" or request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"ok": True, "purchase_id": purchase.id})
            return redirect("purchases")
        else:
            if request.content_type == "application/json" or request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"error": "Invalid purchase form data", "errors": form.errors}, status=400)
    else:
        form = PurchaseForm(initial={"date": timezone.localdate()})

    return render(
        request,
        "pos/purchase_form.html",
        {
            "form": form,
            "stock_items": stock_items,
            "suppliers": suppliers,
            "open_shift": open_shift,
            "page_title": "New Purchase",
        },
    )


@role_required("Owner")
def purchase_detail(request, purchase_id):
    purchase = get_object_or_404(
        Purchase.objects.select_related("supplier", "expense").prefetch_related("lines__stock_item"),
        pk=purchase_id,
    )
    return render(
        request,
        "pos/purchase_detail.html",
        {
            "purchase": purchase,
            "lines": purchase.lines.all(),
        },
    )


@role_required("Owner")
def supplier_list(request):
    suppliers = Supplier.objects.all().prefetch_related("purchases__lines").order_by("name")
    query = request.GET.get("q", "").strip()
    if query:
        suppliers = suppliers.filter(Q(name__icontains=query) | Q(phone__icontains=query))

    dues_only = request.GET.get("dues_only") in ("1", "true") or request.GET.get("dues") == "1"
    if dues_only:
        s_ids = [s.id for s in suppliers if s.outstanding_dues > Decimal("0.00")]
        suppliers = suppliers.filter(id__in=s_ids)

    all_suppliers = list(suppliers)
    total_suppliers_count = len(all_suppliers)
    total_purchases_sum = sum((s.total_purchases for s in all_suppliers), Decimal("0.00"))
    total_paid_sum = sum((s.total_paid for s in all_suppliers), Decimal("0.00"))
    total_dues_sum = sum((s.outstanding_dues for s in all_suppliers), Decimal("0.00"))

    paginator = Paginator(suppliers, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    form = SupplierForm()
    return render(
        request,
        "pos/supplier_list.html",
        {
            "suppliers": page_obj,
            "page_obj": page_obj,
            "query": query,
            "dues_only": dues_only,
            "total_suppliers_count": total_suppliers_count,
            "total_purchases_sum": total_purchases_sum,
            "total_paid_sum": total_paid_sum,
            "total_dues_sum": total_dues_sum,
            "form": form,
        },
    )


@role_required("Owner")
def supplier_create(request):
    form = SupplierForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        supplier = form.save()
        log_audit(
            request,
            "CREATE",
            supplier,
            {
                "name": [None, supplier.name],
                "phone": [None, supplier.phone],
            },
        )
        messages.success(request, f"Supplier '{supplier.name}' added successfully.")
        return redirect("suppliers")
    return render(
        request,
        "pos/supplier_form.html",
        {
            "form": form,
            "page_title": "Add Supplier",
            "submit_label": "Add Supplier",
        },
    )


@role_required("Owner")
def supplier_detail(request, supplier_id):
    supplier = get_object_or_404(Supplier, pk=supplier_id)
    purchases = supplier.purchases.prefetch_related("lines__stock_item").order_by("-date", "-id")
    return render(
        request,
        "pos/supplier_detail.html",
        {
            "supplier": supplier,
            "purchases": purchases,
        },
    )


@role_required("Owner")
def supplier_edit(request, supplier_id):
    supplier = get_object_or_404(Supplier, pk=supplier_id)
    old_name = supplier.name
    old_phone = supplier.phone
    old_notes = supplier.notes

    form = SupplierForm(request.POST or None, instance=supplier)
    if request.method == "POST" and form.is_valid():
        form.save()
        changes = {}
        if supplier.name != old_name:
            changes["name"] = [old_name, supplier.name]
        if supplier.phone != old_phone:
            changes["phone"] = [old_phone, supplier.phone]
        if supplier.notes != old_notes:
            changes["notes"] = [old_notes, supplier.notes]
        if changes:
            log_audit(request, "UPDATE", supplier, changes)
        messages.success(request, f"Supplier '{supplier.name}' updated successfully.")
        return redirect("supplier_detail", supplier.id)
    return render(
        request,
        "pos/supplier_form.html",
        {
            "form": form,
            "supplier": supplier,
            "page_title": "Edit Supplier",
            "submit_label": "Save Changes",
        },
    )


@role_required("Cashier", "Owner")
def wastage_list(request):
    is_owner = request.user.is_superuser or request.user.groups.filter(name="Owner").exists()

    wastage_qs = Wastage.objects.select_related("product", "stock_item", "recorded_by").all()

    # Reason filter
    reason_filter = request.GET.get("reason", "").strip().upper()
    if reason_filter and reason_filter in dict(Wastage.REASON_CHOICES):
        wastage_qs = wastage_qs.filter(reason=reason_filter)

    # Item type filter
    item_type_filter = request.GET.get("item_type", "").strip().lower()
    if item_type_filter == "product":
        wastage_qs = wastage_qs.filter(product__isnull=False)
    elif item_type_filter == "stock_item":
        wastage_qs = wastage_qs.filter(stock_item__isnull=False)

    # Date range filters
    from_date = parse_date_param(request, "from_date")
    to_date = parse_date_param(request, "to_date")
    if from_date:
        wastage_qs = wastage_qs.filter(created_at__date__gte=from_date)
    if to_date:
        wastage_qs = wastage_qs.filter(created_at__date__lte=to_date)

    # Search filter
    q = request.GET.get("q", "").strip()
    if q:
        wastage_qs = wastage_qs.filter(
            Q(product__name__icontains=q)
            | Q(stock_item__name__icontains=q)
            | Q(note__icontains=q)
            | Q(recorded_by__username__icontains=q)
        )

    # Summary metrics
    aggregates = wastage_qs.aggregate(
        total_qty=Sum("quantity"),
        total_cost=Sum("cost"),
        count=Count("id"),
    )
    total_qty = aggregates["total_qty"] or Decimal("0.00")
    total_cost = aggregates["total_cost"] or Decimal("0.00")
    total_count = aggregates["count"] or 0

    paginator = Paginator(wastage_qs, 30)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    form = WastageForm(is_owner=is_owner)

    return render(
        request,
        "pos/wastage.html",
        {
            "page_obj": page_obj,
            "form": form,
            "total_qty": total_qty,
            "total_cost": total_cost,
            "total_count": total_count,
            "reason_choices": Wastage.REASON_CHOICES,
            "selected_reason": reason_filter,
            "selected_item_type": item_type_filter,
            "from_date": from_date,
            "to_date": to_date,
            "query": q,
            "is_owner": is_owner,
            "business_settings": BusinessSettings.current(),
        },
    )


@role_required("Cashier", "Owner")
def wastage_create(request):
    is_owner = request.user.is_superuser or request.user.groups.filter(name="Owner").exists()

    if request.method == "GET":
        form = WastageForm(is_owner=is_owner)
        return render(
            request,
            "pos/wastage.html",
            {
                "page_obj": Paginator(Wastage.objects.none(), 30).get_page(1),
                "form": form,
                "total_qty": Decimal("0.00"),
                "total_cost": Decimal("0.00"),
                "total_count": 0,
                "reason_choices": Wastage.REASON_CHOICES,
                "selected_reason": "",
                "selected_item_type": "",
                "from_date": None,
                "to_date": None,
                "query": "",
                "is_owner": is_owner,
                "business_settings": BusinessSettings.current(),
            },
        )

    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])

    # Owner-only check for corrections or negative quantity
    raw_reason = request.POST.get("reason", "").strip().upper()
    try:
        raw_qty = Decimal(str(request.POST.get("quantity", "0")).strip())
    except Exception:
        raw_qty = None

    if not is_owner and (raw_reason == Wastage.REASON_CORRECTION or (raw_qty is not None and raw_qty <= Decimal("0.00"))):
        return HttpResponseForbidden("Only owners can log correction entries.")

    form = WastageForm(request.POST, is_owner=is_owner)
    if not form.is_valid():
        err_msg = "; ".join([f"{f}: {', '.join(errs)}" for f, errs in form.errors.items()])
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
            return JsonResponse({"error": err_msg}, status=400)
        messages.error(request, err_msg)
        return redirect("wastage")

    product = form.cleaned_data.get("product")
    stock_item = form.cleaned_data.get("stock_item")
    quantity = form.cleaned_data.get("quantity")
    reason = form.cleaned_data.get("reason")
    note = form.cleaned_data.get("note", "").strip()

    # If reason is CORRECTION and quantity entered as positive, negate it
    if reason == Wastage.REASON_CORRECTION:
        qty = -abs(quantity)
    elif quantity < Decimal("0.00"):
        qty = quantity
        reason = Wastage.REASON_CORRECTION
    else:
        qty = quantity

    with transaction.atomic():
        if product:
            # Current cost price snapshot
            unit_cost = product.cost_price or Decimal("0.00")
            cost = (qty * unit_cost).quantize(TWO_PLACES)

            # Deduct stock if tracked
            if product.stock_tracked:
                Product.objects.filter(pk=product.pk).update(stock=F("stock") - qty)
                product.refresh_from_db(fields=["stock"])

            # Deduct ingredient stock if product has recipe
            if product.has_recipe:
                rlines = list(product.recipe_lines.all())
                sid_list = sorted([rl.stock_item_id for rl in rlines])
                locked_sis = {si.pk: si for si in StockItem.objects.select_for_update().filter(id__in=sid_list)}
                for rl in rlines:
                    si = locked_sis.get(rl.stock_item_id)
                    if si:
                        si.current_qty = (si.current_qty - (rl.qty_per_unit * qty)).quantize(TWO_PLACES)
                        si.save(update_fields=["current_qty"])

            wastage = Wastage.objects.create(
                product=product,
                quantity=qty,
                cost=cost,
                reason=reason,
                note=note,
                recorded_by=request.user if request.user.is_authenticated else None,
            )
        else:
            # Raw material stock item
            unit_cost = stock_item.cost_per_unit or Decimal("0.00")
            cost = (qty * unit_cost).quantize(TWO_PLACES)

            # Deduct raw material stock
            si = StockItem.objects.select_for_update().get(pk=stock_item.pk)
            si.current_qty = (si.current_qty - qty).quantize(TWO_PLACES)
            si.save(update_fields=["current_qty"])

            wastage = Wastage.objects.create(
                stock_item=si,
                quantity=qty,
                cost=cost,
                reason=reason,
                note=note,
                recorded_by=request.user if request.user.is_authenticated else None,
            )

        log_audit(
            request,
            "CREATE",
            wastage,
            {
                "item": [None, wastage.item_name],
                "quantity": [None, str(qty)],
                "cost": [None, str(cost)],
                "reason": [None, reason],
            },
        )

    business_settings = BusinessSettings.current()
    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
        return JsonResponse({
            "ok": True,
            "id": wastage.id,
            "item_name": wastage.item_name,
            "quantity": str(qty),
            "cost": str(cost),
            "reason": wastage.reason,
        })

    messages.success(
        request,
        f"Wastage logged: {wastage.item_name} x {qty} ({wastage.get_reason_display()}) - {business_settings.currency_symbol}{cost:.2f}",
    )
    return redirect("wastage")


def set_language_view(request):
    lang_code = request.POST.get("language") or request.GET.get("language")
    next_url = (
        request.POST.get("next")
        or request.GET.get("next")
        or request.META.get("HTTP_REFERER")
        or "/"
    )
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = "/"

    valid_languages = [code for code, _ in django_settings.LANGUAGES]
    if lang_code and lang_code in valid_languages:
        from django.utils import translation
        translation.activate(lang_code)

        if hasattr(request, "session"):
            request.session["_language"] = lang_code

        if request.user.is_authenticated:
            try:
                profile = request.user.staff_profile
            except StaffProfile.DoesNotExist:
                profile = StaffProfile.objects.create(user=request.user)
            profile.language = lang_code
            profile.save(update_fields=["language"])

        response = redirect(next_url)
        response.set_cookie(
            django_settings.LANGUAGE_COOKIE_NAME,
            lang_code,
            max_age=365 * 24 * 60 * 60,
        )
        return response

    return redirect(next_url)


def service_worker_view(request):
    sw_path = django_settings.BASE_DIR / "pos" / "static" / "pos" / "sw.js"
    try:
        with open(sw_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        content = ""
    response = HttpResponse(content, content_type="application/javascript")
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Service-Worker-Allowed"] = "/"
    return response


def manifest_view(request):
    manifest_path = django_settings.BASE_DIR / "pos" / "static" / "pos" / "manifest.webmanifest"
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        content = "{}"
    response = HttpResponse(content, content_type="application/manifest+json")
    response["Cache-Control"] = "public, max-age=3600"
    return response


def offline_view(request):
    return render(request, "pos/offline.html")


@role_required("Owner")
def daily_summary(request):
    date_str = request.GET.get("date", "").strip()
    today = timezone.localdate()
    target_date = today
    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date = today

    # 1. Bills & Sales on target_date
    bills = Bill.objects.filter(
        created_at__date=target_date,
        status=Bill.STATUS_ACTIVE,
    )
    total_bills = bills.count()
    total_sales = bills.aggregate(s=Sum("grand_total"))["s"] or Decimal("0.00")

    # 2. Payments Breakdown (Cash / UPI / Khata / Card)
    payment_totals = BillPayment.objects.filter(bill__in=bills).aggregate(
        cash=Sum("amount", filter=Q(method=Bill.CASH)),
        upi=Sum("amount", filter=Q(method=Bill.UPI)),
        card=Sum("amount", filter=Q(method=Bill.CARD)),
        khata=Sum("amount", filter=Q(method=Bill.KHATA)),
    )
    cash_sales = payment_totals["cash"] or Decimal("0.00")
    upi_sales = payment_totals["upi"] or Decimal("0.00")
    card_sales = payment_totals["card"] or Decimal("0.00")
    khata_sales = payment_totals["khata"] or Decimal("0.00")

    unpaid_bills = bills.filter(payments__isnull=True)
    if unpaid_bills.exists():
        legacy_totals = unpaid_bills.aggregate(
            cash=Sum("grand_total", filter=Q(payment_method=Bill.CASH)),
            upi=Sum("grand_total", filter=Q(payment_method=Bill.UPI)),
            card=Sum("grand_total", filter=Q(payment_method=Bill.CARD)),
            khata=Sum("grand_total", filter=Q(payment_method=Bill.KHATA)),
        )
        cash_sales += legacy_totals["cash"] or Decimal("0.00")
        upi_sales += legacy_totals["upi"] or Decimal("0.00")
        card_sales += legacy_totals["card"] or Decimal("0.00")
        khata_sales += legacy_totals["khata"] or Decimal("0.00")

    # 3. Expenses on target_date
    total_expenses = Expense.objects.filter(
        expense_date=target_date,
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0.00")

    # 4. COGS & Wastage & Net Profit
    bill_items_qs = BillItem.objects.filter(bill__in=bills).annotate(
        line_cost=ExpressionWrapper(
            F("cost_price") * F("quantity"),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        ),
    )
    total_cogs = bill_items_qs.aggregate(s=Sum("line_cost"))["s"] or Decimal("0.00")
    wastage_cost = Wastage.objects.filter(
        created_at__date=target_date,
    ).aggregate(s=Sum("cost"))["s"] or Decimal("0.00")

    net_profit = total_sales - total_cogs - total_expenses - wastage_cost

    # 5. Top 3 Items
    top_items_raw = list(
        bill_items_qs.values("item_name")
        .annotate(
            total_qty=Sum("quantity"),
            total_rev=Sum("total"),
        )
        .order_by("-total_qty", "-total_rev")[:3]
    )

    top_items = []
    top_items_text_list = []
    for idx, item in enumerate(top_items_raw, 1):
        qty = item["total_qty"] or Decimal("0.00")
        qty_str = f"{qty:g}" if isinstance(qty, Decimal) else str(qty)
        top_items.append({
            "rank": idx,
            "name": item["item_name"],
            "quantity": qty,
            "revenue": item["total_rev"] or Decimal("0.00"),
        })
        top_items_text_list.append(f"{idx}. {item['item_name']} ({qty_str})")

    top_items_str = ", ".join(top_items_text_list) if top_items_text_list else "None"

    # 6. Dues (Customer Khata dues & Supplier dues)
    customer_dues = (
        Customer.objects.with_outstanding()
        .filter(outstanding__gt=0)
        .aggregate(s=Sum("outstanding"))["s"]
        or Decimal("0.00")
    )
    supplier_dues = sum(
        (s.outstanding_dues for s in Supplier.objects.all()),
        Decimal("0.00"),
    )
    total_dues = customer_dues + supplier_dues

    # 7. Formatted Summary Text
    formatted_date = target_date.strftime("%d %b %Y")
    summary_text = (
        f"📊 Cafe POS Daily Summary ({formatted_date})\n"
        f"Sales: ₹{total_sales:.2f} ({total_bills} bills)\n"
        f"Payments: Cash ₹{cash_sales:.2f} | UPI ₹{upi_sales:.2f} | Khata ₹{khata_sales:.2f}\n"
        f"Expenses: ₹{total_expenses:.2f}\n"
        f"Net Profit: ₹{net_profit:.2f}\n"
        f"Top Items: {top_items_str}\n"
        f"Dues: Khata ₹{customer_dues:.2f} | Supplier ₹{supplier_dues:.2f} (Total: ₹{total_dues:.2f})"
    )

    # 8. WhatsApp Link (using owner's phone from settings)
    bs = BusinessSettings.current()
    raw_phone = bs.phone or ""
    clean_phone = re.sub(r"[^\d]", "", raw_phone)
    encoded_text = urllib.parse.quote(summary_text)
    if clean_phone:
        whatsapp_url = f"https://wa.me/{clean_phone}?text={encoded_text}"
    else:
        whatsapp_url = f"https://wa.me/?text={encoded_text}"

    context = {
        "target_date": target_date,
        "is_today": (target_date == today),
        "total_bills": total_bills,
        "total_sales": total_sales,
        "cash_sales": cash_sales,
        "upi_sales": upi_sales,
        "card_sales": card_sales,
        "khata_sales": khata_sales,
        "total_expenses": total_expenses,
        "total_cogs": total_cogs,
        "wastage_cost": wastage_cost,
        "net_profit": net_profit,
        "top_items": top_items,
        "customer_dues": customer_dues,
        "supplier_dues": supplier_dues,
        "total_dues": total_dues,
        "summary_text": summary_text,
        "whatsapp_url": whatsapp_url,
        "owner_phone": raw_phone,
        "clean_phone": clean_phone,
    }
    return render(request, "pos/daily_summary.html", context)



