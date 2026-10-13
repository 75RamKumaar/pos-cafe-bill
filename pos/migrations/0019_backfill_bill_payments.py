from django.db import migrations


def backfill_bill_payments(apps, schema_editor):
    Bill = apps.get_model("pos", "Bill")
    BillPayment = apps.get_model("pos", "BillPayment")
    for bill in Bill.objects.all():
        if not BillPayment.objects.filter(bill=bill).exists():
            method = bill.payment_method if bill.payment_method != "SPLIT" else "CASH"
            BillPayment.objects.create(
                bill=bill,
                method=method,
                amount=bill.grand_total,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0018_alter_bill_payment_method_billpayment"),
    ]

    operations = [
        migrations.RunPython(backfill_bill_payments, migrations.RunPython.noop),
    ]

