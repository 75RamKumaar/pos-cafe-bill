from datetime import date
from django.core.management.base import BaseCommand
from pos.models import process_recurring_expenses


class Command(BaseCommand):
    help = "Process active recurring expenses that are due and generate corresponding expense records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Target date in YYYY-MM-DD format (defaults to today).",
        )

    def handle(self, *args, **options):
        target_date_str = options.get("date")
        target_date = None
        if target_date_str:
            target_date = date.fromisoformat(target_date_str)
        count = process_recurring_expenses(target_date=target_date)
        self.stdout.write(
            self.style.SUCCESS(f"Successfully processed recurring expenses. Created {count} expense(s).")
        )

