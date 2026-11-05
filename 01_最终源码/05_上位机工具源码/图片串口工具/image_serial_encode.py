"""Prepare a single character image and encode the deployed FPGA UART protocol.

This module performs image conversion only. It does not run a recognition model.
The wire frame is always 32x32: prepared 28x28 pixels plus a black border, or an
explicitly selected existing 32x32 protocol canvas with all its pixels intact.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import struct

from PIL import Image, ImageOps

from serial_image_codec import crc16, scan_packets


def _grayscale_source(source) -> tuple[Image.Image, tuple[int, int]]:
    if isinstance(source, Image.Image):
        original = source.copy()
    else:
        with Image.open(source) as opened:
            original = opened.copy()
    oriented = ImageOps.exif_transpose(original)
    size = oriented.size
    if not size[0] or not size[1] or size[0] * size[1] > 64 * 1024 * 1024:
        raise ValueError("源图像尺寸无效或超过6400万像素。")
    rgba = oriented.convert("RGBA")
    # A transparent black character is normally intended as black ink on white.
    # White compositing makes this explicit before the user's inversion policy.
    white = Image.new("RGBA", rgba.size, "white")
    return Image.alpha_composite(white, rgba).convert("L"), size


def _border_mean(image: Image.Image) -> float:
    width, height = image.size
    if height == 1:
        return sum(image.getpixel((x, 0)) for x in range(width)) / width
    if width == 1:
        return sum(image.getpixel((0, y)) for y in range(height)) / height
    edge = [image.getpixel((x, y)) for y in (0, height - 1) for x in range(width)]
    edge.extend(image.getpixel((x, y)) for x in (0, width - 1) for y in range(1, height - 1))
    return sum(edge) / len(edge)


def prepare_image(source, mode: str = "fit28", invert: str = "auto") -> dict:
    """Return quantized previews and 1024 deployed-model pixel integers.

    ``fit28`` preserves aspect ratio and fits the whole image in a black 28x28
    canvas. Native 28x28 input is not geometrically changed. ``center20`` finds
    foreground above grayscale 32, fits its bounding box in 20x20, then centers
    it in 28x28. ``frame32`` preserves an existing 32x32 protocol canvas, including
    its edges, and rejects any other dimensions. ``auto`` inversion uses the
    source border's mean brightness.

    The previews are reconstructed from the same 0..16 values sent on UART.
    """
    if mode not in ("fit28", "center20", "frame32"):
        raise ValueError("预处理模式必须为 fit28、center20 或 frame32。")
    if invert not in ("auto", "yes", "no"):
        raise ValueError("反色选项必须为 auto、yes 或 no。")
    image, source_size = _grayscale_source(source)
    if mode == "frame32" and source_size != (32, 32):
        raise ValueError("已有协议画布模式严格要求32×32图片；其他尺寸请改用适配28×28或前景居中。")
    border_mean = _border_mean(image)
    inversion = invert == "yes" or (invert == "auto" and border_mean > 127)
    if inversion:
        image = ImageOps.invert(image)
    foreground_bbox = None
    if mode == "frame32":
        quantized32 = image.point(lambda value: (value * 16 + 127) // 255)
        quantized28 = quantized32.crop((2, 2, 30, 30))
    else:
        canvas = Image.new("L", (28, 28), 0)
        if mode == "center20":
            foreground_bbox = image.point(lambda value: 255 if value > 32 else 0).getbbox()
            if foreground_bbox is not None:
                foreground = image.crop(foreground_bbox)
                fitted = ImageOps.contain(foreground, (20, 20), Image.Resampling.BILINEAR)
                canvas.paste(fitted, ((28 - fitted.width) // 2, (28 - fitted.height) // 2))
        else:
            fitted = ImageOps.contain(image, (28, 28), Image.Resampling.BILINEAR)
            canvas.paste(fitted, ((28 - fitted.width) // 2, (28 - fitted.height) // 2))
        quantized28 = canvas.point(lambda value: (value * 16 + 127) // 255)
        quantized32 = Image.new("L", (32, 32), 0)
        quantized32.paste(quantized28, (2, 2))
    pixels = list(quantized32.tobytes())
    image28 = Image.frombytes("L", (28, 28), bytes(value * 255 // 16 for value in quantized28.tobytes()))
    image32 = Image.frombytes("L", (32, 32), bytes(value * 255 // 16 for value in pixels))
    return {
        "image28": image28, "image32": image32, "pixels": pixels,
        "inversion_applied": inversion, "source_size": source_size,
        "mode": mode, "invert": invert, "source_border_mean": border_mean,
        "foreground_bbox": foreground_bbox,
    }


def encode_request(pixels, sequence: int = 1, raw_head: bool = False) -> bytes:
    """Build one CRC-validated 1036-byte A5 59 image request."""
    if not isinstance(sequence, int) or isinstance(sequence, bool) or not 0 <= sequence <= 65535:
        raise ValueError("序号必须为0～65535的整数。")
    if not isinstance(raw_head, bool):
        raise ValueError("raw_head必须是布尔值。")
    try:
        values = list(pixels)
    except TypeError as error:
        raise ValueError("像素必须是1024个0～16整数。") from error
    if len(values) != 1024 or any(not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 16 for value in values):
        raise ValueError("像素必须恰好为1024个0～16整数。")
    body = struct.pack("<BBBBHH", 1, 32, 32, int(raw_head), sequence, 1024) + bytes(values)
    return b"\xa5\x59" + body + struct.pack("<H", crc16(body))


def split_request(packet: bytes, chunk_size: int = 256) -> list[bytes]:
    """Validate the exact request before splitting it; never add framing bytes."""
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size <= 0:
        raise ValueError("分段长度必须为正整数。")
    if not isinstance(packet, (bytes, bytearray, memoryview)):
        raise ValueError("请求必须是二进制字节。")
    packet = bytes(packet)
    decoded = scan_packets(packet)
    if len(decoded) != 1 or decoded[0]["kind"] != "request" or decoded[0]["raw"] != packet:
        raise ValueError("只能拆分单个完整图片请求，不接受前导数据或识别回包。")
    return [packet[offset:offset + chunk_size] for offset in range(0, len(packet), chunk_size)]


def _valid_prefix(prefix: str) -> str:
    if (not isinstance(prefix, str) or not prefix or prefix in (".", "..")
            or prefix[-1] in (" ", ".") or re.search(r'[\\/:*?"<>|\x00-\x1f]', prefix)
            or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", prefix, flags=re.I)):
        raise ValueError("导出名称无效，请使用普通文件夹名，不要包含路径分隔符或Windows保留名称。")
    return prefix


def export_request(destination, prefix: str, prepared: dict, sequence: int = 1,
                   raw_head: bool = False) -> dict:
    """Write a fresh ``destination/prefix`` folder and return its metadata.

    An existing folder is rejected rather than overwriting an earlier image.
    The result contains absolute ``directory`` and ``paths`` fields for GUI use.
    """
    prefix = _valid_prefix(prefix)
    packet = encode_request(prepared["pixels"], sequence, raw_head)
    chunks = split_request(packet)
    image32 = Image.frombytes("L", (32, 32), bytes(value * 255 // 16 for value in prepared["pixels"]))
    image28 = image32.crop((2, 2, 30, 30))
    # Verify that the provided preview and wire pixels still refer to one image.
    if prepared["image28"].mode != "L" or prepared["image28"].size != (28, 28) or prepared["image28"].tobytes() != image28.tobytes():
        raise ValueError("28×28预览与待发送像素不一致，请重新转换图片。")
    if prepared["image32"].mode != "L" or prepared["image32"].size != (32, 32) or prepared["image32"].tobytes() != image32.tobytes():
        raise ValueError("32×32预览与待发送像素不一致，请重新转换图片。")
    folder = Path(destination).resolve() / prefix
    try:
        folder.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise FileExistsError(f"导出目录已存在，未覆盖旧文件：{folder}。请换一个导出名称。") from error
    paths = {
        "request_bin": folder / "request.bin", "request_hex": folder / "request.hex",
        "input28": folder / "input28.png", "input32": folder / "input32.png",
        "metadata": folder / "metadata.json",
        "chunks": [folder / f"chunk_{index}.hex" for index in range(1, len(chunks) + 1)],
    }
    paths["request_bin"].write_bytes(packet)
    paths["request_hex"].write_text(packet.hex(" ").upper() + "\n", encoding="ascii")
    image28.save(paths["input28"], format="PNG")
    image32.save(paths["input32"], format="PNG")
    for path, data in zip(paths["chunks"], chunks):
        path.write_text(data.hex(" ").upper() + "\n", encoding="ascii")
    metadata = {
        "directory": str(folder), "protocol": "RV32I image request A5 59 version 1",
        "sequence": sequence, "raw_head": raw_head,
        "frame_size": [32, 32], "content_size": [32, 32] if prepared["mode"] == "frame32" else [28, 28], "pixel_range": [0, 16],
        "packet_bytes": len(packet), "pixel_bytes": 1024,
        "chunk_bytes": [len(chunk) for chunk in chunks],
        "request_sha256": hashlib.sha256(packet).hexdigest(),
        "crc16_ccitt_false": f"{crc16(packet[2:-2]):04X}",
        "crc_wire_little_endian": packet[-2:].hex(" ").upper(),
        "preprocessing": {"source_size": list(prepared["source_size"]),
                          "mode": prepared["mode"], "invert": prepared["invert"],
                          "inversion_applied": bool(prepared["inversion_applied"])},
        "paths": {key: [str(item) for item in value] if isinstance(value, list) else str(value)
                  for key, value in paths.items()},
        "display_note": "input PNGs are reconstructed from sent pixels; no model inference is performed.",
        "send_note": "Send five chunks in order with HEX sending enabled; do not add packet headers or CR/LF bytes.",
    }
    paths["metadata"].write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata
