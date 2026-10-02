import csv
import json
from functools import wraps
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
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.http import HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from django.utils import timezone
from django.utils.dateparse import parse_date
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

from .forms import BusinessSettingsForm, CustomerForm, ExpenseForm, MenuItemForm
from .models import (
    Bill,
    BillItem,
    Customer,
    Expense,
    KhataTransaction,
    AuditLog,
    Product,
    BusinessSettings,
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
            return HttpResponseForbidden("You do not have permission to access this page.")

        return wrapped

    return decorator


@role_required("Cashier", "Owner")
def pos(request):
    return redirect("billing")


@role_required("Cashier", "Owner")
def billing(request):
    products = Product.objects.filter(is_active=True)
    categories = products.values_list("category", flat=True).distinct().order_by("category")
    return render(request, "pos/billing.html", {
        "products": products,
        "categories": categories,
        "customers": Customer.objects.all(),
        "payment_methods": Bill.PAYMENT_CHOICES,
        "business_settings": BusinessSettings.current(),
    })


@role_required("Cashier", "Owner")
def customer_search(request):
    query = request.GET.get("q", "").strip()
    customers = Customer.objects.all()
    if query:
        customers = customers.filter(Q(name__icontains=query) | Q(phone__icontains=query))
    return JsonResponse({"customers": [{"id": c.id, "name": c.name, "phone": c.phone} for c in customers[:10]]})


@role_required("Cashier", "Owner")
def bill_create(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

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

    payment_method = data.get("payment_method")
    if payment_method not in dict(Bill.PAYMENT_CHOICES):
        return JsonResponse({"error": "Select a valid payment method."}, status=400)

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

    if payment_method == Bill.KHATA and customer is None:
        return JsonResponse({"error": "A customer is required for Khata credit checkout."}, status=400)

    raw_discount = data.get("discount", "0")
    if raw_discount is None or raw_discount == "":
        raw_discount = "0"
    try:
        discount = parse_decimal(raw_discount, min_value=Decimal("0.00"), max_value=Decimal("99999999.99"))
    except ValueError:
        return JsonResponse({"error": "Invalid discount."}, status=400)

    merged_items = {}
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

        merged_items[product_id] = merged_items.get(product_id, Decimal("0.00")) + qty
        if merged_items[product_id] > Decimal("9999.00"):
            return JsonResponse({"error": "Quantity cannot exceed 9999."}, status=400)

    if len(merged_items) > 100:
        return JsonResponse({"error": "Cannot exceed 100 items per bill."}, status=400)

    sorted_product_ids = sorted(merged_items.keys())
    with transaction.atomic():
        products_query = Product.objects.select_for_update().filter(
            pk__in=sorted_product_ids,
            is_active=True,
        )
        products_by_id = {p.pk: p for p in products_query}

        for p_id in sorted_product_ids:
            if p_id not in products_by_id:
                return JsonResponse({"error": "An item is unavailable and was not added."}, status=400)

        for p_id in sorted_product_ids:
            product = products_by_id[p_id]
            req_qty = merged_items[p_id]
            if product.stock < req_qty:
                return JsonResponse(
                    {
                        "error": f"Insufficient stock for {product.name}. Available: {product.stock}"
                    },
                    status=400,
                )

        validated_items = []
        subtotal = Decimal("0.00")
        for p_id in sorted_product_ids:
            product = products_by_id[p_id]
            quantity = merged_items[p_id]
            line_total = (product.price * quantity).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            subtotal += line_total
            validated_items.append((product, quantity, line_total))

        if discount > subtotal:
            return JsonResponse({"error": "Discount cannot exceed the subtotal."}, status=400)

        grand_total = (subtotal - discount).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

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

        bill = Bill.objects.create(
            bill_number=bill_number,
            customer=customer,
            created_by=request.user if request.user.is_authenticated else None,
            subtotal=subtotal,
            discount=discount,
            grand_total=grand_total,
            payment_method=payment_method,
        )

        for product, quantity, line_total in validated_items:
            BillItem.objects.create(
                bill=bill,
                menu_item=product,
                item_name=product.name,
                shortcut_key=product.shortcut_key,
                price=product.price,
                cost_price=product.cost_price,
                quantity=quantity,
                total=line_total,
            )
            Product.objects.filter(pk=product.pk).update(stock=F("stock") - quantity)

        if payment_method == Bill.KHATA:
            KhataTransaction.objects.create(
                customer=customer,
                bill=bill,
                kind=KhataTransaction.CREDIT,
                amount=grand_total,
                note=f"Credit sale: {bill.bill_number}",
            )

    return JsonResponse({
        "ok": True,
        "bill_id": bill.id,
        "bill_number": bill.bill_number,
        "total": f"{bill.grand_total:.2f}",
        "detail_url": f"{bill.get_absolute_url() if hasattr(bill, 'get_absolute_url') else f'/billing/{bill.id}/'}?print=1",
    })


@role_required("Cashier", "Owner")
def billing_history(request):
    query = request.GET.get("q", "").strip()
    raw_date = request.GET.get("date", "").strip()
    parsed_date = parse_date_param(request, "date")
    bills = Bill.objects.select_related("customer").order_by("-created_at", "-id")
    if query:
        bills = bills.filter(Q(bill_number__icontains=query) | Q(customer__name__icontains=query))
    if parsed_date:
        bills = bills.filter(created_at__date=parsed_date)
    paginator = Paginator(bills, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "pos/billing_history.html", {
        "bills": page_obj,
        "page_obj": page_obj,
        "query": query,
        "selected_date": raw_date if parsed_date else "",
    })


@role_required("Cashier", "Owner")
def bill_detail(request, bill_id):
    bill = get_object_or_404(Bill.objects.select_related("customer").prefetch_related("items"), pk=bill_id)
    mode = request.GET.get("mode", "a4").lower()
    if mode not in {"a4", "thermal", "thermal58"}:
        mode = "a4"
    return render(request, "pos/bill_detail.html", {
        "bill": bill,
        "business_settings": BusinessSettings.current(),
        "auto_print": request.GET.get("print") == "1",
        "receipt_mode": mode,
    })


@role_required("Owner")
def bill_delete(request, bill_id):
    if request.method == "POST":
        with transaction.atomic():
            bill = get_object_or_404(Bill.objects.prefetch_related("items__menu_item"), pk=bill_id)
            for item in bill.items.all():
                Product.objects.filter(pk=item.menu_item_id).update(stock=F("stock") + item.quantity)
            is_khata = (bill.payment_method == Bill.KHATA)
            log_audit(
                request,
                "DELETE",
                bill,
                {
                    "grand_total": str(bill.grand_total),
                    "restored_items": bill.items.count(),
                    "khata_credit_reversed": is_khata,
                },
            )
            bill.delete()
        messages.success(request, "Bill deleted.")
    return redirect("billing_history")


@role_required("Owner")
def menu_items(request):
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    items = Product.objects.all()
    if query:
        items = items.filter(name__icontains=query)
    if category:
        items = items.filter(category=category)

    categories = Product.objects.exclude(category="").values_list(
        "category", flat=True
    ).distinct().order_by("category")
    return render(request, "pos/menu_items.html", {
        "items": items,
        "categories": categories,
        "query": query,
        "selected_category": category,
    })


@role_required("Owner")
def menu_item_create(request):
    form = MenuItemForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Menu item added successfully.")
        return redirect("menu_items")
    return render(request, "pos/menu_item_form.html", {
        "form": form,
        "page_title": "Add Menu Item",
        "submit_label": "Add Menu Item",
    })


@role_required("Owner")
def menu_item_edit(request, item_id):
    item = get_object_or_404(Product, pk=item_id)
    form = MenuItemForm(request.POST or None, request.FILES or None, instance=item)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Menu item updated successfully.")
        return redirect("menu_items")
    return render(request, "pos/menu_item_form.html", {
        "form": form,
        "item": item,
        "page_title": "Edit Menu Item",
        "submit_label": "Save Changes",
    })


@role_required("Owner")
def menu_item_delete(request, item_id):
    if request.method != "POST":
        return redirect("menu_items")
    item = get_object_or_404(Product, pk=item_id)
    try:
        item.delete()
    except ProtectedError:
        messages.error(
            request,
            "This item is used in existing sales and cannot be deleted. Disable it instead.",
        )
    else:
        messages.success(request, "Menu item deleted successfully.")
    return redirect("menu_items")


@role_required("Cashier", "Owner")
def khata(request):
    return render(request, "pos/khata.html", {
        "customers": Customer.objects.with_outstanding(),
    })


@role_required("Cashier", "Owner")
def customers(request):
    query = request.GET.get("q", "").strip()
    customer_list = Customer.objects.with_outstanding()
    if query:
        customer_list = customer_list.filter(Q(name__icontains=query) | Q(phone__icontains=query))
    paginator = Paginator(customer_list, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "pos/customers.html", {"customers": page_obj, "page_obj": page_obj, "query": query})


@role_required("Cashier", "Owner")
def customer_create(request):
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        customer = form.save()
        messages.success(request, "Customer added successfully.")
        return redirect("customer_detail", customer.id)
    return render(request, "pos/customer_form.html", {"form": form, "page_title": "Add Customer", "submit_label": "Add Customer"})


@role_required("Cashier", "Owner")
def customer_detail(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)
    bills = customer.bills.all()
    return render(request, "pos/customer_detail.html", {
        "customer": customer,
        "bills": bills,
        "bill_count": bills.count(),
        "total_spent": bills.aggregate(total=Sum("grand_total"))["total"] or Decimal("0"),
    })


@role_required("Owner")
def customer_edit(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Customer updated successfully.")
        return redirect("customer_detail", customer.id)
    return render(request, "pos/customer_form.html", {"form": form, "customer": customer, "page_title": "Edit Customer", "submit_label": "Save Changes"})


@role_required("Owner")
def customer_delete(request, customer_id):
    if request.method == "POST":
        customer = get_object_or_404(Customer, pk=customer_id)
        log_audit(request, "DELETE", customer, {"phone": customer.phone})
        customer.delete()
        messages.success(request, "Customer deleted.")
    return redirect("customers")


@role_required("Cashier", "Owner")
@transaction.atomic
def khata_payment(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)

    if request.method == "POST":
        try:
            amount = Decimal(request.POST.get("amount", "0"))
        except InvalidOperation:
            amount = Decimal("0")

        if amount > 0:
            payment = KhataTransaction.objects.create(
                customer=customer,
                kind=KhataTransaction.PAYMENT,
                amount=amount,
                note=request.POST.get("note", "Payment received"),
            )
            log_audit(
                request,
                "PAYMENT",
                payment,
                {"customer": customer.name, "amount": str(amount), "note": payment.note},
            )

    return redirect("khata")


@role_required("Owner")
def expenses(request):
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    raw_date = request.GET.get("date", "").strip()
    parsed_date = parse_date_param(request, "date")
    expense_list = Expense.objects.all().order_by("-expense_date", "-id")
    if query:
        expense_list = expense_list.filter(Q(title__icontains=query) | Q(category__icontains=query) | Q(notes__icontains=query))
    if category:
        expense_list = expense_list.filter(category=category)
    if parsed_date:
        expense_list = expense_list.filter(expense_date=parsed_date)
    today = timezone.localdate()
    paginator = Paginator(expense_list, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "pos/expenses.html", {
        "expenses": page_obj,
        "page_obj": page_obj,
        "categories": Expense.objects.values_list("category", flat=True).distinct().order_by("category"),
        "query": query, "selected_category": category, "selected_date": raw_date if parsed_date else "",
        "today_total": Expense.objects.filter(expense_date=today).aggregate(v=Sum("amount"))["v"] or Decimal("0"),
        "month_total": Expense.objects.filter(expense_date__year=today.year, expense_date__month=today.month).aggregate(v=Sum("amount"))["v"] or Decimal("0"),
        "all_total": Expense.objects.aggregate(v=Sum("amount"))["v"] or Decimal("0"),
    })


@role_required("Owner")
def expense_create(request):
    form = ExpenseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Expense added successfully.")
        return redirect("expenses")
    return render(request, "pos/expense_form.html", {"form": form, "page_title": "Add Expense", "submit_label": "Add Expense"})


@role_required("Owner")
def expense_edit(request, expense_id):
    expense = get_object_or_404(Expense, pk=expense_id)
    form = ExpenseForm(request.POST or None, instance=expense)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Expense updated successfully.")
        return redirect("expenses")
    return render(request, "pos/expense_form.html", {"form": form, "expense": expense, "page_title": "Edit Expense", "submit_label": "Save Changes"})


@role_required("Owner")
def expense_delete(request, expense_id):
    if request.method == "POST":
        expense = get_object_or_404(Expense, pk=expense_id)
        log_audit(request, "DELETE", expense, {"amount": str(expense.amount)})
        expense.delete()
        messages.success(request, "Expense deleted.")
    return redirect("expenses")


@role_required("Owner")
def dashboard(request):
    today = timezone.localdate()
    start_at = timezone.make_aware(datetime.combine(today, time.min))
    end_at = start_at + timedelta(days=1)
    products_sold = (
        BillItem.objects.filter(bill__created_at__gte=start_at, bill__created_at__lt=end_at)
        .values("item_name", "menu_item__category")
        .annotate(
            quantity_sold=Sum("quantity"),
            selling_amount=Sum("total"),
            profit=Sum((F("price") - F("cost_price")) * F("quantity")),
        )
        .order_by("item_name")
    )
    total_sales = sum((row["selling_amount"] for row in products_sold), Decimal("0"))
    total_quantity = sum((row["quantity_sold"] for row in products_sold), Decimal("0"))
    total_profit = sum((row["profit"] for row in products_sold), Decimal("0"))
    top_products = sorted(products_sold, key=lambda row: row["selling_amount"], reverse=True)[:5]
    low_stock = Product.objects.filter(is_active=True, stock__lte=5)
    customers_with_outstanding = Customer.objects.with_outstanding()
    total_outstanding = customers_with_outstanding.aggregate(total=Sum("outstanding"))["total"] or Decimal("0")
    return render(request, "pos/dashboard.html", {
        "products_sold": products_sold,
        "top_products": top_products,
        "low_stock_products": low_stock.order_by("stock", "name"),
        "low_stock_count": low_stock.count(),
        "total_sales": total_sales,
        "total_quantity": total_quantity,
        "total_profit": total_profit,
        "total_outstanding": total_outstanding,
        "outstanding_customers": customers_with_outstanding.filter(outstanding__gt=0),
    })


@role_required("Owner")
def reports(request):
    from_date, to_date, date_error = report_date_range(request)
    bills = report_bills(from_date, to_date)
    context = report_context(bills, from_date, to_date)
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
    ).select_related("customer").prefetch_related("items__menu_item").order_by("created_at", "bill_number")


def report_context(bills, from_date, to_date):
    totals = bills.aggregate(
        sales=Sum("grand_total"),
        cash=Sum("grand_total", filter=Q(payment_method=Bill.CASH)),
        upi=Sum("grand_total", filter=Q(payment_method=Bill.UPI)),
        card=Sum("grand_total", filter=Q(payment_method=Bill.CARD)),
        khata=Sum("grand_total", filter=Q(payment_method=Bill.KHATA)),
    )
    item_total = bills.aggregate(total=Sum("items__quantity"))["total"] or Decimal("0")
    daily_sales = list(
        bills.annotate(sale_day=TruncDate("created_at"))
        .values("sale_day")
        .annotate(sales=Sum("grand_total"))
        .order_by("sale_day")
    )
    return {
        "bills": bills,
        "from_date": from_date,
        "to_date": to_date,
        "total_sales": totals["sales"] or Decimal("0"),
        "total_bills": bills.count(),
        "total_items": item_total,
        "cash_sales": totals["cash"] or Decimal("0"),
        "upi_sales": totals["upi"] or Decimal("0"),
        "card_sales": totals["card"] or Decimal("0"),
        "khata_sales": totals["khata"] or Decimal("0"),
        "sales_chart_labels": json.dumps([row["sale_day"].strftime("%d %b %Y") for row in daily_sales]),
        "sales_chart_values": json.dumps([float(row["sales"] or 0) for row in daily_sales]),
        "payment_chart_labels": json.dumps([label for _, label in Bill.PAYMENT_CHOICES]),
        "payment_chart_values": json.dumps([
            float(totals[method.lower()] or 0) for method, _ in Bill.PAYMENT_CHOICES
        ]),
        "business_settings": BusinessSettings.current(),
    }


@role_required("Owner")
def export_sales_csv(request):
    from_date, to_date, date_error = report_date_range(request)
    if date_error:
        return JsonResponse({"error": date_error}, status=400)

    bills = report_bills(from_date, to_date)
    filename = f"cafe_sales_{from_date:%Y-%m-%d}"
    if from_date != to_date:
        filename += f"_to_{to_date:%Y-%m-%d}"
    response = render_csv_response(filename)
    writer = csv.writer(response)
    writer.writerow([
        "Bill Number", "Date", "Time", "Customer Name", "Item Name", "Category",
        "Quantity", "Unit Price", "Item Total", "Subtotal", "Discount", "Grand Total",
        "Payment Method",
    ])
    for bill in bills:
        customer_name = bill.customer.name if bill.customer else "Walk-in Customer"
        for item in bill.items.all():
            writer.writerow([
                bill.bill_number,
                timezone.localtime(bill.created_at).strftime("%d-%m-%Y"),
                timezone.localtime(bill.created_at).strftime("%I:%M %p"),
                customer_name,
                item.item_name,
                item.menu_item.category,
                item.quantity,
                item.price,
                item.total,
                bill.subtotal,
                bill.discount,
                bill.grand_total,
                bill.get_payment_method_display(),
            ])
    return response


def render_csv_response(filename):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    response.write("\ufeff")
    return response


@role_required("Owner")
def settings_page(request):
    settings = BusinessSettings.current()
    form = BusinessSettingsForm(request.POST or None, instance=settings)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Business settings saved successfully.")
        return redirect("settings")
    return render(request, "pos/settings.html", {"form": form, "business_settings": settings})


def log_audit(request, action, instance, details=None):
    actor = request.user if request.user.is_authenticated else None
    return AuditLog.objects.create(
        actor=actor,
        action=action,
        model_name=instance.__class__.__name__,
        object_repr=str(instance),
        details=details or {},
    )


@role_required("Owner")
def audit_logs(request):
    logs = AuditLog.objects.select_related("actor").order_by("-timestamp", "-id")
    paginator = Paginator(logs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "pos/audit_logs.html", {"logs": page_obj, "page_obj": page_obj})
