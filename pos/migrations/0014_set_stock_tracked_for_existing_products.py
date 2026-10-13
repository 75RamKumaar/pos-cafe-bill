from django.db import migrations


def set_stock_tracked_for_existing_products(apps, schema_editor):
    Product = apps.get_model("pos", "Product")
    Product.objects.filter(stock__gt=0).update(stock_tracked=True)


def reverse_stock_tracked(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("pos", "0013_product_stock_tracked"),
    ]

    operations = [
        migrations.RunPython(
            set_stock_tracked_for_existing_products,
            reverse_stock_tracked,
        ),
    ]

