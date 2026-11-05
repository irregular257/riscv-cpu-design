"""Package an independently qualified CoreMark-only resource comparison.

This does not replace or relax the full application release gates. It never
includes, claims to qualify, or promotes a multimodal application bitstream.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import uuid

import finalize_maxperf_release as gate


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('core-build', 'core-run', 'multiply-audit', 'clock-audit', 'release-dir'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--hz', type=int, required=True)
    parser.add_argument('--score-comparison', type=Path)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    build, run, multiply_path, clock_path, target = [gate.local(path) for path in
        (args.core_build, args.core_run, args.multiply_audit, args.clock_audit, args.release_dir)]
    target.relative_to(gate.ROOT/'release')
    gate.need(target != gate.ROOT/'release' and not target.exists(), 'Use a new immutable release directory')
    audit = gate.full_pds_audit(build, 'coremark', args.hz)
    summary, coremark = gate.verify_coremark(run, build, args.hz)
    rtl = gate.bind_rtl(summary, build)
    multiply = gate.verify_multiply(multiply_path, {'coremark': build})
    clock, clock_files = gate.verify_clock(clock_path, {'coremark': build}, args.hz)
    metrics = gate.scoring_metrics(coremark, audit, build)
    comparison_files, comparison_hashes = [], {}
    if args.score_comparison:
        comparison_files, comparison_hashes = gate.verify_score_comparison(
            gate.local(args.score_comparison), build, coremark, metrics, audit)
    report = {'status': 'PASS_COREMARK_ONLY_COMPARISON_GATES_BOARD_PENDING',
              'release_scope': 'CoreMark resource/performance comparison only; no multimodal application qualification',
              'physical_board_verified': False, 'application_included': False, 'cpu_hz': args.hz,
              'coremark_rtl': coremark, 'pds': audit, 'mapped_multiplier': multiply,
              'pll_model': clock, 'scoring_metrics': metrics, 'candidate_score_comparison': comparison_hashes,
              'final_rtl_sha256': rtl, 'release_script_sha256': gate.sha(Path(__file__)),
              'shared_gates_script_sha256': gate.sha(Path(gate.__file__)), 'artifacts': {}}
    if args.check_only:
        print(json.dumps({'status': report['status'], 'published': False, 'application_included': False,
                          'scoring_metrics': metrics}, ensure_ascii=False, indent=2))
        return 0
    stage = target.parent/(target.name+'.staging_'+uuid.uuid4().hex[:12])
    stage.relative_to(gate.ROOT/'release')
    stage.mkdir(parents=False)
    frequency = f'{args.hz/1e6:g}'.replace('.', 'p')+'mhz'
    name = f'coremark_pg2l200h_resource_comparison_{frequency}_2000iter'
    bit = stage/(name+'.sbit')
    shutil.copyfile(build/'work/generate_bitstream/advanced_top.sbit', bit)
    gate.need(gate.sha(bit) == audit['bitstream_sha256'], 'Comparison bit differs from audited bit')
    evidence = [run/'summary.json', run/'uart.log', multiply_path, *clock_files, *comparison_files]
    for item in multiply.values():
        evidence += [Path(item[key]) for key in ('summary_path', 'testbench_path', 'log_path')]
        evidence += [Path(path) for path in item['extra_artifacts_sha256']]
    sources = {source: gate.local(source) for source in summary['sources']}
    archive_meta = gate.archive(stage/(name+'_source.zip'), build, 'coremark', run, evidence, sources)
    report['artifacts'][bit.name] = {'sha256': gate.sha(bit), 'bytes': bit.stat().st_size}
    report['artifacts'][name+'_source.zip'] = archive_meta
    for file in comparison_files:
        shutil.copyfile(file, stage/('candidate_scores'+file.suffix))
    note = f'''CoreMark 资源效率对照，实验箱5 / PG2L200H-6 FBB676

烧录文件：{name}.sbit
这是独立性能对照，只运行CoreMark，不包含数字/汉字识别、DDR或点阵应用。
测试后烧回完整multimodal应用位流恢复原功能。

时钟 {args.hz/1e6:g} MHz；串口115200、8N1、文本/ASCII接收。
烧录复位后自动运行2000次，行间隔1.2秒；等待完整CRC和Correct operation validated。
本包还没有新实板成绩。RTL长测：{coremark['timed_cycles']}周期，{coremark['simulated_seconds']:.6f}秒，{coremark['simulated_coremark']:.6f} CoreMark。
板端gap1200与仿真gap0固件源码/优化选项相同，ELF不同。

三项独立指标：
Score=N/(TotalTime×Frequency_MHz)={metrics['score_coremark_per_mhz']:.9f} CoreMark/MHz（RTL）。
本候选通过已定义PDS时序约束的实际实现频率={args.hz/1e6:g} MHz，未用估计Fmax代替。
CoreMark/LUT={metrics['coremark_per_lut']:.9f}（RTL成绩/CoreMark工程后布线LUT）。
CoreMark工程资源：LUT {metrics['resources']['LUT']}、FF {metrics['resources']['FF']}、APM {metrics['resources']['APM']}、DRM {metrics['resources']['DRM']}。
没有官方权重，不合成总分，也不因为某一项较优就替换完整应用主版本。

已核对冻结来源/ELF/HEX/RTL、2000次CRC、PDS Slow/Fast约束与原引脚、实际综合网表四种乘法、原厂PLL频率和复位。
外部异步IO仍缺完整输入/输出延时约束，新物理板卡结果待验收；乘法检查不是全CPU门级执行。
源码ZIP、JSON证据和SHA256清单供复核。本包没有改写旧发布版本。
'''
    (stage/'使用说明.txt').write_text(note, encoding='utf-8')
    gate.save(stage/'coremark_comparison_release.json', report)
    gate.save(stage/'SHA256.json', {item.name: gate.sha(item) for item in stage.iterdir() if item.is_file()})
    gate.need(not target.exists(), 'Target appeared during packaging; refusing overwrite')
    stage.rename(target)
    print(json.dumps({'status': report['status'], 'release': str(target), 'application_included': False,
                      'artifacts': report['artifacts']}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('Core comparison rejected: '+str(error), file=sys.stderr)
        raise SystemExit(1)
