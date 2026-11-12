# The operating-room scene

The 3D room behind the homepage is a Blender file. The page loads an export of it.

| File | What it is |
|---|---|
| `scene/or.blend` | The scene. Open it in Blender and edit it. |
| `assets/or.glb` | The export the page loads. Regenerate it after every edit. |
| `assets/theatre.js` | Lights the room, moves the robot arm, draws the live monitor, orbits the camera on scroll. |
| `scene/export_glb.py` | Exports `or.blend` to `or.glb`. |
| `scene/build_or.py` | The script that generated the first `or.blend`. Running it again **overwrites your edits**. |

## Edit, export, preview

Needs Blender 4.2 or newer (built with 5.3). On Windows the program is at
`C:\Program Files\Blender Foundation\Blender 5.3\blender.exe`; use that path where the commands say `blender`.

1. Open `scene/or.blend`, change what you like, save.
2. Export, from the repository root:

   ```
   blender -b scene/or.blend --python scene/export_glb.py
   ```

3. Preview. The page must be served, not opened as a file:

   ```
   python -m http.server 8000
   ```

   Then open http://localhost:8000.
4. Commit `scene/or.blend` and `assets/or.glb`, and push.

To render a still inside Blender, press F12: the file has a camera at the page's opening
view and its own lights. Those are for Blender only and are not exported.

## Names the page depends on

The page finds these objects by name. Keep the names and the parenting, or the export stops with an error.

| Object | Role |
|---|---|
| `arm_yaw` > `arm_upper` > `arm_fore` > `arm_wrist` | Robot arm joints. Links run along local +X and hinge about local Y. The page poses them every frame. |
| `arm_tool_tip` | End of the tool; its distance along the wrist's X sets the tool length. |
| `lamp_A`, `lamp_B` | Surgical light heads. The beam leaves along local -Z. |
| `lamp_A_aim`, `lamp_B_aim` | Where each light points. |
| `monitor_screen`, `wall_screen` | Flat screens the page draws on. Their UVs must fill 0 to 1. |
| `knee_target` | Centre of the operative field: the arm, the target box and its label follow it. |

The material named `lamp_led` is dimmed and brightened by the page during the lights-on sequence.
Everything else (meshes, materials, extra equipment) can be changed freely.

Keep `or.glb` small: it is 1.6 MB now, and every visitor downloads it.
