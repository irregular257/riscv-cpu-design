"""Portable, two-way FPGA image / UART desktop tool (no host inference)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import traceback
import uuid
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from image_serial_encode import encode_request, export_request, prepare_image, split_request
from serial_image_codec import draw_overlay, request_image, scan_packets
from serial_image_viewer import EXAMPLES, ROOT, SerialImageViewer, read_data_file

VERSION = "1.0.0"
MODES = {"保持整图，适配28×28": "fit28", "前景居中，长边20像素": "center20", "已有32×32协议图，不缩放": "frame32"}
INVERTS = {"自动判断黑白背景": "auto", "反色：白底黑字转黑底白字": "yes", "不反色": "no"}


class ScrollPage(ttk.Frame):
    """Keep controls reachable on smaller laptop displays."""
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.content = ttk.Frame(self.canvas)
        self.item = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.item, width=e.width))


class ImageEncoderPage:
    def __init__(self, suite, parent):
        self.suite = suite
        self.window = suite.window
        self.source_path = tk.StringVar()
        self.mode = tk.StringVar(value=next(iter(MODES)))
        self.invert = tk.StringVar(value=next(iter(INVERTS)))
        self.sequence = tk.StringVar(value="1")
        self.raw_head = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="选择PNG、JPG或BMP；预览确认后复制HEX到远程网站。")
        self.prepared = self.packet = self.photo = self.signature = None
        self._build(parent)
        for variable in (self.mode, self.invert, self.sequence, self.raw_head):
            variable.trace_add("write", self.dirty)

    def _build(self, parent):
        outer = ttk.Frame(parent, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="图片转串口", font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        ttk.Label(outer, text="生成与当前FPGA固件兼容的图像请求；转换与预览不会在电脑上执行识别。", wraplength=1000).pack(anchor="w", pady=(4, 10))
        source = ttk.Frame(outer)
        source.pack(fill="x")
        ttk.Button(source, text="打开图片…", command=self.open_image).pack(side="left")
        ttk.Entry(source, textvariable=self.source_path, state="readonly").pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(source, text="数字3示例", command=lambda: self.load_example(3)).pack(side="left")
        ttk.Button(source, text="数字8示例", command=lambda: self.load_example(8)).pack(side="left", padx=(6, 0))

        options = ttk.LabelFrame(outer, text="图像与数据包设置", padding=8)
        options.pack(fill="x", pady=10)
        ttk.Label(options, text="预处理").grid(row=0, column=0, sticky="w")
        ttk.Combobox(options, textvariable=self.mode, values=tuple(MODES), state="readonly", width=28).grid(row=0, column=1, padx=6, sticky="w")
        ttk.Label(options, text="黑白背景").grid(row=0, column=2, padx=(12, 0))
        ttk.Combobox(options, textvariable=self.invert, values=tuple(INVERTS), state="readonly", width=30).grid(row=0, column=3, padx=6, sticky="w")
        ttk.Label(options, text="序号 0～65535").grid(row=1, column=0, sticky="w", pady=(8, 0))
        sequence_row = ttk.Frame(options)
        sequence_row.grid(row=1, column=1, sticky="w", padx=6, pady=(8, 0))
        ttk.Entry(sequence_row, textvariable=self.sequence, width=8).pack(side="left")
        ttk.Button(sequence_row, text="下一张 +1", command=self.next_sequence).pack(side="left", padx=6)
        ttk.Checkbutton(options, text="返回原始检测头（调试用，回包增加480字节）", variable=self.raw_head).grid(row=1, column=2, columnspan=2, sticky="w", padx=(12, 0), pady=(8, 0))

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(0, 10))
        ttk.Button(actions, text="生成 / 更新串口数据", command=self.generate).pack(side="left")
        ttk.Button(actions, text="复制整包 HEX", command=self.copy_all).pack(side="left", padx=7)
        ttk.Button(actions, text="导出 HEX、BIN 和预览图…", command=self.export).pack(side="left")
        ttk.Button(actions, text="发送至已连接的本地COM", command=self.suite.send_local).pack(side="left", padx=7)

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", padx=(0, 12))
        ttk.Label(left, text="实际发送的32×32像素（最近邻放大）").pack(anchor="w", pady=(0, 5))
        self.picture = tk.Label(left, text="请选择图片\n\n处理后的像素将在这里显示", bg="#171d27", fg="#dae3f0",
                                font=("Microsoft YaHei UI", 12), width=32, height=17)
        self.picture.pack(fill="both", expand=True)
        self.info = tk.StringVar(value="预览与发送数据使用相同的0～16灰度像素。")
        ttk.Label(left, textvariable=self.info, wraplength=390).pack(anchor="w", pady=7)
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)
        ttk.Label(right, text="完整发送HEX（A5 59图像请求）").pack(anchor="w", pady=(0, 5))
        self.hex_text = tk.Text(right, height=14, width=58, wrap="word", font=("Consolas", 10), state="disabled")
        self.hex_text.pack(fill="both", expand=True)
        chunks = ttk.LabelFrame(right, text="远程网页分五次发送", padding=8)
        chunks.pack(fill="x", pady=(9, 0))
        for i in range(5):
            count = 256 if i < 4 else 12
            ttk.Button(chunks, text=f"复制第{i+1}段\n{count}字节", command=lambda n=i: self.copy_chunk(n)).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Label(right, text="HEX发送模式：第1→2→3→4→5段依次发送；不要附加换行字节，等待完整回复后再发下一张。", wraplength=560).pack(anchor="w", pady=8)
        ttk.Button(right, text="转到“串口转图片”查看或粘贴回包", command=self.suite.show_decoder).pack(anchor="w")
        ttk.Label(outer, textvariable=self.status, wraplength=1040, foreground="#174c78").pack(anchor="w", pady=(10, 0))

    def dirty(self, *_):
        if self.packet is not None:
            self.status.set("参数已更改；复制、导出或发送时会先更新数据。")

    def next_sequence(self):
        try:
            value = int(self.sequence.get())
            if not 0 <= value <= 65535:
                raise ValueError()
            self.sequence.set(str((value + 1) & 0xffff))
        except ValueError:
            self.show_error(ValueError("序号必须是0～65535的整数。"))

    def show_error(self, error):
        self.status.set(f"未生成：{error}")
        messagebox.showerror("图片转换失败", str(error), parent=self.window)

    def open_image(self):
        path = filedialog.askopenfilename(initialdir=EXAMPLES if EXAMPLES.exists() else Path.home(),
            filetypes=[("图像", "*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff"), ("全部文件", "*.*")])
        if path:
            self.source_path.set(path)
            self.generate()

    def load_example(self, digit):
        self.source_path.set(str(EXAMPLES / f"数字{digit}_还原28x28.png"))
        self.mode.set(next(k for k, v in MODES.items() if v == "fit28"))
        self.invert.set(next(k for k, v in INVERTS.items() if v == "no"))
        self.sequence.set(str(3 if digit == 3 else 28))
        self.raw_head.set(False)
        return self.generate()

    def current_signature(self):
        path = Path(self.source_path.get())
        if not self.source_path.get() or not path.is_file():
            raise ValueError("请先选择一张图片。")
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("图片文件超过64 MiB，请先裁剪或缩小。")
        sequence = int(self.sequence.get())
        if not 0 <= sequence <= 65535:
            raise ValueError("序号必须是0～65535的整数。")
        return (str(path.resolve()), path.stat().st_mtime_ns, path.stat().st_size,
                MODES[self.mode.get()], INVERTS[self.invert.get()], sequence, self.raw_head.get())

    def generate(self):
        try:
            signature = self.current_signature()
            if getattr(self.suite, 'awaiting_sequence', None) is not None and signature != self.signature:
                raise ValueError("本地串口正在等待回包，请先等待当前图片完成，或在接收页断开串口后再修改发送图片。")
            prepared = prepare_image(signature[0], mode=signature[3], invert=signature[4])
            packet = encode_request(prepared["pixels"], signature[5], signature[6])
            self.prepared, self.packet, self.signature = prepared, packet, signature
            self.photo = ImageTk.PhotoImage(prepared["image32"].resize((384, 384), Image.Resampling.NEAREST))
            self.picture.configure(image=self.photo, text="", width=0, height=0)
            self.hex_text.configure(state="normal")
            self.hex_text.delete("1.0", "end")
            self.hex_text.insert("end", packet.hex(" ").upper())
            self.hex_text.configure(state="disabled")
            self.info.set(f"源图 {prepared['source_size'][0]}×{prepared['source_size'][1]} → 发送32×32；反色：{'是' if prepared['inversion_applied'] else '否'}。\n序号{signature[5]}，共1036字节；CRC {int.from_bytes(packet[-2:], 'little'):04X}。")
            self.suite.decoder.accept_packets(scan_packets(packet), "本工具生成的发送请求（未据此确认已发送）")
            self.status.set("已生成1036字节，图片已缓存；收到同序号回包后可直接叠框。请先确认预览，再发送。")
            return True
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            self.show_error(error)
            return False

    def ensure_current(self):
        try:
            signature = self.current_signature()
        except (OSError, ValueError) as error:
            self.show_error(error)
            return False
        return True if self.packet is not None and signature == self.signature else self.generate()

    def copy_bytes(self, data, description):
        self.window.clipboard_clear()
        self.window.clipboard_append(data.hex(" ").upper())
        self.status.set(f"已复制{description}：{len(data)}字节。请在网页勾选HEX发送。")

    def copy_all(self):
        if self.ensure_current():
            self.copy_bytes(self.packet, "整包")

    def copy_chunk(self, index):
        if self.ensure_current():
            self.copy_bytes(split_request(self.packet)[index], f"第{index+1}/5段")

    def export(self):
        if not self.ensure_current():
            return
        destination = filedialog.askdirectory(title="选择导出位置（会新建一个独立子文件夹）", initialdir=Path.home())
        if destination:
            try:
                result = export_request(destination, f"frame_{self.signature[5]:05d}_{uuid.uuid4().hex[:8]}",
                    self.prepared, sequence=self.signature[5], raw_head=self.signature[6])
                self.status.set(f"导出完成：{result['directory']}")
                messagebox.showinfo("导出完成", f"已导出完整包、五段HEX、两张预览PNG和校验记录：\n{result['directory']}", parent=self.window)
            except (ValueError, OSError) as error:
                self.show_error(error)


class ImageSerialSuite:
    def __init__(self, window, initialize_ports=True):
        self.window = window
        window.title(f"图片与串口双向工具 · FPGA LAB {VERSION}")
        sw, sh = window.winfo_screenwidth(), window.winfo_screenheight()
        w, h = min(1160, sw-60), min(820, sh-100)
        window.geometry(f"{w}x{h}+{max(10,(sw-w)//2)}+{max(10,(sh-h)//2-15)}")
        window.minsize(min(960, w), min(660, h))
        self.notebook = ttk.Notebook(window)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=8)
        self.encode_page, self.decode_page = ScrollPage(self.notebook), ScrollPage(self.notebook)
        self.notebook.add(self.encode_page, text="  图片转串口  ")
        self.notebook.add(self.decode_page, text="  串口转图片  ")
        self.decoder = SerialImageViewer(window, container=self.decode_page.content,
            manage_window=False, initialize_ports=initialize_ports)
        self.encoder = ImageEncoderPage(self, self.encode_page.content)
        self.send_events = queue.Queue()
        self.send_thread = None
        self.send_stop = threading.Event()
        self.awaiting_sequence = None
        self.send_started = 0.0
        self.window.after(100, self.poll_send)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

    def show_decoder(self):
        self.notebook.select(self.decode_page)

    def send_local(self):
        if not self.encoder.ensure_current():
            return
        if self.send_thread is not None and self.send_thread.is_alive():
            messagebox.showinfo("正在发送", "请等待当前图片发送完成。", parent=self.window)
            return
        if self.awaiting_sequence is not None:
            messagebox.showinfo("等待FPGA回包", "当前图片尚未收到回包，请等待；若需重新开始，请在接收页断开串口后重新连接。", parent=self.window)
            return
        port = self.decoder.port
        if port is None or not port.is_open:
            messagebox.showinfo("请先连接", "请先在“串口转图片”页连接本地COM口。\n远程网站使用复制HEX功能。", parent=self.window)
            self.show_decoder()
            return
        if self.decoder.receive_settings[0] != "自动识别协议包":
            messagebox.showinfo("接收方式", "请在接收页停止串口，将解析方式改为“自动识别协议包”后重新连接。", parent=self.window)
            return
        packet = self.encoder.packet
        self.decoder.accept_packets(scan_packets(packet), "本工具向本地COM发送的请求")
        self.awaiting_sequence = self.encoder.signature[5]
        self.send_started = time.monotonic()
        self.send_stop = threading.Event()
        self.send_thread = threading.Thread(target=self.write_packet,
            args=(port, packet, self.send_stop, self.send_events), daemon=True)
        self.send_thread.start()
        self.encoder.status.set(f"正在向本地COM发送序号{self.awaiting_sequence}…")

    @staticmethod
    def write_packet(port, packet, stop, events):
        try:
            port.write_timeout = 2
            for chunk in split_request(packet):
                if stop.is_set():
                    return
                offset = 0
                while offset < len(chunk):
                    written = port.write(chunk[offset:])
                    if not written:
                        raise OSError("串口未能写入数据。")
                    offset += written
                if stop.wait(0.05):
                    return
            events.put(("sent", None))
        except Exception as error:
            events.put(("error", str(error)))

    def poll_send(self):
        while not self.send_events.empty():
            kind, value = self.send_events.get_nowait()
            if kind == "error":
                self.awaiting_sequence = None
                self.encoder.status.set(f"本地串口发送失败：{value}")
            elif self.awaiting_sequence is not None:
                self.encoder.status.set(f"1036字节已写入本地串口，等待序号{self.awaiting_sequence}的FPGA回包。")
        if self.awaiting_sequence is not None:
            reply = self.decoder.replies.get(self.awaiting_sequence)
            if reply is not None:
                self.encoder.status.set(f"已收到序号{self.awaiting_sequence}的回包，状态{reply['status']}；到接收页查看。")
                self.awaiting_sequence = None
            elif self.decoder.port is None:
                self.send_stop.set()
                self.awaiting_sequence = None
                self.encoder.status.set("串口已断开，可以重新连接后发送。")
            elif time.monotonic() - self.send_started > 60:
                self.awaiting_sequence = None
                self.encoder.status.set("60秒内未收到同序号回包，请检查READY、波特率与固件；未自动重发。")
        self.window.after(100, self.poll_send)

    def close(self):
        self.send_stop.set()
        self.decoder.stop_serial()
        if self.send_thread is not None:
            self.send_thread.join(timeout=0.4)
        self.window.destroy()


def self_test(report_path):
    """Exercise shipped resources, bidirectional conversion and bundled Tcl/Tk."""
    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_dir = report_path.parent / "artifacts"
    artifact_dir.mkdir(exist_ok=True)
    checks, artifacts = {}, []
    window = app = None
    report = dict(result="FAIL", version=VERSION, frozen=bool(getattr(sys, "frozen", False)),
                  resource_root=str(EXAMPLES.resolve()), checks=checks, artifacts=artifacts)
    try:
        window = tk.Tk()
        window.withdraw()
        app = ImageSerialSuite(window, initialize_ports=False)
        for digit, sequence in ((3, 3), (8, 28)):
            prepared = prepare_image(EXAMPLES / f"数字{digit}_还原28x28.png", invert="no")
            expected = read_data_file(EXAMPLES / f"数字{digit}_图片请求.hex")
            packet = encode_request(prepared["pixels"], sequence)
            assert packet == expected, "Encoded sample changed"
            assert b"".join(split_request(packet)) == expected
            decoded = scan_packets(packet)[0]
            assert request_image(decoded).tobytes() == prepared["image32"].tobytes()
            reply = scan_packets(read_data_file(EXAMPLES / f"数字{digit}_识别回包.hex"))[0]
            assert reply["detections"][0]["label"] == digit
            overlay = draw_overlay(prepared["image32"], reply["detections"])
            for suffix, image in (("input", prepared["image28"]), ("overlay", overlay)):
                file = artifact_dir / f"digit{digit}_{suffix}.png"
                image.save(file)
                artifacts.append(dict(path=file.relative_to(report_path.parent).as_posix(),
                    sha256=hashlib.sha256(file.read_bytes()).hexdigest(), width=image.width, height=image.height))
        checks.update(encode_request=True, decode_request=True, decode_reply=True, five_chunks=True)
        assert app.encoder.load_example(3)
        app.decoder.process(read_data_file(EXAMPLES / "数字3_识别回包.hex"), "随包实板记录")
        assert app.decoder.source_image is not None
        assert app.decoder.current_response["detections"][0]["label"] == 3
        assert app.decoder.photo is not None
        app.decoder.process(read_data_file(EXAMPLES / "数字8_识别回包.hex"), "不同序号检查")
        assert app.decoder.source_image is None and app.decoder.preview_image is None
        checks.update(sequence_pairing=True, tk_gui=True, no_fake_pixels=True)
        window.update_idletasks()
        report["result"] = "PASS"
    except Exception as error:
        report["error"] = str(error)
        report["traceback"] = traceback.format_exc()
    finally:
        if app is not None:
            app.close()
        elif window is not None:
            window.destroy()
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["result"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test-report", type=Path)
    parser.add_argument("--demo", choices=("3", "8"))
    parser.add_argument("--demo-reply", action="store_true")
    args = parser.parse_args()
    if args.self_test_report:
        return self_test(args.self_test_report)
    window = tk.Tk()
    app = ImageSerialSuite(window)
    if args.demo:
        app.encoder.load_example(int(args.demo))
        if args.demo_reply:
            app.decoder.process(read_data_file(EXAMPLES / f"数字{args.demo}_识别回包.hex"), "随包演示实板记录")
            app.show_decoder()
    def callback_error(exc_type, value, tb):
        log_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ImageSerialTool"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "error.log").write_text("".join(traceback.format_exception(exc_type, value, tb)), encoding="utf-8")
        except OSError:
            pass
        messagebox.showerror("程序错误", f"{value}\n\n错误记录：{log_dir / 'error.log'}", parent=window)
    window.report_callback_exception = callback_error
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
