from pathlib import Path
import hashlib,json
ROOT=Path('D:/codex_rv32_advanced_20261028')
BASE=ROOT/'reports/maxperf_20261103/icache_data_decouple'
OLD=ROOT/'reports/maxperf_20261103/selected_bram_i8_d4/rtl'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
old={p.name:sha(p)for p in OLD.glob('*.v')}
new={p.name:sha(p)for p in (BASE/'rtl').glob('*.v')}
assert old.keys()==new.keys()
assert [n for n in old if old[n]!=new[n]]==['cache_2way.v']
proof=json.loads((BASE/'equivalence/summary.json').read_text())
oracle=json.loads((BASE/'cache_regression/summary.json').read_text())
perf=json.loads((BASE/'coremark_perf/summary.json').read_text())
baseline=ROOT/'reports/maxperf_20261103/cache_bram_candidate/coremark_perf/summary.json'
before=json.loads(baseline.read_text())
assert proof['status']=='PASS' and oracle['result']=='PASS' and perf['result']=='PASS'
assert perf['metrics']==before['metrics'] and perf['crc']==before['crc']
checks=[BASE/'equivalence/summary.json',BASE/'cache_regression/summary.json',BASE/'coremark_perf/summary.json',baseline]
summary={'status':'PASS_RTL_HANDSHAKE_EQUIVALENT','rtl':new,'changed_files':['cache_2way.v'],
 'change':'Only speculative I-cache rdata selection; ready/error, backend protocol, state transitions, data RAM process and D-cache are unchanged.',
 'proof':proof['proof'],'consumer':proof['consumer'],'combinational_assignments':proof['combinational_assignments'],
 'lockstep_runs':proof['runs'],'coremark_all_metrics_identical':True,'coremark_all_crc_identical':True,
 'timed_cycles':perf['metrics']['timed_cycles'],'evidence':{str(p.relative_to(ROOT)):sha(p)for p in checks},
 'physical_board_validation':'NOT_RUN','pds_timing':'NOT_RUN',
 'pds36_prepare_only':'pds36_synth prepared but never synthesized; parent handles final 48 MHz candidates.'}
(BASE/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
print('PASS frozen RTL '+str(BASE/'rtl'))
