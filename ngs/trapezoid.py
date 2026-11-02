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
    Top   = Force Angle ? Bottom - 2 * Height / tan(Angle) : Top Width
    r     = Top / Bottom
    x'    = x + x * (t * (r - 1))               # uniform horizontal taper
    y'    = y,  z' = z

Because ``t`` is linear in ``y`` the two legs stay straight, so the result is
a planar isosceles trapezoid (or a rectangle / degenerate triangle when
``Top`` equals / reaches 0).  The mesh spans ``[-Bottom/2, Bottom/2] x
[-Height/2, Height/2]`` and lies in the XY plane, exactly like the source.

``Force Angle`` derives the top width from the leg angle instead of taking
``Top Width``: a leg that leans ``Angle`` radians away from vertical removes
``Height / tan(Angle)`` from each side of the top edge (45 deg is the source
default, 90 deg gives vertical legs and a rectangle).

One deliberate divergence from the source group
-----------------------------------------------
The source graph runs the angle socket through a ``Radians`` node before
``Tangent``.  ``Angle`` is an ``ANGLE`` socket, so Blender already stores it in
radians and the extra conversion shrinks it by 57.3x: at the 45 deg default the
source computes ``Bottom - 2*Height/tan(0.0137)`` and returns a 143 unit wide
inverted shape instead of a trapezoid.  That is a unit bug, not the documented
intent (the source's own frame label reads ``底边-2*高度/tangent角度``), so this
node consumes ``Angle`` as a real radian angle: ``Bottom - 2*Height/tan(Angle)``.
``Force Angle = False`` (the default) still matches the source bit for bit.

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

from .extrude import _Graph, _interface, MIN_BLENDER, is_supported

GROUP_NAME = "trapezoid"
# bump together with bl_info["version"]; a group carrying a different tag was
# built by an older release and gets rebuilt in place on next use.
VERSION = (0, 1, 0)

EPSILON = 1e-8
# tan(45 deg), the source group's default leg angle
DEFAULT_ANGLE = 0.7853981633974483


def _guard(g, value, fallback):
    """Return *fallback* when ``|value| < EPSILON``, otherwise *value*."""
    return g.switch("FLOAT", g.math("LESS_THAN", g.math("ABSOLUTE", value), EPSILON),
                    value, fallback)


def _top_width(g, inputs):
    """``Force Angle ? Bottom - 2 * Height / tan(Angle) : Top Width``.

    ``Angle`` is an ANGLE socket, i.e. already radians - no Radians node here
    (the source group has one, which is the unit bug documented above).
    """
    tangent = g.math("TANGENT", inputs["Angle"])
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
    sockets[("Angle", "INPUT")].subtype = "ANGLE"
    sockets[("Bottom Width", "INPUT")].description = "Width of the bottom edge (the grid's Size X)"
    sockets[("Top Width", "INPUT")].description = "Width of the top edge; ignored when Force Angle is on"
    sockets[("Height", "INPUT")].description = "Distance between the bottom and top edges"
    sockets[("Subdivisions X", "INPUT")].description = "Extra columns; the grid always has at least 2 vertices across"
    sockets[("Subdivisions Y", "INPUT")].description = "Extra rows; the grid always has at least 2 vertices up"
    sockets[("Force Angle", "INPUT")].description = "Derive Top Width from the leg angle instead of using Top Width"
    sockets[("Angle", "INPUT")].description = "Leg angle away from vertical when Force Angle is on"
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


def get_or_create_group(name=GROUP_NAME):
    nt = bpy.data.node_groups.get(name)
    if nt is None:
        return build(name)
    if nt.bl_idname != "GeometryNodeTree":
        raise TypeError("Existing group is not a GeometryNodeTree")
    if nt.get("bi1mcs_version") != ".".join(map(str, VERSION)):
        return build(name, nt)
    return nt
