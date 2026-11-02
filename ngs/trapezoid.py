# -*- coding: utf-8 -*-
"""BI1MCS :: ``trapezoid`` - symmetric trapezoid grid generator.

Rebuilt from the ``梯形`` node group of ``Window_01`` in
``BuildBase.blend`` (Scene_M2).  The original graph is a generator: a
subdivided grid is sheared into an isosceles trapezoid whose bottom edge is
``Bottom Width``, whose top edge is ``Top Width`` and whose height is
``Height``.  Nothing else in that group is exposed, so nothing else is
exposed here either.

Algorithm (identical to the source graph, except where noted below)
-------------------------------------------------------------------
::

    grid  = Mesh Grid(Size X = Bottom Width,
                      Size Y = Height,
                      Vertices X = Subdivisions X + 2,
                      Vertices Y = Subdivisions Y + 2)   # centred on origin
    t     = y / Height + 0.5                    # 0 at the bottom edge, 1 at top
    Top   = Force Angle ? Bottom - 2 * Height / tan(radians(Angle)) : Top Width
    r     = Top / Bottom
    x'    = x + x * (t * (r - 1))               # uniform horizontal taper
    y'    = y,  z' = z

Because ``t`` is linear in ``y`` the two legs stay straight, so the result is
a planar isosceles trapezoid (or a rectangle / degenerate triangle when
``Top`` equals / reaches 0).  The mesh spans ``[-Bottom/2, Bottom/2] x
[-Height/2, Height/2]`` and lies in the XY plane, exactly like the source.

``Force Angle`` derives the top width from the leg angle instead of taking
``Top Width``: a leg that leans ``Angle`` **degrees** away from vertical removes
``Height / tan(radians(Angle))`` from each side of the top edge (45 deg is the
source default, 90 deg gives vertical legs and a rectangle).

Why ``Angle`` is a plain float in degrees
-----------------------------------------
The source group declares its angle socket as an ``ANGLE`` socket - whose value
is stored in radians - but feeds it straight into ``Radians`` -> ``Tangent``, so
the number it actually consumes is degrees.  Typing the visible default (45 deg
= 0.785 stored) therefore computes ``Bottom - 2*Height/tan(0.0137)`` and returns
a 143 unit wide inverted shape; only a raw driven value of ``45`` produces the
intended trapezoid.  This node removes the ambiguity: ``Angle`` is a plain
``Float`` in degrees (default 45) and is converted internally, so the value shown
in the modifier, the value typed in the socket and the value driven from a field
all mean the same thing.  At equal numeric input it matches the source graph bit
for bit, including ``Force Angle`` on.

Degenerate guards (deliberate, documented divergence points)
-----------------------------------------------------------
* ``|Bottom Width| < 1e-8`` - the taper ratio would be (or approach) a
  division by zero; the ratio falls back to 1 so every vertex is finite.  The
  grid is degenerate anyway (all x are 0), so the shape is unchanged.
* ``|Height| < 1e-8`` - same fallback for ``y / Height``; every ``y`` is 0 in
  that case, so ``t`` would be 0/0 and the fallback pins it to 0.5.
* ``tan(Angle) == 0`` - Blender's Math node returns 0 for a division by zero,
  so ``Top`` falls back to ``Bottom`` (a rectangle) instead of ``inf``.

Requires Blender 5.2+ (``MIN_BLENDER`` is shared with the rest of the add-on,
not with a specific node here).
"""
import bpy
from math import degrees

from .extrude import _Graph, _interface, MIN_BLENDER, is_supported

GROUP_NAME = "trapezoid"
# bump together with bl_info["version"]; a group carrying a different tag was
# built by an older release and gets rebuilt in place on next use.
# 0.2.0: Angle became a plain float in DEGREES (was an ANGLE socket feeding
#        Radians, i.e. the stored radian value was re-converted as degrees).
VERSION = (0, 2, 0)

EPSILON = 1e-8
# the source group's default leg angle, in degrees
DEFAULT_ANGLE = 45.0


def _guard(g, value, fallback):
    """Return *fallback* when ``|value| < EPSILON``, otherwise *value*."""
    return g.switch("FLOAT", g.math("LESS_THAN", g.math("ABSOLUTE", value), EPSILON),
                    value, fallback)


def _top_width(g, inputs):
    """``Force Angle ? Bottom - 2 * Height / tan(radians(Angle)) : Top Width``.

    ``Angle`` is a plain float in degrees, so it is converted here - the source
    graph's ``Radians`` node does the same thing to a value it also calls degrees.
    """
    tangent = g.math("TANGENT", g.math("RADIANS", inputs["Angle"]))
    inset = g.math("MULTIPLY", g.math("DIVIDE", inputs["Height"], tangent), 2)
    forced = g.math("SUBTRACT", inputs["Bottom Width"], inset)
    return g.switch("FLOAT", inputs["Force Angle"], inputs["Top Width"], forced)


def build(group_name=GROUP_NAME, nt=None):
    """Create the ``trapezoid`` geometry-node group and return it.

    Pass an existing tree as *nt* to rebuild it in place: the group keeps its
    identity, so every node instance that already points at it keeps working
    instead of being orphaned by a fresh group of the same name.
    """
    if not is_supported():
        raise RuntimeError("trapezoid requires Blender 5.2+")
    if nt is None:
        nt = bpy.data.node_groups.new(group_name, "GeometryNodeTree")
    else:
        nt.nodes.clear()

    specs = [("Bottom Width", "Float", 1.0),
             ("Top Width", "Float", 1.0),
             ("Height", "Float", 1.0),
             ("Subdivisions X", "Int", 0),
             ("Subdivisions Y", "Int", 0),
             ("Force Angle", "Bool", False),
             ("Angle", "Float", DEFAULT_ANGLE)]
    sockets = _interface(nt, specs, [("Geometry", "Geometry")])
    for name in ("Bottom Width", "Top Width", "Height"):
        sockets[(name, "INPUT")].subtype = "DISTANCE"
    sockets[("Bottom Width", "INPUT")].description = "Width of the bottom edge (the grid's Size X)"
    sockets[("Top Width", "INPUT")].description = "Width of the top edge; ignored when Force Angle is on"
    sockets[("Height", "INPUT")].description = "Distance between the bottom and top edges"
    sockets[("Subdivisions X", "INPUT")].description = "Extra columns; the grid always has at least 2 vertices across"
    sockets[("Subdivisions Y", "INPUT")].description = "Extra rows; the grid always has at least 2 vertices up"
    sockets[("Force Angle", "INPUT")].description = "Derive Top Width from the leg angle instead of using Top Width"
    angle = sockets[("Angle", "INPUT")]
    # A group built by 0.1.0 declared this as an ANGLE socket; a rebuild keeps the
    # socket (same type), so its subtype has to be reset explicitly.
    angle.subtype = "NONE"
    angle.description = ("Leg angle away from vertical, in DEGREES (converted to radians "
                         "internally; 45 = 1:1 slope, 90 = vertical legs). Values above 90 "
                         "lean the other way, giving a top edge wider than the bottom.")
    nt.description = ("Isosceles trapezoid grid: bottom/top width, height, "
                      "X/Y subdivisions and an optional leg-angle driven top width")

    g = _Graph(nt)
    inputs = g.node("NodeGroupInput").outputs

    grid = g.node("GeometryNodeMeshGrid")
    g.link(inputs["Bottom Width"], grid.inputs["Size X"])
    g.link(inputs["Height"], grid.inputs["Size Y"])
    g.link(g.math("ADD", inputs["Subdivisions X"], 2), grid.inputs["Vertices X"])
    g.link(g.math("ADD", inputs["Subdivisions Y"], 2), grid.inputs["Vertices Y"])

    pos = g.node("GeometryNodeInputPosition").outputs[0]
    x, y, z = g.split(pos)
    ratio = g.math("DIVIDE", _top_width(g, inputs), _guard(g, inputs["Bottom Width"], 1.0))
    t = g.math("ADD", g.math("DIVIDE", y, _guard(g, inputs["Height"], 1.0)), 0.5)
    # Keep the source's exact operation order (x * (t * (r - 1)), applied as an
    # Offset) so both graphs agree bit for bit, not just to within an epsilon.
    offset = g.math("MULTIPLY", x, g.math("MULTIPLY", t, g.math("SUBTRACT", ratio, 1)))

    move = g.node("GeometryNodeSetPosition")
    g.link(grid.outputs["Mesh"], move.inputs["Geometry"])
    g.link(g.combine((offset, 0.0, 0.0)), move.inputs["Offset"])

    out = g.node("NodeGroupOutput")
    g.link(move.outputs["Geometry"], out.inputs["Geometry"])

    nt["bi1mcs_version"] = ".".join(map(str, VERSION))
    nt["bi1mcs_tool"] = GROUP_NAME
    return nt


def _migrate_angle_values(group):
    """Convert stored ``Angle`` values of a 0.1.0 group from radians to degrees.

    0.1.0 declared ``Angle`` as an ANGLE socket, so every instance stored radians
    (45 deg -> 0.785).  0.2.0 reads the same number as degrees, so an untouched
    instance would suddenly mean 0.785 deg.  Linked sockets are fields and cannot
    be migrated here; only literal values are rewritten.
    """
    def convert(value):
        return degrees(value) if abs(value) > 1e-9 else value

    for tree in bpy.data.node_groups:
        for node in tree.nodes:
            if node.bl_idname != 'GeometryNodeGroup' or node.node_tree != group:
                continue
            socket = node.inputs.get('Angle')
            if socket is not None and not socket.is_linked:
                socket.default_value = convert(socket.default_value)
    identifiers = [s.identifier for s in group.interface.items_tree
                   if s.item_type == 'SOCKET' and s.in_out == 'INPUT'
                   and s.name == 'Angle']
    for obj in bpy.data.objects:
        for mod in obj.modifiers:
            if mod.type != 'NODES' or mod.node_group != group:
                continue
            for identifier in identifiers:
                holder = getattr(getattr(mod, 'properties', None), 'inputs', None)
                holder = getattr(holder, identifier, None) if holder is not None else None
                if holder is not None and hasattr(holder, 'value'):
                    holder.value = convert(holder.value)
                elif identifier in mod_ids(mod):
                    mod[identifier] = convert(mod[identifier])


def mod_ids(mod):
    try:
        return list(mod.keys())
    except TypeError:
        return []


def _tagged_groups():
    return [g for g in bpy.data.node_groups
            if g.bl_idname == "GeometryNodeTree" and g.get("bi1mcs_tool") == GROUP_NAME]


def get_or_create_group(name=GROUP_NAME):
    """Return the ``trapezoid`` group, building it the first time.

    Blender IDs are unique **case-insensitively**, so a file that already owns a
    group called ``Trapezoid`` (``BuildBase.blend`` does) forces ours to be named
    ``trapezoid.001`` no matter what.  A pure name lookup would then miss it and
    pile up ``trapezoid.002``, ``trapezoid.003`` ... on every insert, so the group
    is identified by its ``bi1mcs_tool`` tag as well, and a foreign group that
    merely occupies the name is never overwritten.
    """
    nt = bpy.data.node_groups.get(name)
    if nt is None or nt.get("bi1mcs_tool") != GROUP_NAME:
        tagged = _tagged_groups()
        if tagged:
            nt = tagged[0]
        elif nt is None:
            return build(name)
    if nt.bl_idname != "GeometryNodeTree":
        raise TypeError("Existing group is not a GeometryNodeTree")
    version = nt.get("bi1mcs_version")
    if version != ".".join(map(str, VERSION)):
        if version == "0.1.0":
            _migrate_angle_values(nt)
        return build(nt.name, nt)
    return nt
