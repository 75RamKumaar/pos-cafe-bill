from django.core.management.base import BaseCommand
from django.core.management.utils import get_random_secret_key


class Command(BaseCommand):
    help = "Generate and print a new cryptographically secure secret key."

    def handle(self, *args, **options):
        secret_key = get_random_secret_key()
        self.stdout.write(secret_key)

