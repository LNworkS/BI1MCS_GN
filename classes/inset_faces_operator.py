# -*- coding: utf-8 -*-
"""BI1MCS_GN :: "Inset Faces" node group entry in the Geometry-Nodes *Add* menu.

Adds a `BI1MCS` sub-menu to the node editor's ``Add`` menu (``NODE_MT_add``)
containing the single entry ``Inset Faces``, which inserts the node group.
"""
import bpy

from ..ngs.inset_faces import (GROUP_NAME, MIN_BLENDER, get_or_create_group,
                               is_supported)

MENU_IDNAME = "NODE_MT_bi1mcs"


def _node_editor(context):
    space = getattr(context, "space_data", None)
    if space is None or space.type != 'NODE_EDITOR':
        return None
    if getattr(space, "tree_type", "") != 'GeometryNodeTree':
        return None
    return space


def insert_group_node(tree, space=None, group=None):
    """Insert (=Add) the Inset Faces group node into *tree* and return it."""
    group = group or get_or_create_group()
    for node in tree.nodes:
        node.select = False

    node = tree.nodes.new("GeometryNodeGroup")
    node.node_tree = group
    node.label = GROUP_NAME
    node.select = True
    tree.nodes.active = node

    loc = getattr(space, "cursor_location", None) if space else None
    if loc is None or (abs(loc[0]) < 1e-6 and abs(loc[1]) < 1e-6):
        others = [n for n in tree.nodes if n is not node]
        if others:
            loc = (sum(n.location[0] for n in others) / len(others),
                   sum(n.location[1] for n in others) / len(others))
        else:
            loc = (0.0, 0.0)
    node.location = (loc[0], loc[1])
    return node


class NODE_OT_bi1mcs_inset_faces(bpy.types.Operator):
    bl_idname = "node.bi1mcs_inset_faces"
    bl_label = GROUP_NAME
    bl_description = ("Inset Faces - Geometry-Nodes port of the Edit-Mode "
                      "operator (Thickness / Depth / Even / Relative / "
                      "Individual / Boundary / Outset / Edge Rail)")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        space = _node_editor(context)
        return space is not None and getattr(space, "edit_tree", None) is not None

    def execute(self, context):
        if not is_supported():
            self.report({"ERROR"},
                        "Inset Faces needs Blender %d.%d or newer"
                        % MIN_BLENDER[:2])
            return {"CANCELLED"}

        space = _node_editor(context)
        tree = getattr(space, "edit_tree", None)
        if tree is None:
            self.report({"ERROR"}, "no geometry node tree to edit")
            return {"CANCELLED"}

        insert_group_node(tree, space)
        return {"FINISHED"}


class NODE_MT_bi1mcs(bpy.types.Menu):
    bl_idname = MENU_IDNAME
    bl_label = "BI1MCS"

    def draw(self, context):
        layout = self.layout
        layout.operator(NODE_OT_bi1mcs_inset_faces.bl_idname,
                        text=GROUP_NAME)


def draw_bi1mcs_menu(self, context):
    if _node_editor(context) is None:
        return
    self.layout.separator()
    self.layout.menu(NODE_MT_bi1mcs.bl_idname, icon='NODETREE')


classes = (
    NODE_OT_bi1mcs_inset_faces,
    NODE_MT_bi1mcs,
)
