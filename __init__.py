# -*- coding: utf-8 -*-
"""BI1MCS_GN - Geometry-Nodes toolset (Blender add-on).

Registers a ``BI1MCS`` sub-menu in the node editor's ``Add`` menu
(``NODE_MT_add``), shown for ``GeometryNodeTree`` only::

    Add > BI1MCS > Inset Faces

``Inset Faces`` is a Geometry-Nodes port of the Edit-Mode *Inset Faces*
operator (``bmesh.ops.inset_region`` / ``inset_individual``) with every
parameter exposed - Thickness / Depth / Even Offset / Relative Offset /
Individual / Boundary / Outset / Edge Rail.  The node group is built at
runtime by ``ngs.inset_faces`` (no .blend asset to ship).

Requires Blender 5.2 or newer (it relies on ``Extrude Mesh``'s ``Top`` /
``Side`` outputs being boolean selections).
"""
bl_info = {
    "name": "BI1MCS_GN",
    "author": "BI1MCS",
    "version": (0, 1, 1),
    "blender": (5, 2, 0),
    "location": "Node Editor > Add > BI1MCS > Inset Faces",
    "description": ("Geometry-Nodes toolset. 'Inset Faces' reproduces the "
                    "Edit-Mode Inset Faces operator (Thickness / Depth / Even "
                    "/ Relative / Individual / Boundary / Outset / Edge Rail) "
                    "as a node group under Add > BI1MCS"),
    "warning": "",
    "doc_url": "",
    "category": "Node",
}

import bpy

from .classes import inset_faces_operator as _inset_faces
from .ui.inset_faces import draw_bi1mcs_menu

all_classes = _inset_faces.classes


def register():
    for cls in all_classes:
        bpy.utils.register_class(cls)

    bpy.types.NODE_MT_add.append(draw_bi1mcs_menu)


def unregister():
    bpy.types.NODE_MT_add.remove(draw_bi1mcs_menu)

    for cls in all_classes:
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
