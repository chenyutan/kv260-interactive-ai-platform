"""Offline regression tests. Tcl hardware-manager commands are mocked; no board is modified."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import tkinter
except ImportError:
    tkinter = None

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
import bridge
from xilinx_bridge import core, kv260, vitis_ai
from xilinx_bridge.board_profile import BoardProfile


class PortabilityTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.board = BoardProfile(SKILL / "assets/boards/zedboard.json")

    def profile(self, name, **extra):
        profile = json.loads((SKILL / f"assets/boards/{name}.json").read_text())
        path = self.root / "board.json"
        path.write_text(json.dumps({**profile, **extra}))
        return path

    def test_project_and_hls_follow_board(self):
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.board.parameters("vivado_create_project", {"part": "xc7a35tcpg236-1"})
        xpr = self.root / "project.xpr"
        xpr.write_text('<Project><Configuration><Option Name="Part" Val="xc7z020clg484-1"/>'
                       '</Configuration><Runs><Run Id="impl_1" Part="xc7a35tcpg236-1"/></Runs></Project>')
        with self.assertRaisesRegex(ValueError, "impl_1"):
            self.board.parameters("vivado_build_all", {"xpr": str(xpr)})
        self.assertEqual(self.board.parameters("vitis_build", {"hls": {"top": "filter"}}),
                         {"hls": {"top": "filter", "part": self.board.part}})
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.board.parameters("vitis_build", {"hls": {"part": "wrong"}})

    def test_profile_needs_observed_jtag_ids(self):
        self.assertEqual(self.board.parameters("hw_list_devices", {}), {"server": "localhost:3121"})
        for tool in ("hw_program_fpga", "hw_ila_capture", "hw_program_flash"):
            with self.assertRaisesRegex(ValueError, "exact"):
                self.board.parameters(tool, {})
        with self.assertRaisesRegex(ValueError, "agree"):
            BoardProfile(self.profile("zedboard", jtag={"device_part": "xc7a35t"}))

    @unittest.skipUnless(tkinter, "Tcl 8.6 required for hardware-manager fixture")
    def test_live_tcl_selection_before_each_hardware_operation(self):
        # Execute the scripts actually emitted by the engine against two simulated
        # cables and a DAP + FPGA chain, stopping at first successful selection.
        artifact = str(self.root / "test.bit")
        Path(artifact).touch()
        operations = [(core.program_fpga, (artifact,)), (core.ila_capture, (artifact,)),
                      (core.program_flash, (artifact, "fixture-flash"))]
        for function, args in operations:
            for target, device, part, selected in [
                ("cableB", "xc7z020_1", "xc7z020", True),
                ("missing-cable", "xc7z020_1", "xc7z020", False),
                ("cableB", "missing", "xc7z020", False),
                ("cableB", "xc7z020_1", "xc7a35t", False),
                ("cableB", "arm_dap_0", "xc7z020", False),
            ]:
                with self.subTest(tool=function.__name__, target=target, device=device, part=part):
                    tcl = tkinter.Tcl()
                    tcl.eval("""
                        proc open_hw_manager {} {}
                        proc connect_hw_server {args} {}
                        proc get_hw_targets {} {return {cableA cableB}}
                        proc open_hw_target {target} {set ::cable $target}
                        proc get_hw_devices {} {return {0 1}}
                        proc get_property {prop dev} {
                            switch $prop {
                                NAME {return [lindex {arm_dap_0 xc7z020_1} $dev]}
                                PART {return xc7z020}
                                PROGRAM.IS_SUPPORTED {return $dev}
                            }
                        }
                        proc current_hw_device {dev} {set ::chosen $dev; error STOP_AFTER_SELECTION}
                        proc exit {code} {error "EXIT $code"}
                        rename puts tcl_puts
                        proc puts {args} {}
                    """)
                    with patch.object(core.MANAGER, "alloc_dir", return_value=self.root), \
                         patch.object(core, "run_vivado_tcl", side_effect=RuntimeError("capture")) as run:
                        with self.assertRaisesRegex(RuntimeError, "capture"):
                            function(*args, server="localhost:3121", target_name=target,
                                     device_name=device, device_part=part)
                    script = run.call_args.args[0]
                    with self.assertRaises(tkinter.TclError):
                        tcl.eval(script)
                    self.assertEqual(bool(int(tcl.eval("info exists ::chosen"))), selected)
                    if selected:
                        self.assertEqual((tcl.eval("set ::cable"), tcl.eval("set ::chosen")),
                                         ("cableB", "1"))

    def test_ssh_inspection_and_kv260_guard(self):
        cfg = self.profile("kv260", ssh={"host": "lab-board.local", "user": "dev", "port": 2222})
        response = {"returncode": 0, "stdout": "Linux\n6.8\naarch64\nOther board\nother,board\n"}
        with patch.object(kv260._SshTransport, "ssh", return_value=response) as remote:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(bridge.main(["--board-profile", str(cfg), "board-inspect"]), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["reported_model"], "Other board")
            self.assertEqual(result["compatible"], ["other,board"])
            self.assertIn("device-tree/compatible", remote.call_args.args[0])
            with contextlib.redirect_stderr(io.StringIO()) as error, patch.object(core, "kv260_board_flow") as target:
                self.assertEqual(bridge.main(["--board-profile", str(cfg), "call", "kv260_board_flow"]), 2)
                self.assertIn("did not identify", error.getvalue())
                target.assert_not_called()
        with self.assertRaisesRegex(ValueError, "kv260 board adapter"):
            self.board.parameters("kv260_board_flow", {})

    def test_dpu_architecture_is_selected_per_design(self):
        with self.assertRaisesRegex(ValueError, "dpu_arch"):
            self.board.parameters("vitis_ai_flow", {"action": "compile"})
        path = self.profile("zedboard", dpu_arch="board-design/arch.json")
        args = BoardProfile(path).parameters("vitis_ai_flow", {"action": "compile"})
        self.assertEqual(Path(args["arch_json"]), self.root / "board-design/arch.json")
        for option in ("-aother.json", "-a=other.json", "--ar=other.json", "-nwrong", "-xother"):
            with self.subTest(option=option), self.assertRaisesRegex(ValueError, "compiler_options"):
                vitis_ai.validate_request(action="compile", model="m.xmodel", compiler_options=[option])


if __name__ == "__main__":
    unittest.main()
