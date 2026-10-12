# -*- coding: utf-8 -*-
"""BI1MCS_GN - Geometry-Nodes toolset (Blender add-on).

Registers a ``BI1MCS`` sub-menu in the node editor's ``Add`` menu
(``NODE_MT_add``), shown for ``GeometryNodeTree`` only::

    Add > BI1MCS > Inset Faces
    Add > BI1MCS > Bevel

``Inset Faces`` is a Geometry-Nodes port of the Edit-Mode *Inset Faces*
operator (``bmesh.ops.inset_region`` / ``inset_individual``) with every
parameter exposed - Thickness / Depth / Even Offset / Relative Offset /
Individual / Boundary / Outset / Edge Rail.

``Bevel`` is a Geometry-Nodes port of the Bevel *modifier* with its whole
parameter surface exposed - the native node's four offsets (each run through
the Width Method: Offset / Width / Depth / Percent / Absolute) / Segments /
Profile / Limit Method (Angle / Weight / Vertex Group) / Affect (Edges /
Vertices) / Miter Outer & Inner / Spread - plus ``Bevel Faces`` and
``Bevel Edges`` selection outputs.  ``Miter Outer`` and ``Miter Inner`` drive
the native miter flag independently, per vertex.

Both node groups are built at runtime by ``ngs.inset_faces`` / ``ngs.bevel``
(no .blend asset to ship).

Requires Blender 5.2 or newer: ``Inset Faces`` relies on ``Extrude Mesh``'s
``Top`` / ``Side`` outputs being boolean selections, and ``Bevel`` drives the
native ``Mesh Bevel`` node.
"""
bl_info = {
    "name": "BI1MCS_GN",
    "author": "BI1MCS",
    "version": (0, 4, 0),
    "blender": (5, 2, 0),
    "location": "Node Editor > Add > BI1MCS > Inset Faces / Bevel",
    "description": ("Geometry-Nodes toolset. 'Inset Faces' reproduces the "
                    "Edit-Mode Inset Faces operator and 'Bevel' reproduces "
                    "the Bevel modifier, each as a node group under "
                    "Add > BI1MCS"),
    "warning": "",
    "doc_url": "",
    "category": "Node",
}

import bpy

from .classes import bevel_operator as _bevel
from .classes import inset_faces_operator as _inset_faces
from .ui import bevel as _ui_bevel
from .ui.inset_faces import draw_bi1mcs_menu

all_classes = _inset_faces.classes + _bevel.classes


def register():
    _inset_faces.MENU_ENTRIES[:] = [_inset_faces.menu_entry,
                                    _ui_bevel.menu_entry]

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
