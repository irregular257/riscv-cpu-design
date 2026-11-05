"""Strict LabSerial packet decoding and pixel rendering, without PC inference.

An A5 59 request contains a 32x32 image.  A 5A 59 response contains detections
and optionally raw network activations; it never contains image pixels.
"""
from __future__ import annotations

import html
import re
import struct
from typing import Iterable

from PIL import Image, ImageDraw


HANZI_LABELS = ("开", "关", "上", "下", "左", "右", "正", "反", "停", "行")
MAX_IMAGE_PIXELS = 4096 * 4096
_CHANNEL = re.compile(r"\b(TX|RX)\s*(?:[←→]|<[-=]?|[-=]?>|:)\s*", re.IGNORECASE)
_TIMESTAMP = re.compile(r"^\s*\[(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+)?\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?\]\s*")


class IncompletePacket(ValueError):
    """The final recognized packet is truncated; no bytes are synthesized."""

    def __init__(self, kind: str, expected: int, received: int, offset: int = 0, complete_packets=None):
        self.kind = kind
        self.expected = expected
        self.received = received
        self.offset = offset
        self.complete_packets = list(complete_packets or [])
        name = {"request": "图像请求", "response": "识别回包", "header": "包头"}.get(kind, kind)
        super().__init__(f"{name}不完整：需要 {expected} 字节，当前仅 {received} 字节（偏移 {offset}）。请补齐数据，不能补零。")


def crc16(data: Iterable[int]) -> int:
    """CRC16/CCITT-FALSE, initial FFFF, poly 1021, no final XOR."""
    crc = 0xFFFF
    for value in data:
        crc ^= int(value) << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _hex_bytes(text: str) -> bytes:
    # A token may be one byte, a contiguous sequence of complete bytes, or an
    # explicitly prefixed byte.  Invalid words are rejected, never stripped.
    tokens = re.split(r"[\s,]+", text.strip())
    values = []
    for token in tokens:
        if not token:
            continue
        if token.lower().startswith("0x"):
            if not re.fullmatch(r"0[xX][0-9A-Fa-f]{2}", token):
                raise ValueError(f"HEX 字节格式错误：{token!r}；0x 前缀后必须是两位十六进制数。")
            token = token[2:]
        if not re.fullmatch(r"(?:[0-9A-Fa-f]{2})+", token):
            raise ValueError(f"HEX 数据含非字节内容或缺少半个字节：{token!r}。请勿使用省略号。")
        values.append(bytes.fromhex(token))
    return b"".join(values)


def extract_hex(text: str, direction: str = "all") -> bytes:
    """Decode pasted HEX or TX/RX logs, excluding timestamps and direction tags.

    Wrapped HEX-only lines inherit the preceding log direction.  A direction
    filter applies to labeled logs; plain HEX remains accepted with any filter.
    Unrelated text, malformed HEX and missing nibbles are always errors.
    """
    if direction not in ("all", "tx", "rx"):
        raise ValueError("direction 必须是 all、tx 或 rx。")
    if not isinstance(text, str):
        raise ValueError("请提供 HEX 文本。")
    text = html.unescape(text).replace("**", "").replace("\ufeff", "")
    lines = text.splitlines()
    pieces = []
    current = None
    saw_channel = False
    for original in lines:
        line = original.strip()
        if not line or re.fullmatch(r"```(?:hex|text|plaintext)?", line, flags=re.I):
            continue
        line = _TIMESTAMP.sub("", line)
        matches = list(_CHANNEL.finditer(line))
        if matches:
            if line[:matches[0].start()].strip():
                raise ValueError("串口日志的 TX/RX 标记前含无法识别的内容。")
            saw_channel = True
            for index, match in enumerate(matches):
                current = match.group(1).lower()
                end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
                payload = line[match.end():end].strip()
                # Ignore the other channel entirely, so textual TX startup
                # commands do not contaminate an RX-only byte capture.
                if direction == "all" or direction == current:
                    pieces.append(_hex_bytes(payload))
        elif direction == "all" or current is None or direction == current:
            pieces.append(_hex_bytes(line))
    data = b"".join(pieces)
    if not data:
        suffix = f"（筛选 {direction.upper()}）" if saw_channel and direction != "all" else ""
        raise ValueError(f"没有可解析的 HEX 字节{suffix}。")
    return data


def _validate_crc(raw: bytes, offset: int) -> None:
    received = struct.unpack_from("<H", raw, len(raw) - 2)[0]
    calculated = crc16(raw[2:-2])
    if received != calculated:
        raise ValueError(f"偏移 {offset} 的数据包 CRC 错误：收到 {received:04X}，计算为 {calculated:04X}。数据不会被自动修复。")


def _validate_detection(box: dict, width: int = 32, height: int = 32) -> None:
    try:
        x, y, w, h, label, score = [int(box[key]) for key in ("x", "y", "w", "h", "label", "confidence")]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("检测框字段不完整。") from error
    if (x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height
            or not 0 <= label <= 9 or not 0 <= score <= 255):
        raise ValueError("检测框越界、类别无效或目标得分不在 0～255。")


def scan_packets(data: bytes) -> list[dict]:
    """Scan complete request/response packets, allowing startup/prefix noise.

    Multiple packets are returned in stream order.  Recognized incomplete or
    corrupt packets raise an exception instead of being padded or substituted.
    "length" is the complete wire packet size, including sync and CRC bytes.
    """
    data = bytes(data)
    packets = []
    position = 0
    while position < len(data):
        starts = [index for sync in (b"\xa5\x59", b"\x5a\x59")
                  if (index := data.find(sync, position)) >= 0]
        if not starts:
            if data[-1:] in (b"\xa5", b"\x5a"):
                raise IncompletePacket("header", 2, 1, len(data) - 1, packets)
            break
        start = min(starts)
        kind = "request" if data[start] == 0xA5 else "response"
        available = len(data) - start
        header_size = 10 if kind == "request" else 12
        if available < header_size:
            raise IncompletePacket(kind, header_size, available, start, packets)
        if kind == "request":
            version, width, height, flags, sequence, payload_length = struct.unpack_from("<BBBBHH", data, start + 2)
            if version != 1 or width != 32 or height != 32 or payload_length != 1024 or flags > 1:
                raise ValueError(f"偏移 {start} 的图像请求头不受支持；当前协议要求版本1、32×32、1024像素、flags为0或1。")
            total = header_size + payload_length + 2
        else:
            version, status, sequence, count, flags, cycles = struct.unpack_from("<BBHBBI", data, start + 2)
            if version != 1 or status > 3 or count > 4 or flags > 1:
                raise ValueError(f"偏移 {start} 的识别回包头无效：版本、状态、框数或 flags 不受支持。")
            total = header_size + count * 8 + (480 if flags & 1 else 0) + 2
        if available < total:
            raise IncompletePacket(kind, total, available, start, packets)
        raw = data[start:start + total]
        _validate_crc(raw, start)
        packet = dict(kind=kind, version=version, sequence=sequence, flags=flags,
                      length=total, raw=raw, crc_ok=True, offset=start)
        if kind == "request":
            pixels = list(raw[10:-2])
            if any(value > 16 for value in pixels):
                raise ValueError("图像包像素超出 0～16，不能按当前定点灰度协议显示。")
            packet.update(width=width, height=height, pixels=pixels, payload_length=payload_length)
        else:
            boxes = []
            for index in range(count):
                x, y, w, h, label, confidence, reserved0, reserved1 = raw[12 + index * 8:20 + index * 8]
                box = dict(x=x, y=y, w=w, h=h, label=label, confidence=confidence)
                _validate_detection(box)
                if reserved0 or reserved1:
                    raise ValueError("检测框保留字节不为零，可能使用了不同的串口协议。")
                boxes.append(box)
            packet.update(status=status, cycles=cycles, detections=boxes)
            if flags & 1:
                packet["head_q4"] = list(struct.unpack_from("<240h", raw, 12 + count * 8))
        packets.append(packet)
        position = start + total
    if not packets:
        raise ValueError("未找到 A5 59 图像请求包或 5A 59 识别回包；裸像素请使用原始像素模式。")
    return packets


class PacketStream:
    """Incremental, bounded request/response receiver for a local COM stream.

    ``feed`` returns every valid completed packet immediately.  ``pending`` is
    a bytearray holding only the final candidate fragment (at most 1035 bytes
    after feed).  Bad complete frames never produce results: errors are stored
    as readable strings in ``errors`` (newest 64); callers may clear that list.
    Startup ASCII and bytes outside a recognized packet are discarded.
    """

    def __init__(self):
        self.pending = bytearray()
        self.errors = []

    def _error(self, error):
        self.errors.append(str(error))
        del self.errors[:-64]

    def feed(self, data: bytes) -> list[dict]:
        self.pending.extend(data)
        completed = []
        while self.pending:
            starts = [i for sync in (b"\xa5\x59", b"\x5a\x59")
                      if (i := self.pending.find(sync)) >= 0]
            if not starts:
                keep = self.pending[-1:] if self.pending[-1:] in (b"\xa5", b"\x5a") else b""
                self.pending[:] = keep
                break
            start = min(starts)
            if start:
                del self.pending[:start]
            is_request = self.pending[0] == 0xA5
            header_size = 10 if is_request else 12
            if len(self.pending) < header_size:
                break
            if is_request:
                version, width, height, flags, sequence, payload_length = struct.unpack_from("<BBBBHH", self.pending, 2)
                valid = version == 1 and width == 32 and height == 32 and flags <= 1 and payload_length == 1024
                expected = 1036
            else:
                version, status, sequence, count, flags, cycles = struct.unpack_from("<BBHBBI", self.pending, 2)
                valid = version == 1 and status <= 3 and count <= 4 and flags <= 1
                expected = 14 + count * 8 + (480 if flags & 1 else 0)
            if not valid:
                self._error("忽略无效串口包头：版本、尺寸、长度、状态、框数或 flags 不受支持。")
                del self.pending[:1]
                continue
            if len(self.pending) < expected:
                break
            frame = bytes(self.pending[:expected])
            del self.pending[:expected]
            try:
                completed.extend(scan_packets(frame))
            except ValueError as error:
                self._error(error)
        return completed


def raw_image(data: bytes, width: int, height: int, format: str = "gray16") -> Image.Image:
    """Render exact raw grayscale or RGB565 bytes; never infer missing pixels."""
    if (not isinstance(width, int) or not isinstance(height, int) or isinstance(width, bool)
            or isinstance(height, bool) or width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS):
        raise ValueError("图像宽高必须为正整数，且总像素不能超过 4096×4096。")
    if format not in ("gray16", "gray8", "rgb565le", "rgb565be"):
        raise ValueError("不支持的像素格式。")
    data = bytes(data)
    expected = width * height * (2 if format.startswith("rgb565") else 1)
    if len(data) != expected:
        raise ValueError(f"像素字节数不符：{width}×{height} 的 {format} 需要 {expected} 字节，实际 {len(data)} 字节。")
    if format in ("gray16", "gray8"):
        if format == "gray16":
            if any(value > 16 for value in data):
                raise ValueError("0～16 灰度模式中存在大于16的像素；8位灰度请改用 gray8。")
            data = bytes(value * 255 // 16 for value in data)
        return Image.frombytes("L", (width, height), data)
    byteorder = "little" if format.endswith("le") else "big"
    colors = []
    for index in range(0, len(data), 2):
        value = int.from_bytes(data[index:index + 2], byteorder)
        colors.append((((value >> 11) & 31) * 255 // 31,
                       ((value >> 5) & 63) * 255 // 63,
                       (value & 31) * 255 // 31))
    image = Image.new("RGB", (width, height))
    image.putdata(colors)
    return image


def request_image(packet: dict) -> Image.Image:
    """Return pixels only from a validated image request, never a response."""
    if packet.get("kind") != "request":
        raise ValueError("识别回包不包含图像像素；请加载对应的原始图像或 A5 59 发送包。")
    return raw_image(bytes(packet["pixels"]), int(packet["width"]), int(packet["height"]), "gray16")


def draw_overlay(image: Image.Image, detections: Iterable[dict], model: str = "digit", scale: int = 12) -> Image.Image:
    """Enlarge a real source frame and annotate FPGA detections; no inference."""
    if image.size != (32, 32):
        raise ValueError("当前检测框坐标基于32×32输入，叠框底图必须为32×32。")
    if model not in ("digit", "hanzi"):
        raise ValueError("model 必须是 digit 或 hanzi。")
    if not isinstance(scale, int) or isinstance(scale, bool) or not 1 <= scale <= 64:
        raise ValueError("放大倍数必须为1～64的整数。")
    output = image.convert("RGB").resize((32 * scale, 32 * scale), Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(output)
    for box in detections:
        _validate_detection(box)
        x, y, w, h = (int(box[key]) for key in ("x", "y", "w", "h"))
        draw.rectangle((x * scale, y * scale, (x + w) * scale - 1, (y + h) * scale - 1),
                       outline="#ffd400", width=max(1, min(scale, 2)))
        # Use ASCII on the raster so the core does not depend on installed CJK
        # fonts.  GUI text can show HANZI_LABELS[label] alongside the image.
        label = str(box["label"]) if model == "digit" else f"ID{box['label']}"
        bounds = draw.textbbox((0, 0), label)
        text_width, text_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        if output.width >= text_width + 4 and output.height >= text_height + 4:
            tx = min(x * scale + 2, output.width - text_width - 2)
            ty = max(1, y * scale - text_height - 3)
            draw.text((tx, ty), label, fill="#00ffff", stroke_width=1, stroke_fill="black")
    return output
