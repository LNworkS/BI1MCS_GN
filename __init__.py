# -*- coding: utf-8 -*-
"""BI1MCS_GN - Geometry-Nodes toolset (Blender add-on).

Registers a ``BI1MCS`` sub-menu in the node editor's ``Add`` menu
(``NODE_MT_add``), shown for ``GeometryNodeTree`` only::

    Add > BI1MCS > Inset Faces
    Add > BI1MCS > Bevel
    Add > BI1MCS > Extrude Faces Along Normals
    Add > BI1MCS > Solidify
    Add > BI1MCS > trapezoid

``Inset Faces`` is a Geometry-Nodes port of the Edit-Mode *Inset Faces*
operator (``bmesh.ops.inset_region`` / ``inset_individual``) with every
parameter exposed - Thickness / Depth / Even Offset / Relative Offset /
Individual / Boundary / Outset / Edge Rail.

``Bevel`` is a Geometry-Nodes port of the Bevel *modifier* with its whole
parameter surface exposed - the native node's four offsets (each run through
the Width Method: Offset / Width / Depth / Percent / Absolute) / Segments /
Profile / Limit Method (Angle / Weight / Vertex Group) / Affect (Edges /
Vertices) / Miter / Spread - plus ``Bevel Faces`` and ``Bevel Edges``
selection outputs.  ``Miter`` is a single boolean, exactly like the native
``Mesh Bevel`` node's input; it reproduces the modifier's ``(Sharp, Sharp)``
and ``(Patch, Arc)`` combinations bit for bit.

``Extrude Faces Along Normals`` provides region selection, Offset, Flip Normals
and Offset Even only. ``Solidify`` exposes Simple/Complex Mode, Thickness, Offset,
Even Thickness and point-domain Vertex Group weights. Unexposed controls use
modifier defaults; Complex Constraints supports disconnected orientable vertex
fans, but not general radial sectors or short-edge merging. Unsupported Complex topology returns the input and Warning,
never silently substitutes Simple.

``trapezoid`` is a generator (no Geometry input): a subdivided grid sheared into
an isosceles trapezoid from Bottom Width / Top Width / Height, with optional
X/Y subdivisions and a ``Force Angle`` mode that derives the top width from the
leg angle (``Bottom - 2 * Height / tan(radians(Angle))``; ``Angle`` is a plain
float in degrees).  Rebuilt from the ``梯形`` group inside ``Window_01`` of
``BuildBase.blend`` (Scene_M2).

All node groups are built at runtime (no .blend asset to ship).

Requires Blender 5.2 or newer: ``Inset Faces`` relies on ``Extrude Mesh``'s
``Top`` / ``Side`` outputs being boolean selections, and ``Bevel`` drives the
native ``Mesh Bevel`` node.
"""
bl_info = {
    "name": "BI1MCS_GN",
    "author": "BI1MCS",
    "version": (0, 8, 0),
    "blender": (5, 2, 0),
    "location": "Node Editor > Add > BI1MCS > Inset Faces / Bevel / Extrude Faces Along Normals / Solidify / trapezoid",
    "description": ("Geometry-Nodes toolset. 'Inset Faces' reproduces the "
                    "Edit-Mode Inset Faces operator and 'Bevel' reproduces "
                    "the Bevel modifier. 'Extrude Faces Along Normals' provides "
                    "region extrusion; 'Solidify' generates double shells with "
                    "Simple or manifold Complex Constraints; 'trapezoid' generates "
                    "a subdivided isosceles trapezoid grid. Node groups appear under "
                    "Add > BI1MCS"),
    "warning": "",
    "doc_url": "",
    "category": "Node",
}

import bpy

from .classes import solidify_operator as _solidify
from .ui import solidify as _ui_solidify
from .classes import extrude_operator as _extrude
from .ui import extrude as _ui_extrude
from .classes import trapezoid_operator as _trapezoid
from .ui import trapezoid as _ui_trapezoid
from .classes import bevel_operator as _bevel
from .classes import inset_faces_operator as _inset_faces
from .ui import bevel as _ui_bevel
from .ui.inset_faces import draw_bi1mcs_menu

all_classes = (_inset_faces.classes + _bevel.classes + _extrude.classes
               + _solidify.classes + _trapezoid.classes)


def register():
    _inset_faces.MENU_ENTRIES[:] = [_inset_faces.menu_entry,
                                    _ui_bevel.menu_entry,
                                    _ui_extrude.menu_entry,
                                    _ui_solidify.menu_entry,
                                    _ui_trapezoid.menu_entry]

    for cls in all_classes:
        bpy.utils.register_class(cls)

    bpy.types.NODE_MT_add.append(draw_bi1mcs_menu)


def unregister():
    bpy.types.NODE_MT_add.remove(draw_bi1mcs_menu)

    for cls in all_classes:
        bpy.utils.unregister_class(cls)

    _inset_faces.MENU_ENTRIES[:] = []


if __name__ == "__main__":
    register()
