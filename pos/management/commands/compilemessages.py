from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from pos.utils.msgfmt import compile_po_to_mo


class Command(BaseCommand):
    help = "Compiles .po files to .mo files for gettext support."

    def add_arguments(self, parser):
        parser.add_argument(
            "--locale",
            "-l",
            action="append",
            default=[],
            help="Locale(s) to process (e.g. ta, en). Default is to process all.",
        )
        parser.add_argument(
            "--exclude",
            "-x",
            action="append",
            default=[],
            help="Locales to exclude from processing.",
        )
        parser.add_argument(
            "--use-fuzzy",
            "-f",
            dest="fuzzy",
            action="store_true",
            help="Use fuzzy translations.",
        )
        parser.add_argument(
            "--ignore",
            "-i",
            action="append",
            default=[],
            help="Patterns to ignore.",
        )

    def handle(self, *args, **options):
        locales = options.get("locale") or []
        excludes = options.get("exclude") or []

        locale_dirs = set()
        for p in getattr(settings, "LOCALE_PATHS", []):
            p = Path(p).resolve()
            if p.is_dir():
                locale_dirs.add(p)

        base_dir = Path(settings.BASE_DIR).resolve()
        base_locale = base_dir / "locale"
        if base_locale.is_dir():
            locale_dirs.add(base_locale)

        for app in settings.INSTALLED_APPS:
            try:
                mod = __import__(app, fromlist=["locale"])
                if hasattr(mod, "__file__") and mod.__file__:
                    app_file = Path(mod.__file__).resolve()
                    if base_dir in app_file.parents and ".venv" not in app_file.parts:
                        app_locale = app_file.parent / "locale"
                        if app_locale.is_dir():
                            locale_dirs.add(app_locale)
            except Exception:
                pass

        compiled_count = 0
        for locale_dir in sorted(locale_dirs):
            for po_path in locale_dir.rglob("*.po"):
                rel = po_path.relative_to(locale_dir)
                lang = rel.parts[0] if rel.parts else ""
                if locales and lang not in locales:
                    continue
                if excludes and lang in excludes:
                    continue

                mo_path = po_path.with_suffix(".mo")
                try:
                    num = compile_po_to_mo(po_path, mo_path)
                    compiled_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"processing file {po_path.name} in {po_path.parent}, created {mo_path.name} ({num} messages)"
                        )
                    )
                except Exception as e:
                    self.stderr.write(
                        self.style.ERROR(f"Error compiling {po_path}: {e}")
                    )

        if compiled_count == 0:
            self.stdout.write("No .po files found to compile.")
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Successfully compiled {compiled_count} message catalog(s).")
            )
