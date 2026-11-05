"""Run the existing CPU ISA suite against the exact final frozen source tree."""
from pathlib import Path
root=Path('D:/codex_rv32_advanced_20261028')
source=root/'reports/maxperf_20261103/multiplier/run_validation.py'
code=source.read_text()
code=code.replace("BASE=ROOT/'reports/maxperf_20261103/multiplier'", "BASE=ROOT/'reports/maxperf_20261103/icache_data_decouple/frozen_isa'")
code=code.replace("RTL=BASE/'rtl'", "RTL=ROOT/'reports/maxperf_20261103/selected_decoupled_i8_d4'")
code=code.replace("'.PRIVILEGED(1),.BRANCH_PREDICT(1),.ZMMUL(ZMMUL)'", "'.PRIVILEGED(1),.BRANCH_PREDICT(1),.ZMMUL(ZMMUL),.DIRECT_JUMP_PREDICT(1),.RETURN_PREDICT(1)'")
exec(compile(code,str(source),'exec'),{'__name__':'__main__','__file__':str(source)})
