import shutil
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Create a timestamped SQLite backup and remove backups older than 30 days."

    def handle(self, *args, **options):
        database_path = Path(settings.DATABASES["default"]["NAME"])
        if not database_path.is_file():
            raise CommandError(f"Database file not found: {database_path}")

        backup_dir = settings.BASE_DIR / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"db_{timestamp}.sqlite3"
        shutil.copyfile(database_path, backup_path)

        cutoff = timezone.now() - timedelta(days=30)
        removed_count = 0
        for old_backup in backup_dir.glob("db_*.sqlite3"):
            modified_at = timezone.make_aware(
                timezone.datetime.fromtimestamp(old_backup.stat().st_mtime),
                timezone.get_current_timezone(),
            )
            if modified_at < cutoff:
                old_backup.unlink()
                removed_count += 1

        self.stdout.write(self.style.SUCCESS(f"Database backup created: {backup_path}"))
        self.stdout.write(f"Removed {removed_count} backup(s) older than 30 days.")
