# -*- coding: utf-8 -*-
"""Insert the runtime Solidify group in the geometry node editor."""
import bpy
from ..ngs.solidify import GROUP_NAME, get_or_create_group, is_supported
from .inset_faces_operator import _node_editor, insert_group_node

menu_entry = ('node.bi1mcs_solidify', GROUP_NAME)


class NODE_OT_bi1mcs_solidify(bpy.types.Operator):
    bl_idname = 'node.bi1mcs_solidify'
    bl_label = GROUP_NAME
    bl_description = 'Solidify double shell: Simple or manifold Complex Constraints, thickness, offset, even thickness and vertex weights'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        space = _node_editor(context)
        return space is not None and getattr(space, 'edit_tree', None) is not None

    def execute(self, context):
        if not is_supported():
            self.report({'ERROR'}, 'Solidify needs Blender 5.2 or newer')
            return {'CANCELLED'}
        space = _node_editor(context)
        tree = getattr(space, 'edit_tree', None)
        if tree is None:
            return {'CANCELLED'}
        node = insert_group_node(tree, space, get_or_create_group(), GROUP_NAME)
        node.width = 280
        return {'FINISHED'}


classes = (NODE_OT_bi1mcs_solidify,)
