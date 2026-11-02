# Main-menu branding

- `fpga-competition-10th.png`: user-supplied competition anniversary mark,
  copied without pixel changes from the attachment on 2026-10-22. Original
  dimensions are 283 x 80; visible alpha bounds are (0, 0, 229, 80). The native
  UI fits this visible area without distorting the artwork.
- `amd-logo-white.svg`: original AMD white wordmark and arrow, retrieved from
  the [official AMD ROCm documentation](https://rocm.docs.amd.com/en/latest/).
  Exact asset: https://rocm.docs.amd.com/en/latest/_static/images/amd-header-logo.svg
  Retrieved 2026-10-22. No changes to SVG paths, proportions or colors.
- `amd-logo-white.png`: deployment raster of that SVG, rendered through the
  standard GdkPixbuf SVG loader at 560 x 132. This avoids a new SVG dependency
  on the board. The original vector is retained for provenance.

Display order is competition on the left, AMD on the right. AMD's white mark
uses a dark neutral background for contrast. These are static brand assets,
not menu controls. AMD and the AMD Arrow logo are trademarks of Advanced
Micro Devices, Inc. No endorsement or certification claim is added.

## DeepSeek assistant

- `deepseek-official.svg`: original official artwork from
  https://github.com/deepseek-ai/DeepSeek-LLM/blob/main/images/logo.svg
  retrieved 2026-10-24.
- `deepseek-whale.svg`: whale-only viewport of the original artwork; paths
  and official blue color are unchanged. Used by the local phone portal.
- `deepseek-whale.png`: transparent 256 x 187 raster rendered from that
  viewport through GdkPixbuf, avoiding a new board SVG runtime dependency.

The whale identifies the DeepSeek API used by this project's assistant;
no sponsorship or endorsement claim is made.
