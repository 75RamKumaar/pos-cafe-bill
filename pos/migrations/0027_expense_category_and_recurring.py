import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def convert_expense_categories(apps, schema_editor):
    ExpenseCategory = apps.get_model("pos", "ExpenseCategory")
    Expense = apps.get_model("pos", "Expense")
    has_category_fk = any(f.name == "category_fk" for f in Expense._meta.fields)

    if has_category_fk:
        for exp in Expense.objects.all():
            raw_name = getattr(exp, "category", "")
            if isinstance(raw_name, str):
                name_str = raw_name.strip() or "General"
            elif raw_name and hasattr(raw_name, "name"):
                name_str = raw_name.name
            else:
                name_str = "General"
            cat_obj, _ = ExpenseCategory.objects.get_or_create(
                name=name_str,
                defaults={"is_active": True},
            )
            exp.category_fk = cat_obj
            exp.save(update_fields=["category_fk"])
    else:
        for exp in Expense.objects.all():
            if not exp.category_id:
                cat_obj, _ = ExpenseCategory.objects.get_or_create(
                    name="General",
                    defaults={"is_active": True},
                )
                exp.category = cat_obj
                exp.save(update_fields=["category"])


def reverse_expense_categories(apps, schema_editor):
    Expense = apps.get_model("pos", "Expense")
    has_category_fk = any(f.name == "category_fk" for f in Expense._meta.fields)
    has_category = any(f.name == "category" for f in Expense._meta.fields)
    if has_category_fk and has_category:
        for exp in Expense.objects.all():
            if exp.category_fk:
                exp.category = exp.category_fk.name
                exp.save(update_fields=["category"])


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0026_businesssettings_low_stock_threshold_staffprofile"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExpenseCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name_plural": "Expense categories",
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="expense",
            name="category_fk",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="expenses_migrated", to="pos.expensecategory"),
        ),
        migrations.RunPython(convert_expense_categories, reverse_expense_categories),
        migrations.RemoveField(
            model_name="expense",
            name="category",
        ),
        migrations.RenameField(
            model_name="expense",
            old_name="category_fk",
            new_name="category",
        ),
        migrations.AlterField(
            model_name="expense",
            name="category",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="expenses", to="pos.expensecategory"),
        ),
        migrations.AddField(
            model_name="expense",
            name="receipt_image",
            field=models.FileField(blank=True, null=True, upload_to="receipts/"),
        ),
        migrations.AlterField(
            model_name="expense",
            name="payment_method",
            field=models.CharField(choices=[("CASH", "Cash"), ("UPI", "UPI"), ("CARD", "Card"), ("BANK", "Bank Transfer")], default="CASH", max_length=20),
        ),
        migrations.CreateModel(
            name="RecurringExpense",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=120)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=10)),
                ("method", models.CharField(choices=[("CASH", "Cash"), ("UPI", "UPI"), ("CARD", "Card"), ("BANK", "Bank Transfer")], default="CASH", max_length=20)),
                ("frequency", models.CharField(choices=[("DAILY", "Daily"), ("WEEKLY", "Weekly"), ("MONTHLY", "Monthly")], default="MONTHLY", max_length=10)),
                ("next_due", models.DateField(default=django.utils.timezone.localdate)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("category", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="recurring_expenses", to="pos.expensecategory")),
            ],
            options={
                "ordering": ["next_due", "title"],
            },
        ),
        migrations.AddField(
            model_name="expense",
            name="recurring_expense",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="generated_expenses", to="pos.recurringexpense"),
        ),
    ]

