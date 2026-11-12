"""Build the operating-room scene from scratch.

    blender -b --factory-startup --python scene/build_or.py
    blender -b --factory-startup --python scene/build_or.py -- --render preview.png

Writes scene/or.blend and assets/or.glb. This is the seed: once you start
editing or.blend by hand, stop running this (it overwrites the file) and use
export_glb.py instead.

Positions below are written in the page's coordinates (x right, y up, z toward
the viewer, metres) and converted to Blender's z-up space by B().
"""

import math
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_glb  # noqa: E402

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
RENDER_TO = ARGS[ARGS.index("--render") + 1] if "--render" in ARGS else None

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


def B(x, y, z):
    """Page coordinates -> Blender coordinates."""
    return Vector((x, -z, y))


# ---------------------------------------------------------------- materials

def linear(hex_color):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return (ch(hex_color >> 16 & 255), ch(hex_color >> 8 & 255), ch(hex_color & 255), 1.0)


def material(name, color, rough=0.5, metal=0.0, emit=None, strength=1.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = linear(color)
    p.inputs["Roughness"].default_value = rough
    p.inputs["Metallic"].default_value = metal
    if emit is not None:
        p.inputs["Emission Color"].default_value = linear(emit)
        p.inputs["Emission Strength"].default_value = strength
    return m


class M:
    floor = material("floor", 0x16363D, 0.3)
    wall = material("wall", 0x15363D, 0.9)
    trim = material("wall_trim", 0x1F4D55, 0.55)
    steel = material("steel", 0xB9C6C9, 0.26, 1.0)
    steel_dark = material("steel_dark", 0x5F6F73, 0.38, 1.0)
    shell = material("shell", 0xE9EFEF, 0.34)
    shell_grey = material("shell_grey", 0x9EADB0, 0.5)
    kit = material("kit_dark", 0x1B3038, 0.45)
    rubber = material("rubber", 0x0A1012, 0.85)
    pad = material("pad", 0x0E1D21, 0.55)
    drape = material("drape", 0x1E8A86, 0.86)
    drape_light = material("drape_light", 0x55B5AE, 0.8)
    skin = material("mannequin", 0xC9BBAA, 0.6)
    field = material("operative_field", 0xD9A476, 0.5)
    hose = material("hose", 0xBAC9CD, 0.5)
    glass = material("glass_dark", 0x081216, 0.08)
    diffuser = material("lamp_diffuser", 0x9FB3B6, 0.2)
    vapor_a = material("vaporizer_yellow", 0xD8A93A, 0.4)
    vapor_b = material("vaporizer_blue", 0x3A6FB8, 0.4)
    # emissive: the page drives lamp_led during power-up and replaces `screen` with live canvases
    led = material("lamp_led", 0x101010, 0.3, emit=0xFFFAF2, strength=8.0)
    canopy = material("canopy_light", 0x101010, 0.5, emit=0xCFEEEE, strength=2.0)
    screen = material("screen", 0x020607, 0.2, emit=0x1E8A86, strength=0.6)
    idle = material("screen_idle", 0x020607, 0.2, emit=0x62C9F2, strength=1.6)
    accent = material("accent_led", 0x051012, 0.4, emit=0x58D6CC, strength=5.0)
    amber = material("amber_led", 0x120A04, 0.4, emit=0xFFB35C, strength=5.0)


# ----------------------------------------------------------------- builders

_names = {}


def unique(base):
    n = _names.get(base, 0)
    _names[base] = n + 1
    return base if n == 0 else f"{base}_{n}"


def to_mesh(name, bm, sharp_angle=35):
    """Shade smooth, keeping any edge sharper than sharp_angle crisp."""
    limit = math.radians(sharp_angle)
    for e in bm.edges:
        if len(e.link_faces) == 2 and e.calc_face_angle(0.0) > limit:
            e.smooth = False
    for f in bm.faces:
        f.smooth = True
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


def place(name, me, mat=None, loc=(0, 0, 0), rot=(0, 0, 0), parent=None, bevel=0.0, segs=3):
    o = bpy.data.objects.new(unique(name), me)
    scene.collection.objects.link(o)
    o.location = loc
    o.rotation_euler = rot
    if parent is not None:
        o.parent = parent
    if mat is not None:
        me.materials.append(mat)
    if bevel:
        b = o.modifiers.new("Bevel", "BEVEL")
        b.width, b.segments, b.limit_method, b.angle_limit = bevel, segs, "ANGLE", math.radians(35)
        w = o.modifiers.new("WeightedNormal", "WEIGHTED_NORMAL")
        w.keep_sharp = True
    return o


def empty(name, loc=(0, 0, 0), parent=None, size=0.1):
    o = bpy.data.objects.new(name, None)
    scene.collection.objects.link(o)
    o.empty_display_size = size
    o.location = loc
    if parent is not None:
        o.parent = parent
    return o


def bm_box(dx, dy, dz):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(dx, dy, dz), verts=bm.verts)
    return bm


def bm_cyl(r_start, r_end, length, axis="Z", segs=32):
    """Cylinder or cone centred on the origin; r_start is the -axis end."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segs, radius1=r_start, radius2=r_end, depth=length)
    if axis == "X":
        bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(90), 3, "Y"), verts=bm.verts)
    elif axis == "Y":
        bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(-90), 3, "X"), verts=bm.verts)
    return bm


def bm_ball(r, sx=1.0, sy=1.0, sz=1.0, u=32, v=16):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=u, v_segments=v, radius=r)
    bmesh.ops.scale(bm, vec=(sx, sy, sz), verts=bm.verts)
    return bm


def bm_limb(a, b, r_a, r_b):
    """Tapered capsule between two Blender-space points."""
    d = b - a
    bm = bm_cyl(r_a, r_b, d.length, segs=20)
    bmesh.ops.create_uvsphere(bm, u_segments=20, v_segments=10, radius=r_a, matrix=Matrix.Translation((0, 0, -d.length / 2)))
    bmesh.ops.create_uvsphere(bm, u_segments=20, v_segments=10, radius=r_b, matrix=Matrix.Translation((0, 0, d.length / 2)))
    xf = Matrix.Translation((a + b) / 2) @ d.to_track_quat("Z", "Y").to_matrix().to_4x4()
    bmesh.ops.transform(bm, matrix=xf, verts=bm.verts)
    return bm


# Page-coordinate shorthands for free-standing parts: (width, height, depth) at (x, y, z).
def Box(name, w, h, d, x, y, z, mat, bevel=0.01, yaw=0.0):
    return place(name, to_mesh(name, bm_box(w, d, h)), mat, B(x, y, z), (0, 0, yaw), bevel=bevel)


def Cyl(name, r_bottom, r_top, h, x, y, z, mat, bevel=0.006):
    return place(name, to_mesh(name, bm_cyl(r_bottom, r_top, h)), mat, B(x, y, z), bevel=bevel)


def Ball(name, r, x, y, z, mat, sx=1.0, sy=1.0, sz=1.0):
    """sx along the table, sy up, sz across."""
    return place(name, to_mesh(name, bm_ball(r, sx, sz, sy)), mat, B(x, y, z))


def Tube(name, points, radius, mat, parent=None):
    """Smooth tube through Blender-space points (a bevelled Bezier curve, converted to a mesh)."""
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions, cu.bevel_depth, cu.bevel_resolution, cu.resolution_u, cu.use_fill_caps = "3D", radius, 4, 10, True
    sp = cu.splines.new("BEZIER")
    sp.bezier_points.add(len(points) - 1)
    for bp, p in zip(sp.bezier_points, points):
        bp.co = p
        bp.handle_left_type = bp.handle_right_type = "AUTO"
    tmp = bpy.data.objects.new("tmp_curve", cu)
    scene.collection.objects.link(tmp)
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg))
    bpy.data.objects.remove(tmp)
    bpy.data.curves.remove(cu)
    me.name = name
    for poly in me.polygons:
        poly.use_smooth = True
    return place(name, me, mat, parent=parent)


def Screen(name, w, h, mat, loc, parent=None, rot=(0, 0, 0)):
    """Flat display facing the object's local -Y, with UVs filling 0..1 (upright)."""
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    corners = [(-w / 2, 0, -h / 2, 0, 0), (w / 2, 0, -h / 2, 1, 0), (w / 2, 0, h / 2, 1, 1), (-w / 2, 0, h / 2, 0, 1)]
    verts = [bm.verts.new((x, y, z)) for x, y, z, _, _ in corners]
    face = bm.faces.new(verts)
    for loop, (_, _, _, u, v) in zip(face.loops, corners):
        loop[uv].uv = (u, v)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return place(name, me, mat, loc, rot, parent)


# --------------------------------------------------------------------- room

Box("floor", 14, 0.1, 12, 0, -0.05, -0.5, M.floor, bevel=0)
Box("ceiling", 14, 0.1, 12, 0, 3.6, -0.5, M.wall, bevel=0)
Box("wall_back", 14, 3.6, 0.12, 0, 1.8, -4.31, M.wall, bevel=0)
Box("wall_left", 0.12, 3.6, 12, -6.66, 1.8, -0.5, M.wall, bevel=0)
Box("wall_right", 0.12, 3.6, 12, 6.66, 1.8, -0.5, M.wall, bevel=0)
Box("wall_rail", 13.2, 0.14, 0.035, 0, 1.0, -4.235, M.trim)
Box("wall_skirting", 13.2, 0.12, 0.02, 0, 0.06, -4.245, M.trim, bevel=0.006)
for i in range(9):
    Box("wall_seam", 0.014, 3.5, 0.008, -6 + i * 1.5, 1.75, -4.247, M.trim, bevel=0)

# double door, left of centre
Box("door_frame", 1.9, 2.25, 0.05, -3.9, 1.125, -4.235, M.steel_dark)
for dx in (-0.46, 0.46):
    Box("door_leaf", 0.88, 2.12, 0.05, -3.9 + dx, 1.08, -4.215, M.trim)
    Box("door_window", 0.3, 0.55, 0.012, -3.9 + dx, 1.55, -4.187, M.glass, bevel=0.004)
    Box("door_plate", 0.16, 0.9, 0.012, -3.9 + dx * 0.36, 1.05, -4.187, M.steel, bevel=0.003)

# glass-fronted supply cabinets, right
for i in range(3):
    cx = 3.5 + i * 0.86
    Box("cabinet", 0.84, 2.1, 0.42, cx, 1.05, -4.03, M.shell_grey, bevel=0.012)
    Box("cabinet_glass", 0.7, 1.15, 0.012, cx, 1.38, -3.815, M.glass, bevel=0.004)
    Box("cabinet_handle", 0.02, 0.3, 0.02, cx + 0.3, 0.55, -3.805, M.steel, bevel=0.004)

# wall display (the page draws a knee-alignment plan on wall_screen)
Box("wall_display", 2.24, 1.34, 0.07, 0.3, 1.85, -4.2, M.kit, bevel=0.015)
Screen("wall_screen", 2.1, 1.2, M.screen, B(0.3, 1.85, -4.162))

# laminar-flow canopy over the table
Box("canopy_frame", 3.9, 0.08, 3.0, 0.3, 3.5, 0.0, M.steel_dark, bevel=0.01)
for ix in range(5):
    for iz in range(4):
        Box("canopy_panel", 0.7, 0.02, 0.66, 0.3 - 1.48 + ix * 0.74, 3.455, -1.05 + iz * 0.7, M.canopy, bevel=0.004)

# --------------------------------------------------------- operating table

TX = 0.1
Box("table_base", 0.95, 0.09, 0.56, TX, 0.045, 0, M.kit, bevel=0.03)
Box("table_column", 0.36, 0.42, 0.34, TX, 0.3, 0, M.shell, bevel=0.03)
Box("table_column", 0.27, 0.42, 0.26, TX, 0.66, 0, M.steel, bevel=0.012)
Box("table_frame", 2.06, 0.05, 0.5, TX, 0.875, 0, M.steel_dark, bevel=0.008)
for centre, length in ((-0.78, 0.32), (-0.3, 0.6), (0.27, 0.5), (0.83, 0.58)):
    place("table_pad", to_mesh("table_pad", bm_box(length, 0.54, 0.07)), M.pad, B(centre, 0.935, 0), bevel=0.022, segs=4)
for z in (0.268, -0.268):
    Box("table_rail", 1.5, 0.03, 0.012, TX, 0.875, z, M.steel, bevel=0.004)
    for x in (-0.5, 0.1, 0.7):
        Box("table_rail_post", 0.03, 0.03, 0.03, x, 0.875, z * 0.95, M.steel_dark, bevel=0.004)

# ------------------------------------------------------ patient and drape
# The body is a mannequin: only the head, neck and upper chest stay visible. The rest
# exists so the cloth has something to fall on, and is deleted once the drape has settled.

colliders = []


def collider(bm):
    o = place("collider", to_mesh("collider", bm))
    colliders.append(o)
    return o


chest = place("patient_chest", to_mesh("patient_chest", bm_ball(1.0, 0.3, 0.2, 0.115, 40, 20)), M.skin, B(-0.33, 1.085, 0))
collider(bm_ball(1.0, 0.18, 0.19, 0.105)).location = B(0.02, 1.075, 0)                        # pelvis
for side in (1, -1):
    collider(bm_limb(B(0.08, 1.06, 0.1 * side), B(0.5, 1.045, 0.105 * side), 0.088, 0.062))   # thigh
    collider(bm_limb(B(0.5, 1.045, 0.105 * side), B(0.93, 1.02, 0.1 * side), 0.058, 0.042))   # shin
    collider(bm_ball(1.0, 0.05, 0.045, 0.125)).location = B(0.99, 1.1, 0.1 * side)            # foot
    collider(bm_limb(B(-0.52, 1.06, 0.25 * side), B(0.02, 1.02, 0.27 * side), 0.05, 0.04))    # arm
collider(bm_box(2.1, 0.58, 0.16)).location = B(TX, 0.892, 0)                                  # table top
colliders.append(chest)

place("patient_neck", to_mesh("patient_neck", bm_limb(B(-0.74, 1.07, 0), B(-0.6, 1.075, 0), 0.048, 0.06)), M.skin)
Ball("patient_head", 0.1, -0.8, 1.075, 0, M.skin, 1.08, 1.0, 0.88)
Ball("patient_cap", 0.104, -0.832, 1.087, 0, M.drape_light, 1.0, 0.98, 0.9)
Ball("patient_mask", 0.042, -0.775, 1.168, 0, M.shell, 1.15, 0.7, 1.0)
place("head_rest", to_mesh("head_rest", bm_box(0.26, 0.3, 0.05)), M.drape_light, B(-0.8, 0.995, 0), bevel=0.02, segs=4)

# cloth: a sheet dropped over the body and the table, simulated, then frozen
CELL = 0.022
bm = bmesh.new()
bmesh.ops.create_grid(bm, x_segments=round(2.1 / CELL), y_segments=round(1.68 / CELL), size=1.0)
bmesh.ops.scale(bm, vec=(1.05, 0.84, 1.0), verts=bm.verts)
for f in bm.faces:
    f.smooth = True
me = bpy.data.meshes.new("drape")
bm.to_mesh(me)
bm.free()
drape = place("drape", me, M.drape, B(0.5, 1.27, 0))

for c in colliders:
    c.modifiers.new("Collision", "COLLISION")
    c.collision.thickness_outer = 0.012
    c.collision.cloth_friction = 25
cloth = drape.modifiers.new("Cloth", "CLOTH")
cs = cloth.settings
cs.quality, cs.mass, cs.air_damping = 10, 0.25, 2.5
cs.tension_stiffness = cs.compression_stiffness = 12
cs.shear_stiffness, cs.bending_stiffness = 4, 0.25
cloth.collision_settings.use_collision = True
cloth.collision_settings.distance_min = 0.008
cloth.collision_settings.collision_quality = 4
FRAMES = 100
scene.frame_start, scene.frame_end = 1, FRAMES
cloth.point_cache.frame_start, cloth.point_cache.frame_end = 1, FRAMES
for frame in range(1, FRAMES + 1):
    scene.frame_set(frame)
dg = bpy.context.evaluated_depsgraph_get()
settled = bpy.data.meshes.new_from_object(drape.evaluated_get(dg), depsgraph=dg)
settled.transform(drape.matrix_world)          # bake the drop height in, so the object sits at the origin
bm = bmesh.new()                               # relax the simulation's zig-zags, hems included
bm.from_mesh(settled)
for _ in range(4):
    bmesh.ops.smooth_vert(bm, verts=bm.verts, factor=0.5, use_axis_x=True, use_axis_y=True, use_axis_z=True)
bm.to_mesh(settled)
bm.free()
drape.modifiers.clear()
old = drape.data
drape.data = settled
drape.location = (0, 0, 0)
bpy.data.meshes.remove(old)
settled.name = "drape"
scene.frame_set(1)
for c in colliders:
    c.modifiers.clear()
    if c is not chest:
        bpy.data.objects.remove(c)
bpy.context.view_layer.update()


def drape_height(x, z):
    """Top of the drape under page position (x, z); None off the cloth."""
    hit, loc, _, _ = drape.ray_cast(Vector((x, -z, 3.0)), Vector((0, 0, -1)))
    return loc.z if hit else None


KNEE = (0.56, 0.105)
knee_y = drape_height(*KNEE) or 1.13
empty("knee_target", B(KNEE[0], knee_y, KNEE[1]), size=0.08)

# operative field: a prepped patch following the cloth over the knee
bm = bmesh.new()
bmesh.ops.create_grid(bm, x_segments=10, y_segments=8, size=1.0)
bmesh.ops.scale(bm, vec=(0.085, 0.06, 1.0), verts=bm.verts)
for v in bm.verts:
    px, pz = KNEE[0] + v.co.x, KNEE[1] - v.co.y
    v.co = B(px, (drape_height(px, pz) or knee_y) + 0.004, pz)
place("operative_field", to_mesh("operative_field", bm, sharp_angle=80), M.field)

# ---------------------------------------------------------- surgical lights

HUB = (0.1, -0.35)
Cyl("light_ceiling_plate", 0.15, 0.15, 0.05, HUB[0], 3.525, HUB[1], M.steel_dark)
Cyl("light_drop_tube", 0.05, 0.05, 0.5, HUB[0], 3.26, HUB[1], M.shell)
Ball("light_hub", 0.085, HUB[0], 3.02, HUB[1], M.shell)


def surgical_light(tag, pos, aim, r):
    """Light head at pos pointing at aim (page coords). Local -Z is the beam."""
    head = empty(f"lamp_{tag}", B(*pos), size=0.2)
    direction = B(*aim) - B(*pos)
    head.rotation_mode = "QUATERNION"
    head.rotation_quaternion = direction.to_track_quat("-Z", "Y")
    empty(f"lamp_{tag}_aim", B(*aim), size=0.06)

    place("lamp_shell", to_mesh("lamp_shell", bm_cyl(r, r * 0.66, 0.1, segs=48)), M.shell, (0, 0, 0.05), parent=head, bevel=0.014)
    place("lamp_rim", to_mesh("lamp_rim", bm_cyl(r * 1.02, r * 1.02, 0.022, segs=48)), M.kit, (0, 0, 0.004), parent=head, bevel=0.005)
    place("lamp_face", to_mesh("lamp_face", bm_cyl(r * 0.95, r * 0.95, 0.006, segs=48)), M.diffuser, (0, 0, -0.006), parent=head)
    for i in range(6):
        a = i * math.pi / 3
        place("lamp_pod", to_mesh("lamp_pod", bm_cyl(r * 0.2, r * 0.2, 0.006, segs=24)), M.led,
              (math.cos(a) * r * 0.6, math.sin(a) * r * 0.6, -0.011), parent=head)
    place("lamp_pod", to_mesh("lamp_pod", bm_cyl(r * 0.16, r * 0.16, 0.006, segs=24)), M.led, (0, 0, -0.011), parent=head)
    place("lamp_handle", to_mesh("lamp_handle", bm_cyl(0.02, 0.024, 0.1, segs=20)), M.shell, (0, 0, -0.06), parent=head, bevel=0.006)
    place("lamp_joint", to_mesh("lamp_joint", bm_ball(0.055)), M.kit, (0, 0, 0.115), parent=head)

    joint = B(*pos) - direction.normalized() * 0.12
    hub = B(HUB[0], 3.02, HUB[1])
    out = Vector((hub.x * 0.35 + joint.x * 0.65, hub.y * 0.35 + joint.y * 0.65, hub.z - 0.02))
    over = Vector((joint.x, joint.y, (hub.z + joint.z) / 2 + 0.12))
    Tube("lamp_arm", [hub, out, over, joint], 0.03, M.shell)


LAMP_A = ((-0.4, 2.22, 0.42), (0.25, 1.0, 0.03), 0.38)
LAMP_B = ((1.12, 2.3, -0.28), (KNEE[0], 1.1, KNEE[1]), 0.3)
surgical_light("A", *LAMP_A)
surgical_light("B", *LAMP_B)

# ---------------------------------------------------------------- robot arm
# Hierarchy the page animates: arm_yaw > arm_upper > arm_fore > arm_wrist.
# Links run along local +X and hinge about local Y; the wrist's +X is the tool axis.

SHOULDER = (0.8, 1.17, -0.98)
L1, L2, TOOL = 0.74, 0.68, 0.3
sx, sy, sz = SHOULDER
Box("robot_plinth", 0.66, 0.08, 0.66, sx, 0.04, sz, M.kit, bevel=0.02)
Box("robot_cart", 0.62, 0.7, 0.62, sx, 0.43, sz, M.shell, bevel=0.05)
Box("robot_cart_led", 0.626, 0.014, 0.626, sx, 0.66, sz, M.accent, bevel=0)
Box("robot_cart_panel", 0.36, 0.22, 0.012, sx, 0.42, sz + 0.312, M.kit, bevel=0.004)
Screen("robot_cart_screen", 0.3, 0.16, M.idle, B(sx, 0.42, sz + 0.32))
Cyl("robot_mast", 0.12, 0.098, 0.28, sx, 0.92, sz, M.kit, bevel=0.01)

yaw = empty("arm_yaw", B(*SHOULDER))
upper = empty("arm_upper", parent=yaw)
fore = empty("arm_fore", (L1, 0, 0), parent=upper)
wrist = empty("arm_wrist", (L2, 0, 0), parent=fore)


def part(name, bm, mat, loc, parent, bevel=0.0):
    return place(name, to_mesh(name, bm), mat, loc, parent=parent, bevel=bevel)


part("arm_turret", bm_cyl(0.098, 0.09, 0.11), M.shell, (0, 0, -0.065), yaw, 0.012)
part("arm_shoulder", bm_cyl(0.084, 0.084, 0.2, "Y"), M.shell, (0, 0, 0), yaw, 0.014)
for y in (0.102, -0.102):
    part("arm_cap", bm_cyl(0.062, 0.062, 0.012, "Y"), M.kit, (0, y, 0), yaw, 0.003)
part("arm_link_upper", bm_cyl(0.06, 0.05, L1, "X"), M.shell, (L1 / 2, 0, 0), upper)
part("arm_elbow", bm_cyl(0.07, 0.07, 0.17, "Y"), M.shell, (L1, 0, 0), upper, 0.012)
part("arm_elbow_led", bm_cyl(0.0715, 0.0715, 0.014, "Y"), M.accent, (L1, 0, 0), upper)
for y in (0.087, -0.087):
    part("arm_cap", bm_cyl(0.05, 0.05, 0.012, "Y"), M.kit, (L1, y, 0), upper, 0.003)
part("arm_link_fore", bm_cyl(0.05, 0.04, L2, "X"), M.shell, (L2 / 2, 0, 0), fore)
part("arm_wrist_housing", bm_cyl(0.055, 0.055, 0.13, "Y"), M.shell, (L2, 0, 0), fore, 0.01)
part("arm_wrist_roll", bm_cyl(0.04, 0.04, 0.08, "X"), M.kit, (0.06, 0, 0), wrist, 0.006)
part("arm_flange", bm_cyl(0.048, 0.048, 0.014, "X"), M.steel, (0.105, 0, 0), wrist, 0.003)
part("arm_tool", bm_box(0.07, 0.05, 0.05), M.shell, (0.145, 0, 0), wrist, 0.01)
part("arm_tool_shaft", bm_cyl(0.006, 0.005, TOOL - 0.17, "X", 12), M.steel, (0.17 + (TOOL - 0.17) / 2, 0, 0), wrist)
part("arm_tool_tip", bm_ball(0.009, u=12, v=8), M.amber, (TOOL, 0, 0), wrist)


def pose_arm(tip):
    """Two-link inverse kinematics: tool pointing straight down at tip (page coords)."""
    dx, dz = tip[0] - sx, tip[2] - sz
    r, y = math.hypot(dx, dz), tip[1] + TOOL - sy
    d = min(math.hypot(r, y), L1 + L2 - 0.001)
    a1 = math.atan2(y, r) + math.acos((L1 * L1 + d * d - L2 * L2) / (2 * L1 * d))
    a2 = a1 - (math.pi - math.acos((L1 * L1 + L2 * L2 - d * d) / (2 * L1 * L2)))
    yaw.rotation_euler = (0, 0, math.atan2(-dz, dx))
    upper.rotation_euler = (0, -a1, 0)
    fore.rotation_euler = (0, -(a2 - a1), 0)
    wrist.rotation_euler = (0, math.pi / 2 + a2, 0)


pose_arm((KNEE[0] + 0.07, knee_y + 0.2, KNEE[1]))

# ----------------------------------------------------- vitals monitor boom

MON = (-1.42, 1.86, -0.8)
MON_YAW = 0.4
Cyl("monitor_ceiling_plate", 0.1, 0.1, 0.04, MON[0], 3.53, MON[2] - 0.16, M.steel_dark)
Tube("monitor_boom", [B(MON[0], 3.5, MON[2] - 0.16), B(MON[0], 2.5, MON[2] - 0.16), B(MON[0], 2.12, MON[2] - 0.13), B(MON[0], 1.9, MON[2] - 0.05)], 0.028, M.shell)
monitor = Box("monitor_body", 0.88, 0.55, 0.06, *MON, M.kit, bevel=0.016, yaw=MON_YAW)
Screen("monitor_screen", 0.8, 0.46, M.screen, (0, -0.0315, 0), parent=monitor)
Box("monitor_mount", 0.14, 0.14, 0.06, MON[0] - 0.02, MON[1], MON[2] - 0.05, M.steel_dark, bevel=0.01, yaw=MON_YAW)

# ------------------------------------------------------ anaesthesia machine

AX, AZ = -1.9, 0.4
Box("anaes_base", 0.62, 0.1, 0.62, AX, 0.11, AZ, M.kit, bevel=0.02)
for dx in (-0.25, 0.25):
    for dz in (-0.25, 0.25):
        Cyl("caster", 0.04, 0.04, 0.05, AX + dx, 0.03, AZ + dz, M.rubber)
Box("anaes_cabinet", 0.56, 0.58, 0.52, AX, 0.45, AZ, M.shell_grey, bevel=0.02)
for i in range(3):
    Box("anaes_drawer", 0.48, 0.15, 0.012, AX, 0.27 + i * 0.18, AZ + 0.262, M.shell, bevel=0.005)
    Box("anaes_drawer_pull", 0.16, 0.014, 0.014, AX, 0.3 + i * 0.18, AZ + 0.272, M.steel, bevel=0.003)
Box("anaes_worktop", 0.64, 0.03, 0.62, AX, 0.755, AZ, M.steel, bevel=0.008)
Box("anaes_tower", 0.56, 0.56, 0.3, AX, 1.05, AZ - 0.14, M.shell_grey, bevel=0.02)
Box("anaes_bezel", 0.36, 0.26, 0.012, AX - 0.06, 1.14, AZ + 0.014, M.kit, bevel=0.004)
Screen("anaes_screen", 0.32, 0.22, M.idle, B(AX - 0.06, 1.14, AZ + 0.022))
for i, m in enumerate((M.vapor_a, M.vapor_b)):
    vx = AX - 0.14 + i * 0.17
    Cyl("vaporizer", 0.06, 0.06, 0.15, vx, 0.845, AZ + 0.16, M.shell, bevel=0.008)
    Cyl("vaporizer_dial", 0.045, 0.04, 0.035, vx, 0.937, AZ + 0.16, m, bevel=0.005)
Cyl("anaes_bellows", 0.07, 0.07, 0.2, AX + 0.19, 0.87, AZ - 0.02, M.glass, bevel=0.006)
Cyl("anaes_bellows_cap", 0.074, 0.074, 0.02, AX + 0.19, 0.98, AZ - 0.02, M.kit, bevel=0.004)
Box("anaes_monitor", 0.3, 0.22, 0.05, AX + 0.1, 1.46, AZ - 0.14, M.kit, bevel=0.012)
Screen("anaes_monitor_screen", 0.26, 0.18, M.idle, B(AX + 0.1, 1.46, AZ - 0.114))
for off in (0.0, 0.035):   # breathing circuit to the mask
    Tube("breathing_hose", [B(-0.775, 1.2, off), B(-0.95, 1.33, 0.08 + off), B(-1.3, 1.16, 0.26 + off), B(AX + 0.3, 0.88, AZ + 0.02 + off)], 0.011, M.hose)

# ----------------------------------------------------------------- IV pole

IX, IZ = -1.3, -0.62
Cyl("iv_base", 0.22, 0.2, 0.03, IX, 0.035, IZ, M.steel_dark, bevel=0.008)
Cyl("iv_pole", 0.014, 0.011, 2.0, IX, 1.04, IZ, M.steel, bevel=0)
Box("iv_hooks", 0.3, 0.012, 0.012, IX, 2.03, IZ, M.steel, bevel=0.003)
Box("iv_bag", 0.11, 0.19, 0.035, IX + 0.11, 1.86, IZ, M.hose, bevel=0.016)
Cyl("iv_drip", 0.011, 0.011, 0.06, IX + 0.11, 1.72, IZ, M.shell, bevel=0.003)
Tube("iv_line", [B(IX + 0.11, 1.69, IZ), B(IX + 0.14, 1.3, IZ + 0.05), B(-0.75, 1.02, -0.33), B(-0.45, 1.0, -0.27)], 0.004, M.hose)

# ---------------------------------------------- instrument stand and tables

SX, SZ = 2.0, 0.25
Cyl("stand_base", 0.27, 0.25, 0.035, SX, 0.02, SZ, M.steel_dark, bevel=0.008)
Cyl("stand_pole", 0.022, 0.018, 0.98, SX, 0.52, SZ, M.steel, bevel=0)
Box("stand_tray", 0.62, 0.02, 0.42, SX, 1.02, SZ, M.steel, bevel=0.006)
Box("stand_cover", 0.58, 0.006, 0.38, SX, 1.033, SZ, M.drape_light, bevel=0)
for i, (dx, length, tilt) in enumerate(((-0.21, 0.17, 0.05), (-0.13, 0.2, -0.04), (-0.05, 0.15, 0.08), (0.03, 0.22, 0.0), (0.11, 0.18, -0.07), (0.19, 0.14, 0.04))):
    Box("instrument", 0.014, 0.008, length, SX + dx, 1.041, SZ - 0.02, M.steel, bevel=0.003, yaw=tilt)
    Box("instrument_grip", 0.02, 0.011, 0.05, SX + dx, 1.042, SZ + length / 2 - 0.03, M.steel_dark, bevel=0.004, yaw=tilt)
Cyl("stand_bowl", 0.035, 0.05, 0.04, SX + 0.2, 1.056, SZ + 0.12, M.steel, bevel=0.004)

BX, BZ = 3.2, -1.7
Box("back_table", 1.5, 0.04, 0.65, BX, 0.88, BZ, M.steel, bevel=0.008)
Box("back_table_cover", 1.56, 0.4, 0.71, BX, 0.71, BZ, M.drape, bevel=0.01)
for dx in (-0.68, 0.68):
    for dz in (-0.27, 0.27):
        Cyl("back_table_leg", 0.018, 0.018, 0.86, BX + dx, 0.43, BZ + dz, M.steel, bevel=0)
Box("instrument_tray", 0.5, 0.05, 0.3, BX - 0.4, 0.93, BZ, M.steel_dark, bevel=0.008)
Box("instrument_tray", 0.4, 0.08, 0.28, BX + 0.25, 0.945, BZ - 0.05, M.steel, bevel=0.008)
Box("pack", 0.26, 0.1, 0.2, BX + 0.55, 0.955, BZ + 0.14, M.drape_light, bevel=0.02)

# ------------------------------------------------- stool and kick bucket

Cyl("stool_seat", 0.17, 0.18, 0.06, -0.55, 0.52, 1.0, M.kit, bevel=0.02)
Cyl("stool_post", 0.022, 0.022, 0.42, -0.55, 0.28, 1.0, M.steel, bevel=0)
Cyl("stool_base", 0.24, 0.22, 0.03, -0.55, 0.04, 1.0, M.steel_dark, bevel=0.008)
Cyl("kick_bucket", 0.14, 0.17, 0.28, 1.45, 0.24, 0.95, M.steel, bevel=0.008)
Cyl("kick_bucket_dolly", 0.19, 0.19, 0.04, 1.45, 0.06, 0.95, M.steel_dark, bevel=0.008)

# ------------------------------------------------------- ceiling pendant

PX, PZ = 2.3, -1.0
Cyl("pendant_plate", 0.14, 0.14, 0.05, PX + 0.7, 3.525, PZ - 0.6, M.steel_dark)
Tube("pendant_arm", [B(PX + 0.7, 3.5, PZ - 0.6), B(PX + 0.7, 2.7, PZ - 0.6), B(PX + 0.35, 2.48, PZ - 0.3), B(PX, 2.4, PZ)], 0.045, M.shell)
Box("pendant_column", 0.42, 1.15, 0.4, PX, 1.82, PZ, M.shell_grey, bevel=0.025)
for i in range(3):
    Box("pendant_shelf", 0.5, 0.025, 0.46, PX, 1.36 + i * 0.36, PZ + 0.03, M.steel, bevel=0.006)
Box("pendant_device", 0.4, 0.16, 0.34, PX, 1.455, PZ + 0.05, M.kit, bevel=0.012)
Screen("pendant_device_screen", 0.16, 0.08, M.idle, B(PX - 0.08, 1.46, PZ + 0.222))
Box("pendant_device", 0.38, 0.2, 0.32, PX, 1.835, PZ + 0.05, M.shell, bevel=0.012)
Screen("pendant_device_screen", 0.2, 0.1, M.amber, B(PX + 0.04, 1.84, PZ + 0.212))

# ------------------------------------------------------------------ C-arm
# Parked fluoroscopy unit, far left: the silhouette that says "orthopaedic theatre".

CC = Vector(B(-3.0, 1.3, -2.2))                     # centre of the C
u = Vector((math.cos(0.5), -math.sin(0.5), 0.0))    # horizontal direction the C opens toward
RC = 0.78
arc = [CC + RC * (-math.cos(a) * u + math.sin(a) * Vector((0, 0, 1))) for a in [math.radians(t) for t in range(-104, 105, 13)]]
Tube("carm_arc", arc, 0.05, M.shell)
top, bottom = arc[-1], arc[0]
place("carm_detector", to_mesh("carm_detector", bm_cyl(0.17, 0.15, 0.26)), M.shell, top - Vector((0, 0, 0.12)), bevel=0.02)
place("carm_source", to_mesh("carm_source", bm_box(0.26, 0.26, 0.22)), M.shell_grey, bottom + Vector((0, 0, 0.1)), bevel=0.03)
back = CC - u * RC
place("carm_holder", to_mesh("carm_holder", bm_box(0.2, 0.24, 0.3)), M.kit, back, rot=(0, 0, -0.5), bevel=0.02)
Tube("carm_neck", [back, back - u * 0.35 + Vector((0, 0, -0.05)), back - u * 0.6 + Vector((0, 0, -0.3))], 0.07, M.shell_grey)
cart = back - u * 0.85
place("carm_cart", to_mesh("carm_cart", bm_box(0.75, 0.6, 0.9)), M.shell, Vector((cart.x, cart.y, 0.55)), rot=(0, 0, -0.5), bevel=0.05)
place("carm_cart_base", to_mesh("carm_cart_base", bm_box(0.85, 0.66, 0.1)), M.kit, Vector((cart.x, cart.y, 0.06)), rot=(0, 0, -0.5), bevel=0.02)

# ------------------------------------------- preview lights and camera
# For rendering inside Blender only; nothing below is exported to the page.

world = bpy.data.worlds.new("theatre")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = linear(0x071A1F)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.6
scene.world = world

for tag, (pos, aim, _), watts in (("A", LAMP_A, 900), ("B", LAMP_B, 650)):
    data = bpy.data.lights.new(f"spot_{tag}", "SPOT")
    data.energy, data.spot_size, data.spot_blend, data.shadow_soft_size = watts, math.radians(64), 0.6, 0.12
    data.color = (0.95, 0.99, 1.0)
    light = bpy.data.objects.new(f"spot_{tag}", data)
    scene.collection.objects.link(light)
    direction = B(*aim) - B(*pos)
    light.location = B(*pos) + direction.normalized() * 0.03
    light.rotation_mode = "QUATERNION"
    light.rotation_quaternion = direction.to_track_quat("-Z", "Y")

fill = bpy.data.lights.new("fill", "AREA")
fill.energy, fill.size, fill.color = 260, 4.0, (0.55, 0.9, 0.92)
fill_obj = bpy.data.objects.new("fill", fill)
scene.collection.objects.link(fill_obj)
fill_obj.location = B(3.5, 3.2, 4.5)
fill_obj.rotation_mode = "QUATERNION"
fill_obj.rotation_quaternion = (B(0.2, 1.0, 0.0) - fill_obj.location).to_track_quat("-Z", "Y")

cam_data = bpy.data.cameras.new("hero_camera")
cam_data.sensor_fit, cam_data.angle_y = "VERTICAL", math.radians(36)
cam = bpy.data.objects.new("hero_camera", cam_data)
scene.collection.objects.link(cam)
cam.location = B(3.81, 2.07, 5.05)                # where the page's camera starts
cam.rotation_mode = "QUATERNION"
cam.rotation_quaternion = (B(0.2, 1.0, 0.0) - cam.location).to_track_quat("-Z", "Y")
scene.camera = cam

scene.render.resolution_x, scene.render.resolution_y = 1600, 900
scene.render.engine = "CYCLES"
scene.cycles.samples = 128
scene.cycles.use_denoising = True
scene.view_settings.view_transform = "AgX"
try:                                               # use the GPU when there is one
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for kind in ("OPTIX", "CUDA", "METAL", "HIP", "ONEAPI"):
        try:
            prefs.compute_device_type = kind
        except TypeError:
            continue
        prefs.refresh_devices()
        if any(d.type == kind for d in prefs.devices):
            for d in prefs.devices:
                d.use = d.type == kind
            scene.cycles.device = "GPU"
            break
except Exception as err:                           # no Cycles GPU support: CPU rendering still works
    print("GPU not configured:", err)

# -------------------------------------------------------------------- out

blend_path = os.path.join(HERE, "or.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend_path, check_existing=False)
print("Saved", blend_path)
export_glb.export()

if RENDER_TO:
    scene.render.filepath = os.path.abspath(RENDER_TO)
    bpy.ops.render.render(write_still=True)
    print("Rendered", scene.render.filepath)
