module pixel_threshold(input wire [7:0] gray, output wire foreground);
    assign foreground = (gray >= 8'd128);
endmodule
