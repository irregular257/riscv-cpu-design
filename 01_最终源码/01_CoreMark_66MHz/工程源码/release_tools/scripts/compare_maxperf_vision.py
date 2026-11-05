"""Compare existing actual RTL UART/model/display evidence without rerunning RTL."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from vision_serial import decode

ROOT = Path(__file__).resolve().parents[1]


def need(condition, message):
    if not condition:
        raise ValueError(message)


def local(path):
    result = (ROOT/path).resolve()
    result.relative_to(ROOT)
    return result


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def compare(before_dir, after_dir):
    before, after = [load(folder/'summary.json') for folder in (before_dir, after_dir)]
    need(before['result'] == after['result'] == 'PASS_RTL_BEHAVIORAL_DDR', 'Both RTL runs must pass')
    need(before['references'] == after['references'], 'Model/oracle provenance differs')
    for folder, record in ((before_dir, before), (after_dir, after)):
        need(len(record['cases']) == len(record['metrics']['cases']) == 53, 'Expected 53 completed transactions')
        need(record['metrics']['result'] == 'PASS', 'Runner did not pass')
        need(load(folder/'runner_metrics.json') == record['metrics'], 'Runner metrics differ from summary')
        for name, digest in record['firmware'].items():
            need(sha(folder/'firmware'/name) == digest, 'Frozen firmware changed: ' + name)
        need(sum(case['head_values_checked'] for case in record['metrics']['cases']) == 3840,
             'Incomplete raw-head comparisons')
        need(all(case['result'] == 'PASS' for case in record['metrics']['cases']), 'A transaction failed')
    for name, digest in after['sources'].items():
        need(sha(local(Path(name))) == digest, 'Current RTL/runner source differs from after-run evidence: ' + name)
    before_actual = {case['name']: case for case in before['metrics']['cases']}
    after_actual = {case['name']: case for case in after['metrics']['cases']}
    rows = []
    raw_cases = 0
    for old, new in zip(before['cases'], after['cases']):
        for key in ('name', 'kind', 'request_sha256', 'glyph', 'status', 'expected_brightness'):
            need(old[key] == new[key], 'Test request or expected behavior changed: ' + key)
        for folder, case in ((before_dir, old), (after_dir, new)):
            need(sha(folder/'fixture'/case['name']/'request.bin') == case['request_sha256'], 'Captured request differs')
        for key in ('status', 'cfg_glyph', 'active_glyph', 'brightness', 'head_values_checked', 'boxes_checked'):
            need(before_actual[old['name']][key] == after_actual[new['name']][key], 'Actual display/detection result differs: ' + key)
        if new['kind'] != 'V' or new['status'] != 0:
            continue
        before_path = before_dir/old['name']/'response.bin'
        after_path = after_dir/new['name']/'response.bin'
        x, y = decode(before_path.read_bytes()), decode(after_path.read_bytes())
        for key in ('detections', 'head_q4', 'status'):
            need(x.get(key) == y.get(key), 'Actual vision response differs: ' + key)
        old_cycles, new_cycles = x['cycles'], y['cycles']
        need(old_cycles > 0 and new_cycles > 0, 'Invalid actual inference cycle count')
        raw = 'head_q4' in y
        raw_cases += raw
        rows.append({'name': new['name'], 'mode': new['reference'],
                     'before_inference_cycles': old_cycles, 'after_inference_cycles': new_cycles,
                     'cycle_reduction_percent': 100*(1-new_cycles/old_cycles),
                     'throughput_increase_at_same_clock_percent': 100*(old_cycles/new_cycles-1),
                     'outputs_identical': True, 'raw_head_present': raw, 'box_count': len(y['detections']),
                     'before_response_sha256': sha(before_path), 'after_response_sha256': sha(after_path)})
    need(len(rows) == 18 and raw_cases == 16, 'Expected 18 inferences and 16 raw-head responses')
    old_total = sum(row['before_inference_cycles'] for row in rows)
    new_total = sum(row['after_inference_cycles'] for row in rows)
    return {'result': 'PASS_SAME_MODEL_OUTPUTS_DIFFERENT_FIRMWARE', 'transactions': 53, 'inferences': 18,
            'raw_output_cases': raw_cases, 'raw_head_values_checked': 3840,
            'all_boxes_and_display_results_identical': True, 'model_and_reference_provenance_identical': True,
            'before_elf_sha256': before['firmware']['multimodal.elf'], 'after_elf_sha256': after['firmware']['multimodal.elf'],
            'before_total_inference_cycles': old_total, 'after_total_inference_cycles': new_total,
            'aggregate_cycle_reduction_percent': 100*(1-new_total/old_total),
            'min_case_cycle_reduction_percent': min(row['cycle_reduction_percent'] for row in rows),
            'max_case_cycle_reduction_percent': max(row['cycle_reduction_percent'] for row in rows),
            'aggregate_throughput_increase_at_same_clock_percent': 100*(old_total/new_total-1), 'cases': rows,
            'scope': 'Actual SoC RTL UART responses with independent original model oracle; not physical frame rate or new recognition accuracy benchmark',
            'attribution': 'Both hardware and compiled firmware/ISA differ. This is end-to-end application cycle improvement, not a same-ELF hardware-only comparison. Clock-frequency uplift is not included in cycle reduction.',
            'simulation_clock_hz': after['simulation_clock_hz'],
            'summary_hashes': {'before': sha(before_dir/'summary.json'), 'after': sha(after_dir/'summary.json')},
            'comparison_script_sha256': sha(Path(__file__))}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, default=Path('reports/performance_20261103/multimodal_optimized'))
    parser.add_argument('--after', type=Path, required=True)
    args = parser.parse_args()
    before, after = local(args.before), local(args.after)
    report = compare(before, after)
    (after/'comparison.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'cases'}, indent=2))


if __name__ == '__main__':
    main()
