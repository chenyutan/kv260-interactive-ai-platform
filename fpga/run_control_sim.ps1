$ErrorActionPreference='Stop'
$vr=$env:XILINX_VIVADO; if (-not $vr) { throw 'Set XILINX_VIVADO to the Vivado 2022.2 installation' }
$env:RDI_APPROOT=$vr;$env:HDI_APPROOT=$vr;$env:RDI_BASEROOT=(Split-Path $vr);$env:RDI_BINROOT="$vr/bin";$env:RDI_DATADIR="$vr/data";$env:RDI_LIBDIR="$vr/lib/win64.o";$env:RDI_BINDIR="$vr/bin";$env:RDI_TPS_ROOT="$vr/tps/win64";$env:RDI_OPT_EXT='.o';$env:XILINX_VIVADO=$vr;$env:XILINX="$vr/ids_lite/ISE";$env:RDI_PLATFORM='win64';$env:TCL_LIBRARY="$vr/tps/tcl/tcl8.5";$env:RT_LIBPATH="$vr/scripts/rt/data";$env:RT_TCL_PATH="$vr/scripts/rt/base_tcl/tcl";$env:SYNTH_COMMON=$env:RT_LIBPATH;$env:ISL_IOSTREAMS_RSA="$vr/tps/isl";$env:RDI_BUILD='yes';$env:RDI_PYTHON3="$vr/tps/win64/python-3.8.3";$env:PYTHONHOME=$env:RDI_PYTHON3;$env:PYTHONPATH="$env:RDI_PYTHON3;$env:RDI_PYTHON3/bin;$env:RDI_PYTHON3/lib;$env:RDI_PYTHON3/lib/site-packages"
$env:PATH="$vr/bin;$vr/lib/win64.o;$vr/tps/win64;$vr/tps/win64/python-3.8.3;$vr/tps/win64/python-3.8.3/bin;$vr/tps/win64/python-3.8.3/lib;$vr/tps/win64/jre11.0.11_9/bin/server;$vr/tps/win64/jre11.0.11_9/bin;$vr/ids_lite/ISE/bin/nt64;$vr/ids_lite/ISE/lib/nt64;$vr/tps/mingw/6.2.0/win64.o/nt/bin;$vr/tps/mingw/6.2.0/win64.o/nt/libexec/gcc/x86_64-w64-mingw32/6.2.0;C:/Windows/System32;C:/Windows"
$folder=if($MaxBurst -eq 256){'sim/dma-build'}else{"sim/dma-build-$MaxBurst"}
$build=Join-Path $PSScriptRoot 'sim/control-build';New-Item -ItemType Directory -Force $build | Out-Null
Push-Location $build
try {
 & "$vr/bin/unwrapped/win64.o/xvlog.exe" --sv ../../rtl/bodyink_axil_control.sv ../tb_control.sv -log compile.log
 if($LASTEXITCODE -ne 0){throw 'compile failed'}
 & "$vr/bin/unwrapped/win64.o/xelab.exe" tb_control -s control_sim -debug typical -log elaborate.log
 if($LASTEXITCODE -ne 0){throw 'elaboration failed'}
 "open_vcd control.vcd`nlog_vcd /tb_control/rst /tb_control/clk /tb_control/s_awvalid /tb_control/s_awready /tb_control/s_wvalid /tb_control/s_wready /tb_control/s_bvalid /tb_control/s_bready /tb_control/s_bresp /tb_control/s_rvalid /tb_control/s_rready /tb_control/s_rdata /tb_control/s_rresp /tb_control/dma_start /tb_control/dma_cancel /tb_control/dma_done /tb_control/irq`nrun all`nclose_vcd`nquit" | Set-Content -Encoding ascii run.tcl
 & "$vr/bin/unwrapped/win64.o/xsim.exe" control_sim -tclbatch run.tcl -log simulation.log
 $result=Get-Content -Raw simulation.log
 if($LASTEXITCODE -ne 0 -or $result -match 'Fatal:|FAIL:' -or $result -notmatch 'PASS: AXIL control'){throw 'control tests failed'}
} finally {Pop-Location}

