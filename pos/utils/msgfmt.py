"""
Pure Python PO-to-MO compiler.
Generates standard GNU gettext binary .mo files without requiring external gettext tools.
"""

import array
import ast
import os
import struct
import sys
from pathlib import Path


def clean_chunk(s: str) -> str:
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        try:
            return ast.literal_eval(s)
        except Exception:
            return s[1:-1]
    return ""


def parse_po_file(filepath: Path | str) -> dict[str, str]:
    with open(filepath, "r", encoding="utf-8") as f:
        po_text = f.read()
    return parse_po_content(po_text)


def parse_po_content(po_text: str) -> dict[str, str]:
    messages: dict[str, str] = {}
    current_key = None
    current_msgid: list[str] = []
    current_msgstr: list[str] = []

    for line in po_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("msgid "):
            if current_key and current_msgid is not None:
                messages["".join(current_msgid)] = "".join(current_msgstr)
            current_msgid = [clean_chunk(line[6:])]
            current_msgstr = []
            current_key = "msgid"
        elif line.startswith("msgstr "):
            current_msgstr = [clean_chunk(line[7:])]
            current_key = "msgstr"
        elif line.startswith('"') and line.endswith('"'):
            val = clean_chunk(line)
            if current_key == "msgid":
                current_msgid.append(val)
            elif current_key == "msgstr":
                current_msgstr.append(val)

    if current_key and current_msgid is not None:
        messages["".join(current_msgid)] = "".join(current_msgstr)

    return messages


def generate_mo_bytes(messages: dict[str, str]) -> bytes:
    byte_messages = {k.encode("utf-8"): v.encode("utf-8") for k, v in messages.items()}
    keys = sorted(byte_messages.keys())
    offsets = []
    ids = b""
    strs = b""
    for id_bytes in keys:
        offsets.append((len(ids), len(id_bytes), len(strs), len(byte_messages[id_bytes])))
        ids += id_bytes + b"\0"
        strs += byte_messages[id_bytes] + b"\0"
    keystart = 7 * 4 + 16 * len(keys)
    valuestart = keystart + len(ids)
    koffsets = []
    voffsets = []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]
    output = struct.pack(
        "Iiiiiii",
        0x950412DE,
        0,
        len(keys),
        7 * 4,
        7 * 4 + len(keys) * 8,
        0,
        0,
    )
    output += array.array("i", koffsets).tobytes()
    output += array.array("i", voffsets).tobytes()
    output += ids + strs
    return output


def compile_po_to_mo(po_path: Path | str, mo_path: Path | str) -> int:
    messages = parse_po_file(po_path)
    mo_bytes = generate_mo_bytes(messages)
    os.makedirs(os.path.dirname(os.path.abspath(mo_path)), exist_ok=True)
    with open(mo_path, "wb") as f:
        f.write(mo_bytes)
    return len(messages)


def main():
    """
    CLI interface mimicking GNU msgfmt:
    msgfmt -o output.mo input.po
    msgfmt --check-format -o output.mo input.po
    """
    args = sys.argv[1:]
    out_file = None
    in_file = None

    idx = 0
    while idx < len(args):
        arg = args[idx]
        if arg == "-o":
            idx += 1
            if idx < len(args):
                out_file = args[idx]
        elif arg.startswith("-o"):
            out_file = arg[2:]
        elif arg.startswith("--output-file="):
            out_file = arg.split("=", 1)[1]
        elif not arg.startswith("-"):
            in_file = arg
        idx += 1

    if not in_file:
        print("Usage: python -m pos.utils.msgfmt [-o output.mo] input.po", file=sys.stderr)
        sys.exit(1)

    if not out_file:
        in_p = Path(in_file)
        out_file = str(in_p.with_suffix(".mo"))

    count = compile_po_to_mo(in_file, out_file)
    print(f"Compiled {count} messages from {in_file} to {out_file}")


if __name__ == "__main__":
    main()

