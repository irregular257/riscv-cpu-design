#!/usr/bin/env python3
"""Remote LabSerial image packets and FPGA detection rendering; no PC inference.

GUI path: encode image -> copy HEX -> paste into LabSerial HEX-send -> paste
the returned HEX into this program. Local COM is optional via exchange.
"""
import argparse
import html
import json
from pathlib import Path
import re
import struct
import time

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
VISION = ROOT / "software/vision"


def crc16(data):
    crc = 0xffff
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xffff if crc & 0x8000 else (crc << 1) & 0xffff
    return crc


def image_pixels(path, invert=False):
    image = Image.open(path).convert("L").resize((32, 32), Image.Resampling.BILINEAR)
    if invert:
        image = ImageOps.invert(image)
    return [(x * 16 + 127) // 255 for x in image.getdata()]


def encode(pixels, seq=1, raw_head=False):
    if len(pixels) != 1024 or any(not 0 <= int(v) <= 16 for v in pixels):
        raise ValueError("Expected exactly 1024 grayscale integers in 0..16")
    body = struct.pack("<BBBBHH", 1, 32, 32, int(raw_head), seq & 0xffff, 1024) + bytes(pixels)
    return b"\xa5\x59" + body + struct.pack("<H", crc16(body))


def parse_hex(text):
    # Clipboard exports may wrap RX labels in Markdown and escape characters
    # inside HEX bytes (e.g. "&#x35;A" for "5A"). Decode presentation only;
    # never synthesize missing payload bytes or parse timestamp/TX fields.
    text = html.unescape(text).replace("**", "")
    # LabSerial exported logs may include timestamps and TX entries. Only RX
    # portions are data; do not accidentally parse timestamps as payload bytes.
    rx = re.findall(r"RX\s*(?:←|<[-=]?|:)\s*([^\r\n]*)", text, flags=re.I)
    data = " ".join(rx) if rx else text.strip()
    data = re.sub(r"\s+", " ", data).strip()
    if not data:
        raise ValueError("No received HEX data")
    tokens = data.split()
    if any(not re.fullmatch(r"[0-9a-fA-F]{2}", t) for t in tokens):
        if re.fullmatch(r"(?:[0-9a-fA-F]{2})+", data):
            return bytes.fromhex(data)
        raise ValueError("Paste HEX bytes (e.g. 5A 59 01 ...), or an RX-only LabSerial log")
    return bytes.fromhex(data)


def decode(data):
    for start in range(max(0, len(data) - 1)):
        if data[start:start + 2] != b"\x5a\x59" or len(data) - start < 14:
            continue
        version, status, seq, count, flags, cycles = struct.unpack_from("<BBHBBI", data, start + 2)
        if version != 1 or count > 4 or flags > 1:
            continue
        length = 12 + count * 8 + (480 if flags & 1 else 0) + 2
        if len(data) - start < length:
            raise ValueError(f"Incomplete response: need {length} bytes, received {len(data) - start}")
        response = data[start:start + length]
        if crc16(response[2:-2]) != struct.unpack_from("<H", response, length - 2)[0]:
            raise ValueError("FPGA response CRC does not match")
        boxes = []
        for i in range(count):
            x, y, width, height, label, confidence, _, _ = response[12 + i * 8:20 + i * 8]
            if label > 9 or not width or not height or x + width > 32 or y + height > 32:
                raise ValueError("Invalid FPGA detection fields")
            boxes.append(dict(x=x, y=y, w=width, h=height, label=label, confidence=confidence))
        result = dict(version=version, status=status, sequence=seq, detections=boxes,
                      cycles=cycles, milliseconds_at_27mhz=cycles / 27000, crc_ok=True)
        if flags & 1:
            result["head_q4"] = list(struct.unpack_from("<240h", response, 12 + count * 8))
        return result
    raise ValueError("No complete 5A 59 response found")


def render(pixels, detections, scale=12):
    image = Image.new("RGB", (32, 32))
    image.putdata([(int(p) * 255 // 16,) * 3 for p in pixels])
    image = image.resize((32 * scale, 32 * scale), Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(image)
    for box in detections:
        x, y, w, h = [box[n] for n in ("x", "y", "w", "h")]
        draw.rectangle((x * scale, y * scale, (x + w) * scale - 1, (y + h) * scale - 1), outline="#ffd400", width=2)
        draw.text((x * scale + 2, y * scale + 2), f"{box['label']} {box['confidence']/255:.0%}", fill="#00ffff", stroke_width=1, stroke_fill="black")
    return image


def get_pixels(args):
    if args.image:
        return image_pixels(args.image, args.invert)
    vectors = json.loads((VISION / "test_vectors.json").read_text())
    return vectors[args.vector or 0]["pixels"]


def gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox
    from PIL import ImageTk
    window = tk.Tk(); window.title("TinyGridYOLO FPGA / LabSerial")
    vectors = json.loads((VISION / "test_vectors.json").read_text())
    state = {"pixels": vectors[0]["pixels"], "photo": None, "sequence": 1}
    canvas = tk.Label(window); canvas.grid(row=0, column=0, rowspan=4, padx=12, pady=12)
    status = tk.StringVar(value="PC only encodes/displays. Inference executes on the FPGA.")
    def show(detections=()):
        state["photo"] = ImageTk.PhotoImage(render(state["pixels"], detections))
        canvas.configure(image=state["photo"])
    def load():
        path = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All", "*.*")])
        if path:
            state["pixels"] = image_pixels(path); show()
    def invert():
        state["pixels"] = [16 - v for v in state["pixels"]]; show()
    def copy_packet():
        packet = encode(state["pixels"], state["sequence"])
        window.clipboard_clear(); window.clipboard_append(packet.hex(" ").upper())
        status.set(f"Copied {len(packet)} bytes. Enable HEX-send in LabSerial, paste and send ONCE.")
        state["sequence"] = (state["sequence"] + 1) & 0xffff
    def parse():
        try:
            result = decode(parse_hex(received.get("1.0", "end")))
            if result["status"]:
                raise ValueError(f"FPGA returned status {result['status']} (1=header,2=CRC,3=pixel range)")
            show(result["detections"])
            status.set(f"FPGA: {len(result['detections'])} detections, {result['cycles']} cycles, CRC OK")
        except Exception as error:
            messagebox.showerror("Response", str(error))
    buttons = tk.Frame(window); buttons.grid(row=0, column=1, sticky="w", padx=8)
    for text, command in (("Load image", load), ("Invert", invert), ("Copy request HEX", copy_packet), ("Parse FPGA response", parse)):
        tk.Button(buttons, text=text, command=command).pack(side="left", padx=3)
    tk.Label(window, text="Paste received HEX bytes or RX log here:").grid(row=1, column=1, sticky="w")
    received = tk.Text(window, width=72, height=18); received.grid(row=2, column=1, padx=8)
    tk.Label(window, textvariable=status, wraplength=850).grid(row=4, column=0, columnspan=2, pady=8)
    show(); window.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    enc = commands.add_parser("encode")
    enc.add_argument("--image", type=Path); enc.add_argument("--vector", type=int, default=0)
    enc.add_argument("--invert", action="store_true"); enc.add_argument("--sequence", type=int, default=1)
    enc.add_argument("--raw-head", action="store_true"); enc.add_argument("--output", type=Path, default=VISION / "images/request")
    dec = commands.add_parser("decode"); dec.add_argument("received", type=Path)
    dec.add_argument("--image", type=Path); dec.add_argument("--vector", type=int, default=0)
    dec.add_argument("--invert", action="store_true"); dec.add_argument("--output", type=Path, default=VISION / "images/response.png")
    com = commands.add_parser("exchange"); com.add_argument("--port", required=True)
    com.add_argument("--baud", type=int, default=115200); com.add_argument("--timeout", type=float, default=60)
    com.add_argument("--image", type=Path); com.add_argument("--vector", type=int, default=0);com.add_argument("--invert", action="store_true")
    com.add_argument("--output", type=Path, default=VISION / "images/response.png")
    commands.add_parser("gui")
    args = parser.parse_args()
    if args.command == "gui":
        return gui()
    pixels = get_pixels(args)
    if args.command == "encode":
        packet = encode(pixels, args.sequence, args.raw_head)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix(".bin").write_bytes(packet)
        args.output.with_suffix(".hex").write_text(packet.hex(" ").upper() + "\n")
        render(pixels, ()).save(args.output.with_suffix(".png"))
        print(f"{len(packet)} bytes; paste {args.output.with_suffix('.hex')} in LabSerial HEX-send")
        return
    if args.command == "decode":
        text = args.received.read_text(encoding="utf-8")
        result = decode(parse_hex(text))
    else:
        import serial
        with serial.Serial(args.port, args.baud, timeout=.1) as port:
            port.reset_input_buffer(); port.write(encode(pixels)); data = bytearray()
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                data.extend(port.read(4096))
                try:
                    result = decode(data);break
                except ValueError as error:
                    if "Incomplete" not in str(error) and "No complete" not in str(error):raise
            else:raise TimeoutError("No complete FPGA response; send only after READY and do not pipeline requests")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    render(pixels, result["detections"]).save(args.output)
    args.output.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
