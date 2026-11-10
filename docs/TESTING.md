# Verification and capability report

Test date: 2026-09-15.

## Environment

- Apple silicon macOS; installed Cascadeur.app reports bundle version `inHouse`, Python API version `dev`.
- Embedded Python: 3.11.0, built 2026-09-02. External server tested with Python 3.12.14 and official MCP SDK 2.2.0.
- App license query: export available; Pro features unavailable.
- Independent Qt/main-thread mailbox bridge. Cascadeur's built-in MCP was not used.
- MCP protocol tested through the official Python SDK, including real stdio initialization, tool discovery, tool calls, resources, error responses and image content.

## Live animation test

Source: the user's locally installed `samples/Cascy.casc`, copied into a dedicated test workspace. The vendor sample is not distributed here.

The scene contained 289 objects, including 66 joints, 51 Box controls, 43 Point controls, 21 rigid bodies, and 13 animation tracks. The test authored frames 0, 12, 24, 36 and 48, driving a torso turn, right-arm raise and all 15 right-hand finger Box controls. It then returned to the initial pose.

| Check | Result |
| --- | --- |
| Body control rotation at frame 24 | Approximately 7.959° relative to frame 0 |
| Right index proximal control rotation | Approximately 30.000° relative to frame 0 |
| Intermediate poses at frames 6, 18, 30 and 42 | Read successfully; finite positions |
| Finger endpoint returns to start | Passed, under 0.15 scene-unit tolerance |
| Right foot maximum displacement per two-frame sample | Approximately 0.00548 scene units |
| Left foot maximum displacement per two-frame sample | Approximately 0.00554 scene units |
| Full-body captures | Five PNGs returned as MCP image content |
| Finger captures | Open and curled hand closeups returned as MCP images |
| Native save | `.casc` saved through `view.save` |
| Reopen persistence | Frame-24 positions match before/after reload within 0.15 scene units |
| FBX animation export | Produced a nonempty FBX file through the app API |

Visual inspection confirmed body movement and finger curling on the mesh. This is a functional animation test, not a production-quality performance assessment. Small foot movement was measured; it was not treated as mathematically perfect contact. Export into a separate DCC/game engine was not tested.

Run [examples/live_smoke.py](../examples/live_smoke.py) to reproduce these checks. It produces a detailed JSON report and scene/previews in your selected workspace.

## Bugs found during integration

### Keyframes need animation-data allocation

Adding key sections alone left the underlying animation data at frame 0. A subsequent write at frame 24 failed. Calling `model_editor.fit_animation_size_by_layers()` after creating/normalizing keys and before writing values resolved this. The method existed in the running app but was absent from the installed stub file. The server follows this sequence.

### Saving must be verified by reopening

The tested `DataSourceManager.save_scene_as` path did not save the animated data when asked to save to the same path. Reopening exposed the unchanged original pose. The server now uses `view.save(path)`; animation persistence was verified after reload.

### Native Python video export crashed the app

Calling `csc.tools.RenderToFile.play_to_video_file` on the tested build caused a main-thread `EXC_BAD_ACCESS` / `SIGSEGV`. The top crash frames were `view::PlayToFileDialog::viewModel()` and `view::RenderToFileTool::playToVideoFile(...)`.

The direct video-export tool was removed. `take_image` works and is the supported preview path. The native `play_to_images_sequence` API was not tested and is not presented as a verified substitute. Do not retry the crashing video call from an advanced script. Capture PNG frames and encode outside Cascadeur instead.

### Physics settings live only in the UI; pumping events crashed the app

AutoPhysics settings such as secondary motion and its alpha are not exposed by the Python API (`SettingsManager`/`SettingsHandler` are read-only key lookups; the AutoPhysics editor only exposes `turn_off`). Each Physics Settings control binds a `view::ViewSettingsUnit` (`unitData`) with a `value` property and `setValue`. On Cascadeur 2026.2.3 (macOS), calling `setValue` on the visible panel's unit switched `Secondary motion` on and set `Secondary alpha` from 90 to 30; the panel buttons updated and the other scene tab's copy stayed unchanged, confirming per-scene settings. Out-of-range, wrong-type and unknown-name changes were rejected before any write.

During exploration, a script that called `QCoreApplication.processEvents()` inside the bridge crashed Cascadeur (`EXC_BAD_ACCESS` in `PyImport_ImportModule`, re-entered from a `presenter::PythonEvents` timer). The physics-settings code never pumps events.

A first implementation walked the QML tree with PySide item wrappers. It worked in a fresh session but failed after the panel was closed, reopened or undocked: PySide kept stale wrappers (`Internal C++ object already deleted`) that blocked the rebuilt items, even after explicit invalidation. The tools now run the walk as JavaScript in Cascadeur's QML engine and exchange JSON; reads and writes kept working across two close/reopen cycles. Values persist in the saved scene.

Effect on a solve: on the heavy run, `Secondary motion` on at alpha 30 changed the snapped result by at most 0.18 units on the hands and 0.44 on the toes compared with the same run without it, with no measurable change in acceleration, overlap timing or contacts. Higher alphas were not measured.

### Inbetween frames are computed lazily

Keying a 150-frame cycle in batches of 13 keyframes left 19 inbetween frames (107-148) holding poses from earlier keys, while every key frame was correct. Waiting 30 s did not fix them; neither did `SceneUpdater.generate_update()`, `get_interpolator().reload()/interpolate()`, or `run_update` on the point positions (`run_update` on all data raised a dependency-cycle error). A timeline visit that rested about 1 s recomputed roughly +/-4 frames around it; visits of 0.1-0.3 s did nothing. `sample_motion` therefore visits every sixth frame for 1 s before sampling: on the stale cycle it returned 0 stale frames in 28 s, versus 19 without refresh.

## Discovered, not proven by this test

The live capability query found tools including AutoPhysics, AutoPosing, Mirror, BallisticTrajectory, Attractor, Rigging, Retargeting, AnimationUnbaking, Inbetweening, Mocap, AnimationLayers, FBX/GLB/USD loaders and RenderToFile.

Presence is not proof of a usable API or successful execution. In particular:

- AutoPhysics editor exposed `turn_off` and `turn_off_all_fulcrum_points`; wider operations are menu actions.
- AnimationLayers and Timeline editors exposed no public methods in the tested runtime.
- AutoPosing editor exposed `activate`, `deactivate`, `add`, and `update`.
- Rigging editor exposed template/generation methods; complete rig creation was not live-tested.
- Retargeting, mocap, unbaking, physics corrections, AI generation, additive layers, cycle operations and all menu-action outcomes remain unverified.
- FPS setting was not implemented; confirm it in Cascadeur's Timeline UI.

## Automated tests

17 tests passed locally. They cover real stdio MCP handshakes, default/opt-in tool exposure, guide resources, invalid schemas, expired or wrong-session requests, cross-client serialization, cancellation cleanup, workspace/path checks, symlink escapes, overwrite handling, stale output rejection, finite transforms, and generated client configuration consistency.

Run `python -m pytest -q` from the repository after installing `.[dev]`. CI is configured for Python 3.11, 3.12 and 3.13 on Linux; the live Cascadeur app test is manual.

Windows/Linux app integration and the Claude/Cursor UIs are unverified. The bridge depends on current Cascadeur APIs and its bundled PySide6. There is no claim that every historical Cascadeur release works.
