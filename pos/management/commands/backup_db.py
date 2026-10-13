import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from pos.models import BusinessSettings


class Command(BaseCommand):
    help = (
        "Create a verified SQLite online backup, timestamp files, keep the last 30, "
        "and support a custom backup directory (e.g. USB path)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--backup-dir",
            "--dest",
            dest="backup_dir",
            type=str,
            default=None,
            help="Destination directory for backups (defaults to BusinessSettings or 'backups/').",
        )
        parser.add_argument(
            "--db-path",
            dest="db_path",
            type=str,
            default=None,
            help="Path to the database file to back up.",
        )

    def resolve_backup_dir(self, custom_dir=None):
        if custom_dir and str(custom_dir).strip():
            return Path(str(custom_dir).strip())

        try:
            bs = BusinessSettings.current()
            if bs.backup_directory and bs.backup_directory.strip():
                return Path(bs.backup_directory.strip())
        except Exception:
            pass

        return settings.BASE_DIR / "backups"

    def handle(self, *args, **options):
        # 1. Determine destination directory
        raw_backup_dir = options.get("backup_dir")
        backup_dir = self.resolve_backup_dir(raw_backup_dir)

        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CommandError(
                f"Failed to access or create backup directory '{backup_dir}': {exc}. "
                f"Please verify directory permissions or ensure the USB drive is connected."
            )

        # 2. Determine source database path
        if options.get("db_path"):
            database_path = Path(options["db_path"])
        else:
            from django.db import connection
            db_name = connection.settings_dict.get("NAME") or settings.DATABASES["default"]["NAME"]
            database_path = Path(db_name)

        if not database_path.is_file():
            fallback_path = settings.BASE_DIR / "db.sqlite3"
            if fallback_path.is_file():
                database_path = fallback_path
            else:
                raise CommandError(
                    f"Source database file not found at '{database_path}'. "
                    f"Please ensure SQLite database exists before backing up."
                )

        # 3. Form timestamped backup filename
        timestamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"db_{timestamp}.sqlite3"

        # 4. Perform SQLite online backup (safe while concurrent reads/writes occur)
        try:
            src_conn = sqlite3.connect(database_path)
            dst_conn = sqlite3.connect(backup_path)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
                src_conn.close()
        except Exception as exc:
            if backup_path.exists():
                try:
                    backup_path.unlink()
                except OSError:
                    pass
            raise CommandError(f"SQLite online backup failed: {exc}")

        # 5. Verify backup copy with PRAGMA integrity_check
        try:
            verify_conn = sqlite3.connect(backup_path)
            try:
                cursor = verify_conn.cursor()
                cursor.execute("PRAGMA integrity_check;")
                result = cursor.fetchone()
            finally:
                verify_conn.close()
        except Exception as exc:
            if backup_path.exists():
                backup_path.unlink()
            raise CommandError(f"Failed to verify backup integrity: {exc}")

        if not result or str(result[0]).lower() != "ok":
            check_msg = result[0] if result else "None"
            if backup_path.exists():
                backup_path.unlink()
            raise CommandError(
                f"Backup verification failed: PRAGMA integrity_check returned '{check_msg}'."
            )

        # 6. Retention: Keep the last 30 backup files
        removed_count = 0
        try:
            all_backups = sorted(
                backup_dir.glob("db_*.sqlite3"),
                key=lambda p: (p.stat().st_mtime, p.name),
                reverse=True,
            )
            if len(all_backups) > 30:
                for old_backup in all_backups[30:]:
                    try:
                        old_backup.unlink()
                        removed_count += 1
                    except OSError:
                        pass
        except Exception as exc:
            self.stderr.write(self.style.WARNING(f"Warning during backup retention cleanup: {exc}"))

        size_bytes = backup_path.stat().st_size
        self.stdout.write(
            self.style.SUCCESS(
                f"Database backup created successfully: {backup_path.name} "
                f"({size_bytes} bytes, integrity: ok)"
            )
        )
        self.stdout.write(f"Location: {backup_dir}")
        self.stdout.write(f"Retention: kept newest 30 (removed {removed_count} older backup(s)).")
        return str(backup_path)
