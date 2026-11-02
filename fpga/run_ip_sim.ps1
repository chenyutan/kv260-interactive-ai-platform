param([int]$MaxBurst=256,[switch]$Quick)
$ErrorActionPreference='Stop'
$vr=$env:XILINX_VIVADO; if (-not $vr) { throw 'Set XILINX_VIVADO to the Vivado 2022.2 installation' }
$env:RDI_APPROOT=$vr;$env:HDI_APPROOT=$vr;$env:RDI_BASEROOT=(Split-Path $vr);$env:RDI_BINROOT="$vr/bin";$env:RDI_DATADIR="$vr/data";$env:RDI_LIBDIR="$vr/lib/win64.o";$env:RDI_BINDIR="$vr/bin";$env:RDI_TPS_ROOT="$vr/tps/win64";$env:RDI_OPT_EXT='.o';$env:XILINX_VIVADO=$vr;$env:XILINX="$vr/ids_lite/ISE";$env:RDI_PLATFORM='win64';$env:TCL_LIBRARY="$vr/tps/tcl/tcl8.5";$env:RT_LIBPATH="$vr/scripts/rt/data";$env:RT_TCL_PATH="$vr/scripts/rt/base_tcl/tcl";$env:SYNTH_COMMON=$env:RT_LIBPATH;$env:ISL_IOSTREAMS_RSA="$vr/tps/isl";$env:RDI_BUILD='yes';$env:RDI_PYTHON3="$vr/tps/win64/python-3.8.3";$env:PYTHONHOME=$env:RDI_PYTHON3;$env:PYTHONPATH="$env:RDI_PYTHON3;$env:RDI_PYTHON3/bin;$env:RDI_PYTHON3/lib;$env:RDI_PYTHON3/lib/site-packages"
$env:PATH="$vr/bin;$vr/lib/win64.o;$vr/tps/win64;$vr/tps/win64/python-3.8.3;$vr/tps/win64/python-3.8.3/bin;$vr/tps/win64/python-3.8.3/lib;$vr/tps/win64/jre11.0.11_9/bin/server;$vr/tps/win64/jre11.0.11_9/bin;$vr/ids_lite/ISE/bin/nt64;$vr/ids_lite/ISE/lib/nt64;$vr/tps/mingw/6.2.0/win64.o/nt/bin;$vr/tps/mingw/6.2.0/win64.o/nt/libexec/gcc/x86_64-w64-mingw32/6.2.0;C:/Windows/System32;C:/Windows"
$folder=if($MaxBurst -eq 256){'sim/ip-build'}else{"sim/ip-build-$MaxBurst"}
$build=Join-Path $PSScriptRoot $folder;New-Item -ItemType Directory -Force $build | Out-Null
Copy-Item -Path "$PSScriptRoot/roi-vectors/*.hex","$PSScriptRoot/roi-vectors/*.txt","$PSScriptRoot/vectors/quant_*.hex" -Destination $build
Push-Location $build
try {
 & "$vr/bin/unwrapped/win64.o/xvlog.exe" --sv ../../rtl/bodyink_preprocess_core.sv ../../rtl/bodyink_roi_frontend.sv ../../rtl/bodyink_axi_reader.sv ../../rtl/bodyink_axi_writer.sv ../../rtl/bodyink_preprocess_dma.sv ../../rtl/bodyink_axil_control.sv ../../rtl/bodyink_preprocess_ip.sv ../tb_ip.sv -log compile.log
 if($LASTEXITCODE -ne 0){throw 'xvlog failed'}
 $quickValue=[int]$Quick.IsPresent
 & "$vr/bin/unwrapped/win64.o/xelab.exe" tb_ip -s ip_sim -generic_top "MAX_BURST=$MaxBurst" -generic_top "QUICK=$quickValue" -debug typical -log elaborate.log
 if($LASTEXITCODE -ne 0){throw 'xelab failed'}
 @'
open_vcd dma.vcd
log_vcd /tb_ip/clk /tb_ip/rst /tb_ip/arvalid /tb_ip/arready /tb_ip/araddr /tb_ip/arlen /tb_ip/rvalid /tb_ip/rready /tb_ip/rlast /tb_ip/awvalid /tb_ip/awready /tb_ip/awaddr /tb_ip/awlen /tb_ip/wvalid /tb_ip/wready /tb_ip/wdata /tb_ip/wlast /tb_ip/bvalid /tb_ip/bready /tb_ip/done
run 80 us
close_vcd
run all
quit
'@ | Set-Content -Encoding ascii run.tcl
 if($MaxBurst -ne 256){"run all`nquit" | Set-Content -Encoding ascii run.tcl}
 & "$vr/bin/unwrapped/win64.o/xsim.exe" ip_sim -tclbatch run.tcl -log simulation.log
 $result=Get-Content -Raw simulation.log
 if($LASTEXITCODE -ne 0 -or $result -match 'Fatal:|FAIL:|FAIL_DETAIL' -or $result -notmatch 'PASS: AXIL integrated DMA'){throw 'Simulation verification failed'}
} finally {Pop-Location}


