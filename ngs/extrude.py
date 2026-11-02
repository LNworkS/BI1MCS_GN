# -*- coding: utf-8 -*-
"""Pure GN Extrude Faces Along Normals, Blender 5.2 fbe6228777e7.

Only region selection, Offset, Flip Normals and Offset Even are exposed.
No dissolve, mirror or proportional-editing implementation remains in this graph.
Shared graph helpers are also used by Solidify. Non-manifold face fans may differ
from BMesh's region splitting. Retained bottoms weld by source ID, not position.
"""
import bpy
from .inset_faces import (
    _n, _l, _math, _vm, _scale, _dot, _len, _normv, _bool, _sw,
    _fai, _acc, _sock,
)

GROUP_NAME = "Extrude Faces Along Normals"
VERSION = (0, 2, 0)
MIN_BLENDER = (5, 2, 0)


def is_supported():
    return tuple(bpy.app.version) >= MIN_BLENDER


class _Graph:
    def __init__(self, nt):
        self.nt = nt
        self.count = 0

    def loc(self):
        self.count += 1
        return ((self.count // 12) * 220, -(self.count % 12) * 180)

    def node(self, kind, **props):
        return _n(self.nt, kind, self.loc(), **props)

    def link(self, value, socket):
        if _sock(value):
            _l(self.nt, value, socket)
        else:
            socket.default_value = value

    def math(self, op, a, b=None):
        return _math(self.nt, self.loc(), op, a, b, 0.0 if op == "COMPARE" else None)

    def vec(self, op, a, b=None):
        return _vm(self.nt, self.loc(), op, a, b)

    def scale(self, a, b):
        if not _sock(b):
            b = self.math("ADD", b, 0)
        return _scale(self.nt, self.loc(), a, b)

    def dot(self, a, b):
        return _dot(self.nt, self.loc(), a, b)

    def length(self, a):
        return _len(self.nt, self.loc(), a)

    def norm(self, a):
        return _normv(self.nt, self.loc(), a)

    def boolean(self, op, a, b=None):
        return _bool(self.nt, self.loc(), op, a, b)

    def switch(self, dtype, cond, false, true):
        return _sw(self.nt, self.loc(), dtype, cond, false, true)

    def at(self, domain, dtype, value, index):
        return _fai(self.nt, self.loc(), domain, dtype, value, index)

    def accum(self, domain, dtype, group, value):
        if not _sock(group):
            group = self.math("ADD", group, 0)
        if not _sock(value):
            value = self.math("ADD", value, 0)
        return _acc(self.nt, self.loc(), domain, dtype, group, value)

    def capture(self, geo, domain, **values):
        nd = self.node("GeometryNodeCaptureAttribute", domain=domain)
        nd.capture_items.clear()
        for name, (dtype, _) in values.items():
            nd.capture_items.new(dtype, name)
        self.link(geo, nd.inputs["Geometry"])
        for name, (_, value) in values.items():
            self.link(value, nd.inputs[name])
        return nd.outputs["Geometry"], {name: nd.outputs[name] for name in values}

    def stat(self, geo, domain, value, output="Max", dtype="FLOAT"):
        nd = self.node("GeometryNodeAttributeStatistic", domain=domain, data_type=dtype)
        self.link(geo, nd.inputs["Geometry"])
        self.link(value, nd.inputs["Attribute"])
        return nd.outputs[output]

    def separate(self, geo, domain, selection):
        nd = self.node("GeometryNodeSeparateGeometry", domain=domain)
        self.link(geo, nd.inputs["Geometry"])
        self.link(selection, nd.inputs["Selection"])
        return nd.outputs["Selection"]

    def sample(self, geo, dtype, value, index):
        nd = self.node("GeometryNodeSampleIndex", domain="POINT", data_type=dtype)
        nd.clamp = False
        self.link(geo, nd.inputs["Geometry"])
        self.link(value, nd.inputs["Value"])
        self.link(index, nd.inputs["Index"])
        return nd.outputs["Value"]

    def nearest(self, geo, position):
        nd = self.node("GeometryNodeSampleNearest", domain="POINT")
        self.link(geo, nd.inputs["Geometry"])
        self.link(position, nd.inputs["Sample Position"])
        return nd.outputs["Index"]

    def setpos(self, geo, position, selection=True):
        nd = self.node("GeometryNodeSetPosition")
        self.link(geo, nd.inputs["Geometry"])
        self.link(position, nd.inputs["Position"])
        self.link(selection, nd.inputs["Selection"])
        return nd.outputs["Geometry"]

    def split(self, vector):
        nd = self.node("ShaderNodeSeparateXYZ")
        self.link(vector, nd.inputs[0])
        return [nd.outputs[a] for a in "XYZ"]

    def combine(self, components):
        nd = self.node("ShaderNodeCombineXYZ")
        for i, v in enumerate(components):
            self.link(v, nd.inputs[i])
        return nd.outputs[0]


def _interface(nt, inputs, outputs):
    """Remove obsolete sockets only; surviving names retain identifiers and links."""
    desired = {(name, direction): "NodeSocket" + kind
               for direction, specs in (("INPUT", inputs), ("OUTPUT", outputs))
               for name, kind, *_ in specs}
    existing = {}
    for socket in list(nt.interface.items_tree):
        if socket.item_type != "SOCKET":
            continue
        key = (socket.name, socket.in_out)
        if desired.get(key) != socket.socket_type:
            nt.interface.remove(socket)
        else:
            existing[key] = socket
    result = {}
    for direction, specs in (("INPUT", inputs), ("OUTPUT", outputs)):
        for rank, (name, kind, *defaults) in enumerate(specs):
            socket = existing.get((name, direction))
            if socket is None:
                socket = nt.interface.new_socket(name=name, in_out=direction,
                                                socket_type="NodeSocket" + kind)
            if defaults and defaults[0] is not None:
                socket.default_value = defaults[0]
            if direction == "INPUT" and kind == "Bool" and name != "Selection":
                socket.force_non_field = True
            result[(name, direction)] = socket
    return result


def _corner_fields(g, face_selection, face_normal):
    idx = g.node("GeometryNodeInputIndex").outputs[0]
    pos = g.node("GeometryNodeInputPosition").outputs[0]
    foc = g.node("GeometryNodeFaceOfCorner")
    voc = g.node("GeometryNodeVertexOfCorner")
    cov = g.node("GeometryNodeCornersOfVertex")
    g.link(idx, foc.inputs["Corner Index"])
    g.link(idx, voc.inputs["Corner Index"])
    g.link(idx, cov.inputs["Vertex Index"])
    fi, vi, ci = foc.outputs["Face Index"], voc.outputs["Vertex Index"], cov.outputs["Corner Index"]
    directions = []
    for offset in (-1, 1):
        nd = g.node("GeometryNodeOffsetCornerInFace")
        g.link(idx, nd.inputs["Corner Index"])
        nd.inputs["Offset"].default_value = offset
        p = g.at("CORNER", "FLOAT_VECTOR", pos, nd.outputs["Corner Index"])
        directions.append(g.norm(g.vec("SUBTRACT", p, pos)))
    angle = g.math("ARCCOSINE", g.math("MINIMUM", 1, g.math("MAXIMUM", -1, g.dot(*directions))))
    return idx, fi, vi, ci, angle, g.at("FACE", "BOOLEAN", face_selection, fi), g.at("FACE", "FLOAT_VECTOR", face_normal, fi)


def _point_selection(g, selection):
    normal = g.node("GeometryNodeInputNormal").outputs[0]
    _, _, vi, ci, _, sc, _ = _corner_fields(g, selection, normal)
    count = g.accum("CORNER", "FLOAT", vi, g.switch("FLOAT", sc, 0, 1))
    return g.math("GREATER_THAN", g.at("CORNER", "FLOAT", count, ci), 0)


def _normal_shell(g, selection, face_normal):
    _, _, vi, ci, angle, sc, fn = _corner_fields(g, selection, face_normal)
    mask = g.switch("FLOAT", sc, 0, 1)
    weighted = g.accum("CORNER", "FLOAT_VECTOR", vi, g.scale(fn, g.math("MULTIPLY", angle, mask)))
    normal = g.norm(g.at("CORNER", "FLOAT_VECTOR", weighted, ci))
    nc = g.at("POINT", "FLOAT_VECTOR", normal, vi)
    absdot = g.math("ABSOLUTE", g.dot(nc, fn))
    reciprocal = g.switch("FLOAT", g.math("LESS_THAN", absdot, 1e-8), g.math("DIVIDE", 1, absdot), 1)
    pos = g.node("GeometryNodeInputPosition").outputs[0]
    idx = g.node("GeometryNodeInputIndex").outputs[0]
    lengths = []
    for offset in (-1, 1):
        neighbour = g.node("GeometryNodeOffsetCornerInFace")
        g.link(idx, neighbour.inputs["Corner Index"])
        neighbour.inputs["Offset"].default_value = offset
        position = g.at("CORNER", "FLOAT_VECTOR", pos, neighbour.outputs["Corner Index"])
        lengths.append(g.length(g.vec("SUBTRACT", position, pos)))
    degenerate = g.math("COMPARE", g.math("MULTIPLY", *lengths), 0)
    zero_angle = g.switch("FLOAT", g.math("GREATER_THAN", g.math("ADD", *lengths), 0), 0, 1.0471975511965976)
    shell_angle = g.switch("FLOAT", degenerate, angle, zero_angle)
    weight = g.math("MULTIPLY", shell_angle, mask)
    numerator = g.accum("CORNER", "FLOAT", vi, g.math("MULTIPLY", weight, reciprocal))
    denominator = g.accum("CORNER", "FLOAT", vi, weight)
    a = g.at("CORNER", "FLOAT", numerator, ci)
    b = g.at("CORNER", "FLOAT", denominator, ci)
    shell = g.switch("FLOAT", g.math("GREATER_THAN", b, 0), 1, g.math("DIVIDE", a, b))
    return normal, shell


def build(group_name=GROUP_NAME, nt=None):
    if not is_supported():
        raise RuntimeError("Extrude Faces Along Normals requires Blender 5.2+")
    if nt is None:
        nt = bpy.data.node_groups.new(group_name, "GeometryNodeTree")
    else:
        nt.nodes.clear()
    specs = [("Geometry", "Geometry", None), ("Selection", "Bool", True),
             ("Offset", "Float", .1), ("Flip Normals", "Bool", False),
             ("Offset Even", "Bool", False)]
    sockets = _interface(nt, specs, [("Geometry", "Geometry"), ("Top", "Bool"), ("Side", "Bool")])
    sockets[("Offset", "INPUT")].subtype = "DISTANCE"
    sockets[("Selection", "INPUT")].description = "Face region selection"
    sockets[("Flip Normals", "INPUT")].description = "Reverse generated side faces only"
    sockets[("Offset Even", "INPUT")].description = "Use selected-face angle-weighted shell compensation"
    nt.description = "Extrude a face region along its selected-face vertex normals"
    g = _Graph(nt)
    inputs = g.node("NodeGroupInput").outputs
    pos = g.node("GeometryNodeInputPosition").outputs[0]
    normal_input = g.node("GeometryNodeInputNormal").outputs[0]
    idx = g.node("GeometryNodeInputIndex").outputs[0]
    original, attrs = g.capture(inputs["Geometry"], "FACE", selected=("BOOLEAN", inputs["Selection"]), fn=("VECTOR", normal_input))
    selection, face_normal = attrs["selected"], attrs["fn"]
    original, attrs = g.capture(original, "POINT", touched=("BOOLEAN", _point_selection(g, selection)), source_id=("INT", idx))
    touched, source_id = attrs["touched"], attrs["source_id"]
    ci, _, _, _, _, selected_corner, _ = _corner_fields(g, selection, face_normal)
    edges = g.node("GeometryNodeEdgesOfCorner")
    g.link(ci, edges.inputs["Corner Index"])
    edge = edges.outputs["Next Edge Index"]
    selected_count = g.accum("CORNER", "FLOAT", edge, g.switch("FLOAT", selected_corner, 0, 1))
    all_count = g.accum("CORNER", "FLOAT", edge, 1)
    mixed = g.boolean("AND", g.math("GREATER_THAN", selected_count, 0), g.math("LESS_THAN", selected_count, all_count))
    delete_original = g.math("GREATER_THAN", g.stat(original, "CORNER", g.switch("FLOAT", mixed, 0, 1)), 0)
    ex = g.node("GeometryNodeExtrudeMesh", mode="FACES")
    ex.inputs["Individual"].default_value = False
    ex.inputs["Offset"].default_value = (0, 0, 0)
    ex.inputs["Offset Scale"].default_value = 0
    g.link(original, ex.inputs["Mesh"])
    g.link(selection, ex.inputs["Selection"])
    extruded, markers = g.capture(ex.outputs["Mesh"], "FACE", top=("BOOLEAN", ex.outputs["Top"]), side=("BOOLEAN", ex.outputs["Side"]))
    top, side = markers["top"], markers["side"]
    extruded, points = g.capture(extruded, "POINT", top_point=("BOOLEAN", _point_selection(g, top)))
    bottom = g.separate(original, "FACE", selection)
    flip = g.node("GeometryNodeFlipFaces")
    g.link(bottom, flip.inputs["Mesh"])
    join = g.node("GeometryNodeJoinGeometry")
    g.link(extruded, join.inputs["Geometry"])
    g.link(flip.outputs["Mesh"], join.inputs["Geometry"])
    # Encode bottom identity in a temporary integer lattice before welding.
    # Never merge distinct source IDs, even with zero Offset or coincident positions.
    joined, frozen = g.capture(join.outputs["Geometry"], "POINT", position=("VECTOR", pos))
    identity = g.combine((g.math("MODULO", source_id, 512),
                          g.math("MODULO", g.math("FLOOR", g.math("DIVIDE", source_id, 512)), 512),
                          g.math("FLOOR", g.math("DIVIDE", source_id, 262144))))
    encoded = g.setpos(joined, identity)
    merge = g.node("GeometryNodeMergeByDistance")
    merge.inputs["Distance"].default_value = .1
    g.link(encoded, merge.inputs["Geometry"])
    g.link(g.boolean("AND", touched, g.boolean("NOT", points["top_point"])), merge.inputs["Selection"])
    restored = g.setpos(merge.outputs["Geometry"], frozen["position"])
    geometry = g.switch("GEOMETRY", delete_original, restored, extruded)
    face_no = g.switch("VECTOR", side, normal_input, (0, 0, 0))
    normal, shell = _normal_shell(g, top, face_no)
    selected = _point_selection(g, top)
    geometry, frozen = g.capture(geometry, "POINT", position=("VECTOR", pos), selected=("BOOLEAN", selected), normal=("VECTOR", normal), shell=("FLOAT", shell))
    amount = g.math("MULTIPLY", inputs["Offset"], g.switch("FLOAT", inputs["Offset Even"], 1, frozen["shell"]))
    geometry = g.setpos(geometry, g.vec("ADD", frozen["position"], g.scale(frozen["normal"], amount)), frozen["selected"])
    flip = g.node("GeometryNodeFlipFaces")
    g.link(geometry, flip.inputs["Mesh"])
    g.link(g.boolean("AND", side, inputs["Flip Normals"]), flip.inputs["Selection"])
    out = g.node("NodeGroupOutput")
    g.link(flip.outputs["Mesh"], out.inputs["Geometry"])
    g.link(top, out.inputs["Top"])
    g.link(side, out.inputs["Side"])
    nt["bi1mcs_version"] = ".".join(map(str, VERSION))
    nt["bi1mcs_tool"] = GROUP_NAME
    if "bi1mcs_dissolve_integrated" in nt:
        del nt["bi1mcs_dissolve_integrated"]
    return nt


def get_or_create_group(name=GROUP_NAME):
    nt = bpy.data.node_groups.get(name)
    if nt is None and name == GROUP_NAME:
        legacy = bpy.data.node_groups.get("Extrude")
        if legacy is not None and legacy.get("bi1mcs_version") is not None:
            nt = legacy
            nt.name = name
            for tree in bpy.data.node_groups:
                for node in tree.nodes:
                    if node.bl_idname == 'GeometryNodeGroup' and node.node_tree == nt and node.label == 'Extrude':
                        node.label = GROUP_NAME
    if nt is None:
        return build(name)
    if nt.bl_idname != "GeometryNodeTree":
        raise TypeError("Existing group is not a GeometryNodeTree")
    if nt.get("bi1mcs_version") != ".".join(map(str, VERSION)):
        return build(name, nt)
    return nt
