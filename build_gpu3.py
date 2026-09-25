#!/usr/bin/env python3
"""Reproducibly refine the binary-only Adrenium 5.5.0 GPU release.

The repository has no Java sources. Patches are restricted to verified classfile
constructor/UI defaults; the Minecraft rendering implementation stays untouched.
"""

import hashlib
import json
from pathlib import Path
import struct
import zipfile

BASE = Path(__file__).resolve().parent
SOURCE = BASE / "Adrenium-5.5.0-gpu2.jar"
OUTPUT = BASE / "Adrenium-5.5.1-gpu3.jar"
SOURCE_SHA256 = "b2241587a75575471bf12b7846545b258306ffd1017f30f08b77c0ca5bec7372"
RESOLUTION = "dev/adrenium/config/AdreniumConfig$Resolution.class"
SCREEN = "dev/adrenium/config/AdreniumConfigScreen.class"


def replace_once(data: bytes, old: bytes, new: bytes) -> bytes:
    if data.count(old) != 1:
        raise ValueError(f"Expected exactly one occurrence of {old.hex()}, got {data.count(old)}")
    return data.replace(old, new, 1)


def constant_pool(data: bytes):
    """Return String constant references and UTF-8 byte ranges of a JVM classfile."""
    if data[:4] != b"\xca\xfe\xba\xbe":
        raise ValueError("Not a JVM classfile")
    count = struct.unpack_from(">H", data, 8)[0]
    strings, utf8, index = {}, {}, 1
    offset = 10
    while index < count:
        tag = data[offset]
        offset += 1
        if tag == 1:
            length = struct.unpack_from(">H", data, offset)[0]
            utf8[index] = (offset - 1, offset + 2, offset + 2 + length)
            offset += 2 + length
        elif tag == 8:
            strings[index] = struct.unpack_from(">H", data, offset)[0]
            offset += 2
        elif tag in (7, 16, 19, 20):
            offset += 2
        elif tag in (9, 10, 11, 12, 17, 18, 3, 4):
            offset += 4
        elif tag in (5, 6):
            offset += 8
            index += 1
        elif tag == 15:
            offset += 3
        else:
            raise ValueError(f"Unknown constant pool tag {tag}")
        index += 1
    return strings, utf8, offset


def string_reference(data: bytes, text: str) -> bytes:
    strings, utf8, _ = constant_pool(data)
    indices = [i for i, (_, start, end) in utf8.items() if data[start:end] == text.encode()]
    refs = [i for i, target in strings.items() if target in indices]
    if len(refs) != 1:
        raise ValueError(f"Expected one String constant for {text!r}, got {refs}")
    ref = refs[0]
    return b"\x12" + bytes([ref]) if ref < 256 else b"\x13" + struct.pack(">H", ref)


def update_string(data: bytes, old: str, new: str) -> bytes:
    _, utf8, _ = constant_pool(data)
    matches = [(start, end) for _, start, end in utf8.values() if data[start:end] == old.encode()]
    if len(matches) != 1:
        raise ValueError(f"Expected one UTF-8 constant for {old!r}")
    start, end = matches[0]
    encoded = new.encode("utf-8")
    if len(encoded) > 65535:
        raise ValueError("JVM UTF-8 constant is too long")
    return data[:start - 2] + struct.pack(">H", len(encoded)) + encoded + data[end:]


def patch_resolution(data: bytes) -> bytes:
    # Constructor: aload_0; iconst_0; putfield enabled; aload_0; bipush 75;
    # putfield scalePercent. No instruction widths, offsets or stack maps change.
    old = bytes.fromhex("2a 03 b5 00 07 2a 10 4b b5 00 0d")
    new = bytes.fromhex("2a 04 b5 00 07 2a 10 55 b5 00 0d")
    return replace_once(data, old, new)


def patch_screen(data: bytes) -> bytes:
    # Keep YACL's Reset-to-default bindings aligned with the constructor.
    data = replace_once(data, string_reference(data, "Enable render scaling") + b"\x12\xd6\x03",
                        string_reference(data, "Enable render scaling") + b"\x12\xd6\x04")
    data = replace_once(data, string_reference(data, "Render scale (%)") + b"\x12\xe6\x10\x4b",
                        string_reference(data, "Render scale (%)") + b"\x12\xe6\x10\x55")
    strings, utf8, _ = constant_pool(data)
    tooltip = next(data[start:end].decode() for _, start, end in utf8.values()
                   if data[start:end].startswith(b"Opt-in render resolution scaling,"))
    data = update_string(data, tooltip, tooltip.replace(
        "Opt-in render resolution scaling,", "Render resolution scaling,").replace(
        "Off by default; enable it on devices that need the headroom,",
        "Enabled at 85% by default; disable it if you prefer native resolution,"))
    toggle = next(data[start:end].decode() for _, start, end in constant_pool(data)[1].values()
                  if data[start:end].startswith(b"Render the world into a smaller frame buffer"))
    data = update_string(data, toggle, toggle.replace(
        "Render the world into a smaller frame buffer", "By default, render the world into a smaller frame buffer"))
    return data


def build():
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("Input JAR differs from the audited 5.5.0-gpu2 release")
    with zipfile.ZipFile(SOURCE) as original, zipfile.ZipFile(OUTPUT, "w") as result:
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename == RESOLUTION:
                data = patch_resolution(data)
            elif info.filename == SCREEN:
                data = patch_screen(data)
            elif info.filename == "fabric.mod.json":
                metadata = json.loads(data)
                assert metadata["version"] == "5.5.0-gpu2"
                metadata["version"] = "5.5.1-gpu3"
                metadata["description"] += ("\n\nGPU3: New installs default to 85% world-only render scale "
                    "(approximately 28% fewer world pixels to shade). The HUD stays sharp; "
                    "existing saved settings are respected. Change or disable scaling in Render Resolution.")
                data = json.dumps(metadata, indent=2, ensure_ascii=False).encode() + b"\n"
            result.writestr(info, data)
    print(f"Built {OUTPUT.name}")


if __name__ == "__main__":
    build()
