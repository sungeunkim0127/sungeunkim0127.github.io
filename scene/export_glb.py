"""Export the operating-room scene to assets/or.glb for the homepage.

Run after editing scene/or.blend by hand:

    blender -b scene/or.blend --python scene/export_glb.py

Lights and cameras stay in the .blend (the page lights the room itself).
The page finds these objects by name, so keep the names when you edit:
arm_yaw, arm_upper, arm_fore, arm_wrist, arm_tool_tip, lamp_A, lamp_A_aim,
lamp_B, lamp_B_aim, monitor_screen, wall_screen, knee_target (see README.md).
"""

import os

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "assets", "or.glb")

REQUIRED = (
    "arm_yaw", "arm_upper", "arm_fore", "arm_wrist", "arm_tool_tip",
    "lamp_A", "lamp_A_aim", "lamp_B", "lamp_B_aim",
    "monitor_screen", "wall_screen", "knee_target",
)


def export(path=OUT):
    missing = [n for n in REQUIRED if n not in bpy.data.objects]
    if missing:
        raise SystemExit(f"Not exported: the page needs these objects, and they are missing: {', '.join(missing)}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=path,
        export_format="GLB",
        export_apply=True,          # bake bevels and other modifiers into the mesh
        export_yup=True,
        export_cameras=False,
        export_lights=False,
        export_animations=False,
        export_extras=False,
    )
    print(f"Exported {path} ({os.path.getsize(path) / 1e6:.2f} MB)")


if __name__ == "__main__":
    export()
