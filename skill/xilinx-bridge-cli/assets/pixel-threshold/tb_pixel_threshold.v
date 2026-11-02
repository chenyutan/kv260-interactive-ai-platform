`timescale 1ns/1ps
module tb_pixel_threshold;
    reg [7:0] gray;
    wire foreground;
    integer sample;
    pixel_threshold dut(.gray(gray), .foreground(foreground));

    initial begin
        for (sample = 0; sample < 256; sample = sample + 1) begin
            gray = sample;
            #1;
            if (foreground !== gray[7]) begin
                $display("WB_FAIL|pixel=%0d observed=%0b", gray, foreground);
                $finish;
            end
        end
        $display("WB_PASS|256 gray levels checked");
        $finish;
    end
endmodule
