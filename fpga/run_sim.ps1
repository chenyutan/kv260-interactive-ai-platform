$ErrorActionPreference='Stop'
$vr=$env:XILINX_VIVADO; if (-not $vr) { throw 'Set XILINX_VIVADO to the Vivado 2022.2 installation' }
$env:RDI_APPROOT=$vr;$env:HDI_APPROOT=$vr;$env:RDI_BASEROOT=(Split-Path $vr);$env:RDI_BINROOT="$vr/bin";$env:RDI_DATADIR="$vr/data";$env:RDI_LIBDIR="$vr/lib/win64.o";$env:RDI_BINDIR="$vr/bin";$env:RDI_TPS_ROOT="$vr/tps/win64";$env:RDI_OPT_EXT='.o';$env:XILINX_VIVADO=$vr;$env:XILINX="$vr/ids_lite/ISE";$env:RDI_PLATFORM='win64';$env:TCL_LIBRARY="$vr/tps/tcl/tcl8.5";$env:RT_LIBPATH="$vr/scripts/rt/data";$env:RT_TCL_PATH="$vr/scripts/rt/base_tcl/tcl";$env:SYNTH_COMMON=$env:RT_LIBPATH;$env:ISL_IOSTREAMS_RSA="$vr/tps/isl";$env:RDI_BUILD='yes';$env:RDI_PYTHON3="$vr/tps/win64/python-3.8.3";$env:PYTHONHOME=$env:RDI_PYTHON3;$env:PYTHONPATH="$env:RDI_PYTHON3;$env:RDI_PYTHON3/bin;$env:RDI_PYTHON3/lib;$env:RDI_PYTHON3/lib/site-packages"
$env:PATH="$vr/bin;$vr/lib/win64.o;$vr/tps/win64;$vr/tps/win64/python-3.8.3;$vr/tps/win64/python-3.8.3/bin;$vr/tps/win64/python-3.8.3/lib;$vr/tps/win64/jre11.0.11_9/bin/server;$vr/tps/win64/jre11.0.11_9/bin;$vr/ids_lite/ISE/bin/nt64;$vr/ids_lite/ISE/lib/nt64;$vr/tps/mingw/6.2.0/win64.o/nt/bin;$vr/tps/mingw/6.2.0/win64.o/nt/libexec/gcc/x86_64-w64-mingw32/6.2.0;C:/Windows/System32;C:/Windows"
$build=Join-Path $PSScriptRoot 'sim/build';New-Item -ItemType Directory -Force $build | Out-Null
Copy-Item -LiteralPath "$PSScriptRoot/vectors/input.hex","$PSScriptRoot/vectors/expected.hex","$PSScriptRoot/vectors/count.txt","$PSScriptRoot/vectors/quant_r.hex","$PSScriptRoot/vectors/quant_g.hex","$PSScriptRoot/vectors/quant_b.hex" -Destination $build
Push-Location $build
try {
 & "$vr/bin/unwrapped/win64.o/xvlog.exe" --sv ../../rtl/bodyink_preprocess_core.sv ../tb_preprocess.sv -log compile.log
 if($LASTEXITCODE -ne 0){throw 'xvlog failed'}
 & "$vr/bin/unwrapped/win64.o/xelab.exe" tb_preprocess -s preprocess_sim -debug typical -log elaborate.log
 if($LASTEXITCODE -ne 0){throw 'xelab failed'}
 @'
open_vcd pipeline.vcd
log_vcd /tb_preprocess/clk /tb_preprocess/rst /tb_preprocess/s_valid /tb_preprocess/s_ready /tb_preprocess/m_valid /tb_preprocess/m_ready /tb_preprocess/m_tag /tb_preprocess/m_rgb /tb_preprocess/m_int8 /tb_preprocess/dut/valid
run 25 us
close_vcd
run all
quit
'@ | Set-Content -Encoding ascii run.tcl
 & "$vr/bin/unwrapped/win64.o/xsim.exe" preprocess_sim -tclbatch run.tcl -log simulation.log
 $result=Get-Content -Raw simulation.log
 if($LASTEXITCODE -ne 0 -or $result -match 'Fatal:|FAIL:|FAIL_DETAIL' -or $result -notmatch 'PASS: exact RGB/INT8'){throw 'Simulation verification failed'}
} finally {Pop-Location}
