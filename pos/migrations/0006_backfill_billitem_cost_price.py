from django.db import migrations


def copy_product_cost_to_bill_items(apps, schema_editor):
    BillItem = apps.get_model("pos", "BillItem")
    for item in BillItem.objects.select_related("menu_item").iterator():
        item.cost_price = item.menu_item.cost_price
        item.save(update_fields=["cost_price"])


def leave_cost_snapshots_unchanged(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("pos", "0005_billitem_cost_price"),
    ]

    operations = [
        migrations.RunPython(copy_product_cost_to_bill_items, leave_cost_snapshots_unchanged),
    ]
