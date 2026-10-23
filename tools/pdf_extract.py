"""Minimal PDF text extractor for Corel/Office-exported PDFs with Identity-H fonts.

Decodes 2-byte glyph codes through each font's ToUnicode CMap, tracking the
current font (Tf) and the text position (TD/Td/Tm/T*), inserting line breaks
when the baseline moves.
"""
import re
import sys

from pypdf import PdfReader

CMAP_BFCHAR = re.compile(rb"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>")
CMAP_BFRANGE = re.compile(rb"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>")


def utoi(b):
    return int.from_bytes(b, "big")


def parse_cmap(data):
    m = {}
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in CMAP_BFCHAR.findall(block):
            try:
                m[utoi(bytes.fromhex(src.decode()))] = bytes.fromhex(
                    dst.decode()
                ).decode("utf-16-be", "replace")
            except Exception:
                pass
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for lo, hi, dst in CMAP_BFRANGE.findall(block):
            lo_i, hi_i = utoi(bytes.fromhex(lo.decode())), utoi(bytes.fromhex(hi.decode()))
            try:
                base = bytes.fromhex(dst.decode()).decode("utf-16-be")
            except Exception:
                continue
            for k in range(lo_i, min(hi_i, lo_i + 65535) + 1):
                if len(base) == 1:
                    m[k] = chr(ord(base) + (k - lo_i))
                else:
                    m[k] = base
    return m


def font_maps(page):
    maps = {}
    try:
        fonts = page["/Resources"].get("/Font", {})
    except Exception:
        return maps
    if hasattr(fonts, "get_object"):
        fonts = fonts.get_object()
    for name, ref in fonts.items():
        f = ref.get_object()
        if "/ToUnicode" in f:
            try:
                maps[name] = parse_cmap(f["/ToUnicode"].get_object().get_data())
            except Exception:
                maps[name] = {}
        else:
            maps[name] = {}
    return maps


HEX = re.compile(r"<([0-9a-fA-F\s]*)>")


def codes_to_text(raw, cmap):
    s = re.sub(r"\s", "", raw)
    if len(s) % 4 == 0:
        codes = [int(s[i:i + 4], 16) for i in range(0, len(s), 4)]
    else:
        codes = [int(s[i:i + 2], 16) for i in range(0, len(s), 2)]
    return "".join(cmap.get(c, "") for c in codes)


def literal_to_text(lit, cmap):
    return lit


def decode_string(fragment, cmap):
    """fragment: text between the show operator and 'Tj'/'TJ'."""
    if "<" in fragment:
        return "".join(codes_to_text(h, cmap) for h in HEX.findall(fragment))
    return fragment.strip().strip("()")


def numbers(line):
    return [float(x) for x in re.findall(r"-?\d+\.?\d*", line) if x not in (".", "-")]


def extract(path):
    reader = PdfReader(path)
    chunks = []
    for i, page in enumerate(reader.pages, 1):
        maps = font_maps(page)
        cmap = {}
        try:
            data = page.get_contents().get_data().decode("latin-1")
        except Exception:
            chunks.append(f"\n===== PAGE {i} =====\n")
            continue
        buf = []
        last_y = None
        for raw_line in data.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if line.endswith("Tf") and line.startswith("/"):
                name = line.split()[0]
                if name in maps:
                    cmap = maps[name]
                continue
            if line.endswith("Tj"):
                body = line[: -len("Tj")]
                if "<" in body:
                    text = "".join(codes_to_text(h, cmap) for h in HEX.findall(body))
                else:
                    text = body.strip().strip("()")
                buf.append(text)
                continue
            if line.endswith("TJ"):
                body = line[: -len("TJ")]
                buf.append(decode_string(body, cmap))
                continue
            if line.endswith("TD") or line.endswith("Td"):
                nums = numbers(line[:-2])
                if len(nums) >= 2:
                    ty = nums[1]
                    if abs(ty) > 2:
                        buf.append("\n")
                    elif ty == 0 and nums[0] > 12:
                        buf.append(" ")
                continue
            if line.endswith("Tm"):
                nums = numbers(line[:-2])
                if len(nums) >= 6:
                    y = nums[5]
                    if last_y is not None and abs(y - last_y) > 3:
                        buf.append("\n")
                    last_y = y
                continue
            if line == "T*":
                buf.append("\n")
        text = "".join(buf)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        chunks.append(f"\n===== PAGE {i} =====\n{text.strip()}\n")
    return "".join(chunks)


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    out = extract(src)
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(out)
    print("chars", len(out))
