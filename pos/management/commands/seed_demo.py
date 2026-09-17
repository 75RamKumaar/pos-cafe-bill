from django.core.management.base import BaseCommand
from pos.models import Customer, Product

class Command(BaseCommand):
    help = "Create demo cafe products and customers."

    def handle(self, *args, **kwargs):
        products = [
            ("Masala Tea", "Tea", 20, 8),
            ("Coffee", "Hot Drinks", 30, 12),
            ("Bun Maska", "Snacks", 35, 16),
            ("Samosa", "Snacks", 15, 7),
            ("Lime Soda", "Cold Drinks", 40, 18),
            ("Veg Sandwich", "Snacks", 60, 28),
        ]

        for name, category, price, cost in products:
            Product.objects.get_or_create(
                name=name,
                defaults={"category":category, "price":price, "cost_price":cost, "stock":100}
            )

        for name, phone in [("Sharma Uncle", "9000000001"), ("Auto Stand Group", "9000000002")]:
            Customer.objects.get_or_create(name=name, defaults={"phone":phone})

        self.stdout.write(self.style.SUCCESS("Demo products and customers created."))
