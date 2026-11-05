"""Independent, fail-closed audit of a frozen PG2L200H candidate.

No constraints or build inputs are changed. External IO timing limitations are
reported explicitly; a pass is not a physical-board or whole-system sign-off.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[3]
BASE_CORE = ROOT / 'reports/performance_20261103/pds_coremark/work'
BASE_APP = ROOT / 'reports/performance_20261103/selected/pds_multimodal/work'


def need(ok, message):
    if not ok:
        raise ValueError(message)


def text(path):
    return path.read_text(encoding='utf-8-sig', errors='replace')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean(value):
    value = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/',
                   lambda m: m[0] if m[0].startswith('"') else ' ', value)
    return re.sub(r'\s+', '', value)


def modules(value):
    found = list(re.finditer(r'\bmodule\s+(\w+)\b[\s\S]*?\bendmodule\b', value))
    result = {m[1]: m[0] for m in found}
    need(len(result) == len(found), 'Duplicate Verilog module definitions')
    return result


def hex_words(path):
    values = text(path).split()
    need(len(values) == 16384, f'Incomplete 64 KiB image: {path}')
    need(all(re.fullmatch(r'[0-9a-fA-F]{8}', word) for word in values), 'Unexpected HEX format')
    return [int(word, 16) for word in values]


def check_elf_images(fixture, kind, design):
    stem = 'coremark' if kind == 'coremark' else 'multimodal'
    data = (fixture / (stem + '.elf')).read_bytes()
    need(data[:6] == b'\x7fELF\x01\x01' and struct.unpack_from('<H', data, 18)[0] == 243,
         'Expected little-endian ELF32 RISC-V firmware')
    offset = struct.unpack_from('<I', data, 32)[0]
    width, count = struct.unpack_from('<HH', data, 46)
    images = {'rom': bytearray(65536), 'ram': bytearray(65536)}
    occupied = {'rom': [], 'ram': []}
    for index in range(count):
        section = struct.unpack_from('<10I', data, offset + width*index)
        section_type, flags, address, file_offset, size = section[1:6]
        if not flags & 2 or not size:
            continue
        space = 'rom' if flags & 4 else 'ram'
        need(address + size <= 65536, 'ELF section exceeds physical memory')
        need(all(address >= end or address + size <= start for start, end in occupied[space]),
             'ELF sections overlap in the same physical memory')
        occupied[space].append((address, address + size))
        need(section_type in (1, 8), 'Unsupported allocated ELF section type')
        if section_type == 1:
            need(file_offset + size <= len(data), 'Truncated ELF section')
            images[space][address:address + size] = data[file_offset:file_offset + size]
    result = {}
    for module, space in [('rom_sync', 'rom'), ('ram_sync', 'ram')]:
        name = ('coremark_' if kind == 'coremark' else 'advanced_') + space + '.hex'
        words = hex_words(fixture / name)
        reconstructed = b''.join(word.to_bytes(4, 'little') for word in words)
        need(reconstructed == images[space], 'ELF differs from frozen ' + name)
        matches = re.findall(r"mem\[(\d+)\]\s*=\s*32'h([0-9a-fA-F]+);", design[module])
        embedded = {int(index): int(word, 16) for index, word in matches}
        need(len(matches) == 16384 and embedded == dict(enumerate(words)),
             'Embedded Verilog differs from frozen ' + name)
        need('$readmemh' not in clean(design[module]), 'Embedded image retains external file dependency')
        result[space] = {'words': len(words), 'hex_sha256': sha(fixture / name)}
    return result


def pin_map(path):
    result = {}
    for line in text(path).splitlines():
        fields = line.split('|')
        if len(fields) >= 7 and fields[4]:
            need(fields[4] not in result, 'Duplicated logical pin')
            result[fields[4]] = {'pin': fields[0], 'direction': fields[5]}
    return result


def check_sources(work, kind, hz):
    manifest = json.loads(text(work.parent / 'build.json'))
    top = 'advanced_top' if kind == 'coremark' else 'multimodal_top'
    need((manifest['kind'], manifest['top'], manifest['cpu_hz']) == (kind, top, hz),
         'Requested configuration differs from candidate manifest')
    need(manifest.get('elf_hex_embedded_images_verified') is True,
         'Old diagnostic probe lacks required frozen-ELF/HEX provenance; not releasable')
    for key in ('inputs', 'source'):
        base = work if key == 'inputs' else work.parent / 'source'
        for name, digest in manifest[key].items():
            path = (base / name).resolve()
            path.relative_to(base.resolve())
            need(sha(path) == digest, 'Frozen ' + key + ' changed: ' + name)
    source = work.parent / 'source'
    need((source / 'build_scripts/scripts/build_maxperf_pds.py').exists(), 'Missing frozen build script')
    meta = json.loads(text(source / 'firmware/build.json'))
    need(meta == manifest['firmware_build'], 'Firmware metadata differs from frozen snapshot')
    stem = 'coremark' if kind == 'coremark' else 'multimodal'
    need(sha(source / 'firmware' / (stem + '.elf')) == meta['elf_sha256'], 'ELF hash mismatch')
    need(meta['cpu_hz'] == hz, 'Firmware timing frequency mismatch')
    if kind == 'coremark':
        need(meta['iterations'] == 2000 and not meta['validation'], 'Expected selected 2000-iteration performance firmware')
        need(meta['uart_gap_ms'] == 1200 and meta['uart_line_bytes_max'] <= 48, 'Wrong board UART formatting')
        need(meta['data_size_bytes'] == 2000, 'Wrong CoreMark workload size')
        need('-DCPU_HZ=' + str(hz) in meta['command'], 'Compiler command has wrong CPU_HZ')
        need('-DITERATIONS=2000' in meta['command'], 'Compiler command has wrong iteration count')
    else:
        need(meta['simulation'] is False and meta['with_llm'] is True, 'Wrong application firmware feature set')
        need(meta['uart_timeout_cycles'] == 60*hz, 'Application UART timeout clock mismatch')
    for name, digest in meta['source_sha256'].items():
        need(sha(source / 'firmware_sources' / name) == digest, 'Frozen firmware source mismatch: ' + name)
    design = modules(text(work / 'rtl/design.v'))
    rtl = {}
    for path in (source / 'rtl').glob('*.v'):
        for name, body in modules(text(path)).items():
            need(name not in rtl, 'Duplicate source module')
            rtl[name] = body
    excluded = {top, 'rom_sync', 'ram_sync'}
    for name in set(design) & set(rtl) - excluded:
        need(clean(design[name]) == clean(rtl[name]), 'Packaged RTL changed from frozen source: ' + name)
    preserved = []
    if kind == 'multimodal':
        baseline = modules(text(BASE_APP / 'rtl/multimodal_embedded.v'))
        for name in set(baseline) - set(rtl) - {top, 'multimodal_soc', 'rom_sync', 'ram_sync'}:
            need(clean(design[name]) == clean(baseline[name]), 'Application peripheral changed: ' + name)
            preserved.append(name)
        need(clean(design['multimodal_soc']) == clean(modules(text(source / 'multimodal_soc.v'))['multimodal_soc']),
             'Packaged multimodal SoC differs from frozen source')
        vendor = {p.relative_to(work / 'vendor').as_posix(): sha(p) for p in (work / 'vendor').rglob('*') if p.is_file()}
        reference = {p.relative_to(BASE_APP / 'vendor').as_posix(): sha(p) for p in (BASE_APP / 'vendor').rglob('*') if p.is_file()}
        need(vendor == reference, 'Vendor DDR IP changed')
    images = check_elf_images(source / 'firmware', kind, design)
    top_text = clean(design[top])
    need('CLK_HZ=' + str(hz) in top_text and 'BAUD=115200' in top_text, 'Top clock/UART parameters differ')
    need('.CLK_HZ(CLK_HZ)' in top_text and '.BAUD(BAUD)' in top_text, 'Top does not propagate timing parameters')
    if hz != 27000000:
        need('always@(posedgecpu_clkornegedgerun_n)' in top_text and 'if(!run_n)por<=' in top_text,
             'POR does not assert asynchronously on PLL unlock and release on CPU clock')
        need('wirerun_n=rst_n&&pll_locked;' in top_text, 'PLL lock missing from reset qualification')
        need('.clk(cpu_clk),.rst(rst)' in top_text, 'SoC is not clocked by PLL output')
        need('posedgesys_clk' not in top_text and '.clk(sys_clk)' not in top_text, 'Old 27 MHz clock remains on system state')
        if kind == 'multimodal':
            need('.cpu_clk(cpu_clk)' in top_text, 'DDR mailbox CPU side uses wrong clock')
            need('.ddr_ref_clk_p(ddr_ref_clk_p),.ddr_ref_clk_n(ddr_ref_clk_n)' in top_text, 'DDR reference wiring changed')
        ratio = re.search(r'cpu_clock#\(\.FB_DIV\((\d+)\),\.OUT_DIV\((\d+)\)\)', top_text)
        need(ratio is not None, 'Missing explicit PLL ratio')
        feedback, divider = map(int, ratio.groups())
        need(27000000*feedback == hz*divider and 600000000 <= 27000000*feedback <= 1200000000,
             'Illegal or inconsistent PLL frequency')
        need(manifest['pll_ratio'] == [feedback, divider], 'PLL ratio differs from manifest')
        need(clean(design['cpu_clock']) == clean(modules(text(source / 'cpu_clock.v'))['cpu_clock']), 'PLL wrapper differs from frozen source')
        pll_text = clean(design['cpu_clock'])
        for token in ('.CLKIN_FREQ(27.0)', '.STATIC_RATIOI(1)', '.STATIC_RATIOM(1)',
                      '.STATIC_RATIOF(FB_DIV)', '.STATIC_RATIO0(OUT_DIV)',
                      '.CLKOUT0(clk)', '.LOCK(locked)', '.RST(!reset_n)', '.PLL_PWD(1\'b0)'):
            need(token in pll_text, 'PLL primitive binding missing: ' + token)
    fdc_names = ['board.fdc'] if kind == 'coremark' else ['mini_board_candidate.fdc', 'matrix_pins.fdc', 'ddr_pg2l200h.fdc']
    need({p.name for p in (work / 'constraints').iterdir()} == set(fdc_names), 'Unexpected constraint file set')
    for index, name in enumerate(fdc_names):
        baseline = ROOT / 'constraints/mini_board_candidate.fdc' if kind == 'coremark' else BASE_APP / 'constraints' / name
        actual, reference = text(work / 'constraints' / name), text(baseline)
        if index == 0 and hz != 27000000:
            need(actual.startswith(reference), 'Board timing/pin constraints changed')
            added = actual[len(reference):]
            commands = [line.strip() for line in added.splitlines() if line.strip() and not line.lstrip().startswith('#')]
            need(len(commands) == 1 and commands[0].startswith('create_generated_clock -name {cpu_clk} '),
                 'Unexpected additional timing exception or constraint')
            need('[get_ports {sys_clk}]' in commands[0] and '[get_pins {u_cpu_clock.u_pll/CLKOUT0}]' in commands[0],
                 'Generated clock source or target changed')
            ratio = re.search(r'-multiply_by \{(\d+)\} -divide_by \{(\d+)\}', commands[0])
            need(ratio is not None and 27000000*int(ratio[1]) == hz*int(ratio[2]), 'Generated clock ratio mismatch')
        else:
            need(actual == reference, 'Existing constraints were modified: ' + name)
    tcl = text(work / 'run.tcl')
    need('set_arch -family Logos2 -device PG2L200H -speedgrade -6 -package FBB676' in tcl, 'Wrong FPGA target')
    need('compile -top_module ' + top in tcl and 'gen_bit_stream -scbv {High Voltage}' in tcl, 'Wrong top or bitstream configuration')
    need(set(re.findall(r'add_constraint \{constraints/([^}]+)\}', tcl)) == set(fdc_names), 'Tcl does not load all audited constraints')
    return manifest, {'frozen_input_files': len(manifest['inputs']), 'frozen_source_files': len(manifest['source']),
                      'elf_sha256': meta['elf_sha256'], 'images': images, 'preserved_peripherals': sorted(preserved),
                      'cpu_hz': hz, 'constraints_unchanged_except_cpu_generated_clock': True}


def check_timing(work, kind, hz):
    top = 'advanced_top' if kind == 'coremark' else 'multimodal_top'
    baseline = BASE_CORE if kind == 'coremark' else BASE_APP
    rtr_path = work / 'report_timing' / (top + '.rtr')
    rtr = text(rtr_path)
    need('Design Summary : All Constraints Met.' in rtr, 'PDS reports failing timing constraints')
    need('Timing analysis mode : multi corner' in rtr, 'Both timing corners were not analyzed')
    summary = {}
    for match in re.finditer(r'(?m)^(Setup|Hold|Recovery|Removal|Minimum Pulse Width) Summary\((Slow|Fast) Corner\):([\s\S]*?)(?=\n=+)', rtr):
        rows = []
        for line in match[3].splitlines():
            row = re.search(r'(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\d+)\s+(\d+)\s*$', line)
            if row:
                slack, negative, failures, endpoints = float(row[1]), float(row[2]), int(row[3]), int(row[4])
                need(slack >= 0 and negative == 0 and failures == 0, 'Negative timing margin: ' + line)
                rows.append({'clocks': line[:row.start()].strip(), 'slack_ns': slack, 'endpoints': endpoints})
        if match[1] in ('Setup', 'Hold', 'Minimum Pulse Width'):
            need(rows, 'Missing timing rows')
        summary[match[2] + ' ' + match[1]] = rows
    need(len(summary) == 10, 'Incomplete Slow/Fast timing summaries')
    cpu_clock = 'cpu_clk' if hz != 27000000 else 'sys_clk'
    for corner in ('Slow', 'Fast'):
        for check in ('Setup', 'Hold'):
            need(any(re.fullmatch(cpu_clock + r'\s+' + cpu_clock, row['clocks']) and row['endpoints'] > 1000
                     for row in summary[corner + ' ' + check]), 'Missing constrained CPU-domain register paths')
    clocks = {}
    clock_block = rtr.split('Clock Summary:', 1)[1].split('Clock Groups:', 1)[0]
    for line in clock_block.splitlines():
        match = re.match(r'\s*(\w+)\s+([\d.]+)\s+\{[^}]+\}\s+(Declared|Generated \([^)]*\))\s+(\d+)\s+(\d+)\s+\{([^}]*)\}', line)
        if match:
            clocks[match[1]] = {'period_ns': float(match[2]), 'type': match[3], 'clock_loads': int(match[4]),
                                'non_clock_loads': int(match[5]), 'source': match[6]}
    need(cpu_clock in clocks and abs(clocks[cpu_clock]['period_ns'] - 1e9/hz) <= .002,
         'Actual reported CPU clock period differs from requested frequency')
    need(clocks[cpu_clock]['clock_loads'] > 1000, 'CPU clock has too few register loads')
    if hz != 27000000:
        need(clocks['cpu_clk']['type'] == 'Generated (sys_clk)' and 'u_cpu_clock/u_pll/' in clocks['cpu_clk']['source'],
             'Actual generated CPU clock is not derived from the PLL')
        need(clocks['sys_clk']['clock_loads'] == 0, 'System state still uses unaccelerated reference clock')
    allowed_clocks = {'sys_clk', 'cpu_clk'}
    if kind == 'multimodal':
        allowed_clocks |= {'ref_clk', 'rst_clk', 'ddrphy_sysclk', 'phy_dq_clk_0', 'phy_dq_clk_1', 'phy_dq_sysclk_0', 'phy_dq_sysclk_1'}
    need(set(clocks) <= allowed_clocks, 'Unexpected clock domain in actual timing report')
    pins_file = work / 'place_route' / (top + '_prr_route_optimize.prt')
    pins = pin_map(pins_file)
    need(pins == pin_map(baseline / 'place_route' / pins_file.name), 'Physical pins differ from known board baseline')
    need(len(pins) == (8 if kind == 'coremark' else 101), 'Unexpected pin count')
    log = text(work / 'report_timing/run.log')
    external = []
    for line in log.splitlines():
        if not re.match(r'\s*[WCE]:\s*Timing-', line):
            continue
        port = re.search(r"Timing-408[67]: Port '([^']+)' is not constrained", line)
        if port:
            need(port[1] in pins, 'Unconstrained internal signal reported as a port')
            external.append(port[1])
        elif re.search(r'unconstrain|not constrained|without.*clock|no.*clock|unclock', line, re.I):
            raise ValueError('Unreviewed internal clock/constraint warning: ' + line)
    baseline_log = text(baseline / 'report_timing/run.log')
    old_external = set(re.findall(r"Timing-408[67]: Port '([^']+)' is not constrained", baseline_log))
    need(set(external) <= old_external, 'New unconstrained external port warning')
    constraint_report = text(work / 'synthesize/constraint_check.ccr')
    errors = re.search(r'Found (\d+) error\(s\)', constraint_report)
    need(errors is not None and errors[1] == '0', 'PDS constraint editor reports errors')
    issues = constraint_report.split('Constraints With Issues(infos):', 1)[-1].split('Other Issues(infos):', 1)[0]
    def issue_messages(value):
        return {prefix + ' ' + message for prefix, message in re.findall(
            r'(?m)^\s*([WCE]: ConstraintEditor-\d+:)\s*(?:\[[^\n]*?\]\s*)?(.+)$', value)}
    baseline_constraint = text(baseline / 'synthesize/constraint_check.ccr')
    baseline_issues = baseline_constraint.split('Constraints With Issues(infos):', 1)[-1].split('Other Issues(infos):', 1)[0]
    current_issues = issue_messages(issues)
    need(current_issues <= issue_messages(baseline_issues),
         'New constraint-object issue: ' + repr(sorted(current_issues - issue_messages(baseline_issues))))
    result = {'timing_report_sha256': sha(rtr_path), 'summaries': summary, 'clocks': clocks,
              'pins': pins, 'pin_report_sha256': sha(pins_file),
              'no_new_internal_unconstrained_warnings': True,
              'unchanged_constraint_object_warnings': sorted(current_issues),
              'known_external_ports_without_IO_delays': sorted(set(external))}
    if kind == 'multimodal':
        result['ddr_payload_boundaries'] = check_ddr_paths(work)
    resource_file = work / 'place_route' / (top + '.ror')
    resource_text = text(resource_file)
    header = re.search(r'(?m)^\| Module Inst Name\s*\|([^\r\n]+)', resource_text)
    row = re.search(r'(?m)^\|\s*' + top + r'\s*\|([^\r\n]+)', resource_text)
    need(header and row, 'Missing resource summary')
    resource_names = [v.strip() for v in header[1].split('|') if v.strip()]
    resource_values = [v.strip() for v in row[1].split('|') if v.strip()]
    need(len(resource_names) == len(resource_values) and len(set(resource_names)) == len(resource_names),
         'Malformed resource summary columns')
    resources = {}
    for name, value in zip(resource_names, resource_values):
        need(re.fullmatch(r'\d+(?:\.\d+)?', value) is not None, 'Invalid resource count: ' + name)
        numeric = float(value)
        need(math.isfinite(numeric) and numeric >= 0, 'Nonfinite or negative resource count: ' + name)
        # PDS accounts one DRM18K as half a DRM36K. Logic/FF/DSP counts remain integers.
        need(numeric.is_integer() or name.upper().startswith('DRM'), 'Unexpected fractional resource count: ' + name)
        resources[name] = int(numeric) if numeric.is_integer() else numeric
    result['resources'] = resources
    return result


def check_ddr_paths(work):
    path = work / 'report_timing/multimodal_top_exception.rtr'
    data = text(path)
    fdc = text(work / 'constraints/ddr_pg2l200h.fdc').splitlines()
    boundary_lines = {limit: next(i+1 for i, line in enumerate(fdc)
                                  if line.startswith(f'set_max_delay {limit}.0 -datapath_only'))
                      for limit in (8, 20)}
    paths = {8: [], 20: []}
    for block in re.split(r'(?m)^Startpoint\s*:', data)[1:]:
        kind = re.search(r'(?m)^Timing Exception\s*:\s*(.+)', block)
        if not kind:
            continue
        limit = next((n for n in (8, 20) if kind[1].strip() == f'MaxDelay Path {n}.000ns -datapath_only'), None)
        if limit is None:
            continue
        start = block.splitlines()[0].strip()
        end = re.search(r'(?m)^Endpoint\s*:\s*(.+)', block)[1].strip()
        line = int(re.search(r'Exception constraint line number:\s*(\d+)', block)[1])
        corner = re.search(r'Path Type\s*:\s*[^\n]*\((slow|fast) corner\)', block)[1]
        slack = re.search(r'Slack \(MET\)\s+(-?\d+\.\d+)', block)
        need(line == boundary_lines[limit] and slack is not None and float(slack[1]) >= 0,
             'DDR payload boundary missing or violated')
        if limit == 8:
            need(re.fullmatch(r'u_ddr/u_bridge/request_(?:addr|data|write|size)(?:\[\d+\])?/[^/]+/Q', start)
                 and re.fullmatch(r'u_ddr/u_bridge/(?:[^/]*latched[^/]*|byte_offset[^/]*)/[^/]+/D', end),
                 'DDR request max-delay endpoints changed')
        else:
            need(re.fullmatch(r'u_ddr/u_bridge/response_(?:data|error)(?:\[\d+\])?/[^/]+/Q', start)
                 and re.fullmatch(r'u_ddr/u_bridge/rsp_(?:rdata|error)(?:\[\d+\])?/[^/]+/D', end),
                 'DDR response max-delay endpoints changed')
        paths[limit].append({'start': start, 'end': end, 'corner': corner, 'slack_ns': float(slack[1])})
    for limit, rows in paths.items():
        need({row['corner'] for row in rows} == {'slow', 'fast'}, f'Missing both-corner DDR {limit} ns paths')
    return {'exception_report_sha256': sha(path), 'request_max_ns': 8, 'response_max_ns': 20,
            'request_paths': paths[8], 'response_paths': paths[20]}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--kind', choices=('coremark', 'multimodal'), required=True)
    parser.add_argument('--hz', type=int, required=True)
    parser.add_argument('--inputs-only', action='store_true', help='Check frozen sources only; never reports a release pass')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    work = (ROOT / args.work).resolve()
    work.relative_to(ROOT)
    output = args.output or work.parent / 'independent_pds_audit.json'
    result = {'status': 'FAIL', 'work': str(work), 'kind': args.kind, 'cpu_hz': args.hz,
              'audit_script_sha256': sha(Path(__file__)), 'physical_board_verified': False}
    try:
        manifest, source_audit = check_sources(work, args.kind, args.hz)
        result['source_audit'] = source_audit
        if args.inputs_only:
            result['status'] = 'PASS_INPUTS_ONLY_NOT_RELEASE'
        else:
            need(manifest['status'] == 'GENERATED_NEEDS_AUDIT' and manifest['exit_code'] == 0 and not manifest['errors'],
                 'PDS build is incomplete or failed')
            result['timing'] = check_timing(work, args.kind, args.hz)
            top = manifest['top']
            bit = work / 'generate_bitstream' / (top + '.sbit')
            need(bit.stat().st_size > 1000000 and sha(bit) == manifest['bitstream_sha256'], 'Bitstream hash/size mismatch')
            need('gen_bit_stream -scbv {High Voltage}' in text(work / 'generate_bitstream' / (top + '.bgr')),
                 'Wrong generated bitstream voltage setting')
            result.update(status='PASS_DEFINED_TIMING_PINS_PROVENANCE', bitstream_sha256=sha(bit),
                          limitations=['Known external asynchronous IO lacks full input/output delay constraints.',
                                       'This audit does not replace long RTL, matrix netlist equivalence, or physical-board validation.'])
    except Exception as error:
        result['error'] = str(error)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key not in ('source_audit', 'timing')}, ensure_ascii=False, indent=2))
    return 1 if result['status'] == 'FAIL' else 0


if __name__ == '__main__':
    raise SystemExit(main())
