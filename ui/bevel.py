# -*- coding: utf-8 -*-
"""BI1MCS :: Geometry-Nodes *Add* menu entry, Bevel.

Adds to the ``NODE_MT_add`` sub-menu shown as::

    Add > BI1MCS > Bevel
"""
from ..classes.bevel_operator import NODE_OT_bi1mcs_bevel, menu_entry

__all__ = ("NODE_OT_bi1mcs_bevel", "menu_entry")
