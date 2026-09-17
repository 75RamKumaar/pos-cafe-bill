import csv
import json
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.db.models.deletion import ProtectedError

from .forms import BusinessSettingsForm, CustomerForm, ExpenseForm, MenuItemForm
from .models import (
    Bill,
    BillItem,
    Customer,
    Expense,
    KhataTransaction,
    Product,
    Sale,
    SaleItem,
    BusinessSettings,
)


def pos(request):
    return redirect("billing")


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


def customer_search(request):
    query = request.GET.get("q", "").strip()
    customers = Customer.objects.all()
    if query:
        customers = customers.filter(Q(name__icontains=query) | Q(phone__icontains=query))
    return JsonResponse({"customers": [{"id": c.id, "name": c.name, "phone": c.phone} for c in customers[:10]]})


def bill_create(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        data = json.loads(request.body)
        raw_items = data.get("items", [])
        payment_method = data.get("payment_method")
        customer_id = data.get("customer_id") or None
        discount = Decimal(str(data.get("discount", "0")))
    except (json.JSONDecodeError, AttributeError, InvalidOperation, TypeError):
        return JsonResponse({"error": "Invalid bill data."}, status=400)

    if not raw_items:
        return JsonResponse({"error": "Add at least one item."}, status=400)
    if payment_method not in dict(Bill.PAYMENT_CHOICES):
        return JsonResponse({"error": "Select a valid payment method."}, status=400)
    if discount < 0:
        return JsonResponse({"error": "Discount cannot be negative."}, status=400)

    customer = None
    if customer_id:
        customer = get_object_or_404(Customer, pk=customer_id)

    validated_items = []
    subtotal = Decimal("0.00")
    for raw in raw_items:
        try:
            product_id = int(raw["id"])
            quantity = Decimal(str(raw["quantity"]))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return JsonResponse({"error": "Invalid item or quantity."}, status=400)
        if quantity <= 0 or quantity > Decimal("9999"):
            return JsonResponse({"error": "Quantities must be positive."}, status=400)

        product = Product.objects.filter(pk=product_id, is_active=True).first()
        if product is None:
            return JsonResponse({"error": "An item is unavailable and was not added."}, status=400)
        line_total = product.price * quantity
        subtotal += line_total
        validated_items.append((product, quantity, line_total))

    if discount > subtotal:
        return JsonResponse({"error": "Discount cannot exceed the subtotal."}, status=400)

    grand_total = subtotal - discount
    bill_number = f"BILL-{timezone.now():%Y%m%d%H%M%S%f}"
    with transaction.atomic():
        bill = Bill.objects.create(
            bill_number=bill_number,
            customer=customer,
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
            product.stock = max(Decimal("0"), product.stock - quantity)
            product.save(update_fields=["stock"])

    return JsonResponse({
        "ok": True,
        "bill_id": bill.id,
        "bill_number": bill.bill_number,
        "total": f"{bill.grand_total:.2f}",
        "detail_url": bill.get_absolute_url() if hasattr(bill, "get_absolute_url") else f"/billing/{bill.id}/",
    })


def billing_history(request):
    query = request.GET.get("q", "").strip()
    date = request.GET.get("date", "").strip()
    bills = Bill.objects.select_related("customer")
    if query:
        bills = bills.filter(Q(bill_number__icontains=query) | Q(customer__name__icontains=query))
    if date:
        bills = bills.filter(created_at__date=date)
    return render(request, "pos/billing_history.html", {
        "bills": bills,
        "query": query,
        "selected_date": date,
    })


def bill_detail(request, bill_id):
    bill = get_object_or_404(Bill.objects.select_related("customer").prefetch_related("items"), pk=bill_id)
    return render(request, "pos/bill_detail.html", {"bill": bill, "business_settings": BusinessSettings.current(), "auto_print": request.GET.get("print") == "1"})


def bill_delete(request, bill_id):
    if request.method == "POST":
        get_object_or_404(Bill, pk=bill_id).delete()
        messages.success(request, "Bill deleted.")
    return redirect("billing_history")


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


@transaction.atomic
def checkout(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        data = json.loads(request.body)
        items = data.get("items", [])
        payment_mode = data.get("payment_mode")
        customer_id = data.get("customer_id")
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Invalid request"}, status=400)

    if not items:
        return JsonResponse({"error": "Add at least one item"}, status=400)

    if payment_mode not in dict(Sale.PAYMENT_CHOICES):
        return JsonResponse({"error": "Invalid payment mode"}, status=400)

    customer = None
    if payment_mode == Sale.KHATA:
        if not customer_id:
            return JsonResponse({"error": "Select a Khata customer"}, status=400)
        customer = get_object_or_404(Customer, pk=customer_id)

    total = Decimal("0")
    validated_items = []

    for raw in items:
        try:
            product = Product.objects.get(pk=int(raw["id"]), is_active=True)
            quantity = Decimal(str(raw["quantity"]))
        except (KeyError, ValueError, InvalidOperation, Product.DoesNotExist):
            return JsonResponse({"error": "Invalid product or quantity"}, status=400)

        if quantity <= 0:
            return JsonResponse({"error": "Quantity must be positive"}, status=400)

        amount = product.price * quantity
        total += amount
        validated_items.append((product, quantity, amount))

    invoice = f"INV-{timezone.now():%Y%m%d%H%M%S%f}"

    sale = Sale.objects.create(
        invoice_number=invoice,
        customer=customer,
        payment_mode=payment_mode,
        total=total,
    )

    for product, quantity, amount in validated_items:
        SaleItem.objects.create(
            sale=sale,
            product=product,
            quantity=quantity,
            unit_price=product.price,
            cost_price=product.cost_price,
            amount=amount,
        )
        product.stock = max(Decimal("0"), product.stock - quantity)
        product.save(update_fields=["stock"])

    if payment_mode == Sale.KHATA:
        KhataTransaction.objects.create(
            customer=customer,
            sale=sale,
            kind=KhataTransaction.CREDIT,
            amount=total,
            note=f"Invoice {invoice}",
        )

    return JsonResponse({
        "ok": True,
        "invoice": invoice,
        "total": f"{total:.2f}",
        "payment_mode": payment_mode,
    })


def khata(request):
    return render(request, "pos/khata.html", {
        "customers": Customer.objects.all(),
    })


def customers(request):
    query = request.GET.get("q", "").strip()
    customer_list = Customer.objects.all()
    if query:
        customer_list = customer_list.filter(Q(name__icontains=query) | Q(phone__icontains=query))
    return render(request, "pos/customers.html", {"customers": customer_list, "query": query})


def customer_create(request):
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        customer = form.save()
        messages.success(request, "Customer added successfully.")
        return redirect("customer_detail", customer.id)
    return render(request, "pos/customer_form.html", {"form": form, "page_title": "Add Customer", "submit_label": "Add Customer"})


def customer_detail(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)
    bills = customer.bills.all()
    return render(request, "pos/customer_detail.html", {
        "customer": customer,
        "bills": bills,
        "bill_count": bills.count(),
        "total_spent": bills.aggregate(total=Sum("grand_total"))["total"] or Decimal("0"),
    })


def customer_edit(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Customer updated successfully.")
        return redirect("customer_detail", customer.id)
    return render(request, "pos/customer_form.html", {"form": form, "customer": customer, "page_title": "Edit Customer", "submit_label": "Save Changes"})


def customer_delete(request, customer_id):
    if request.method == "POST":
        get_object_or_404(Customer, pk=customer_id).delete()
        messages.success(request, "Customer deleted.")
    return redirect("customers")


@transaction.atomic
def khata_payment(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)

    if request.method == "POST":
        try:
            amount = Decimal(request.POST.get("amount", "0"))
        except InvalidOperation:
            amount = Decimal("0")

        if amount > 0:
            KhataTransaction.objects.create(
                customer=customer,
                kind=KhataTransaction.PAYMENT,
                amount=amount,
                note=request.POST.get("note", "Payment received"),
            )

    return redirect("khata")


def expenses(request):
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    date = request.GET.get("date", "").strip()
    expense_list = Expense.objects.all()
    if query:
        expense_list = expense_list.filter(Q(title__icontains=query) | Q(category__icontains=query) | Q(notes__icontains=query))
    if category:
        expense_list = expense_list.filter(category=category)
    if date:
        expense_list = expense_list.filter(expense_date=date)
    today = timezone.localdate()
    return render(request, "pos/expenses.html", {
        "expenses": expense_list,
        "categories": Expense.objects.values_list("category", flat=True).distinct().order_by("category"),
        "query": query, "selected_category": category, "selected_date": date,
        "today_total": Expense.objects.filter(expense_date=today).aggregate(v=Sum("amount"))["v"] or Decimal("0"),
        "month_total": Expense.objects.filter(expense_date__year=today.year, expense_date__month=today.month).aggregate(v=Sum("amount"))["v"] or Decimal("0"),
        "all_total": Expense.objects.aggregate(v=Sum("amount"))["v"] or Decimal("0"),
    })


def expense_create(request):
    form = ExpenseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Expense added successfully.")
        return redirect("expenses")
    return render(request, "pos/expense_form.html", {"form": form, "page_title": "Add Expense", "submit_label": "Add Expense"})


def expense_edit(request, expense_id):
    expense = get_object_or_404(Expense, pk=expense_id)
    form = ExpenseForm(request.POST or None, instance=expense)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Expense updated successfully.")
        return redirect("expenses")
    return render(request, "pos/expense_form.html", {"form": form, "expense": expense, "page_title": "Edit Expense", "submit_label": "Save Changes"})


def expense_delete(request, expense_id):
    if request.method == "POST":
        get_object_or_404(Expense, pk=expense_id).delete()
        messages.success(request, "Expense deleted.")
    return redirect("expenses")


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
    return render(request, "pos/dashboard.html", {
        "products_sold": products_sold,
        "total_sales": total_sales,
        "total_quantity": total_quantity,
        "total_profit": total_profit,
    })


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
    )
    item_total = bills.aggregate(total=Sum("items__quantity"))["total"] or Decimal("0")
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
        "business_settings": BusinessSettings.current(),
    }


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


def settings_page(request):
    settings = BusinessSettings.current()
    form = BusinessSettingsForm(request.POST or None, instance=settings)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Business settings saved successfully.")
        return redirect("settings")
    return render(request, "pos/settings.html", {"form": form, "business_settings": settings})
