# -*- coding: utf-8 -*-
"""BI1MCS :: Geometry-Nodes *Add* menu entry.

Attached to ``NODE_MT_add`` so the node editor shows::

    Add > BI1MCS > Inset Faces
"""
from ..classes.inset_faces_operator import (NODE_MT_bi1mcs, _node_editor,
                                            draw_bi1mcs_menu)

__all__ = ("draw_bi1mcs_menu", "NODE_MT_bi1mcs", "_node_editor")
