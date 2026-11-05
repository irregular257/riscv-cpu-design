#!/usr/bin/env python3
"""Build one self-contained Verilog upload per profile, using only this package.

The output is source code, NOT a vendor bitstream. Pin/voltage constraints and
target-device synthesis/implementation remain the online FPGA IDE's job.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
PROFILES = {"advanced": "advanced_top"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def without_comments(text: str) -> str:
    # Preserve strings while removing comments; file names may contain '//'.
    pattern = r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/'
    return re.sub(pattern, lambda m: m[0] if m[0].startswith('"') else " ", text)


def modules_from(text: str) -> dict[str, str]:
    matches = re.finditer(r"\bmodule\s+(\w+)\b[\s\S]*?\bendmodule\b", text)
    return {m[1]: m[0] for m in matches}


def dependencies(body: str, known: set[str]) -> set[str]:
    # This source tree uses normal named module instances, with optional #(...).
    # A balanced token walk handles nested parameter expressions reliably.
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[A-Za-z_$][\w$]*|[^\s]', without_comments(body))
    found = set()
    for i, token in enumerate(tokens):
        if token not in known or (i and tokens[i - 1] == "module"):
            continue
        j = i + 1
        if j < len(tokens) and tokens[j] == "#":
            j += 1
            if j >= len(tokens) or tokens[j] != "(":
                continue
            depth = 1
            j += 1
            while j < len(tokens) and depth:
                depth += (tokens[j] == "(") - (tokens[j] == ")")
                j += 1
        if j + 1 < len(tokens) and re.fullmatch(r"[A-Za-z_$][\w$]*", tokens[j]) and tokens[j + 1] == "(":
            found.add(token)
    return found


def read_hex(path: Path) -> dict[int, int]:
    words = {}
    address = 0
    for token in without_comments(path.read_text(encoding="utf-8")).split():
        if token.startswith("@"):
            address = int(token[1:], 16)
            continue
        if not re.fullmatch(r"[0-9a-fA-F]{1,8}", token):
            raise ValueError(f"Unsupported 32-bit hex token in {path.name}: {token!r}")
        if not 0 <= address < 16384:
            raise ValueError(f"Image address exceeds 64 KiB: {path.name}[{address}]")
        words[address] = int(token, 16)
        address += 1
    if set(words) != set(range(16384)):
        raise ValueError(f"Expected a complete 16,384-word image: {path}")
    return words


def embed_memory(body: str, module: str, image: Path) -> str:
    words = read_hex(image)
    # PDS Verilog-4092 caps elaboration loops at 2000 iterations by default.
    # Emit every word (including zeros) once using constant indices: no loop,
    # no external file, and no loss of the complete 64 KiB image.
    # Keep the existing clocked BRAM read/write processes unchanged.
    lines = ["    initial begin",
             "        // Constant indices avoid the PDS 2000-iteration elaboration limit.",
             f"        // Embedded {image.relative_to(ROOT).as_posix()}, SHA256 {digest(image.read_bytes())}"]
    lines += [f"        mem[{i}] = 32'h{word:08x};" for i, word in sorted(words.items())]
    lines += ["    end"]
    replacement = "\n".join(lines) + "\n"
    if module == "rom_sync":
        pattern = r"    integer i;\s*initial begin[\s\S]*?\n    end\s*\n"
    elif module == "ram_sync":
        pattern = r"    initial if\(INIT_FILE!=\"\"\)\$readmemh\(INIT_FILE,mem\);\s*\n"
    else:
        raise ValueError(module)
    result, count = re.subn(pattern, lambda _: replacement, body, count=1)
    if count != 1:
        raise ValueError(f"Expected one recognized initialization block in {module}")
    # The embedded modules are specialized to the complete AI images. Match
    # their defaults to the instance overrides, so standalone editor analysis
    # also sees the 16K-word depth and does not flag constant indices as OOB.
    result, width_count = re.subn(r"(parameter integer ADDR_WIDTH\s*=\s*)\d+",
                                 lambda m: m[1] + "16", result, count=1)
    if width_count != 1:
        raise ValueError(f"Expected ADDR_WIDTH parameter in {module}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["all", *PROFILES], default="all")
    parser.add_argument("--clk-hz", type=int, default=27_000_000,
                        help="actual board oscillator Hz; default 27000000 follows vendor PG2L200H Top.v example")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--led-active-low", action="store_true", help="invert all four status LED outputs")
    parser.add_argument("--iverilog", default=os.environ.get("IVERILOG", "iverilog"))
    parser.add_argument("--no-compile", action="store_true", help="package only; report syntax verification as NOT_RUN")
    parser.add_argument("--images-dir", type=Path, default=Path("software/runtime/images"),
                        help="firmware images relative to advanced/; defaults to the unchanged production path")
    parser.add_argument("--output-dir", type=Path, default=Path("upload"))
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    parser.add_argument("--output-name", help="single-profile Verilog filename, such as rv32i_advanced_chunked.v")
    args = parser.parse_args()
    if args.clk_hz <= 0 or args.baud <= 0 or args.clk_hz // args.baud < 8:
        parser.error("positive clock/baud with at least 8 clocks per UART bit required")
    upload = (ROOT / args.output_dir).resolve()
    reports = (ROOT / args.reports_dir).resolve()
    images = (ROOT / args.images_dir).resolve()
    for directory in (upload,reports,images):
        try:directory.relative_to(ROOT)
        except ValueError:parser.error("image/output/report directories must be inside advanced/")
    if args.output_name and (Path(args.output_name).name!=args.output_name or not args.output_name.endswith('.v')):
        parser.error("--output-name must be a plain .v filename")
    upload.mkdir(parents=True,exist_ok=True)
    reports.mkdir(parents=True,exist_ok=True)
    module_text = {}
    module_path = {}
    for path in sorted((ROOT / "rtl").glob("*.v")):
        for name, body in modules_from(path.read_text(encoding="utf-8-sig")).items():
            if name in module_text:
                raise ValueError(f"Duplicate module: {name}")
            module_text[name] = body
            module_path[name] = path
    known = set(module_text)
    profiles = PROFILES if args.profile == "all" else {args.profile: PROFILES[args.profile]}
    manifest = {"format": 1, "artifact_type": "verilog_source_not_bitstream",
                "clock_hz": args.clk_hz,
                "default_clock_source": "Vendor PG2L200H remote-board manual Top.v example: clk27MHz (D18)",
                "baud": args.baud, "led_active_low": args.led_active_low, "profiles": {}}
    failed = False
    for profile, top in profiles.items():
        selected = set()
        queue = [top]
        while queue:
            name = queue.pop()
            if name in selected:
                continue
            selected.add(name)
            queue.extend(dependencies(module_text[name], known) - selected)
        bodies = {name: module_text[name] for name in selected}
        if profile == "advanced":
            for name, image_name in [("rom_sync", "advanced_rom.hex"), ("ram_sync", "advanced_ram.hex")]:
                bodies[name] = embed_memory(bodies[name], name, images / image_name)
            # Embedded upload files deliberately have no external-file settings.
            bodies[top] = re.sub(r'(parameter\s+(?:ROM_FILE|RAM_FILE)\s*=\s*)"[^"]*"', r'\1""', bodies[top])
        bodies[top], count = re.subn(r"(parameter integer CLK_HZ\s*=\s*)\d+", lambda m: m[1] + str(args.clk_hz), bodies[top], count=1)
        if count != 1:
            raise ValueError(f"Missing CLK_HZ parameter in {top}")
        bodies[top] = re.sub(r"(parameter integer BAUD\s*=\s*)\d+", lambda m: m[1] + str(args.baud), bodies[top], count=1)
        if args.led_active_low:
            bodies[top] = bodies[top].replace("    fpga_top #(", "    wire [3:0] board_led;\n    assign led = ~board_led;\n    fpga_top #(", 1)
            bodies[top] = bodies[top].replace(".led(led)", ".led(board_led)")
        for name, body in list(bodies.items()):
            # Empty INIT_FILE is the selftest default. In AI the active sync
            # modules were embedded above. Remove inactive legacy file loaders
            # as well so uploads never depend on IDE file handling.
            body = re.sub(r'if\s*\(INIT_FILE\s*!=\s*""\)\s*\$readmemh\(INIT_FILE,\s*mem\);',
                          "/* External file loading omitted in single-file upload. */", body)
            # ram_sync uses an initial single-statement loader in selftest.
            body = body.replace("initial /* External file loading omitted in single-file upload. */", "initial begin end")
            if "$readmemh" in without_comments(body):
                raise ValueError(f"Unresolved external memory loader in {name}")
            bodies[name] = body
        ordered = [top, *sorted(selected - {top})]
        instantiated = set().union(*(dependencies(body, selected) for body in bodies.values()))
        roots = selected - instantiated
        if roots != {top}:
            raise ValueError(f"Unexpected upload roots: {sorted(roots)}")
        header = ["`timescale 1ns / 1ps", "// Generated by scripts/package_online.py; do not edit this file.",
                  f"// Profile: {profile}; select top module {top}.",
                  f"// CLK_HZ={args.clk_hz}; BAUD={args.baud}; LED_ACTIVE_LOW={int(args.led_active_low)}.",
                  "// Default 27 MHz follows vendor PG2L200H Top.v example (D18). Match the actual box and timing constraint.",
                  "// No pin locations, I/O voltages, device settings, or bitstream are supplied."]
        if profile == "advanced":
            header += ["// AI ROM/RAM: full 64 KiB images with constant-index initialization (PDS loop-limit fix)."]
        content = "\n".join(header) + "\n\n" + "\n\n".join(
            "// Source: " + module_path[name].relative_to(ROOT).as_posix() + "\n" + bodies[name] for name in ordered) + "\n"
        output = upload / (args.output_name or f"rv32i_{profile}.v")
        output.write_text(content, encoding="utf-8", newline="\n")
        result = {"top": top, "file": output.relative_to(ROOT).as_posix(),
                  "sha256": digest(output.read_bytes()), "bytes": output.stat().st_size,
                  "modules": ordered, "uninstantiated_module_roots": sorted(roots),
                  "sources": [{"file": module_path[n].relative_to(ROOT).as_posix(),
                               "sha256": digest(module_path[n].read_bytes())} for n in ordered],
                  "syntax_compile": {"result": "NOT_RUN"}}
        if profile == "advanced":
            result["memory_initialization"] = "full_constant_indices_no_loop_16384_words_per_image"
            result["embedded_images"] = [{"file": (images / image).relative_to(ROOT).as_posix(),
                                          "sha256": digest((images / image).read_bytes()),
                                          "word_count": len(read_hex(images / image))}
                                         for image in ["advanced_rom.hex", "advanced_ram.hex"]]
        if not args.no_compile:
            compiler = shutil.which(args.iverilog)
            if not compiler:
                result["syntax_compile"] = {"result": "FAIL", "reason": "Icarus compiler not found; set IVERILOG or --iverilog"}
                failed = True
            else:
                object_path = reports / f"rv32i_{profile}.vvp"
                command = [compiler, "-g2012", "-Wall", "-s", top, "-o", str(object_path), str(output)]
                process = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         encoding="utf-8", errors="replace")
                log = reports / f"rv32i_{profile}_syntax.log"
                log.write_text(process.stdout, encoding="utf-8")
                result["syntax_compile"] = {"result": "PASS" if process.returncode == 0 else "FAIL",
                                            "compiler": compiler, "returncode": process.returncode,
                                            "command": command, "log": log.relative_to(ROOT).as_posix()}
                failed |= process.returncode != 0
        manifest["profiles"][profile] = result
        print(f"{profile}: {output.relative_to(ROOT)}; top={top}; syntax={result['syntax_compile']['result']}; SHA256={result['sha256']}")
    (reports / "upload_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
