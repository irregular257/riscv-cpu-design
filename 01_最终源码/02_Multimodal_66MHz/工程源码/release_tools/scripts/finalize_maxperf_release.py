"""Validate all final-candidate evidence, then create a new local release.

No production RTL, existing release, or report document is modified. Missing
evidence fails before any release directory is created. --check-only publishes
nothing. PDS timing and RTL simulations never become a claimed board score.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
import zipfile

from vision_serial import decode
from package_advanced import modules_from, without_comments

ROOT = Path(__file__).resolve().parents[1]
AUDITOR = ROOT / 'reports/maxperf_20261103/compiler/audit_maxperf_pds.py'
DEFAULT_BASELINE = ROOT / 'reports/performance_20261103/multimodal_optimized'


def need(condition, message):
    if not condition:
        raise ValueError(message)


def local(path):
    path = (ROOT / path).resolve()
    path.relative_to(ROOT)
    return path


def reference(path, owner):
    path = Path(path)
    if path.is_absolute():
        return local(path)
    candidates = {p.resolve() for p in (ROOT/path, owner/path) if p.exists()}
    need(len(candidates) == 1, 'Missing or ambiguous evidence path: ' + str(path))
    return local(candidates.pop())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def verify_sources(summary):
    for name, digest in summary['sources'].items():
        need(sha(local(Path(name))) == digest, 'Regression source changed after the run: ' + name)


def bind_rtl(summary, frozen):
    files = sorted((frozen/'source/rtl').glob('*.v'))
    need(files, 'Missing frozen RTL snapshot')
    for path in files:
        matches = [digest for name, digest in summary['sources'].items() if Path(name).name == path.name]
        need(matches == [sha(path)], 'Regression/PDS RTL mismatch: ' + path.name)
    return {p.name: sha(p) for p in files}


def full_pds_audit(build, kind, hz):
    need((build/'work').is_dir(), '--core-build/--app-build must identify the directory containing work/')
    report = build/'independent_pds_audit.json'
    command = [sys.executable, str(AUDITOR), '--work', str(build/'work'),
               '--kind', kind, '--hz', str(hz), '--output', str(report)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
    need(result.returncode == 0, kind + ' PDS audit failed:\n' + result.stdout + result.stderr)
    audit = load(report)
    need(audit['status'] == 'PASS_DEFINED_TIMING_PINS_PROVENANCE', 'Incomplete PDS audit')
    need(audit['audit_script_sha256'] == sha(AUDITOR), 'PDS audit script changed during validation')
    return audit


def verify_coremark(run, board, hz):
    summary = load(run/'summary.json')
    need(summary['result'] == 'PASS', 'Long RTL run did not pass')
    verify_sources(summary)
    fixture = local(Path(summary['firmware']))
    for name, digest in summary['fixtures'].items():
        need(sha(fixture/name) == digest, 'Long-run firmware fixture changed: ' + name)
    meta = load(fixture/'build.json')
    need(meta == summary['build'], 'Long-run firmware metadata mismatch')
    board_meta = load(board/'source/firmware/build.json')
    for key in ('iterations', 'validation', 'cpu_hz', 'data_size_bytes', 'compiler',
                'compiler_flags', 'march', 'optimization', 'lto', 'source_sha256'):
        need(meta[key] == board_meta[key], 'Simulation/board firmware differs beyond UART gap: ' + key)
    need(meta.get('extra_cflags', []) == board_meta.get('extra_cflags', []), 'Extra compiler flags differ')
    need(meta['iterations'] == 2000 and meta['cpu_hz'] == hz and not meta['validation'], 'Wrong long-run configuration')
    need(meta['uart_gap_ms'] == 0 and board_meta['uart_gap_ms'] == 1200, 'Wrong simulation/board UART gaps')
    def normalized(command):
        result = []
        skip = False
        for token in command:
            if skip:
                skip = False
                continue
            if token == '-o':
                skip = True
            elif token.startswith('-Wl,-Map=') or token.startswith('-DCOREMARK_UART_GAP_MS='):
                continue
            else:
                result.append(token)
        return result
    need(normalized(meta['command']) == normalized(board_meta['command']), 'Actual board/simulation compiler commands differ')
    need('-DCOREMARK_UART_GAP_MS=1200' in board_meta['command'], 'Board command does not enable slow UART output')
    need(sha(fixture/'coremark.elf') == meta['elf_sha256'], 'Long-run ELF digest mismatch')
    uart = (run/'uart.log').read_text(encoding='utf-8')
    need('Correct operation validated' in uart and 'ERROR!' not in uart, 'Official long-run validation is missing or failed')
    def number(label, typ):
        match = re.search(re.escape(label) + r'\s*:\s*([0-9.]+)', uart)
        need(match is not None, 'Missing CoreMark field: ' + label)
        return typ(match[1])
    ticks = number('Total ticks', int)
    iterations = number('Iterations', int)
    seconds = number('Total time (secs)', float)
    score = number('Iterations/Sec', float)
    need(iterations == 2000 and summary['metrics']['iterations'] == 2000, 'Wrong measured iteration count')
    need(ticks == summary['metrics']['timed_cycles'] and ticks >= 10*hz, 'Timed interval is inconsistent or below ten seconds')
    need(abs(seconds - ticks/hz) < 1e-6 and abs(score - iterations*hz/ticks) < 1e-6,
         'UART timing arithmetic differs from actual ticks/frequency')
    crc = {}
    for name in ('seedcrc', 'crclist', 'crcmatrix', 'crcstate', 'crcfinal'):
        match = re.search(name + r'\s*:\s*0x([0-9a-fA-F]{4})\b', uart)
        need(match is not None, 'Missing CRC: ' + name)
        crc[name] = match[1].lower()
        need(crc[name] == summary['crc'][name].lower(), 'CRC differs between UART and summary: ' + name)
    need([crc[name] for name in ('seedcrc', 'crclist', 'crcmatrix', 'crcstate')] ==
         ['e9f5', 'e714', '1fd7', '8e3a'], 'Wrong standard 2K performance CRCs')
    logged_flags = uart.split('Compiler flags   : ', 1)[1].split('Memory location', 1)[0]
    need(logged_flags.replace('\r', '').replace('\n', '').strip() == meta['compiler_flags'], 'Printed compiler flags are inaccurate')
    return summary, {'scope': 'Actual 2000-iteration RTL run; not a measured physical-board score',
                     'iterations': iterations, 'cpu_hz': hz, 'timed_cycles': ticks,
                     'compiler': meta['compiler'], 'compiler_flags': meta['compiler_flags'], 'instruction_set': meta['march'],
                     'simulated_seconds': ticks/hz, 'simulated_coremark': iterations*hz/ticks,
                     'simulated_coremark_per_mhz': iterations*1e6/ticks, 'crc': crc,
                     'uart_sha256': sha(run/'uart.log'), 'summary_sha256': sha(run/'summary.json'),
                     'simulation_elf_sha256': meta['elf_sha256'], 'board_elf_sha256': board_meta['elf_sha256'],
                     'identical_elf': meta['elf_sha256'] == board_meta['elf_sha256'],
                     'same_sources_and_flags_except_uart_gap': True,
                     'uart_gap_ms': {'simulation': 0, 'board': 1200}}


def normalize_firmware_sources(meta):
    result = {}
    for name, digest in meta['source_sha256'].items():
        key = 'generated/main.c' if Path(name).name == 'main.c' and name != 'software/runtime/main.c' else name
        need(key not in result, 'Ambiguous firmware source name')
        result[key] = digest
    return result


def vision_commands(meta):
    result = []
    for command in meta['commands']:
        if not Path(command[0]).name.endswith('gcc.exe'):
            continue
        row = []
        skip = False
        for token in command:
            if skip:
                skip = False
                continue
            if token == '-o':
                skip = True
            elif token.startswith('-Wl,-Map='):
                continue
            elif token.startswith('-DUART_TIMEOUT='):
                row.append('-DUART_TIMEOUT=<simulation-or-60-seconds>')
            elif not token.startswith('-') and (token.endswith('.o') or token.endswith('/main.c')):
                row.append(Path(token).name)
            else:
                row.append(token)
        result.append(row)
    return result


def verify_vision(run, baseline, board, hz):
    summary, before, comparison = load(run/'summary.json'), load(baseline/'summary.json'), load(run/'comparison.json')
    need(summary['result'] == before['result'] == 'PASS_RTL_BEHAVIORAL_DDR', 'Vision RTL regression failed')
    verify_sources(summary)
    need(comparison['result'] in ('PASS_SAME_MODEL_OUTPUTS_DIFFERENT_FIRMWARE', 'PASS_SAME_FIRMWARE_AND_MODEL_OUTPUTS'),
         'Missing validated vision comparison')
    need((comparison['transactions'], comparison['inferences'], comparison['raw_head_values_checked']) == (53, 18, 3840),
         'Vision comparison coverage differs from required 53/18/3840')
    need(comparison.get('all_boxes_and_display_results_identical') is True and
         comparison.get('model_and_reference_provenance_identical') is True, 'Vision compatibility not established')
    need(comparison['summary_hashes'] == {'before': sha(baseline/'summary.json'), 'after': sha(run/'summary.json')},
         'Vision comparison refers to a different run')
    if 'comparison_script_sha256' in comparison:
        need(comparison['comparison_script_sha256'] == sha(ROOT/'scripts/compare_maxperf_vision.py'),
             'Vision comparison script changed after generating evidence')
    need(summary['references'] == before['references'], 'Vision model or independent reference changed')
    for folder, record in ((run, summary), (baseline, before)):
        for name, digest in record['firmware'].items():
            need(sha(folder/'firmware'/name) == digest, 'Vision firmware snapshot changed')
    meta, board_meta = load(run/'firmware/build.json'), load(board/'source/firmware/build.json')
    need(meta['simulation'] is True and board_meta['simulation'] is False, 'Wrong application simulation/board fixtures')
    for key in ('march', 'opt_vision', 'opt_runtime', 'cpu_hz', 'with_llm'):
        need(meta[key] == board_meta[key], 'Application firmware configuration mismatch: ' + key)
    need(meta['cpu_hz'] == hz and board_meta['uart_timeout_cycles'] == 60*hz, 'Application timing/frequency mismatch')
    need(normalize_firmware_sources(meta) == normalize_firmware_sources(board_meta), 'Simulation/board application sources differ')
    need(vision_commands(meta) == vision_commands(board_meta), 'Application commands differ beyond simulation UART timeout')
    need(len(summary['cases']) == len(before['cases']) == len(summary['metrics']['cases']) == 53, 'Missing vision transactions')
    need(summary['metrics']['result'] == 'PASS' and all(c['result'] == 'PASS' for c in summary['metrics']['cases']),
         'One or more actual vision transactions failed')
    need(load(run/'runner_metrics.json') == summary['metrics'], 'Actual vision runner metrics differ from summary')
    need(sum(c['head_values_checked'] for c in summary['metrics']['cases']) == 3840, 'Missing actual raw-head checks')
    rows = {c['name']: c for c in comparison['cases']}
    inferences = 0
    for old, new in zip(before['cases'], summary['cases']):
        for key in ('name', 'kind', 'request_sha256', 'glyph', 'status', 'expected_brightness'):
            need(old[key] == new[key], 'Vision request/display behavior changed: ' + key)
        need(sha(run/'fixture'/new['name']/'request.bin') == new['request_sha256'], 'Vision request bytes changed')
        if new['kind'] != 'V' or new['status'] != 0:
            continue
        inferences += 1
        old_path, new_path = baseline/old['name']/'response.bin', run/new['name']/'response.bin'
        old_response, new_response = decode(old_path.read_bytes()), decode(new_path.read_bytes())
        for key in ('detections', 'head_q4', 'status'):
            need(old_response.get(key) == new_response.get(key), 'Actual vision response differs: ' + key)
        row = rows[new['name']]
        need(row['outputs_identical'] is True and row['before_response_sha256'] == sha(old_path)
             and row['after_response_sha256'] == sha(new_path), 'Vision comparison response binding mismatch')
    need(inferences == 18 and len(rows) == 18, 'Missing vision inference comparisons')
    return summary, {'comparison_sha256': sha(run/'comparison.json'), 'summary_sha256': sha(run/'summary.json'),
                     'baseline_summary_sha256': sha(baseline/'summary.json'),
                     'transactions': 53, 'inferences': 18, 'raw_head_values_checked': 3840,
                     'same_models_and_outputs': True, 'simulation_elf_sha256': meta['elf_sha256'],
                     'board_elf_sha256': board_meta['elf_sha256'], 'identical_elf': meta['elf_sha256'] == board_meta['elf_sha256'],
                     'scope': 'Actual RTL protocol, model output and display-state regression; not physical DDR or camera FPS',
                     'aggregate_cycle_reduction_percent': comparison['aggregate_cycle_reduction_percent']}


def verify_matrix(path, app):
    result = load(path)
    netlist = (app/'work/synthesize/multimodal_top_syn.vm').resolve()
    need(result['status'] == 'PASS_GLYPH_CONES' and result['verification_passed'] is True and result['exit_code'] == 0,
         'Matrix synthesized glyph audit failed')
    need(result['words_checked'] == 272 and result['mismatch_count'] == result['unknown_words'] == 0,
         'Matrix glyph coverage or equivalence incomplete')
    need(result['expect_mismatch'] is False, 'Negative-test glyph result cannot qualify a release')
    need(result['source_netlist_sha256'] == sha(netlist) and reference(result['source_netlist'], path.parent) == netlist,
         'Matrix audit used a different application netlist')
    golden = reference(result['golden_source'], path.parent)
    need(sha(golden) == result['golden_sha256'], 'Matrix golden source changed')
    return {'summary_sha256': sha(path), 'netlist_sha256': sha(netlist), 'words_checked': 272,
            'scope': result['scope'], 'folded_bit0': result['folded_bit0']}


def verify_multiply(path, builds):
    aggregate = load(path)
    need(aggregate['status'] == 'PASS_ACTUAL_SYNTH_MULTIPLIER' and set(aggregate['runs']) == set(builds),
         'Actual synthesized multiplier audits must exactly cover every requested build')
    result = {}
    for kind, build in builds.items():
        entry = aggregate['runs'][kind]
        summary_path = reference(entry['summary_path'], path.parent)
        need(entry.get('summary_sha256', sha(summary_path)) == sha(summary_path), 'Multiplier summary changed since aggregation')
        check = load(summary_path)
        top = 'advanced_top' if kind == 'coremark' else 'multimodal_top'
        netlist = (build/'work/synthesize'/f'{top}_syn.vm').resolve()
        need(entry.get('netlist_sha256', sha(netlist)) == sha(netlist), 'Aggregated multiplier netlist digest mismatch')
        need(check['status'] == 'PASS_ACTUAL_SYNTH_MULTIPLIER' and check['result'] == 'PASS_MAPPED_MULTIPLIER_EX_MEM_D',
             'Synthesized multiplier audit failed: ' + kind)
        need(check['netlist_sha256'] == sha(netlist) and reference(check['netlist_path'], summary_path.parent) == netlist,
             'Multiplier audit used a different actual netlist: ' + kind)
        need(check['case_count'] >= 4772 and check['mismatch_count'] == 0 and
             set(check['operations']) == {'MUL', 'MULH', 'MULHSU', 'MULHU'}, 'Incomplete multiplier vector coverage')
        for role in ('testbench', 'log'):
            artifact = reference(check[role+'_path'], summary_path.parent)
            need(sha(artifact) == check[role+'_sha256'], 'Multiplier evidence changed: ' + role)
        actual_log = reference(check['log_path'], summary_path.parent).read_text(encoding='utf-8')
        need(f"MAPPED_MULTIPLY_PASS vectors={check['case_count']} all_four_operations=1 actual_mem_result_d=1" in actual_log
             and 'FATAL' not in actual_log, 'Actual multiplier simulation pass log missing')
        extra = []
        if 'mapped_source_sha256' in check:
            extracted = summary_path.parent/'cpu_mapped.v'
            need(sha(extracted) == check['mapped_source_sha256'], 'Extracted mapped multiplier source changed')
            extra += [extracted, summary_path.parent/'compile.log', summary_path.parent/'vectors.hex']
        if 'vendor_primitive_sha256' in check:
            for name, digest in check['vendor_primitive_sha256'].items():
                need(sha(Path(check['vendor_library'])/(name+'.v')) == digest, 'Multiplier vendor primitive changed: '+name)
        result[kind] = {'summary_path': str(summary_path), 'summary_sha256': sha(summary_path),
                        'netlist_sha256': sha(netlist), 'case_count': check['case_count'], 'scope': check['scope'],
                        'testbench_path': str(reference(check['testbench_path'], summary_path.parent)),
                        'log_path': str(reference(check['log_path'], summary_path.parent)),
                        'forced_boundaries': check.get('forced_boundaries', []),
                        'verified_operand_lut_copies': check.get('verified_operand_lut_copies', []),
                        'extra_artifacts_sha256': {str(item): sha(item) for item in extra}}
    return result


def evidence_record(path, files, **fields):
    files = sorted({local(item) for item in [path, *files]})
    return dict(summary_sha256=sha(path), artifacts_sha256={str(item.relative_to(ROOT)): sha(item) for item in files},
                **fields), files


def verify_clock(path, builds, hz):
    result = load(path)
    need(result['exit_code'] == 0 and result['timed_out'] is False and result['clock_reset_pass'] is True,
         'PLL model run failed')
    need(result['unmodified_vendor_model'] is True and result['negative_cphase_observed'] is False,
         'PLL requires successful unmodified vendor model evidence')
    need(result['target_mhz'] * 1000000 == hz and result['reset_checks'] == 2 and
         result['measured_periods_per_check'] == 10, 'PLL evidence frequency or coverage mismatch')
    fb, out = result['feedback_divider'], result['output_divider']
    need(27000000 * fb == hz * out and result['vco_mhz'] == 27 * fb, 'PLL divider arithmetic mismatch')
    expected = 10e9 / hz
    need(abs(result['expected_10_periods_ns'] - expected) < 1e-8, 'PLL period metadata mismatch')
    for build in builds.values():
        meta = load(build/'build.json')
        need(meta['pll_ratio'] == [fb, out] and result['source_sha256'] == sha(build/'source/cpu_clock.v'),
             'PLL model differs from frozen PDS clock source or ratio')
        vendor = Path(meta['command'][0]).parent.parent/'arch/vendor/pango/verilog/simulation/GTP_GPLL.v'
        need(sha(vendor) == result['vendor_sha256'], 'PLL vendor simulation model changed')
    tb = (path.parent/'tb.v').read_text(encoding='utf-8')
    for name, value in [('FB_DIV', fb), ('OUT_DIV', out)]:
        need(re.search(r'\.'+name+r'\(\s*'+str(value)+r'\s*\)', tb), 'PLL testbench divider mismatch')
    log = (path.parent/'run.log').read_text(encoding='utf-8')
    periods = [float(value) for value in re.findall(r'CLOCK_PASS 10_periods_ns=([0-9.]+) locked=1', log)]
    need(len(periods) == 2 and all(abs(value-expected) < 0.0002 for value in periods) and
         'CLOCK_RESET_PASS' in log and 'NEGATIVE_CPHASE' not in log and 'FATAL' not in log,
         'PLL actual periods or reset evidence failed')
    return evidence_record(path, [path.parent/name for name in ('tb.v', 'run.log', 'compile.log')],
                           scope='Unmodified vendor PLL model; two lock/reset/period checks, not physical clock measurement',
                           cpu_hz=hz, periods_ns=periods, vendor_sha256=result['vendor_sha256'])


def verify_matrix_wave(path, app, hz):
    result = load(path)
    need(result['result'] == 'PASS_PIN_STREAM_REAL_CLOCK_DIVISORS' and result['clock_hz_parameter'] == hz,
         'Matrix pin-stream evidence failed or uses a different frequency')
    need(result['scan_interval_cycles'] == hz//1000 and result['serial_div'] == hz//1000000,
         'Matrix waveform did not use actual clock divisors')
    need(result['frame_cycles'] == 16*(hz//1000+1) and
         abs(result['frame_seconds_nominal'] - result['frame_cycles']/hz) < 1e-10,
         'Matrix waveform frame timing mismatch')
    need({stage['stage'] for stage in result['stages']} == {'verilator', 'compile', 'simulation'} and
         all(stage['returncode'] == 0 for stage in result['stages']), 'Matrix waveform tool stage failed')
    rtl, tb = path.parent/'matrix_display.v', path.parent/'tb_matrix_display.v'
    need(sha(rtl) == result['rtl_sha256'] and sha(tb) == result['adapted_tb_sha256'], 'Matrix waveform source changed')
    for name, digest in result['unchanged_sources_sha256'].items():
        need(sha(local(name)) == digest, 'Matrix original source changed: ' + name)
    need(sha(ROOT/'multimodal/sim/tb_matrix_display.v') == result['original_tb_sha256'], 'Matrix original oracle changed')
    def body(file):
        return re.sub(r'\s+', '', modules_from(without_comments(file.read_text(encoding='utf-8')))['matrix_display'])
    need(body(rtl) == body(app/'work/rtl/design.v'), 'Matrix pin-stream RTL differs from actual PDS application')
    log = (path.parent/'verilator/simulation.log').read_text(encoding='utf-8')
    match = re.search(r'MATRIX PASS ((?:\w+=\d+\s*)+)', log)
    need(match is not None and 'FATAL' not in log, 'Matrix actual pin-stream pass log missing')
    counters = {key: int(value) for key, value in re.findall(r'(\w+)=(\d+)', match[1])}
    need(counters == result['simulation'] and counters['frames'] >= 67 and counters['latches'] >= 1034,
         'Matrix actual waveform coverage mismatch')
    return evidence_record(path, [rtl, tb, *[path.parent/'verilator'/name for name in
                           ('simulation.log', 'compile.log', 'verilator.log')]],
                           scope=result['scope'], cpu_hz=hz, simulation=counters,
                           frame_seconds_nominal=result['frame_seconds_nominal'])


def verify_cache(path, builds):
    result = load(path)
    need(result['status'] == 'PASS_RTL_HANDSHAKE_EQUIVALENT' and result['changed_files'] == ['cache_2way.v'],
         'Cache decoupling equivalence failed or changes more than cache selection')
    need(result['combinational_assignments'] == 262144 and result['coremark_all_metrics_identical'] is True and
         result['coremark_all_crc_identical'] is True, 'Cache decoupling equivalence coverage incomplete')
    for build in builds.values():
        need({item.name: sha(item) for item in (build/'source/rtl').glob('*.v')} == result['rtl'],
             'Cache proof RTL differs from frozen PDS candidate')
    files = []
    for name, digest in result['evidence'].items():
        item = local(name)
        need(sha(item) == digest, 'Cache proof evidence changed: ' + name)
        files.append(item)
    equiv = load(path.parent/'equivalence/summary.json')
    need(equiv['status'] == 'PASS' and equiv['new_sha256'] == result['rtl']['cache_2way.v'] and
         equiv['combinational_assignments'] == 262144 and equiv['runs'] == result['lockstep_runs'],
         'Cache equivalence summary differs')
    old_source = ROOT/'reports/maxperf_20261103/selected_bram_i8_d4/rtl/cache_2way.v'
    need(sha(old_source) == equiv['old_sha256'], 'Cache original reference source changed')
    expected_reference = old_source.read_text(encoding='utf-8').replace('module cache_2way #(', 'module cache_2way_reference #(', 1)
    need((path.parent/'equivalence/cache_2way_reference.v').read_text(encoding='utf-8') == expected_reference,
         'Cache renamed reference source differs from original')
    files.append(old_source)
    need({(run['lookahead'], run['refill_forward']) for run in equiv['runs']} == {(0,0), (0,1), (1,1)},
         'Missing cache lockstep modes')
    for run in equiv['runs']:
        log_path = path.parent/'equivalence'/f"la{run['lookahead']}_forward{run['refill_forward']}.log"
        log = log_path.read_text(encoding='utf-8')
        expected = f"EQUIVALENCE cycles={run['cycles']} handshakes={run['handshakes']} idle_data_differences={run['idle_data_differences']}"
        need(expected in log and 'PASS CACHE_LOOKAHEAD' in log and 'FATAL' not in log and run['handshakes'] >= 192,
             'Cache actual lockstep log failed')
    regression = load(path.parent/'cache_regression/summary.json')
    need(regression['result'] == 'PASS' and regression['rtl_sha256'] == equiv['new_sha256'], 'Cache independent regression failed')
    for name, digest in regression['test_sources_sha256'].items():
        item = local(name)
        need(sha(item) == digest, 'Cache oracle source changed')
        files.append(item)
    after = load(path.parent/'coremark_perf/summary.json')
    before = load(ROOT/'reports/maxperf_20261103/cache_bram_candidate/coremark_perf/summary.json')
    need(after['result'] == before['result'] == 'PASS' and after['metrics'] == before['metrics'] and
         after['crc'] == before['crc'] and after['metrics']['timed_cycles'] == result['timed_cycles'],
         'Cache decoupling changed actual short CoreMark behavior')
    files += list(path.parent.glob('*.py'))
    for folder in ('equivalence', 'cache_regression'):
        files += [item for item in (path.parent/folder).iterdir() if item.suffix in ('.v', '.log', '.json')]
    return evidence_record(path, files, scope='RTL handshake equivalence and independent cache regression; not an Fmax result',
                           combinational_assignments=262144, lockstep_runs=equiv['runs'], same_coremark_metrics_and_crc=True)


def scoring_metrics(core_evidence, core_audit, core_build):
    resources = core_audit['timing']['resources']
    need(resources['LUT'] > 0, 'CoreMark resource denominator must be positive')
    mhz = core_evidence['cpu_hz']/1e6
    normalized = core_evidence['iterations']/(core_evidence['simulated_seconds']*mhz)
    need(abs(normalized-core_evidence['simulated_coremark_per_mhz']) < 1e-12, 'Normalized score arithmetic mismatch')
    return {'score_coremark_per_mhz': normalized,
            'implemented_clock_passing_defined_constraints_mhz': mhz,
            'coremark_per_lut': core_evidence['simulated_coremark']/resources['LUT'],
            'coremark': core_evidence['simulated_coremark'],
            'formula': {'score': 'N / (TotalTime_seconds * Frequency_MHz)',
                        'coremark_per_lut': '(N / TotalTime_seconds) / postroute_CoreMark_top_LUT'},
            'scope': 'RTL score plus actual implemented clock and CoreMark top post-route resources; new board score pending',
            'physical_board_verified': False, 'combined_weighted_score': None,
            'frequency_note': 'This implemented clock passed defined PDS constraints; not the estimated Fmax or a measured physical-board maximum',
            'resource_scope': 'CoreMark advanced_top only; excludes multimodal application DDR/display resource counts',
            'resources': {key: resources[key] for key in ('LUT', 'FF', 'APM', 'DRM')},
            'resource_report': str((core_build/'work/place_route/advanced_top.ror').relative_to(ROOT)),
            'resource_report_sha256': sha(core_build/'work/place_route/advanced_top.ror'),
            'timing_report_sha256': core_audit['timing']['timing_report_sha256']}


def verify_score_comparison(path, core, core_evidence, metrics, core_audit):
    comparison = load(path)
    need(comparison['script_sha256'] == sha(ROOT/'scripts/compare_maxperf_scores.py'), 'Score comparison tool changed')
    rows = [row for row in comparison['rows'] if row.get('core_build') == core.name]
    need(len(rows) == 1, 'Score comparison lacks unique final candidate')
    selected = rows[0]
    for key in ('score_coremark_per_mhz', 'coremark_per_lut', 'coremark', 'implemented_clock_passing_defined_constraints_mhz'):
        need(selected[key] == metrics[key], 'Comparison final score differs: '+key)
    need(selected['provenance']['long_run']['sha256'] == core_evidence['summary_sha256'] and
         selected['provenance']['core_resource_report']['sha256'] == metrics['resource_report_sha256'] and
         selected['provenance']['bitstream']['sha256'] == core_audit['bitstream_sha256'],
         'Score comparison refers to different final evidence')
    files = [path, path.with_suffix('.csv')]
    with files[1].open(encoding='utf-8-sig', newline='') as file:
        csv_rows = list(csv.DictReader(file))
    need(len(csv_rows) == len(comparison['rows']), 'Score comparison CSV row count differs')
    for json_row, csv_row in zip(comparison['rows'], csv_rows):
        need('candidate' in csv_row and all(value == ('' if json_row.get(key) is None else str(json_row[key]))
                                          for key, value in csv_row.items()), 'Score comparison CSV differs from JSON')
    return files, {item.name: sha(item) for item in files}


def archive(path, build, kind, run, evidence, extra_sources):
    manifest = load(build/'build.json')
    files = {'build.json': build/'build.json', 'independent_pds_audit.json': build/'independent_pds_audit.json'}
    for name, digest in manifest['inputs'].items():
        need(sha(build/'work'/name) == digest, 'PDS input changed before archival: ' + name)
        files['work/'+name] = build/'work'/name
    for item in (build/'source').rglob('*'):
        if item.is_file():
            name = item.relative_to(build/'source').as_posix()
            need(name in manifest['source'] and sha(item) == manifest['source'][name], 'Frozen source changed before archival: ' + name)
            files['source/'+name] = item
    top = manifest['top']
    for name in (f'report_timing/{top}.rtr', f'report_timing/{top}_exception.rtr',
                 'report_timing/run.log', f'place_route/{top}_prr_route_optimize.prt',
                 f'place_route/{top}.ror', f'synthesize/{top}_syn.vm',
                 'synthesize/constraint_check.ccr', f'generate_bitstream/{top}.bgr'):
        files['pds_evidence/'+name] = build/'work'/name
    for index, item in enumerate(evidence):
        files[f'verification/{index:02d}_{item.name}'] = item
    for name, item in extra_sources.items():
        files['regression_sources/'+name] = item
    for name in ('scripts/finalize_maxperf_release.py', 'scripts/finalize_maxperf_core_comparison.py',
                 'scripts/compare_maxperf_vision.py', 'scripts/compare_maxperf_scores.py',
                 'scripts/vision_serial.py', 'scripts/package_advanced.py',
                 'reports/maxperf_20261103/compiler/audit_maxperf_pds.py'):
        files['release_tools/'+name] = ROOT/name
    for name in ('LICENSE.md', 'README.md'):
        files['licenses/CoreMark/'+name] = ROOT/'software/coremark_perf/official'/name
    digests = {name: sha(item) for name, item in files.items()}
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for name, item in sorted(files.items()):
            output.write(item, name)
        output.writestr('SHA256.json', json.dumps(digests, indent=2)+'\n')
        output.writestr('重新构建说明.txt',
                        'work/包含可直接用PDS重建的RTL、引脚/时序约束、DDR厂商工程（完整应用）和run.tcl。\n'
                        '使用支持PG2L200H-6 FBB676的PDS及有效许可证，在work目录运行pds_shell -file run.tcl。\n'
                        'source/保留冻结C源码、头文件、链接脚本、编译命令/ELF/HEX、原始RTL及构建脚本。\n'
                        '重新编译C需配置GCC15.2.0路径，并按source/firmware/build.json的命令与选项构建。\n'
                        '商业PDS工具、其许可证和GCC安装包不包含在归档内。RTL及PDS证据不等同于实板验收。\n')
    with zipfile.ZipFile(path) as zipped:
        need(zipped.testzip() is None, 'Corrupt source archive')
        for name, digest in digests.items():
            need(hashlib.sha256(zipped.read(name)).hexdigest() == digest, 'Archive file digest mismatch: ' + name)
    return {'sha256': sha(path), 'bytes': path.stat().st_size, 'source_entries': len(files)}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('core-build', 'app-build', 'core-run', 'vision-run', 'release-dir'):
        parser.add_argument('--'+option, type=Path, required=True)
    parser.add_argument('--hz', type=int, required=True)
    parser.add_argument('--matrix-audit', type=Path)
    parser.add_argument('--multiply-audit', type=Path)
    parser.add_argument('--clock-audit', type=Path)
    parser.add_argument('--matrix-wave-audit', type=Path)
    parser.add_argument('--cache-audit', type=Path)
    parser.add_argument('--score-comparison', type=Path, help='optional current comparison.json from compare_maxperf_scores.py')
    parser.add_argument('--vision-baseline', type=Path, default=DEFAULT_BASELINE)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    need(args.matrix_audit and args.multiply_audit, 'Release requires --matrix-audit and --multiply-audit evidence')
    core, app, core_run, vision_run = [local(path) for path in (args.core_build, args.app_build, args.core_run, args.vision_run)]
    baseline, matrix_file, multiply_file = [local(path) for path in (args.vision_baseline, args.matrix_audit, args.multiply_audit)]
    release = local(args.release_dir)
    release.relative_to(ROOT/'release')
    need(release != ROOT/'release' and not release.exists(), 'Use a new release directory; existing releases are immutable')
    audits = {'coremark': full_pds_audit(core, 'coremark', args.hz), 'multimodal': full_pds_audit(app, 'multimodal', args.hz)}
    core_summary, core_evidence = verify_coremark(core_run, core, args.hz)
    vision_summary, vision_evidence = verify_vision(vision_run, baseline, app, args.hz)
    core_rtl = bind_rtl(core_summary, core)
    need(core_rtl == bind_rtl(core_summary, app), 'Core/application PDS snapshots use different CPU RTL')
    need(core_rtl == bind_rtl(vision_summary, app), 'Vision regression uses a different final RTL snapshot')
    soc_digest = sha(app/'source/multimodal_soc.v')
    soc_sources = [digest for name, digest in vision_summary['sources'].items()
                   if Path(name).suffix == '.v' and re.search(r'(?m)^\s*module\s+multimodal_soc\b',
                                                             local(name).read_text(encoding='utf-8'))]
    need(soc_sources == [soc_digest],
         'Application SoC differs between regression and PDS')
    matrix = verify_matrix(matrix_file, app)
    multiply = verify_multiply(multiply_file, {'coremark': core, 'multimodal': app})
    optional, optional_files = {}, []
    for name, path, check in [('pll_model', args.clock_audit, lambda p: verify_clock(p, {'coremark': core, 'multimodal': app}, args.hz)),
                              ('matrix_pin_stream', args.matrix_wave_audit, lambda p: verify_matrix_wave(p, app, args.hz)),
                              ('cache_equivalence', args.cache_audit, lambda p: verify_cache(p, {'coremark': core, 'multimodal': app}))]:
        if path:
            optional[name], files = check(local(path))
            optional_files += files
    previous_file = ROOT/'reports/coremark_board_20261103/summary.json'
    previous = load(previous_file)
    need(previous['reported_coremark'] == 24.053533 and previous['cpu_hz'] == 27000000 and previous['official_validation_text_present'],
         'Previous measured board reference is missing')
    report = {'status': 'PASS_LOCAL_RELEASE_GATES_BOARD_PENDING', 'cpu_hz': args.hz,
              'device': 'PG2L200H-6 FBB676', 'board': 'Remote experiment box 5',
              'physical_board_verified': False, 'coremark_rtl': core_evidence, 'vision_rtl': vision_evidence,
              'pds': audits, 'matrix_synthesized_glyphs': matrix, 'mapped_multiplier': multiply, 'additional_verification': optional,
              'previous_board_result': {'coremark': 24.053533, 'cpu_hz': 27000000,
                                        'provenance': 'User-reported board UART, fully validated; not agent-operated board',
                                        'record_sha256': sha(previous_file)},
              'final_rtl_sha256': core_rtl, 'multimodal_soc_sha256': soc_digest,
              'release_script_sha256': sha(Path(__file__)), 'artifacts': {}}
    report['scoring_metrics'] = scoring_metrics(core_evidence, audits['coremark'], core)
    comparison_files = []
    if args.score_comparison:
        comparison_files, report['candidate_score_comparison'] = verify_score_comparison(
            local(args.score_comparison), core, core_evidence, report['scoring_metrics'], audits['coremark'])
    if args.check_only:
        print(json.dumps({'status': report['status'], 'published': False, 'coremark_rtl': core_evidence,
                          'scoring_metrics': report['scoring_metrics'], 'additional_verification': optional}, ensure_ascii=False, indent=2))
        return 0
    stage = release.parent/(release.name+'.staging_'+uuid.uuid4().hex[:12])
    stage.relative_to(ROOT/'release')
    stage.mkdir(parents=False)
    for item in comparison_files:
        shutil.copyfile(item, stage/('candidate_scores'+item.suffix))
    frequency = f'{args.hz/1e6:g}'.replace('.', 'p')+'mhz'
    names = {'coremark': f'coremark_pg2l200h_maxperf_{frequency}_2000iter',
             'multimodal': f'multimodal_pg2l200h_maxperf_{frequency}'}
    for kind, build, run, summary in [('coremark', core, core_run, core_summary), ('multimodal', app, vision_run, vision_summary)]:
        top = 'advanced_top' if kind == 'coremark' else 'multimodal_top'
        bit = stage/(names[kind]+'.sbit')
        shutil.copyfile(build/'work/generate_bitstream'/f'{top}.sbit', bit)
        need(sha(bit) == audits[kind]['bitstream_sha256'], 'Published bit differs from audited bit')
        evidence = [run/'summary.json', matrix_file, multiply_file, previous_file]
        evidence += optional_files
        evidence += comparison_files
        evidence += [core_run/'uart.log'] if kind == 'coremark' else [vision_run/'comparison.json']
        evidence += [Path(item['summary_path']) for item in multiply.values()]
        evidence += [Path(item[role+'_path']) for item in multiply.values() for role in ('testbench', 'log')]
        evidence += [Path(name) for item in multiply.values() for name in item['extra_artifacts_sha256']]
        matrix_summary = load(matrix_file)
        evidence.append(reference(matrix_summary['golden_source'], matrix_file.parent))
        evidence += [matrix_file.parent/name for name in ('tb_production.v', 'matrix_extracted.vm', 'simulation.log', 'compile.log')]
        sources = {name: local(Path(name)) for name in summary['sources']}
        if kind == 'multimodal':
            evidence.append(vision_run/'runner_metrics.json')
        for item in run.rglob('response.bin'):
            sources['captured_responses/'+item.relative_to(run).as_posix()] = item
        if kind == 'multimodal':
            for item in (run/'fixture').rglob('*'):
                if item.is_file():
                    sources['test_fixtures/'+item.relative_to(run/'fixture').as_posix()] = item
        archive_meta = archive(stage/(names[kind]+'_source.zip'), build, kind, run, evidence, sources)
        report['artifacts'][bit.name] = {'sha256': sha(bit), 'bytes': bit.stat().st_size}
        report['artifacts'][names[kind]+'_source.zip'] = archive_meta
    note = f'''实验箱5 / PG2L200H-6 FBB676 性能优化候选

日常数字/汉字识别和点阵显示：{names['multimodal']}.sbit
CPU性能测试专用：{names['coremark']}.sbit
两者择一上传。CoreMark会暂时代替识别应用，测完烧回完整应用恢复识别。

CPU实际PLL频率：{args.hz/1e6:g} MHz，串口115200、8N1、接收选文本/ASCII。
CPU指令集包含Zmmul硬件乘法扩展，编译选项见maxperf_release.json。
CoreMark不需要发送命令：打开接收，烧录并复位，自动运行2000次。
输出每行最多48字节，行间隔1.2秒；打印等待不计入成绩，建议等待约2分钟。
以完整CRC及Correct operation validated确认本次运行成功。

已验证的是RTL/PDS候选，尚未取得本版本实板分数。
2000次RTL：{core_evidence['timed_cycles']}周期，按{args.hz/1e6:g}MHz换算{core_evidence['simulated_seconds']:.6f}秒、
{core_evidence['simulated_coremark']:.6f} CoreMark、{core_evidence['simulated_coremark_per_mhz']:.6f} CoreMark/MHz。
以上是仿真结果，不能写成实板成绩；板端gap1200固件与仿真gap0固件源码及选项一致、ELF不同。
此前27MHz版本的用户实测成绩为24.053533 CoreMark，已完整校验。

三项评分单独报告，不合成加权总分：
1. Score = N/(TotalTime×Frequency_MHz) = {report['scoring_metrics']['score_coremark_per_mhz']:.9f} CoreMark/MHz（RTL）。
2. 本候选已实现并通过已定义PDS时序约束的频率：{args.hz/1e6:g} MHz；最高通过档见候选比较，不能用PDS估计Fmax代替。
3. CoreMark/LUT = {report['scoring_metrics']['coremark_per_lut']:.9f}（RTL成绩 ÷ CoreMark工程后布线LUT数）。
资源：LUT {report['scoring_metrics']['resources']['LUT']}、FF {report['scoring_metrics']['resources']['FF']}、APM {report['scoring_metrics']['resources']['APM']}、DRM {report['scoring_metrics']['resources']['DRM']}。
分母取CoreMark advanced_top工程，未混入完整应用的DDR/点阵资源；原实板版本无法绑定确切LUT数，未虚构其面积效率。

本候选通过53项协议/显示/稀疏行为DDR事务、18次识别及3840项原始输出核对，模型与识别输出一致。
PDS Slow/Fast已定义内部时序、原引脚、DDR请求8ns/响应20ns边界通过；未放宽旧约束。
综合后点阵17字形/272列正确，两个实际综合网表的四种乘法通路已核对。
乘法门级检查仅覆盖强制EX操作数至EX/MEM D端的组合通路，并非完整CPU时序仿真。
外部异步IO未建立完整输入/输出延时约束，物理DDR、点阵实物和新板端成绩仍需远程验收。

完整应用上电默认为数字模式。切换汉字模式用HEX发送，不加回车换行：
A5 43 01 02 01 00 01 00 01 AF 4F
之后按原流程发送五段图像数据；上/下/左/右显示方向箭头。
聊天与语音大模型仍为后续功能，不代表板端模型已装载或运行。

本次为有限候选搜索后通过验证的配置，不宣称证明硬件绝对性能上限。
完整构建来源、资源、时序、校验和及验证范围见maxperf_release.json和两个source.zip。
旧发布版本与报告DOCX保持原样。
'''
    (stage/'使用说明.txt').write_text(note, encoding='utf-8')
    save(stage/'maxperf_release.json', report)
    inventory = {p.name: sha(p) for p in stage.iterdir() if p.is_file()}
    save(stage/'SHA256.json', inventory)
    need(not release.exists(), 'Release target appeared during packaging; refusing overwrite')
    stage.rename(release)
    print(json.dumps({'status': report['status'], 'release': str(release), 'artifacts': report['artifacts'],
                      'physical_board_verified': False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('Release rejected: '+str(error), file=sys.stderr)
        raise SystemExit(1)
