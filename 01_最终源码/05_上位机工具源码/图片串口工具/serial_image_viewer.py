"""串口 HEX / 图像包转 PNG。仅还原像素和显示 FPGA 结果，不运行识别模型。"""
from __future__ import annotations

import argparse
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from serial_image_codec import (
    HANZI_LABELS, PacketStream, draw_overlay, extract_hex,
    raw_image, request_image, scan_packets,
)

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" if getattr(sys, "frozen", False) or (ROOT / "examples").is_dir() else ROOT.parents[1] / "串口图片工具示例"
FORMATS = {"灰度 0～16（当前 FPGA）": "gray16", "灰度 0～255": "gray8",
           "RGB565 小端": "rgb565le", "RGB565 大端": "rgb565be"}
STATUS_NAMES = {0: "成功", 1: "请求头错误", 2: "请求 CRC 错误", 3: "像素范围错误"}
MAX_FILE_BYTES = 8 * 1024 * 1024


def read_data_file(path, direction="all"):
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("文件超过8 MiB，请按帧拆分后读取。")
    if path.suffix.lower() in (".bin", ".raw"):
        return path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            text = path.read_text(encoding=encoding)
            break
        except UnicodeError:
            continue
    else:
        raise ValueError("无法读取文本编码；原始二进制请使用 .bin 或 .raw 后缀。")
    return extract_hex(text, direction)


def prepare_reference(path):
    with Image.open(path) as source:
        image = source.convert("L")
    if image.size == (28, 28):
        padded = Image.new("L", (32, 32), 0)
        padded.paste(image, (2, 2))
        return padded
    if image.size != (32, 32):
        raise ValueError("叠框原图须为28×28或32×32；28×28会居中补2像素黑边。请使用实际发送的图像。")
    return image


class SerialImageViewer:
    def __init__(self, window, container=None, manage_window=True, initialize_ports=True):
        self.window = window
        self.container = container if container is not None else window
        if manage_window:
            window.title("串口数据转图片 · FPGA LAB")
            screen_w, screen_h = window.winfo_screenwidth(), window.winfo_screenheight()
            window_w, window_h = min(1120, screen_w - 60), min(780, screen_h - 100)
            window.geometry(f"{window_w}x{window_h}+{max(10, (screen_w-window_w)//2)}+{max(10, (screen_h-window_h)//2-20)}")
            window.minsize(min(960, window_w), min(660, window_h))
        self.packets = []
        self.frames = {}
        self.replies = {}
        self.reference = None
        self.reference_name = ""
        self.source_image = None
        self.protocol_canvas = False
        self.preview_image = None
        self.current_response = None
        self.current_info = ""
        self.photo = None
        self.port = None
        self.serial_thread = None
        self.stop_event = threading.Event()
        self.events = queue.Queue(maxsize=128)
        self.stream = PacketStream()
        self.raw_pending = bytearray()
        self.received_count = 0
        self.receive_settings = None
        self.mode = tk.StringVar(value="自动识别协议包")
        self.direction = tk.StringVar(value="全部")
        self.pixel_format = tk.StringVar(value=next(iter(FORMATS)))
        self.width = tk.StringVar(value="28")
        self.height = tk.StringVar(value="28")
        self.model = tk.StringVar(value="数字 0～9")
        self.crop = tk.BooleanVar(value=True)
        self.clock_mhz = tk.StringVar(value="27")
        self.port_name = tk.StringVar()
        self.baud = tk.StringVar(value="115200")
        self.status = tk.StringVar(value="就绪：打开示例请求或粘贴完整 HEX 数据。")
        self._build()
        if initialize_ports:
            self.refresh_ports()
        if manage_window:
            window.protocol("WM_DELETE_WINDOW", self.close)
        window.after(100, self.poll_serial)

    def _build(self):
        outer = ttk.Frame(self.container, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="串口数据转图片", font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        ttk.Label(outer, text="网页远程串口：复制 HEX 后粘贴；本地 USB 串口：选择 COM 接收。", wraplength=1000).pack(anchor="w", pady=(4, 10))
        serial_row = ttk.LabelFrame(outer, text="本地 COM 接收（远程网站不自动映射为本机 COM）", padding=7)
        serial_row.pack(fill="x")
        ttk.Label(serial_row, text="串口").pack(side="left")
        self.port_choice = ttk.Combobox(serial_row, textvariable=self.port_name, width=12)
        self.port_choice.pack(side="left", padx=6)
        ttk.Button(serial_row, text="刷新", command=self.refresh_ports).pack(side="left")
        ttk.Label(serial_row, text="波特率").pack(side="left", padx=(14, 4))
        ttk.Combobox(serial_row, textvariable=self.baud, values=("115200", "57600", "9600", "230400"), width=10).pack(side="left")
        self.connect_button = ttk.Button(serial_row, text="开始接收", command=self.toggle_serial)
        self.connect_button.pack(side="left", padx=8)
        ttk.Label(serial_row, text="8N1 · 只接收，不发送").pack(side="left", padx=6)

        split = ttk.Panedwindow(outer, orient="horizontal")
        split.pack(fill="both", expand=True, pady=10)
        left, right = ttk.Frame(split), ttk.Frame(split)
        split.add(left, weight=3)
        split.add(right, weight=2)
        options = ttk.Frame(left)
        options.pack(fill="x")
        ttk.Label(options, text="解析方式").grid(row=0, column=0, sticky="w")
        ttk.Combobox(options, textvariable=self.mode, values=("自动识别协议包", "原始像素"), state="readonly", width=18).grid(row=0, column=1, padx=5, sticky="w")
        ttk.Label(options, text="日志通道").grid(row=0, column=2, padx=(10, 0))
        ttk.Combobox(options, textvariable=self.direction, values=("全部", "仅 TX", "仅 RX"), state="readonly", width=8).grid(row=0, column=3, padx=5)
        ttk.Label(options, text="裸像素宽×高").grid(row=1, column=0, sticky="w", pady=6)
        dims = ttk.Frame(options)
        dims.grid(row=1, column=1, sticky="w")
        ttk.Entry(dims, textvariable=self.width, width=5).pack(side="left", padx=5)
        ttk.Label(dims, text="×").pack(side="left")
        ttk.Entry(dims, textvariable=self.height, width=5).pack(side="left", padx=5)
        ttk.Combobox(options, textvariable=self.pixel_format, values=tuple(FORMATS), state="readonly", width=22).grid(row=1, column=2, columnspan=2, sticky="w", padx=(10, 0))

        actions = ttk.Frame(left)
        actions.pack(fill="x", pady=(3, 6))
        ttk.Button(actions, text="打开 HEX / BIN", command=self.open_data).pack(side="left")
        ttk.Button(actions, text="粘贴", command=self.paste).pack(side="left", padx=5)
        ttk.Button(actions, text="解析数据", command=self.parse_text).pack(side="left")
        ttk.Button(actions, text="清空数据", command=lambda: self.text.delete("1.0", "end")).pack(side="left", padx=5)
        self.text = tk.Text(left, height=12, width=60, wrap="word", font=("Consolas", 10), undo=True)
        self.text.pack(fill="both", expand=True)
        ttk.Label(left, text="支持空格 HEX、连续 HEX、0x字节及带时间戳的 TX/RX 日志。", wraplength=560).pack(anchor="w", pady=4)
        ttk.Label(left, text="已校验的数据包（选择可重新查看）").pack(anchor="w", pady=(5, 3))
        self.packet_list = tk.Listbox(left, height=4, exportselection=False, font=("Microsoft YaHei UI", 10))
        self.packet_list.pack(fill="x")
        self.packet_list.bind("<<ListboxSelect>>", self.select_packet)
        ttk.Button(left, text="载入数字3演示（真实请求与实板回包）", command=self.demo).pack(anchor="w", pady=(7, 0))

        display_options = ttk.Frame(right)
        display_options.pack(fill="x", padx=(12, 0))
        ttk.Label(display_options, text="类别模式").pack(side="left")
        combo = ttk.Combobox(display_options, textvariable=self.model, values=("数字 0～9", "汉字十类"), state="readonly", width=12)
        combo.pack(side="left", padx=5)
        combo.bind("<<ComboboxSelected>>", lambda _: self.render())
        ttk.Checkbutton(right, text="仅显示中心28×28（裁去协议图的2像素黑边）", variable=self.crop, command=self.render).pack(anchor="w", padx=(12, 0), pady=7)
        self.picture = tk.Label(right, text="等待图像像素\n\nA5 59 请求包含图片\n5A 59 回包只有识别结果", bg="#171d27", fg="#dae3f0", font=("Microsoft YaHei UI", 12), width=32, height=17)
        self.picture.pack(fill="both", expand=True, padx=(12, 0))
        refs = ttk.Frame(right)
        refs.pack(fill="x", padx=(12, 0), pady=7)
        ttk.Button(refs, text="载入原图用于叠框", command=self.open_reference).pack(side="left")
        ttk.Button(refs, text="移除手动原图", command=self.remove_reference).pack(side="left", padx=5)
        saves = ttk.Frame(right)
        saves.pack(fill="x", padx=(12, 0))
        ttk.Button(saves, text="保存当前显示 PNG", command=self.save_preview).pack(side="left")
        ttk.Button(saves, text="保存原始像素 PNG", command=self.save_source).pack(side="left", padx=5)

        self.details = tk.Text(outer, height=4, wrap="word", state="disabled", font=("Microsoft YaHei UI", 10))
        self.details.pack(fill="x", pady=(0, 6))
        bottom = ttk.Frame(outer)
        bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status, wraplength=860).pack(side="left", fill="x", expand=True)
        ttk.Label(bottom, text="时钟 MHz").pack(side="left")
        ttk.Entry(bottom, textvariable=self.clock_mhz, width=6).pack(side="left", padx=5)
        self.set_details("识别回包不含图片像素。叠框需要同序号的图片请求，或明确载入实际发送的原图。\n目标得分不是类别正确概率；程序只显示 FPGA 返回的数据，不在电脑上推理。")

    def set_details(self, text):
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("end", text)
        self.details.configure(state="disabled")

    def error(self, error):
        self.status.set(f"未转换：{error}")
        messagebox.showerror("数据无法转换", str(error), parent=self.window)

    def direction_key(self):
        return {"全部": "all", "仅 TX": "tx", "仅 RX": "rx"}[self.direction.get()]

    def settings(self):
        width, height = int(self.width.get()), int(self.height.get())
        if not (1 <= width <= 2048 and 1 <= height <= 2048):
            raise ValueError("裸像素宽高请设为1～2048。当前模型常用28×28或32×32。")
        return width, height, FORMATS[self.pixel_format.get()]

    def paste(self):
        try:
            value = self.window.clipboard_get()
            if len(value) > MAX_FILE_BYTES:
                raise ValueError("剪贴板超过8 MiB，请分帧粘贴。")
            if self.text.get("1.0", "end").strip():
                self.text.insert("end", "\n")
            self.text.insert("end", value)
            self.status.set("已追加剪贴板内容；分段数据请按原顺序粘贴后解析。")
        except (tk.TclError, ValueError) as error:
            self.error(error)

    def open_data(self):
        path = filedialog.askopenfilename(initialdir=EXAMPLES if EXAMPLES.exists() else ROOT,
            filetypes=[("串口数据", "*.hex *.txt *.log *.bin *.raw"), ("全部文件", "*.*")])
        if path:
            self.load_path(path)

    def load_path(self, path):
        try:
            data = read_data_file(path, self.direction_key())
            self.text.delete("1.0", "end")
            self.text.insert("end", data.hex(" ").upper())
            self.process(data, f"文件 {Path(path).name}")
        except (ValueError, OSError) as error:
            self.error(error)

    def parse_text(self):
        try:
            text = self.text.get("1.0", "end")
            if len(text) > MAX_FILE_BYTES:
                raise ValueError("数据超过8 MiB，请分帧处理。")
            self.process(extract_hex(text, self.direction_key()), "粘贴 HEX")
        except (ValueError, OSError) as error:
            self.error(error)

    def process(self, data, origin):
        if self.mode.get() == "原始像素":
            self.show_raw(data, self.settings(), origin)
            return
        packets = scan_packets(data)
        self.accept_packets(packets, origin)
        self.status.set(f"已解析 {len(data)} 字节、{len(packets)} 个数据包，CRC全部正确。")

    def show_raw(self, data, settings, origin):
        width, height, fmt = settings
        image = raw_image(data, width, height, fmt)
        self.source_image = image
        self.protocol_canvas = False
        self.current_response = None
        self.current_info = f"{origin}；原始像素 {width}×{height}，{len(data)}字节，{fmt}。"
        self.render()
        self.status.set("原始像素已转换；此模式没有协议CRC。")

    def accept_packets(self, packets, origin):
        for packet in packets:
            seq = packet["sequence"]
            if packet["kind"] == "request":
                self.frames[seq] = request_image(packet)
                # A new frame reusing an old sequence must not reuse a stale reply.
                self.replies.pop(seq, None)
            else:
                packet = dict(packet)
                packet['_paired_image'] = self.frames.get(seq)
                self.replies[seq] = packet
            self.packets.append((packet, origin))
            label = "图片请求" if packet["kind"] == "request" else "识别回包"
            self.packet_list.insert("end", f"{label}  序号 {seq}  {packet['length']}字节  CRC OK")
            if len(self.packets) > 64:
                self.packets.pop(0)
                self.packet_list.delete(0)
            while len(self.frames) > 32:
                self.frames.pop(next(iter(self.frames)))
            while len(self.replies) > 32:
                self.replies.pop(next(iter(self.replies)))
        if packets:
            self.packet_list.selection_clear(0, "end")
            self.packet_list.selection_set("end")
            self.packet_list.see("end")
            self.show_packet(*self.packets[-1])

    def select_packet(self, _event=None):
        selected = self.packet_list.curselection()
        if selected:
            self.show_packet(*self.packets[selected[0]])

    def show_packet(self, packet, origin):
        seq = packet["sequence"]
        self.protocol_canvas = True
        if packet["kind"] == "request":
            self.source_image = request_image(packet)
            self.current_response = None
            self.current_info = f"{origin}；图像请求序号 {seq}；32×32、1024像素（0～16），CRC正确。"
        else:
            self.current_response = packet
            self.source_image = packet.get('_paired_image')
            match = "同序号请求中的真实像素"
            if self.source_image is None and self.reference is not None:
                self.source_image = self.reference
                match = f"手动原图 {self.reference_name}（未自动核对与序号对应）"
            if self.source_image is None:
                match = "没有原图：回包不含像素，请导入同序号请求或载入实际发送的原图"
            self.current_info = f"{origin}；识别回包序号 {seq}；CRC正确。底图来源：{match}。"
        self.render()

    def render(self):
        image = self.source_image
        packet = self.current_response
        lines = [self.current_info] if self.current_info else []
        if packet is not None:
            lines.append(f"FPGA状态：{STATUS_NAMES[packet['status']]}；检测数：{len(packet['detections'])}；周期：{packet['cycles']}。")
            try:
                mhz = float(self.clock_mhz.get())
                if mhz <= 0:
                    raise ValueError()
                lines[-1] += f" 按{mhz:g}MHz换算 {packet['cycles'] / (mhz * 1000):.3f}ms（不含串口传输）。"
            except ValueError:
                lines.append("时钟频率无效，未换算耗时。")
            for box in packet["detections"]:
                label = str(box["label"]) if self.model.get().startswith("数字") else HANZI_LABELS[box["label"]]
                lines.append(f"类别：{label}；目标得分：{box['confidence']}/255；框 xywh：({box['x']}, {box['y']}, {box['w']}, {box['h']})，坐标基于32×32。")
            if packet["flags"] & 1:
                lines.append("另含240个INT16检测头数值；它们不是图像像素。")
        if image is None:
            self.preview_image = None
            self.photo = None
            self.picture.configure(image="", text="回包只有识别结果，没有图片像素\n\n请先导入同序号图片请求\n或载入实际发送的原图")
        else:
            if packet is not None and packet["status"] == 0:
                rendered = draw_overlay(image, packet["detections"],
                    model="digit" if self.model.get().startswith("数字") else "hanzi", scale=12)
                if self.crop.get():
                    rendered = rendered.crop((24, 24, 360, 360))
            else:
                shown = image.crop((2, 2, 30, 30)) if self.crop.get() and self.protocol_canvas and image.size == (32, 32) else image
                scale = min(12, max(1, 384 // max(shown.size)))
                rendered = shown.resize((shown.width * scale, shown.height * scale), Image.Resampling.NEAREST)
            self.preview_image = rendered
            display = rendered.copy()
            display.thumbnail((440, 390), Image.Resampling.NEAREST)
            self.photo = ImageTk.PhotoImage(display)
            self.picture.configure(image=self.photo, text="", width=0, height=0)
        self.set_details("\n".join(lines))

    def open_reference(self):
        path = filedialog.askopenfilename(initialdir=EXAMPLES if EXAMPLES.exists() else ROOT,
            filetypes=[("图像", "*.png *.bmp *.jpg *.jpeg"), ("全部", "*.*")])
        if path:
            try:
                self.reference = prepare_reference(path)
                self.reference_name = Path(path).name
                selected = self.packet_list.curselection()
                if selected:
                    self.show_packet(*self.packets[selected[0]])
                else:
                    self.source_image = self.reference
                    self.protocol_canvas = True
                    self.current_response = None
                    self.current_info = f"手动原图：{self.reference_name}，尚无FPGA识别结果。"
                    self.render()
                self.status.set("原图已载入；仅用于显示，不会在电脑上识别。")
            except (ValueError, OSError) as error:
                self.error(error)

    def remove_reference(self):
        self.reference = None
        self.reference_name = ""
        selected = self.packet_list.curselection()
        if selected:
            self.show_packet(*self.packets[selected[0]])
        else:
            self.source_image = None
            self.render()

    def save_preview(self):
        self.save_image(self.preview_image, "当前显示（放大及检测框）")

    def save_source(self):
        self.save_image(self.source_image, "原始像素（不含检测框）")

    def save_image(self, image, description):
        if image is None:
            self.error(ValueError("没有可保存的图像像素；识别回包本身无法还原原图。"))
            return
        path = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG图片", "*.png")], title=f"保存{description}")
        if path:
            try:
                image.save(path, format="PNG")
                self.status.set(f"已保存 {description}：{path}")
            except OSError as error:
                self.error(error)

    def demo(self):
        try:
            self.mode.set("自动识别协议包")
            request = read_data_file(EXAMPLES / "数字3_图片请求.hex")
            reply = read_data_file(EXAMPLES / "数字3_识别回包.hex")
            self.text.delete("1.0", "end")
            self.text.insert("end", "TX → " + request.hex(" ").upper() + "\nRX ← " + reply.hex(" ").upper())
            self.accept_packets(scan_packets(request + reply), "数字3演示：此前真实请求及用户粘贴的实板回包")
            self.status.set("演示已还原数字3，并叠加此前实板回包中的框；当前未连接开发板。")
        except (OSError, ValueError) as error:
            self.error(error)

    def refresh_ports(self):
        try:
            from serial.tools import list_ports
            ports = [p.device for p in list_ports.comports()]
            self.port_choice["values"] = ports
            if ports and not self.port_name.get():
                self.port_name.set(ports[0])
        except ImportError:
            self.status.set("尚未安装pyserial；HEX粘贴与文件转换仍可使用。")

    def toggle_serial(self):
        if self.serial_thread is not None:
            self.stop_serial()
            return
        try:
            import serial
            name = self.port_name.get().strip()
            if not name:
                raise ValueError("请选择本地 COM 口；远程网站请使用HEX复制粘贴。")
            baud = int(self.baud.get())
            if baud <= 0:
                raise ValueError("波特率必须为正整数。")
            self.receive_settings = (self.mode.get(), self.settings())
            self.port = serial.Serial(name, baudrate=baud, bytesize=8, parity="N", stopbits=1, timeout=0.1)
            self.stream, self.raw_pending = PacketStream(), bytearray()
            self.stop_event = threading.Event()
            self.received_count = 0
            # A separate queue per connection prevents an old stream crossing sessions.
            self.events = queue.Queue(maxsize=128)
            self.serial_thread = threading.Thread(target=self.serial_reader,
                args=(self.port, self.stop_event, self.events), daemon=True)
            self.serial_thread.start()
            self.connect_button.configure(text="停止接收")
            self.status.set(f"正在接收 {name} / {baud} 8N1；解析设置在本次连接中固定。")
        except (ImportError, ValueError, OSError) as error:
            self.error(error)

    @staticmethod
    def serial_reader(port, stop, events):
        try:
            while not stop.is_set():
                data = port.read(min(4096, max(1, port.in_waiting)))
                if data:
                    try:
                        events.put(("data", data), timeout=0.5)
                    except queue.Full:
                        raise RuntimeError("显示处理落后，接收已停止；请降低发送速率并重新接收。")
        except Exception as error:
            if not stop.is_set():
                try:
                    events.put(("error", str(error)), timeout=1)
                except queue.Full:
                    pass
        finally:
            port.close()

    def stop_serial(self):
        self.stop_event.set()
        if self.port is not None:
            try:
                self.port.close()
            except OSError:
                pass
        if self.serial_thread is not None:
            self.serial_thread.join(timeout=0.4)
        self.serial_thread, self.port = None, None
        self.connect_button.configure(text="开始接收")
        self.status.set(f"接收已停止，累计 {self.received_count} 字节。")

    def poll_serial(self):
        try:
            for _ in range(32):
                try:
                    kind, value = self.events.get_nowait()
                except queue.Empty:
                    break
                if kind == "error":
                    self.stop_serial()
                    self.status.set(f"串口停止：{value}")
                    continue
                self.received_count += len(value)
                self.text.insert("end", value.hex(" ").upper() + " ")
                if int(self.text.count("1.0", "end", "chars")[0]) > 100000:
                    self.text.delete("1.0", "end-90000c")
                self.text.see("end")
                mode, settings = self.receive_settings
                try:
                    if mode == "原始像素":
                        width, height, fmt = settings
                        size = width * height * (2 if fmt.startswith("rgb565") else 1)
                        self.raw_pending.extend(value)
                        while len(self.raw_pending) >= size:
                            frame = bytes(self.raw_pending[:size])
                            del self.raw_pending[:size]
                            self.show_raw(frame, settings, "本地串口原始像素")
                        pending = len(self.raw_pending)
                    else:
                        packets = self.stream.feed(value)
                        self.accept_packets(packets, "本地COM接收")
                        pending = len(self.stream.pending)
                        if self.stream.errors:
                            self.set_details("串口错误帧未显示：\n" + "\n".join(self.stream.errors[-3:]))
                            self.stream.errors.clear()
                    self.status.set(f"串口累计 {self.received_count} 字节；尚有 {pending} 字节等待完整帧。")
                except ValueError as error:
                    self.stop_serial()
                    self.status.set(f"接收数据不符合设置：{error}")
            if self.serial_thread is not None and not self.serial_thread.is_alive():
                self.stop_serial()
                self.status.set("串口接收线程已结束；请检查端口及发送速率。")
        finally:
            self.window.after(100, self.poll_serial)

    def close(self):
        self.stop_serial()
        self.window.destroy()


def convert_file(args):
    data = read_data_file(args.input)
    if args.raw:
        if args.response:
            raise ValueError("原始像素直接转换不接受 --response；叠框请使用完整图片请求包。")
        image = raw_image(data, args.width, args.height, args.format)
    else:
        packets = scan_packets(data)
        requests = [p for p in packets if p["kind"] == "request"]
        if not requests:
            raise ValueError("该文件只有识别结果，没有图片像素；请把 --input 指向图片请求包。")
        request_index = max(i for i, p in enumerate(packets) if p["kind"] == "request")
        packet = packets[request_index]
        image = request_image(packet)
        replies = [p for p in packets[request_index + 1:]
                   if p["kind"] == "response" and p["sequence"] == packet["sequence"]]
        if args.response:
            replies = [p for p in scan_packets(read_data_file(args.response))
                       if p["kind"] == "response" and p["sequence"] == packet["sequence"]]
            if not replies:
                raise ValueError("回包与图片请求序号不同，未生成叠框图。")
        if replies:
            reply = replies[-1]
            if reply["status"]:
                raise ValueError(f"FPGA返回错误状态 {reply['status']}，未生成识别图。")
            image = draw_overlay(image, reply["detections"], args.model, 12)
    if args.crop28:
        if image.size == (32, 32):
            image = image.crop((2, 2, 30, 30))
        elif image.size == (384, 384):
            image = image.crop((24, 24, 360, 360))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output, format="PNG")
    print(f"已保存 {args.output} ({image.width}×{image.height})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="打开的HEX/BIN文件")
    parser.add_argument("--response", type=Path, help="同序号识别回包")
    parser.add_argument("--output", type=Path, help="直接转换为PNG，不打开GUI")
    parser.add_argument("--raw", action="store_true", help="输入为原始像素")
    parser.add_argument("--width", type=int, default=28)
    parser.add_argument("--height", type=int, default=28)
    parser.add_argument("--format", choices=tuple(FORMATS.values()), default="gray16")
    parser.add_argument("--model", choices=("digit", "hanzi"), default="digit")
    parser.add_argument("--crop28", action="store_true")
    args = parser.parse_args()
    if args.output:
        if not args.input:
            parser.error("--output 需要 --input。")
        try:
            convert_file(args)
        except (ValueError, OSError) as error:
            parser.exit(1, f"转换失败：{error}\n")
        return
    window = tk.Tk()
    app = SerialImageViewer(window)
    app.model.set("数字 0～9" if args.model == "digit" else "汉字十类")
    if args.raw:
        app.mode.set("原始像素")
        app.width.set(str(args.width))
        app.height.set(str(args.height))
        app.pixel_format.set(next(k for k, v in FORMATS.items() if v == args.format))
    if args.input:
        app.load_path(args.input)
    if args.response:
        app.load_path(args.response)
    window.mainloop()


if __name__ == "__main__":
    main()
