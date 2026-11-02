"""AXI4-Stream Video 小图像自检与黄金模型生成器。

Agent 只描述帧、像素格式和期望算法；这里集中处理静态 TDATA 打包、NPPC、
READY/VALID、SOF/EOL、背压、超时和逐拍比较，避免每个任务重复生成协议样板。
"""
from __future__ import annotations

import re
from pathlib import Path


class AxisVideoSpecError(ValueError):
    pass


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*\Z")
_LITERAL = re.compile(r"[A-Za-z0-9_$'().:+\-*/]+\Z")
_MAX_PIXELS = 262_144
_MAX_EXPLICIT_PIXELS = 16_777_216
_MAX_CYCLES = 2_000_000_000
_PPCS = {1, 2, 4, 8}
_FORMATS = {"custom", "gray", "bayer", "rgb", "yuv444", "yuv422"}

_DEFAULT_PORTS = {
    "clk": "clk", "resetn": "aresetn",
    "s_data": "s_axis_video_tdata", "s_valid": "s_axis_video_tvalid",
    "s_ready": "s_axis_video_tready", "s_user": "s_axis_video_tuser",
    "s_last": "s_axis_video_tlast", "m_data": "m_axis_video_tdata",
    "m_valid": "m_axis_video_tvalid", "m_ready": "m_axis_video_tready",
    "m_user": "m_axis_video_tuser", "m_last": "m_axis_video_tlast",
}


def _as_int(spec: dict, name: str, minimum: int, maximum: int) -> int:
    value = spec.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise AxisVideoSpecError(f"image_test.{name} 必须是整数")
    if not minimum <= value <= maximum:
        raise AxisVideoSpecError(f"image_test.{name} 必须在 {minimum}..{maximum} 之间")
    return value


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise AxisVideoSpecError(f"{label} 不是合法的 Verilog 标识符: {value}")
    return value


def _literal(value: object, label: str) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and _LITERAL.fullmatch(value):
        return value
    raise AxisVideoSpecError(f"{label} 不是安全的 Verilog 常量: {value}")


def _format_config(spec: dict) -> dict:
    raw = spec.get("pixel_format")
    fmt = "custom" if raw is None else str(raw).lower().replace("-", "").replace("_", "")
    aliases = {
        "mono": "gray", "mono8": "gray", "gray8": "gray",
        "raw": "bayer", "raw8": "bayer", "bayer8": "bayer",
        "rgb888": "rgb", "rgb24": "rgb", "yuv4448": "yuv444",
        "yuv4228": "yuv422", "uyvy": "yuv422", "yuyv": "yuv422",
    }
    fmt = aliases.get(fmt, fmt)
    if fmt not in _FORMATS:
        raise AxisVideoSpecError(f"image_test.pixel_format 不支持: {raw}")
    ppc = spec.get("pixels_per_clock", 1)
    if isinstance(ppc, bool) or ppc not in _PPCS:
        raise AxisVideoSpecError("image_test.pixels_per_clock 必须是 1/2/4/8")
    if fmt == "custom":
        bits = spec.get("data_width", 8)
        if isinstance(bits, bool) or not isinstance(bits, int) or not 1 <= bits <= 64:
            raise AxisVideoSpecError("image_test.data_width 必须在 1..64 之间")
        component_bits = bits
        active_bits = bits * ppc
        axis_bits = active_bits
        channels = 1
    else:
        component_bits = spec.get("component_bits", 8)
        if isinstance(component_bits, bool) or not isinstance(component_bits, int) or not 1 <= component_bits <= 16:
            raise AxisVideoSpecError("image_test.component_bits 必须在 1..16 之间")
        if fmt == "yuv422" and ppc == 1:
            raise AxisVideoSpecError("YUV422 每拍至少需要 2 像素，pixels_per_clock 应为 2/4/8")
        channels = 3 if fmt in {"rgb", "yuv444"} else 1
        active_bits = component_bits * ppc * (2 if fmt == "yuv422" else channels)
        axis_bits = ((active_bits + 7) // 8) * 8
        explicit = spec.get("data_width")
        # 0.4.x callers often carried data_width=8. Treat a value equal to the
        # component width as a compatible hint when an explicit format is added.
        if explicit is not None and explicit not in {component_bits, axis_bits}:
            raise AxisVideoSpecError(
                f"image_test.data_width 与格式推导值 {axis_bits} 不一致；可省略 data_width"
            )
    return {
        "pixel_format": fmt, "pixels_per_clock": ppc,
        "component_bits": component_bits, "channels": channels,
        "active_bits": active_bits, "axis_data_width": axis_bits,
    }


def _components(value: object, fmt: str, bits: int, label: str) -> tuple[int, ...]:
    maximum = (1 << bits) - 1
    if fmt in {"custom", "gray", "bayer"}:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
            raise AxisVideoSpecError(f"{label} 必须是 0..{maximum} 的整数")
        return (value,)
    if isinstance(value, int) and not isinstance(value, bool):
        if not 0 <= value < (1 << (bits * 3)):
            raise AxisVideoSpecError(f"{label} 已打包值超出 {bits * 3} 位")
        if fmt == "rgb":
            return (value >> (2 * bits) & maximum, value & maximum,
                    value >> bits & maximum)
        return (value & maximum, value >> bits & maximum,
                value >> (2 * bits) & maximum)
    if isinstance(value, dict):
        keys = ("r", "g", "b") if fmt == "rgb" else ("y", "u", "v")
        try:
            values = tuple(value[k] for k in keys)
        except KeyError as exc:
            raise AxisVideoSpecError(f"{label} 缺少分量 {exc.args[0]}") from exc
    elif isinstance(value, (list, tuple)) and len(value) == 3:
        values = tuple(value)
    else:
        order = "[R,G,B]" if fmt == "rgb" else "[Y,U,V]"
        raise AxisVideoSpecError(f"{label} 必须是 {order}、同名对象或已打包整数")
    for component in values:
        if isinstance(component, bool) or not isinstance(component, int) or not 0 <= component <= maximum:
            raise AxisVideoSpecError(f"{label} 分量必须是 0..{maximum} 的整数")
    return values


def _pixel_word(value: object, fmt: str, bits: int, label: str) -> int:
    c = _components(value, fmt, bits, label)
    if fmt == "rgb":
        r, g, b = c
        return g | (b << bits) | (r << (2 * bits))
    if fmt == "yuv444":
        y, u, v = c
        return y | (u << bits) | (v << (2 * bits))
    return c[0]


def _validate_pixels(values: object, count: int, cfg: dict, label: str) -> list[object]:
    if not isinstance(values, (list, tuple)):
        raise AxisVideoSpecError(f"{label} 必须是像素数组")
    if len(values) != count:
        raise AxisVideoSpecError(f"{label} 像素数应为 {count}，实际 {len(values)}")
    fmt, bits = cfg["pixel_format"], cfg["component_bits"]
    out = list(values)
    for index, value in enumerate(out):
        _components(value, fmt, bits, f"{label}[{index}]")
    if fmt == "yuv422":
        for index in range(0, len(out), 2):
            a = _components(out[index], fmt, bits, f"{label}[{index}]")
            b = _components(out[index + 1], fmt, bits, f"{label}[{index + 1}]")
            if a[1:] != b[1:]:
                raise AxisVideoSpecError(f"{label}[{index}:{index + 2}] 的 YUV422 U/V 必须相同")
    return out


def _pack_frame(values: list[object], width: int, height: int, cfg: dict,
                label: str) -> list[int]:
    fmt, bits, ppc = cfg["pixel_format"], cfg["component_bits"], cfg["pixels_per_clock"]
    if fmt == "yuv422" and width % 2:
        raise AxisVideoSpecError(f"{label} 的 YUV422 帧宽必须为偶数")
    lane_bits = bits * (2 if fmt == "yuv422" else cfg["channels"])
    beats: list[int] = []
    for y in range(height):
        row = values[y * width:(y + 1) * width]
        for start in range(0, width, ppc):
            beat = 0
            if fmt == "yuv422":
                for lane, x in enumerate(range(start, min(start + ppc, width), 2)):
                    y0, u, v = _components(row[x], fmt, bits, f"{label}[{y * width + x}]")
                    y1 = _components(row[x + 1], fmt, bits, f"{label}[{y * width + x + 1}]")[0]
                    group = y0 | (u << bits) | (y1 << (2 * bits)) | (v << (3 * bits))
                    beat |= group << (lane * 4 * bits)
            else:
                for lane, x in enumerate(range(start, min(start + ppc, width))):
                    beat |= _pixel_word(row[x], fmt, bits, f"{label}[{y * width + x}]") << (lane * lane_bits)
            beats.append(beat)
    return beats


def _pack_frames(values: list[object], width: int, height: int, frames: int,
                 cfg: dict, label: str) -> list[int]:
    frame_pixels = width * height
    beats: list[int] = []
    for frame in range(frames):
        start = frame * frame_pixels
        beats.extend(_pack_frame(
            values[start:start + frame_pixels], width, height, cfg,
            f"{label}[frame={frame}]",
        ))
    return beats


def pack_frame(values: list[object], width: int, height: int,
               pixel_format: str = "rgb", component_bits: int = 8,
               pixels_per_clock: int = 1) -> dict:
    """Pack a host image exactly like the generated AXI4-Stream Video testbench.

    This public adapter keeps companion-host frames, RTL simulation memories,
    and board-side DMA buffers on one byte/lane convention.
    """
    spec = {
        "pixel_format": pixel_format, "component_bits": component_bits,
        "pixels_per_clock": pixels_per_clock,
    }
    cfg = _format_config(spec)
    if isinstance(width, bool) or not isinstance(width, int) or not 1 <= width <= 16384:
        raise AxisVideoSpecError("frame.width 必须在 1..16384 之间")
    if isinstance(height, bool) or not isinstance(height, int) or not 1 <= height <= 16384:
        raise AxisVideoSpecError("frame.height 必须在 1..16384 之间")
    checked = _validate_pixels(values, width * height, cfg, "frame.pixels")
    beats = _pack_frame(checked, width, height, cfg, "frame.pixels")
    return {
        "beats": beats, "beat_bytes": (cfg["axis_data_width"] + 7) // 8,
        "beats_per_line": (width + pixels_per_clock - 1) // pixels_per_clock,
        "data_width": cfg["axis_data_width"], "active_data_bits": cfg["active_bits"],
        "pixel_format": cfg["pixel_format"], "component_bits": cfg["component_bits"],
        "pixels_per_clock": cfg["pixels_per_clock"],
    }


def _validate_mem_file(path: Path, beats: int, data_width: int, label: str) -> None:
    count = 0
    maximum = (1 << data_width) - 1
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                line = line.split("//", 1)[0].split("#", 1)[0]
                for token in line.split():
                    try:
                        value = int(token.replace("_", ""), 16)
                    except ValueError as exc:
                        raise AxisVideoSpecError(
                            f"{label} 第 {line_number} 行含非法十六进制拍数据: {token}"
                        ) from exc
                    if value < 0 or value > maximum:
                        raise AxisVideoSpecError(f"{label} 含超出 {data_width} 位的数据")
                    count += 1
                    if count > beats:
                        raise AxisVideoSpecError(
                            f"{label} 拍数应为 {beats}，实际至少 {count}"
                        )
    except OSError as exc:
        raise AxisVideoSpecError(f"无法读取 {label}: {path}") from exc
    if count != beats:
        raise AxisVideoSpecError(f"{label} 拍数应为 {beats}，实际 {count}")


def _pixel_budget(spec: dict) -> int:
    value = spec.get("pixel_budget", _MAX_PIXELS)
    if (isinstance(value, bool) or not isinstance(value, int) or
            not 1 <= value <= _MAX_EXPLICIT_PIXELS):
        raise AxisVideoSpecError(
            f"image_test.pixel_budget 必须在 1..{_MAX_EXPLICIT_PIXELS} 之间"
        )
    return value


def _border_index(index: int, size: int, mode: str) -> int | None:
    if 0 <= index < size:
        return index
    if mode == "zero":
        return None
    if mode == "replicate":
        return min(max(index, 0), size - 1)
    if mode == "wrap":
        return index % size
    if size == 1:
        return 0
    if mode == "reflect":
        period = 2 * size
        mapped = index % period
        return mapped if mapped < size else period - mapped - 1
    if mode == "reflect101":
        period = 2 * size - 2
        mapped = index % period
        return mapped if mapped < size else period - mapped
    raise AxisVideoSpecError(f"golden.border 不支持: {mode}")


def _divide(value: int, divisor: int, rounding: str) -> int:
    if rounding == "truncate":
        return abs(value) // divisor * (-1 if value < 0 else 1)
    if rounding == "nearest":
        return (abs(value) + divisor // 2) // divisor * (-1 if value < 0 else 1)
    raise AxisVideoSpecError("golden.rounding 必须是 truncate 或 nearest")


def _convolution(values: list[object], width: int, height: int, cfg: dict,
                 golden: dict) -> tuple[list[object], int, int, dict]:
    fmt, bits = cfg["pixel_format"], cfg["component_bits"]
    if fmt == "yuv422":
        raise AxisVideoSpecError("卷积黄金模型暂不直接作用于色度抽样 YUV422；请使用 gray/rgb/yuv444")
    kernel = golden.get("kernel")
    if (not isinstance(kernel, (list, tuple)) or not kernel or
            not all(isinstance(row, (list, tuple)) and row for row in kernel)):
        raise AxisVideoSpecError("golden.kernel 必须是非空二维整数数组")
    kh, kw = len(kernel), len(kernel[0])
    if kh > 15 or kw > 15 or kh % 2 == 0 or kw % 2 == 0 or any(len(row) != kw for row in kernel):
        raise AxisVideoSpecError("golden.kernel 必须是最大 15x15 的奇数矩形")
    if any(isinstance(v, bool) or not isinstance(v, int) for row in kernel for v in row):
        raise AxisVideoSpecError("golden.kernel 系数必须是整数")
    border = str(golden.get("border", "replicate")).lower()
    if border not in {"zero", "replicate", "reflect", "reflect101", "wrap", "crop"}:
        raise AxisVideoSpecError("golden.border 必须是 zero/replicate/reflect/reflect101/wrap/crop")
    divisor, bias = golden.get("divisor", 1), golden.get("bias", 0)
    if isinstance(divisor, bool) or not isinstance(divisor, int) or divisor <= 0:
        raise AxisVideoSpecError("golden.divisor 必须是正整数")
    if isinstance(bias, bool) or not isinstance(bias, int):
        raise AxisVideoSpecError("golden.bias 必须是整数")
    rounding, saturate = golden.get("rounding", "truncate"), golden.get("saturate", True)
    if not isinstance(saturate, bool):
        raise AxisVideoSpecError("golden.saturate 必须是布尔值")
    rx, ry = kw // 2, kh // 2
    out_w, out_h = (width - 2 * rx, height - 2 * ry) if border == "crop" else (width, height)
    if out_w <= 0 or out_h <= 0:
        raise AxisVideoSpecError("golden.kernel 大于 crop 后可用图像")
    components = [_components(v, fmt, bits, f"input_pixels[{i}]") for i, v in enumerate(values)]
    maximum = (1 << bits) - 1
    output: list[object] = []
    for oy in range(out_h):
        for ox in range(out_w):
            cx, cy = (ox + rx, oy + ry) if border == "crop" else (ox, oy)
            result: list[int] = []
            for channel in range(cfg["channels"]):
                total = 0
                for ky, row in enumerate(kernel):
                    sy = _border_index(cy + ky - ry, height, "zero" if border == "crop" else border)
                    for kx, coefficient in enumerate(row):
                        sx = _border_index(cx + kx - rx, width, "zero" if border == "crop" else border)
                        if sx is not None and sy is not None:
                            total += coefficient * components[sy * width + sx][channel]
                value = _divide(total, divisor, rounding) + bias
                result.append(min(max(value, 0), maximum) if saturate else value & maximum)
            output.append(result[0] if len(result) == 1 else result)
    return output, out_w, out_h, {
        "operation": "convolution", "kernel_width": kw, "kernel_height": kh,
        "border": border, "divisor": divisor, "bias": bias,
        "rounding": rounding, "saturate": saturate,
    }


def _sv_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace('"', '\\"')


def _port_map(spec: dict) -> dict[str, str]:
    ports = dict(_DEFAULT_PORTS)
    if spec.get("port_style", "axis_video") == "axis":
        for side in ("s", "m"):
            for signal in ("data", "valid", "ready", "user", "last"):
                ports[f"{side}_{signal}"] = f"{side}_axis_t{signal}"
    overrides = spec.get("ports", {})
    if not isinstance(overrides, dict):
        raise AxisVideoSpecError("image_test.ports 必须是对象")
    unknown = set(overrides) - set(ports)
    if unknown:
        raise AxisVideoSpecError(f"image_test.ports 含未知键: {sorted(unknown)}")
    for key, value in overrides.items():
        ports[key] = _identifier(value, f"image_test.ports.{key}")
    return ports


def _testbench(dut: str, input_beats_per_line: int, input_height: int,
               input_frames: int, output_beats_per_line: int, output_height: int,
               output_frames: int, data_width: int,
               input_pixels: int, output_pixels: int, ppc: int,
               input_mem: Path, expected_mem: Path, parameters: dict,
               ports: dict[str, str], backpressure: bool, max_cycles: int,
               inter_frame_gap: int) -> str:
    parameter_lines = ""
    if parameters:
        pairs = [f".{_identifier(k, f'image_test.parameters.{k}')}({_literal(v, f'image_test.parameters.{k}')})"
                 for k, v in parameters.items()]
        parameter_lines = " #(\n        " + ",\n        ".join(pairs) + "\n    )"
    connections = {
        "clk": "clk", "resetn": "aresetn", "s_data": "s_tdata",
        "s_valid": "s_tvalid", "s_ready": "s_tready", "s_user": "s_tuser",
        "s_last": "s_tlast", "m_data": "m_tdata", "m_valid": "m_tvalid",
        "m_ready": "m_tready", "m_user": "m_tuser", "m_last": "m_tlast",
    }
    port_lines = ",\n        ".join(f".{ports[key]}({connections[key]})" for key in connections)
    return f"""`timescale 1ns/1ps
module xab_axis_video_tb;
    localparam integer INPUT_WIDTH = {input_beats_per_line};
    localparam integer OUTPUT_WIDTH = {output_beats_per_line};
    localparam integer INPUT_BEATS_PER_LINE = {input_beats_per_line};
    localparam integer INPUT_HEIGHT = {input_height};
    localparam integer INPUT_FRAMES = {input_frames};
    localparam integer INPUT_BEATS_PER_FRAME = INPUT_BEATS_PER_LINE * INPUT_HEIGHT;
    localparam integer OUTPUT_BEATS_PER_LINE = {output_beats_per_line};
    localparam integer OUTPUT_HEIGHT = {output_height};
    localparam integer OUTPUT_FRAMES = {output_frames};
    localparam integer OUTPUT_BEATS_PER_FRAME = OUTPUT_BEATS_PER_LINE * OUTPUT_HEIGHT;
    localparam integer INPUT_BEATS = INPUT_BEATS_PER_FRAME * INPUT_FRAMES;
    localparam integer OUTPUT_BEATS = OUTPUT_BEATS_PER_FRAME * OUTPUT_FRAMES;
    localparam integer INPUT_PIXELS = {input_pixels};
    localparam integer OUTPUT_PIXELS = {output_pixels};
    localparam integer PIXELS_PER_CLOCK = {ppc};
    localparam integer DATA_WIDTH = {data_width};
    localparam integer MAX_CYCLES = {max_cycles};
    localparam integer ENABLE_BACKPRESSURE = {1 if backpressure else 0};
    localparam integer INTER_FRAME_GAP = {inter_frame_gap};

    reg clk = 1'b0;
    reg aresetn = 1'b0;
    reg [DATA_WIDTH-1:0] s_tdata = {{DATA_WIDTH{{1'b0}}}};
    reg s_tvalid = 1'b0;
    wire s_tready;
    reg s_tuser = 1'b0;
    reg s_tlast = 1'b0;
    wire [DATA_WIDTH-1:0] m_tdata;
    wire m_tvalid;
    reg m_tready = 1'b0;
    wire m_tuser;
    wire m_tlast;

    reg [DATA_WIDTH-1:0] input_beats [0:INPUT_BEATS-1];
    reg [DATA_WIDTH-1:0] expected_beats [0:OUTPUT_BEATS-1];
    integer cycle_count = 0;
    integer output_index = 0;
    reg stalled = 1'b0;
    reg [DATA_WIDTH-1:0] stalled_data;
    reg stalled_user;
    reg stalled_last;

    always #5 clk = ~clk;
    {dut}{parameter_lines} dut (
        {port_lines}
    );

    task automatic wb_fail(input string reason);
        begin
            $display("WB_FAIL|axis_video|%s|cycle=%0d|output_beat=%0d", reason, cycle_count, output_index);
            $finish;
        end
    endtask

    always @(posedge clk) begin
        if (!aresetn) begin
            cycle_count <= 0;
            output_index <= 0;
            stalled <= 1'b0;
        end else begin
            cycle_count <= cycle_count + 1;
            if (cycle_count >= MAX_CYCLES)
                wb_fail("timeout");
            if (stalled) begin
                if (!m_tvalid)
                    wb_fail("m_axis_tvalid dropped during backpressure");
                if (m_tdata !== stalled_data || m_tuser !== stalled_user || m_tlast !== stalled_last)
                    wb_fail("m_axis payload changed during backpressure");
            end
            stalled <= m_tvalid && !m_tready;
            if (m_tvalid && !m_tready) begin
                stalled_data <= m_tdata;
                stalled_user <= m_tuser;
                stalled_last <= m_tlast;
            end
            if (m_tvalid && m_tready) begin
                if (output_index >= OUTPUT_BEATS)
                    wb_fail("extra output beat");
                if (m_tdata !== expected_beats[output_index]) begin
                    $display("WB_MISMATCH|beat=%0d|got=%0h|expected=%0h", output_index, m_tdata, expected_beats[output_index]);
                    wb_fail("payload mismatch");
                end
                if (m_tuser !== ((output_index % OUTPUT_BEATS_PER_FRAME) == 0))
                    wb_fail("SOF/TUSER mismatch");
                if (m_tlast !== ((output_index % OUTPUT_BEATS_PER_LINE) == OUTPUT_BEATS_PER_LINE-1))
                    wb_fail("EOL/TLAST mismatch");
                output_index <= output_index + 1;
            end
        end
    end

    always @(negedge clk) begin
        if (!aresetn)
            m_tready <= 1'b0;
        else if (ENABLE_BACKPRESSURE)
            m_tready <= ((cycle_count % 5) != 2) && ((cycle_count % 11) != 7);
        else
            m_tready <= 1'b1;
    end

    task automatic send_beat(input integer index);
        begin
            if (index > 0 && (index % INPUT_BEATS_PER_FRAME) == 0)
                repeat (INTER_FRAME_GAP) @(negedge clk);
            if (ENABLE_BACKPRESSURE)
                repeat ((index * 7 + 3) % 3) @(negedge clk);
            @(negedge clk);
            s_tdata = input_beats[index];
            s_tuser = ((index % INPUT_BEATS_PER_FRAME) == 0);
            s_tlast = ((index % INPUT_BEATS_PER_LINE) == INPUT_BEATS_PER_LINE-1);
            s_tvalid = 1'b1;
            @(posedge clk);
            while (!s_tready)
                @(posedge clk);
            @(negedge clk);
            s_tvalid = 1'b0;
            s_tuser = 1'b0;
            s_tlast = 1'b0;
        end
    endtask

    integer input_index;
    initial begin
        $readmemh("{_sv_path(input_mem)}", input_beats);
        $readmemh("{_sv_path(expected_mem)}", expected_beats);
        repeat (5) @(posedge clk);
        @(negedge clk);
        aresetn = 1'b1;
        for (input_index = 0; input_index < INPUT_BEATS; input_index = input_index + 1)
            send_beat(input_index);
        wait (output_index == OUTPUT_BEATS);
        repeat (4) @(posedge clk);
        $display("WB_PASS|axis_video|frames=%0d|input_pixels=%0d|output_pixels=%0d|input_beats=%0d|output_beats=%0d|nppc=%0d|cycles=%0d|backpressure=%0d", INPUT_FRAMES, INPUT_PIXELS, OUTPUT_PIXELS, INPUT_BEATS, OUTPUT_BEATS, PIXELS_PER_CLOCK, cycle_count, ENABLE_BACKPRESSURE);
        $finish;
    end
endmodule
"""


def prepare_items(project_dir: Path, spec: dict) -> tuple[list[dict], dict]:
    """验证 image_test 并返回可交给 core._prepare_source_batch 的内部 items。"""
    if not isinstance(spec, dict):
        raise AxisVideoSpecError("image_test 必须是对象")
    dut = _identifier(spec.get("dut"), "image_test.dut")
    width = _as_int(spec, "width", 1, 16_384)
    height = _as_int(spec, "height", 1, 16_384)
    frames = spec.get("frames", 1)
    if isinstance(frames, bool) or not isinstance(frames, int) or not 1 <= frames <= 4096:
        raise AxisVideoSpecError("image_test.frames 必须在 1..4096 之间")
    cfg = _format_config(spec)
    pixel_budget = _pixel_budget(spec)
    input_count = width * height * frames
    if input_count > pixel_budget:
        raise AxisVideoSpecError(
            f"image_test 输入合计 {input_count} 像素，超过 pixel_budget={pixel_budget}；"
            "请降低尺寸/帧数或显式提高预算"
        )

    input_source: Path | None = None
    input_pixels: list[object] | None = None
    input_beats_per_line = (width + cfg["pixels_per_clock"] - 1) // cfg["pixels_per_clock"]
    input_beats_count = input_beats_per_line * height * frames
    if "input_pixels" in spec:
        input_pixels = _validate_pixels(spec["input_pixels"], input_count, cfg, "image_test.input_pixels")
        input_values = _pack_frames(
            input_pixels, width, height, frames, cfg, "image_test.input_pixels"
        )
    else:
        raw_path = spec.get("input_mem")
        if not isinstance(raw_path, str) or not raw_path:
            raise AxisVideoSpecError("image_test 必须提供 input_pixels 或 input_mem")
        input_source = Path(raw_path).expanduser().resolve()
        if not input_source.is_file():
            raise AxisVideoSpecError(f"image_test.input_mem 不存在: {raw_path}")
        _validate_mem_file(
            input_source, input_beats_count, cfg["axis_data_width"],
            "image_test.input_mem",
        )
        input_values = []

    golden_meta = None
    generated_expected: list[object] | None = None
    golden = spec.get("golden")
    if golden is not None:
        if not isinstance(golden, dict) or golden.get("operation") != "convolution":
            raise AxisVideoSpecError("image_test.golden.operation 当前支持 convolution")
        if input_pixels is None:
            raise AxisVideoSpecError("卷积黄金模型需要 input_pixels，不能只提供已打包 input_mem")
        generated_expected = []
        frame_pixels = width * height
        derived_w = derived_h = 0
        for frame in range(frames):
            generated_frame, derived_w, derived_h, golden_meta = _convolution(
                input_pixels[frame * frame_pixels:(frame + 1) * frame_pixels],
                width, height, cfg, golden,
            )
            generated_expected.extend(generated_frame)
    else:
        derived_w, derived_h = width, height
    output_width, output_height = spec.get("output_width", derived_w), spec.get("output_height", derived_h)
    if isinstance(output_width, bool) or not isinstance(output_width, int) or not 1 <= output_width <= 16_384:
        raise AxisVideoSpecError("image_test.output_width 必须在 1..16384 之间")
    if isinstance(output_height, bool) or not isinstance(output_height, int) or not 1 <= output_height <= 16_384:
        raise AxisVideoSpecError("image_test.output_height 必须在 1..16384 之间")
    if generated_expected is not None and (output_width, output_height) != (derived_w, derived_h):
        raise AxisVideoSpecError(f"卷积输出尺寸应为 {derived_w}x{derived_h}")
    output_frames = spec.get("output_frames", frames)
    if (isinstance(output_frames, bool) or not isinstance(output_frames, int) or
            not 1 <= output_frames <= 4096):
        raise AxisVideoSpecError("image_test.output_frames 必须在 1..4096 之间")
    if generated_expected is not None and output_frames != frames:
        raise AxisVideoSpecError("卷积黄金模型不改变帧数，output_frames 必须等于 frames")
    output_count = output_width * output_height * output_frames
    if output_count > pixel_budget:
        raise AxisVideoSpecError(
            f"image_test 输出合计 {output_count} 像素，超过 pixel_budget={pixel_budget}"
        )
    output_beats_per_line = (output_width + cfg["pixels_per_clock"] - 1) // cfg["pixels_per_clock"]
    output_beats_count = output_beats_per_line * output_height * output_frames

    expected_source: Path | None = None
    expected_pixels = generated_expected if generated_expected is not None else spec.get("expected_pixels")
    if expected_pixels is not None:
        validated = _validate_pixels(expected_pixels, output_count, cfg, "image_test.expected_pixels")
        expected_values = _pack_frames(
            validated, output_width, output_height, output_frames, cfg,
            "image_test.expected_pixels",
        )
    else:
        raw_path = spec.get("expected_mem")
        if not isinstance(raw_path, str) or not raw_path:
            raise AxisVideoSpecError("image_test 必须提供 expected_pixels、expected_mem 或 golden")
        expected_source = Path(raw_path).expanduser().resolve()
        if not expected_source.is_file():
            raise AxisVideoSpecError(f"image_test.expected_mem 不存在: {raw_path}")
        _validate_mem_file(
            expected_source, output_beats_count, cfg["axis_data_width"],
            "image_test.expected_mem",
        )
        expected_values = []

    parameters = spec.get("parameters", {})
    if not isinstance(parameters, dict) or len(parameters) > 32:
        raise AxisVideoSpecError("image_test.parameters 必须是最多 32 项的对象")
    ports = _port_map(spec)
    backpressure = spec.get("backpressure", True)
    if not isinstance(backpressure, bool):
        raise AxisVideoSpecError("image_test.backpressure 必须是布尔值")
    inter_frame_gap = spec.get("inter_frame_gap", 2 if frames > 1 else 0)
    if (isinstance(inter_frame_gap, bool) or not isinstance(inter_frame_gap, int) or
            not 0 <= inter_frame_gap <= 1_000_000):
        raise AxisVideoSpecError("image_test.inter_frame_gap 必须在 0..1000000 之间")
    max_cycles = spec.get("max_cycles", max(1000, (input_beats_count + output_beats_count) * 20 + 100))
    if isinstance(max_cycles, bool) or not isinstance(max_cycles, int) or not 100 <= max_cycles <= _MAX_CYCLES:
        raise AxisVideoSpecError(f"image_test.max_cycles 必须在 100..{_MAX_CYCLES} 之间")

    rel_dir = Path(".xab") / "axis_video"
    input_rel, expected_rel, tb_rel = rel_dir / "input.mem", rel_dir / "expected.mem", rel_dir / "xab_axis_video_tb.sv"
    input_path = input_source or (project_dir / input_rel).resolve()
    expected_path = expected_source or (project_dir / expected_rel).resolve()
    digits = max(1, (cfg["axis_data_width"] + 3) // 4)
    items: list[dict] = []
    if input_source is None:
        items.append({"rel_path": str(input_rel), "content": "".join(f"{value:0{digits}x}\n" for value in input_values),
                      "fileset": "sim_1", "add_to_project": False})
    if expected_source is None:
        items.append({"rel_path": str(expected_rel), "content": "".join(f"{value:0{digits}x}\n" for value in expected_values),
                      "fileset": "sim_1", "add_to_project": False})
    items.append({
        "rel_path": str(tb_rel),
        "content": _testbench(
            dut, input_beats_per_line, height, frames,
            output_beats_per_line, output_height, output_frames,
            cfg["axis_data_width"], input_count, output_count, cfg["pixels_per_clock"],
            input_path, expected_path, parameters, ports, backpressure, max_cycles,
            inter_frame_gap,
        ),
        "fileset": "sim_1", "top": "xab_axis_video_tb",
    })
    meta = {
        "profile": "axis_video", "dut": dut, "width": width, "height": height,
        "frames": frames,
        "pixel_budget": pixel_budget,
        "pixel_budget_override": pixel_budget != _MAX_PIXELS,
        "input_pixels": input_count, "input_beats": input_beats_count,
        "output_width": output_width, "output_height": output_height,
        "output_frames": output_frames,
        "output_pixels": output_count, "output_beats": output_beats_count,
        "data_width": cfg["axis_data_width"], "active_data_bits": cfg["active_bits"],
        "pixel_format": cfg["pixel_format"], "component_bits": cfg["component_bits"],
        "pixels_per_clock": cfg["pixels_per_clock"], "backpressure": backpressure,
        "inter_frame_gap": inter_frame_gap,
        "tb_top": "xab_axis_video_tb",
    }
    if golden_meta:
        meta["golden"] = golden_meta
    return items, meta
