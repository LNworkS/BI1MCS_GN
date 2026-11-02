# -*- coding: utf-8 -*-
"""Add the runtime Extrude group to the shared BI1MCS menu."""
import bpy
from ..ngs.extrude import GROUP_NAME, MIN_BLENDER, get_or_create_group, is_supported
from .inset_faces_operator import _node_editor, insert_group_node

menu_entry = ("node.bi1mcs_extrude", GROUP_NAME)


class NODE_OT_bi1mcs_extrude(bpy.types.Operator):
    bl_idname = "node.bi1mcs_extrude"
    bl_label = GROUP_NAME
    bl_description = ("Extrude Faces Along Normals - face region extrusion, "
                      "Offset Even and side normal flip")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        space = _node_editor(context)
        return space is not None and getattr(space, "edit_tree", None) is not None

    def execute(self, context):
        if not is_supported():
            self.report({"ERROR"}, "Extrude needs Blender %d.%d or newer" % MIN_BLENDER[:2])
            return {"CANCELLED"}
        space = _node_editor(context)
        tree = getattr(space, "edit_tree", None)
        if tree is None:
            self.report({"ERROR"}, "no geometry node tree to edit")
            return {"CANCELLED"}
        node = insert_group_node(tree, space, get_or_create_group(), GROUP_NAME)
        node.width = 280
        return {"FINISHED"}


classes = (NODE_OT_bi1mcs_extrude,)
