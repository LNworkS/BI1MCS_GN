# -*- coding: utf-8 -*-
"""BI1MCS_GN :: "Bevel" node group entry in the Geometry-Nodes *Add* menu.

Adds the ``Bevel`` entry to the shared ``BI1MCS`` sub-menu of the node editor's
``Add`` menu (``NODE_MT_add``); the sub-menu itself lives in
``classes.inset_faces_operator``.
"""
import bpy

from ..ngs.bevel import (GROUP_NAME, MIN_BLENDER, get_or_create_group,
                         is_supported)
from .inset_faces_operator import _node_editor, insert_group_node

# (bl_idname, label) shown in the BI1MCS sub-menu
menu_entry = ("node.bi1mcs_bevel", GROUP_NAME)


class NODE_OT_bi1mcs_bevel(bpy.types.Operator):
    bl_idname = "node.bi1mcs_bevel"
    bl_label = GROUP_NAME
    bl_description = ("Bevel - Geometry-Nodes port of the Bevel modifier "
                      "(four offsets / Width Method / Segments / Profile / "
                      "Limit Method / Affect / Miter)")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        space = _node_editor(context)
        return space is not None and getattr(space, "edit_tree", None) is not None

    def execute(self, context):
        if not is_supported():
            self.report({"ERROR"},
                        "Bevel needs Blender %d.%d or newer"
                        % MIN_BLENDER[:2])
            return {"CANCELLED"}

        space = _node_editor(context)
        tree = getattr(space, "edit_tree", None)
        if tree is None:
            self.report({"ERROR"}, "no geometry node tree to edit")
            return {"CANCELLED"}

        insert_group_node(tree, space, get_or_create_group(), GROUP_NAME)
        return {"FINISHED"}


classes = (
    NODE_OT_bi1mcs_bevel,
)
