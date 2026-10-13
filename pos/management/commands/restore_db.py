import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from pos.models import BusinessSettings


class Command(BaseCommand):
    help = (
        "Restore the SQLite database from a verified backup file. "
        "Requires '--confirm' and automatically creates a safety backup of the current DB first."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "backup_file",
            nargs="?",
            default=None,
            help="Path to the backup file to restore. Defaults to the latest backup in backup directory.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="Confirmation flag required to execute database restore.",
        )
        parser.add_argument(
            "--backup-dir",
            dest="backup_dir",
            type=str,
            default=None,
            help="Backup directory to look for latest backup if no file is provided.",
        )
        parser.add_argument(
            "--db-path",
            dest="db_path",
            type=str,
            default=None,
            help="Path to database file to restore into (defaults to active database).",
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
        # 1. Require --confirm flag
        if not options.get("confirm"):
            raise CommandError(
                "Database restore requires the '--confirm' flag to proceed. "
                "WARNING: This operation replaces the current active database with the backup copy. "
                "Run again with '--confirm' (e.g. 'python manage.py restore_db --confirm')."
            )

        # 2. Determine source backup file
        backup_file_arg = options.get("backup_file")
        backup_dir = self.resolve_backup_dir(options.get("backup_dir"))

        if backup_file_arg:
            backup_path = Path(backup_file_arg)
            if not backup_path.is_file():
                # Check if it's a filename inside backup_dir
                candidate = backup_dir / backup_file_arg
                if candidate.is_file():
                    backup_path = candidate
                else:
                    raise CommandError(f"Backup file not found at '{backup_path}'.")
        else:
            if not backup_dir.is_dir():
                raise CommandError(f"Backup directory '{backup_dir}' does not exist.")
            all_backups = sorted(
                backup_dir.glob("db_*.sqlite3"),
                key=lambda p: (p.stat().st_mtime, p.name),
                reverse=True,
            )
            if not all_backups:
                raise CommandError(f"No backup files found in '{backup_dir}' to restore.")
            backup_path = all_backups[0]

        # 3. Verify backup file before doing any modification
        try:
            verify_conn = sqlite3.connect(backup_path)
            try:
                cur = verify_conn.cursor()
                cur.execute("PRAGMA integrity_check;")
                res = cur.fetchone()
            finally:
                verify_conn.close()
        except Exception as exc:
            raise CommandError(f"Unable to read backup file '{backup_path}': {exc}")

        if not res or str(res[0]).lower() != "ok":
            msg = res[0] if res else "None"
            raise CommandError(
                f"Backup file '{backup_path}' failed integrity check ('{msg}'). "
                f"Aborting restore to prevent corrupting database."
            )

        # 4. Determine current database path
        if options.get("db_path"):
            database_path = Path(options["db_path"])
        else:
            db_name = connection.settings_dict.get("NAME") or settings.DATABASES["default"]["NAME"]
            database_path = Path(db_name)

        if not database_path.is_file():
            fallback_path = settings.BASE_DIR / "db.sqlite3"
            if fallback_path.is_file():
                database_path = fallback_path
            else:
                database_path.parent.mkdir(parents=True, exist_ok=True)

        # 5. First: Safety backup of the current DB before overwriting
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
        safety_path = backup_dir / f"pre_restore_{timestamp}.sqlite3"

        if database_path.is_file() and database_path.stat().st_size > 0:
            try:
                src_conn = sqlite3.connect(database_path)
                dst_conn = sqlite3.connect(safety_path)
                try:
                    src_conn.backup(dst_conn)
                finally:
                    dst_conn.close()
                    src_conn.close()
                self.stdout.write(
                    self.style.NOTICE(f"Safety backup created prior to restore: {safety_path.name}")
                )
            except Exception as exc:
                if safety_path.exists():
                    try:
                        safety_path.unlink()
                    except OSError:
                        pass
                raise CommandError(
                    f"Failed to create pre-restore safety backup of current database: {exc}. "
                    f"Restore aborted."
                )

        # 6. Perform the restore using SQLite online backup
        # Close any active Django database connections first
        try:
            connection.close()
        except Exception:
            pass

        try:
            restore_src = sqlite3.connect(backup_path)
            restore_dst = sqlite3.connect(database_path)
            try:
                restore_src.backup(restore_dst)
            finally:
                restore_dst.close()
                restore_src.close()
        except Exception as exc:
            raise CommandError(f"Database restore operation failed: {exc}")

        # 7. Verify integrity of restored database
        try:
            check_conn = sqlite3.connect(database_path)
            try:
                cur = check_conn.cursor()
                cur.execute("PRAGMA integrity_check;")
                check_res = cur.fetchone()
            finally:
                check_conn.close()
        except Exception as exc:
            raise CommandError(f"Restored database verification failed: {exc}")

        if not check_res or str(check_res[0]).lower() != "ok":
            raise CommandError(
                f"Restored database integrity check failed ('{check_res[0] if check_res else 'None'}')."
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Database successfully restored from '{backup_path.name}' "
                f"({database_path.stat().st_size} bytes, integrity: ok)."
            )
        )
        return str(database_path)
