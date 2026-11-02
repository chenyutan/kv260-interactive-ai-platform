# Character source and reproducibility

The default native character combines Quaternius **Casual_Hoodie** (CC0) with locally authored lantern/ribbon accessories and existing procedural game motion. It is an adapted third-party character, not an entirely original model. No Subway Surfers or Nintendo model, texture or animation is included.

`quaternius-hoodie/` retains the unmodified source, original license and retrieval/provenance receipt. `tools/` contains the offline converter and minimal glTF reader. Python with NumPy is required only for regeneration, not on the board.

From the suite root:

```sh
python -B runner/character_source/tools/build_imported_courier.py --source runner/character_source/quaternius-hoodie/Casual_Hoodie.gltf --output runner/native/runner_imported_data.h --report runner/character_source/build-report.json
```

The data header contains 6,206 body triangles and 3,273 unique weighted vertices. Runtime lantern/ribbon accessories and shoulder straps add 344 triangles, for 6,550 total. The 15-node retarget retains at most four influences per vertex. Relaxed closed fingers are baked offline using the source finger joints and collapsed to the forearm; no runtime hand joints were added. Original animation tracks are preserved in the source for editing but are not sampled at runtime.

The original procedural character remains available as `charactermesh::append_character`; the global `append_character` entry uses the imported candidate. Switching that one call back restores the procedural appearance. Hardware screenshots and performance measurements are separate from local authoring checks.


Foot orientation correction candidate (2026-10-19): the source default display pose has asymmetric foot yaw, which the former diagonal shoe mapping retained while reorienting the upper/lower legs. The offline converter now removes each Foot bone's horizontal local-Y yaw before applying existing shoe scale. Only bind/normal-bind rows 9 and 14 change. No source vertex, weight, triangle, motion, solver or runtime API changes. See the independent runner-foot-study validation and CPU comparison before considering integration.
