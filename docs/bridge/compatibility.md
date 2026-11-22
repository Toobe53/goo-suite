# GOO Bridge compatibility / 兼容性
GOO Bridge reports 39 bridge-specific items derived from the 49-row goo-port matrix.
| Level | Count | Meaning |
|---|---:|---|
| L2 | 13 | Fully bridged: the bridge ships the asset/CLI path and detects it, or stock Blender 5.2 already provides the behaviour. |
| L1 | 15 | Partial: an asset or operator reproduces the intent but the path differs from GOO, or one manual step remains. |
| L0 | 11 | Unreachable: lives in GOO's C++ renderer or Blender's core/UI; the bridge reports it as unavailable. |

## Items
| ID | Area | Feature | Level | GOO status | Bridge strategy |
|---|---|---|---|---|---|
| A1 | pipeline | Node-group asset library and Add menu | L2 | Ported | Bundle the goo-port node groups and expose them from the bridge; analyze reports which GOO groups are present so the report can confirm adoption. |
| A2 | pipeline | View-layer overrides and collection force-enable | L2 | Ported (UI) | Bridge operators write the native view-layer and collection properties; the Shift-click gesture itself stays a Blender shortcut and is not simulated. |
| A3 | procedural | Hex Grid texture | L2 | Approx | Ship the GOO Hex Grid node group; analyze detects it by name and the report marks it green. |
| A4 | procedural | Twirl texture | L2 | Ported (+Radius/Angle) | Ship the GOO Twirl node group and let the report verify its presence. |
| A5 | grease-pencil | Grease Pencil auto-close strokes | L2 | Ported (explicit) | Bridge operator writes the native cyclic flag; the report cannot inspect GP attributes, so the item is marked manual at scan time. |
| A6 | viewport | Viewport line width 1.0-3.0 | L2 | Ported | Bridge preference plus viewport draw handler; final renders are unaffected, as in GOO. |
| A7 | pipeline | Scene camera override control | L2 | Approx | Bridge panel and CLI --camera write scene.camera; analyze records active_camera and the report marks this item green when a camera is set. |
| A8 | pipeline | Film transparency toggle | L2 | Ported | Bridge checkbox writes film_transparent; the rendered alpha is the native Blender result. |
| A9 | pipeline | Topology Mirror toggle | L2 | Ported | Bridge operator writes use_mirror_topology; like GOO it only affects armature/mirror editing, not rendering. |
| A10 | pipeline | Render burn-in (frame + file name) | L2 | Ported (CLI) | Bridge render command enables use_stamp, stamp_note/stamp_frame and scales the stamp for PNG output; EXR and light-group composites keep the documented limitation. |
| A11 | shading | Light Groups workflow and compositing | L2 | Approx (+CLI) | Bridge metadata plus multi-pass render and linear composite reproduce the image (mean diff near 1e-6); analyze flags compositor/light-group nodes and the report marks it green. |
| A12 | platform | Cross-platform pure-Python support | L2 | Ported (Windows verified) | No action needed: the bridge reports this capability as always present and documents Windows 11 as the verified platform. |
| A13 | shading | GOO Shader Info and Check Self Shadowing flag | L1 | Approx | Ship the GOO Shader Info asset with its Check Self Shadowing input; analyze detects the group and the report marks it yellow with the AO-proxy caveat. |
| A14 | shading | Set Depth shader | L1 | Approx | Ship the GOO Set Depth asset and consume its Depth socket via the bridge's depth pass; the report names the missing real depth-buffer write. |
| A15 | shading | Light Info (legacy and New) drivers | L1 | Approx | Ship both Light Info assets and the light-info operators that write energy/color/specular/location/rotation drivers; analyze detects the groups and the report marks them yellow. |
| A16 | screen-space | Screen Space Info | L1 | Approx | Ship the GOO Screen Space Info asset and pair it with the bridge's depth pass; analyze detects the group and the report marks it yellow. |
| A17 | screen-space | Curvature | L1 | Approx | Ship the GOO Curvature asset; analyze detects it and the report marks it yellow with the AO/Fresnel note. |
| A18 | procedural | SDF shape primitives | L1 | Approx | Ship the GOO SDF Primitive asset; analyze detects it by group name and the report marks it yellow with the reduced shape list. |
| A19 | procedural | SDF operations, vector ops and noise | L1 | Approx | Ship GOO SDF Op, SDF Vector Op and SDF Noise assets; analyze flags any SDF group and the report marks the cluster yellow. |
| A20 | procedural | Water Ripples | L1 | Approx | Ship the GOO Water Ripples asset; analyze detects it and the report marks it yellow, naming the missing modes. |
| A21 | procedural | OKLab Color Ramp | L1 | Approx | Ship the GOO OKLab Color Ramp asset; analyze flags any OKLab group and the report marks it yellow with the stop-count limit. |
| A22 | procedural | Scene Time (Start/End Frame) | L1 | Approx | Ship the GOO Scene Time group plus its handlers; analyze reports the scene_time flag and the report marks it yellow. |
| A23 | geometry | Active Camera geometry-node override | L1 | Approx | Ship the GOO Active Camera asset and offer --camera in the bridge CLI; analyze reports active_camera_override and the report marks it yellow. |
| A24 | grease-pencil | Grease Pencil texture randomize and sculpt smoothing | L1 | Approx | Bridge operators provide both tools; the report cannot inspect GP attributes, so the item is marked manual at scan time. |
| A25 | rigging | Pose authoring utilities | L1 | Approx | Bridge pose operators cover the three workflows; the report marks them manual because pose state is not part of the analyze JSON. |
| N1 | renderer | Shadow-map internals (shadow ID buffer, contact shadows, bias/CSM/light cache) | L0 | N/A | The report marks them red and points at the native-extension C++ route; the bridge can still bake shadows in an external pass if a project needs them. |
| N2 | renderer | Alpha-blended holdout for transparent materials | L0 | N/A | Report as unavailable; use a cryptomatte/object-index mask in an external composite when needed. |
| N3 | renderer | EEVEE view-layer sample override | L0 | N/A for EEVEE | The bridge exposes scene-level --samples and reports the per-layer override as unavailable. |
| N4 | renderer | Radiance buffer copy between refraction passes | L0 | N/A | Report as unavailable; approximate with screen-space refraction compositing externally if required. |
| N5 | renderer | Principled/SSS shader internals | L0 | N/A | Report as unavailable; rely on stock Principled BSDF terms and document the difference. |
| N6 | renderer | Screen-space shadows and refraction internals | L0 | N/A | Report as unavailable; the bridge depth/mist passes only approximate the result. |
| N7 | pipeline | Float socket inputs accept inf / -inf | L0 | N/A | Report as unavailable; builders should clamp to large finite values instead of infinity. |
| N8 | viewport | Continuous Trackball navigation | L0 | N/A | Report as unavailable; users keep Blender's native navigation. |
| N9 | pipeline | Library Override Resync Enforce ignores Action ID pointers | L0 | N/A | Report as unavailable and document manual resync handling for linked rigs. |
| N10 | interface | Outliner hidden-bone row filter | L1 | Approx | Store and expose the flag, and mark the row filter as manual/unsupported detail in the report. |
| N11 | interface | Disable Search on Key Press | L0 | N/A | Report as unavailable; note the native shortcut workaround in the documentation. |
| N12 | grease-pencil | Grease Pencil 3.0 | L2 | Native | Mark as native in the report; no asset or operator is required. |
| N13 | interface | Material icon rendering and VSE relative-path strips | L0 | N/A | Report as unavailable; keep native icon/path behaviour and document the difference. |
| N14 | interface | Scene camera drop-down in the top bar | L1 | Approx | Provide the panel/CLI control and mark the top-bar placement as a documented UI deviation. |

Generate a report for your scene:

```bash
goo-bridge analyze scene.blend -o analysis.json
goo-bridge compat --analysis analysis.json -o compat.html
```
The HTML report uses green/yellow/red and is self-contained. L0 items are explicitly unsupported, not silently approximated.
