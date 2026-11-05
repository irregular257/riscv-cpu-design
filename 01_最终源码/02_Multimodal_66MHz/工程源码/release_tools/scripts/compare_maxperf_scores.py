"""Recompute the three independent requested metrics from frozen run/PDS evidence.

Missing or unfinished results remain null. No guessed physical-board score,
estimated Fmax substituted for implemented frequency, or combined ranking.
"""
import argparse
import csv
import json
from pathlib import Path
import re
import sys

import finalize_maxperf_release as release

ROOT = release.ROOT
BASE = ROOT/'reports/maxperf_20261103'
CANDIDATES = [
    ('bram36', 'pds_bram36_coremark', 'long_bram36_2000', 'pds_bram36_multimodal'),
    ('bram39', 'pds_bram39_coremark', 'long_bram39_2000', 'pds_bram39_multimodal_greedy'),
    ('bram42', 'pds_bram42_coremark', None, None),
    ('decouple48', 'pds_decouple48_coremark', 'long_decouple48_2000', 'pds_decouple48_multimodal'),
    ('decouple54', 'pds_decouple54_coremark', 'long_decouple54_2000', 'pds_decouple54_multimodal'),
    ('decouple60', 'pds_decouple60_coremark', 'long_decouple60_2000', 'pds_decouple60_multimodal'),
    ('decouple66', 'pds_decouple66_coremark', 'long_decouple66_2000', 'pds_decouple66_multimodal'),
    ('smallcache60', 'pds_smallcache60_coremark', 'long_smallcache60_2000', None),
]


def provenance(path):
    return {'path': str(path.relative_to(ROOT)), 'sha256': release.sha(path)}


def fresh_audit(build, kind, hz):
    manifest = build/'build.json'
    if not manifest.exists():
        return None, 'NOT_PREPARED'
    status = release.load(manifest)['status']
    if status != 'GENERATED_NEEDS_AUDIT':
        return None, status
    try:
        return release.full_pds_audit(build, kind, hz), 'PASS_DEFINED_TIMING_PINS_PROVENANCE'
    except ValueError as error:
        return None, str(error)


def row(label, folder, run_folder, app_folder):
    build = BASE/folder
    result = {'candidate': label, 'result_kind': 'RTL_SIMULATION_BOARD_PENDING',
              'core_build': folder, 'coremark': None, 'score_coremark_per_mhz': None,
              'coremark_per_lut': None, 'implemented_clock_passing_defined_constraints_mhz': None,
              'LUT': None, 'FF': None, 'APM': None, 'DRM': None,
              'physical_board_verified': False, 'long_rtl_binding_passed': False,
              'provenance': {}, 'application_scope': 'Full application candidate' if app_folder else 'CoreMark resource/performance comparison only'}
    if not (build/'build.json').exists():
        result['core_pds_status'] = 'NOT_PREPARED'
        return result
    manifest = release.load(build/'build.json')
    hz = manifest['cpu_hz']
    result['requested_clock_mhz'] = hz/1e6
    result['compiler_flags'] = manifest['firmware_build']['compiler_flags']
    result['provenance']['core_build'] = provenance(build/'build.json')
    result['rtl_sha256'] = {item.name: release.sha(item) for item in (build/'source/rtl').glob('*.v')}
    audit, result['core_pds_status'] = fresh_audit(build, 'coremark', hz)
    if audit:
        result['implemented_clock_passing_defined_constraints_mhz'] = hz/1e6
        result['provenance']['core_audit'] = provenance(build/'independent_pds_audit.json')
        result['provenance']['core_timing_report'] = provenance(build/'work/report_timing/advanced_top.rtr')
        result['provenance']['core_resource_report'] = provenance(build/'work/place_route/advanced_top.ror')
        result['provenance']['bitstream'] = {'sha256': audit['bitstream_sha256']}
        for key in ('LUT', 'FF', 'APM', 'DRM'):
            result[key] = audit['timing']['resources'][key]
        result['slow_setup_ns'] = min(item['slack_ns'] for item in audit['timing']['summaries']['Slow Setup'])
        result['slow_hold_ns'] = min(item['slack_ns'] for item in audit['timing']['summaries']['Slow Hold'])
        report = (build/'work/report_timing/advanced_top.rtr').read_text(encoding='utf-8')
        match = re.search(r'(?m)^\s*cpu_clk\s+([0-9.]+) MHz\s+([0-9.]+) MHz', report)
        result['estimated_fmax_mhz_not_a_tested_frequency'] = float(match[2]) if match else None
    if app_folder:
        app = BASE/app_folder
        app_audit, result['app_pds_status'] = fresh_audit(app, 'multimodal', hz)
        if app_audit:
            result['provenance']['app_audit'] = provenance(app/'independent_pds_audit.json')
    else:
        result['app_pds_status'] = 'NOT_AN_APPLICATION_CANDIDATE'
    run = BASE/run_folder if run_folder else None
    if run and (run/'summary.json').exists():
        try:
            summary, evidence = release.verify_coremark(run, build, hz)
            release.bind_rtl(summary, build)
            result['long_rtl_binding_passed'] = True
            result['iterations'] = evidence['iterations']
            result['timed_cycles'] = evidence['timed_cycles']
            result['total_seconds_rtl'] = evidence['simulated_seconds']
            result['coremark'] = evidence['simulated_coremark']
            result['score_coremark_per_mhz'] = evidence['simulated_coremark_per_mhz']
            result['provenance']['long_run'] = provenance(run/'summary.json')
            result['provenance']['long_uart'] = provenance(run/'uart.log')
            if audit:
                metrics = release.scoring_metrics(evidence, audit, build)
                result['coremark_per_lut'] = metrics['coremark_per_lut']
                result['scoring_metrics'] = metrics
        except ValueError as error:
            result['long_rtl_error'] = str(error)
    if label == 'smallcache60':
        short = BASE/'smallcache_candidate/short_coremark/summary.json'
        if short.exists():
            summary = release.load(short)
            result['short_regression_not_reportable_score'] = {
                'timed_cycles': summary.get('metrics', {}).get('timed_cycles'),
                'result': summary.get('result'), 'provenance': provenance(short)}
    return result


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('reports/maxperf_20261103/score_comparison'))
    args = parser.parse_args()
    output = release.local(args.output)
    output.mkdir(parents=True, exist_ok=True)
    baseline_path = ROOT/'reports/coremark_board_20261103/summary.json'
    old = release.load(baseline_path)
    rows = [{'candidate': 'user_measured_27mhz_baseline', 'result_kind': 'USER_REPORTED_VALIDATED_PHYSICAL_BOARD',
             'physical_board_verified': 'User UART; not directly operated by agent', 'coremark': old['reported_coremark'],
             'requested_clock_mhz': old['cpu_hz']/1e6, 'score_coremark_per_mhz': old['reported_coremark']/(old['cpu_hz']/1e6),
             'coremark_per_lut': None, 'LUT': None, 'FF': None, 'APM': None, 'DRM': None,
             'implemented_clock_passing_defined_constraints_mhz': None,
             'core_pds_status': 'EXACT_UPLOADED_BITSTREAM_NOT_BOUND',
             'resource_unknown_reason': 'User log does not identify exact uploaded bitstream/hash; do not substitute another build resource count',
             'calculation_note': 'Preserves printed CoreMark rounding; normalized value is printed CoreMark divided by 27 MHz',
             'provenance': {'user_board_record': provenance(baseline_path)}}]
    rows += [row(*candidate) for candidate in CANDIDATES]
    passed = [item for item in rows if isinstance(item.get('implemented_clock_passing_defined_constraints_mhz'), (float, int))]
    app_passed = [item for item in passed if item.get('app_pds_status') == 'PASS_DEFINED_TIMING_PINS_PROVENANCE']
    def maximum(items, key):
        values = [item[key] for item in items if isinstance(item.get(key), (float, int))]
        return max(values) if values else None
    report = {'scope': 'Independent metrics; simulation results separate from user board measurements. No weighted total or proof of absolute upper limit.',
              'formula': {'score': 'N / (TotalTime_seconds * Frequency_MHz)', 'coremark': 'N / TotalTime_seconds',
                          'coremark_per_lut': 'coremark / CoreMark_advanced_top_postroute_LUT'},
              'resource_scope': 'CoreMark PDS top only; APM/DRM/FF shown, application DDR/display resources never substituted',
              'frequency_scope': 'Maximum among actually implemented candidates passing defined Slow/Fast constraints; known external IO delay limitations remain. Physical frequency maximum not established.',
              'best_observed_metrics_separately': {
                  'highest_core_pds_passed_clock_mhz': maximum(passed, 'implemented_clock_passing_defined_constraints_mhz'),
                  'highest_both_core_and_app_pds_passed_clock_mhz': maximum(app_passed, 'implemented_clock_passing_defined_constraints_mhz'),
                  'best_validated_rtl_coremark_per_mhz_with_pds': maximum(passed, 'score_coremark_per_mhz'),
                  'best_validated_rtl_coremark_per_lut_with_pds': maximum(passed, 'coremark_per_lut')},
              'combined_weighted_score': None, 'script_sha256': release.sha(Path(__file__)), 'rows': rows}
    release.save(output/'comparison.json', report)
    fields = ['candidate', 'result_kind', 'requested_clock_mhz', 'implemented_clock_passing_defined_constraints_mhz',
              'coremark', 'score_coremark_per_mhz', 'coremark_per_lut', 'LUT', 'FF', 'APM', 'DRM', 'iterations',
              'timed_cycles', 'total_seconds_rtl', 'slow_setup_ns', 'slow_hold_ns', 'core_pds_status', 'app_pds_status',
              'long_rtl_binding_passed', 'physical_board_verified']
    with (output/'comparison.csv').open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({'output': str(output), 'rows': len(rows), **report['best_observed_metrics_separately']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
