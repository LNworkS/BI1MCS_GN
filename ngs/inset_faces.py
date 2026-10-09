# -*- coding: utf-8 -*-
"""BI1MCS :: builder for the "Inset Faces" Geometry-Nodes node group.

Reproduces Blender's Edit-Mode *Inset Faces* (bmesh.ops.inset_region /
bmesh.ops.inset_individual) with Geometry Nodes, since GN has no native Inset.

Derived from Blender main:
  source/blender/bmesh/operators/bmo_inset.cc
  source/blender/bmesh/intern/bmesh_query.cc
  source/blender/blenlib/intern/math_geom_inline.cc

Topology: `Extrude Mesh` (FACES, Offset Scale 0, Individual = input) reproduces
the operator's topology exactly - the extruded "Top" faces are the inset region,
the "Side" quads are the inset ring.  The rim vertices the operator would
never split (Boundary off / open mesh) are welded back with `Merge by
Distance`.

Math (region mode)
------------------
  region-boundary edge = edge with exactly one region (selected) face, plus
  mesh-boundary edges of the region when Boundary is on and Outset is off.
  no_e = normalize(cross(face_normal, edge_dir_in_loop_winding))  (points in)
  per vertex: C = #incident boundary edges, S = sum(no_e), L = sum(len_e)
      C == 0 ................ tvec = 0
      C == 1 ................ tvec = normalize(S)
      C >= 2, Even .......... tvec = 2*S/|S|^2
      C >= 2, not Even ...... tvec = normalize(S)
      C >= 2, Edge Rail ..... tvec = normalize(co_other - v); co_other is
                              the far end of the edge shared by the two
                              tagged edges' faces (bmo_inset.cc 932..956)
      C == 1, Even .......... tvec = flush_dir / |dot(normalize(S), flush_dir)|
  tvec *= Thickness * (Relative ? (C >= 2 ? L/2 : L) : 1)
  Depth:  vno = normalize(sum of region face normals) (original vertex normal
          when nothing was accumulated / for Outset)
          disp = vno * Depth * rel * (Even ? shell_factor : 1)

NOTE: requires Blender 5.2+ - it relies on `Extrude Mesh`'s `Top` / `Side`
outputs being boolean selections instead of geometry.
"""
import bpy

PFX = "bf_"
MIN_BLENDER = (5, 2, 0)


def is_supported():
    return tuple(bpy.app.version) >= MIN_BLENDER


# ------------------------------------------------------------------ helpers
def _n(nt, idname, loc, **props):
    nd = nt.nodes.new(idname)
    nd.location = loc
    for k, v in props.items():
        setattr(nd, k, v)
    return nd


def _l(nt, o, i):
    return nt.links.new(o, i)


def _sock(v):
    return hasattr(v, "node") or hasattr(v, "bl_idname")


def _math(nt, loc, op, a, b=None, c=None):
    nd = _n(nt, "ShaderNodeMath", loc, operation=op)
    for i, v in enumerate((a, b, c)):
        if v is None:
            continue
        if _sock(v):
            _l(nt, v, nd.inputs[i])
        else:
            nd.inputs[i].default_value = float(v)
    return nd.outputs[0]


def _vm(nt, loc, op, a, b=None):
    nd = _n(nt, "ShaderNodeVectorMath", loc, operation=op)
    for i, v in enumerate((a, b)):
        if v is None:
            continue
        if _sock(v):
            _l(nt, v, nd.inputs[i])
        else:
            nd.inputs[i].default_value = v
    return nd.outputs["Vector"]


def _scale(nt, loc, vec, fac):
    nd = _n(nt, "ShaderNodeVectorMath", loc, operation="SCALE")
    _l(nt, vec, nd.inputs[0])
    _l(nt, fac, nd.inputs["Scale"])
    return nd.outputs["Vector"]


def _dot(nt, loc, a, b):
    nd = _n(nt, "ShaderNodeVectorMath", loc, operation="DOT_PRODUCT")
    _l(nt, a, nd.inputs[0]); _l(nt, b, nd.inputs[1])
    return nd.outputs["Value"]


def _len(nt, loc, a):
    nd = _n(nt, "ShaderNodeVectorMath", loc, operation="LENGTH")
    _l(nt, a, nd.inputs[0])
    return nd.outputs["Value"]


def _normv(nt, loc, a):
    return _vm(nt, loc, "NORMALIZE", a)


def _cmp(nt, loc, dtype, op, a, b):
    nd = _n(nt, "FunctionNodeCompare", loc, data_type=dtype, operation=op)
    for s, v in (("A", a), ("B", b)):
        if _sock(v):
            _l(nt, v, nd.inputs[s])
        else:
            nd.inputs[s].default_value = v
    return nd.outputs["Result"]


def _bool(nt, loc, op, a, b=None):
    nd = _n(nt, "FunctionNodeBooleanMath", loc, operation=op)
    _l(nt, a, nd.inputs["Boolean"])
    if b is not None:
        _l(nt, b, nd.inputs["Boolean_001"])
    return nd.outputs["Boolean"]


def _sw(nt, loc, itype, cond, false, true):
    nd = _n(nt, "GeometryNodeSwitch", loc, input_type=itype)
    _l(nt, cond, nd.inputs["Switch"])
    for s, v in (("False", false), ("True", true)):
        if _sock(v):
            _l(nt, v, nd.inputs[s])
        else:
            nd.inputs[s].default_value = v
    return nd.outputs["Output"]


def _val(nt, loc, v):
    nd = _n(nt, "ShaderNodeValue", loc)
    nd.outputs[0].default_value = v
    return nd.outputs[0]


def _fai(nt, loc, domain, dtype, value, index):
    nd = _n(nt, "GeometryNodeFieldAtIndex", loc, domain=domain, data_type=dtype)
    _l(nt, value, nd.inputs["Value"])
    _l(nt, index, nd.inputs["Index"])
    return nd.outputs["Value"]


def _acc(nt, loc, domain, dtype, group_id, value):
    nd = _n(nt, "GeometryNodeAccumulateField", loc, domain=domain, data_type=dtype)
    _l(nt, value, nd.inputs["Value"])
    _l(nt, group_id, nd.inputs["Group ID"])
    return nd.outputs["Total"]


def _store(nt, loc, geo, name, dtype, domain, value):
    nd = _n(nt, "GeometryNodeStoreNamedAttribute", loc, data_type=dtype, domain=domain)
    nd.inputs["Name"].default_value = name
    _l(nt, geo, nd.inputs["Geometry"])
    _l(nt, value, nd.inputs["Value"])
    return nd.outputs["Geometry"]


def _named(nt, loc, name, dtype):
    nd = _n(nt, "GeometryNodeInputNamedAttribute", loc, data_type=dtype)
    nd.inputs["Name"].default_value = name
    return nd.outputs["Attribute"]


def _rm(nt, loc, geo, name):
    nd = _n(nt, "GeometryNodeRemoveAttribute", loc)
    nd.inputs["Name"].default_value = name
    _l(nt, geo, nd.inputs["Geometry"])
    return nd.outputs["Geometry"]


def _setpos(nt, loc, geo, selsock, offset):
    nd = _n(nt, "GeometryNodeSetPosition", loc)
    _l(nt, geo, nd.inputs["Geometry"])
    _l(nt, selsock, nd.inputs["Selection"])
    _l(nt, offset, nd.inputs["Offset"])
    return nd.outputs["Geometry"]


def _one(nt, loc):
    return _math(nt, loc, "ADD", _val(nt, (loc[0] - 200, loc[1] - 120), 1.0),
                 _val(nt, (loc[0] - 200, loc[1] - 180), 0.0))


# ------------------------------------------------------------------ build
GROUP_NAME = "Inset Faces"
# bump together with bl_info["version"]; a group carrying a different tag
# was built by an older release and gets rebuilt in place on next use.
VERSION = (0, 1, 2)
_VER_KEY = "bi1mcs_version"


def build(group_name=GROUP_NAME, nt=None):
    """Create the "Inset Faces" geometry-node group and return it.

    Pass an existing tree as *nt* to rebuild it in place: the group keeps its
    identity, so every modifier that already points at it keeps working
    instead of being orphaned by a fresh group of the same name.
    """
    if nt is None:
        nt = bpy.data.node_groups.new(group_name, "GeometryNodeTree")
    else:
        nt.nodes.clear()
        try:
            nt.interface.clear()
        except AttributeError:
            for _it in list(nt.interface.items_tree):
                nt.interface.remove(_it)

    s = nt.interface.new_socket
    s(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    sg = s(name="Selection", in_out="INPUT", socket_type="NodeSocketBool")
    sg.default_value = True
    for attr, val in (("default_input", "FIELD"), ("structure_type", "AUTO")):
        try:
            setattr(sg, attr, val)
        except Exception:
            pass
    st = s(name="Thickness", in_out="INPUT", socket_type="NodeSocketFloat")
    st.default_value = 0.01
    st.min_value = -1e6
    sd = s(name="Depth", in_out="INPUT", socket_type="NodeSocketFloat")
    sd.default_value = 0.0
    se = s(name="Even Offset", in_out="INPUT", socket_type="NodeSocketBool")
    se.default_value = True
    sr = s(name="Relative Offset", in_out="INPUT", socket_type="NodeSocketBool")
    sr.default_value = False
    si = s(name="Individual", in_out="INPUT", socket_type="NodeSocketBool")
    si.default_value = False
    sb = s(name="Boundary", in_out="INPUT", socket_type="NodeSocketBool")
    sb.default_value = True
    so = s(name="Outset", in_out="INPUT", socket_type="NodeSocketBool")
    so.default_value = False
    sr2 = s(name="Edge Rail", in_out="INPUT", socket_type="NodeSocketBool")
    sr2.default_value = False
    s(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    s(name="Inset Faces", in_out="OUTPUT", socket_type="NodeSocketBool",
      description="the new inner faces created by the inset")
    s(name="Ring", in_out="OUTPUT", socket_type="NodeSocketBool",
      description="the ring faces between the old and the new boundary")

    gi = _n(nt, "NodeGroupInput", (0, 0))
    geo = gi.outputs["Geometry"]
    SEL = gi.outputs["Selection"]
    THK = gi.outputs["Thickness"]
    DEP = gi.outputs["Depth"]
    EVEN = gi.outputs["Even Offset"]
    REL = gi.outputs["Relative Offset"]
    IND = gi.outputs["Individual"]
    BND = gi.outputs["Boundary"]
    OUT = gi.outputs["Outset"]
    ERAIL = gi.outputs["Edge Rail"]

    X0 = 300
    EPS = _val(nt, (X0, -1400), 1e-8)
    ONE = _val(nt, (X0, -1460), 1.0)
    TWO = _val(nt, (X0, -1520), 2.0)
    ZERO = _val(nt, (X0, -1580), 0.0)
    VZERO = (0.0, 0.0, 0.0)

    use_bnd = _bool(nt, (X0, -1660), "AND", BND, _bool(nt, (X0 - 200, -1660), "NOT", OUT))

    idx = _n(nt, "GeometryNodeInputIndex", (X0, -900))
    posf = _n(nt, "GeometryNodeInputPosition", (X0, -1000))
    nrmf = _n(nt, "GeometryNodeInputNormal", (X0, -1100))
    px = idx.outputs["Index"]

    # ---------------- per-corner: faces, neighbours, edge tangents ---------
    X = X0 + 300
    foc = _n(nt, "GeometryNodeFaceOfCorner", (X, 300))
    _l(nt, px, foc.inputs["Corner Index"])
    face_i = foc.outputs["Face Index"]
    voc = _n(nt, "GeometryNodeVertexOfCorner", (X, 180))
    _l(nt, px, voc.inputs["Corner Index"])
    vert_i = voc.outputs["Vertex Index"]

    # region selection as a FACE field (for Extrude Mesh)
    reg_face = _sw(nt, (X, -1800), "BOOLEAN", OUT, false=SEL,
                   true=_bool(nt, (X - 200, -1900), "NOT", SEL))

    sel_c = _fai(nt, (X, 420), "FACE", "BOOLEAN", SEL, face_i)
    reg_c = _sw(nt, (X + 200, 420), "BOOLEAN", OUT,
                false=sel_c, true=_bool(nt, (X, 520), "NOT", sel_c))
    fno_c = _fai(nt, (X + 200, 300), "FACE", "FLOAT_VECTOR",
                 nrmf.outputs["Normal"], face_i)
    regf = _sw(nt, (X + 200, 640), "FLOAT", reg_c, false=ZERO, true=ONE)
    self_f = _sw(nt, (X + 200, 760), "FLOAT", sel_c, false=ZERO, true=ONE)

    nxt = _n(nt, "GeometryNodeOffsetCornerInFace", (X, -60))
    _l(nt, px, nxt.inputs["Corner Index"])
    nxt.inputs["Offset"].default_value = 1
    prvc = _n(nt, "GeometryNodeOffsetCornerInFace", (X, -140))
    _l(nt, px, prvc.inputs["Corner Index"])
    prvc.inputs["Offset"].default_value = -1
    ci_n = nxt.outputs["Corner Index"]
    ci_p = prvc.outputs["Corner Index"]

    pv = posf.outputs["Position"]
    pv_n = _fai(nt, (X + 400, -60), "CORNER", "FLOAT_VECTOR", posf.outputs["Position"], ci_n)
    pv_p = _fai(nt, (X + 400, -160), "CORNER", "FLOAT_VECTOR", posf.outputs["Position"], ci_p)

    d_this = _vm(nt, (X + 700, -60), "SUBTRACT", pv_n, pv)
    d_prev = _vm(nt, (X + 700, -160), "SUBTRACT", pv, pv_p)
    no_this = _normv(nt, (X + 1000, -60),
                     _vm(nt, (X + 850, -60), "CROSS_PRODUCT", fno_c, d_this))
    len_this = _len(nt, (X + 850, -240), d_this)
    len_prev = _len(nt, (X + 850, -300), d_prev)
    no_prev = _normv(nt, (X + 1000, -360),
                     _vm(nt, (X + 850, -420), "CROSS_PRODUCT", fno_c, d_prev))
    dir_next = _normv(nt, (X + 1000, -160), d_this)
    dir_prev = _normv(nt, (X + 1000, -260),
                      _vm(nt, (X + 850, -500), "SUBTRACT", pv_p, pv))

    eoc = _n(nt, "GeometryNodeEdgesOfCorner", (X, -460))
    _l(nt, px, eoc.inputs["Corner Index"])
    e_this = eoc.outputs["Next Edge Index"]

    # ---------------- per-edge tagging (accumulate over e_this) ------------
    X = X0 + 1300
    onek = _val(nt, (X - 200, -1100), 1.0)
    vs1_5 = _val(nt, (X - 200, 900), 1.5)
    onef = _val(nt, (X - 200, 820), 1.0)
    h0f = _val(nt, (X - 200, 1000), 0.5)
    n_reg = _acc(nt, (X, 700), "CORNER", "FLOAT", e_this, regf)
    n_all = _acc(nt, (X, 820), "CORNER", "FLOAT", e_this, onek)
    X += 300
    nreg1 = _cmp(nt, (X, 700), "FLOAT", "EQUAL", n_reg, onef)
    ge2 = _cmp(nt, (X, 780), "FLOAT", "GREATER_THAN", n_all, vs1_5)
    nall1 = _cmp(nt, (X, 880), "FLOAT", "EQUAL", n_all, onef)
    mixed = _bool(nt, (X + 240, 700), "AND", nreg1, ge2)
    bndreg = _bool(nt, (X + 240, 860), "AND", nreg1, nall1)
    tag_e = _bool(nt, (X + 480, 780), "OR", mixed,
                  _bool(nt, (X + 400, 900), "AND", use_bnd, bndreg))

    # Per-face accumulate, kept for the global "does this mesh hold any tagged
    # edge at all" flag below (bmesh: with edge_info_len == 0 every following
    # loop is a no-op, so the mesh must come back completely untouched).
    tagff = _sw(nt, (X + 700, 780), "FLOAT", tag_e, false=ZERO, true=ONE)
    # global "the mesh holds at least one tagged edge" flag, used by depth /
    # relative-offset (the lazy length fallback of bmo_inset.cc stays zero when
    # no edge was tagged at all).
    tagg = _acc(nt, (X + 900, 1080), "CORNER", "FLOAT",
                _val(nt, (X + 700, 1180), 0.0), tagff)
    cof = _n(nt, "GeometryNodeCornersOfFace", (X + 900, 940))
    _l(nt, px, cof.inputs["Face Index"])
    anyf_c = cof.outputs["Corner Index"]
    X += 700
    contrib = _sw(nt, (X, 780), "FLOAT",
                  _bool(nt, (X - 200, 700), "AND", tag_e, reg_c), false=ZERO, true=ONE)
    contrib_b = _cmp(nt, (X + 200, 780), "FLOAT", "GREATER_THAN", contrib, h0f)
    contrib_prev = _fai(nt, (X + 400, 860), "CORNER", "BOOLEAN", contrib_b, ci_p)
    contrib_pf = _fai(nt, (X + 400, 960), "CORNER", "FLOAT", contrib, ci_p)
    contrib_any = _math(nt, (X + 600, 780), "ADD", contrib, contrib_pf)

    # ---------------- per-vertex accumulation -----------------------------
    X = X0 + 2600
    onem = _val(nt, (X - 200, -700), 1.0)
    vno_c_this = _scale(nt, (X - 300, 100), no_this, contrib)
    vno_c_prev = _scale(nt, (X - 300, 160), no_prev, contrib_pf)
    C_v = _acc(nt, (X, 0), "CORNER", "FLOAT", vert_i, contrib_any)
    S_v = _acc(nt, (X, 100), "CORNER", "FLOAT_VECTOR", vert_i,
               _vm(nt, (X - 140, 100), "ADD", vno_c_this, vno_c_prev))
    L_v = _acc(nt, (X, 220), "CORNER", "FLOAT", vert_i,
               _math(nt, (X - 140, 220), "ADD",
                     _math(nt, (X - 300, 220), "MULTIPLY", len_this, contrib),
                     _math(nt, (X - 300, 280), "MULTIPLY", len_prev, contrib_pf)))
    N_v = _acc(nt, (X, 320), "CORNER", "FLOAT_VECTOR", vert_i,
               _scale(nt, (X - 140, 320), fno_c, contrib_any))
    FL_v = _acc(nt, (X, 440), "CORNER", "FLOAT_VECTOR", vert_i,
                _vm(nt, (X - 260, 440), "ADD",
                    _scale(nt, (X - 420, 440), dir_prev, contrib),
                    _scale(nt, (X - 420, 520), dir_next,
                           _sw(nt, (X - 560, 520), "FLOAT", contrib_prev, false=ZERO, true=onem))))
    SEL_v = _acc(nt, (X, 640), "CORNER", "FLOAT", vert_i, self_f)
    LEN_v = _acc(nt, (X, 740), "CORNER", "FLOAT", vert_i,
                 _math(nt, (X - 200, 740), "MULTIPLY", len_this, regf))
    # average length over *all* incident edges (used by the depth fallback)
    LENA_v = _acc(nt, (X, 860), "CORNER", "FLOAT", vert_i, len_this)
    CNTA_v = _acc(nt, (X, 960), "CORNER", "FLOAT", vert_i, onek)

    # ---------------- lift corner group totals to POINT -------------------
    # `CornersOfVertex(Index)` is only correct when this subtree is evaluated in
    # the *point* domain (there `Index` is the vertex id, so it really is "some
    # corner of my vertex").  Every reader below feeds the displacement chain,
    # which ends in Set Position's point-domain Offset, so this is the right
    # form here.  A reader that is consumed in the *corner* domain must take the
    # Accumulate Field output directly instead - see the rail block.
    cov = _n(nt, "GeometryNodeCornersOfVertex", (X + 260, 300))
    _l(nt, px, cov.inputs["Vertex Index"])
    anyc = cov.outputs["Corner Index"]

    def pt(loc, dtype, value):
        return _fai(nt, loc, "CORNER", dtype, value, anyc)

    X += 500
    C = pt((X, 0), "FLOAT", C_v)
    S = pt((X, 100), "FLOAT_VECTOR", S_v)
    L = pt((X, 220), "FLOAT", L_v)
    N = pt((X, 320), "FLOAT_VECTOR", N_v)
    FL = pt((X, 440), "FLOAT_VECTOR", FL_v)
    IS_SEL = _cmp(nt, (X, 640), "FLOAT", "GREATER_THAN", pt((X - 200, 640), "FLOAT", SEL_v), _val(nt, (X - 400, 760), 0.5))
    LENv = pt((X, 740), "FLOAT", LEN_v)
    LENA = pt((X, 860), "FLOAT", LENA_v)
    CNTA = pt((X, 960), "FLOAT", CNTA_v)
    ANYTAG = _cmp(nt, (X, 1080), "FLOAT", "GREATER_THAN",
                  pt((X - 200, 1080), "FLOAT", tagg),
                  _val(nt, (X - 400, 1200), 0.5))

    # ---------------- thickness -------------------------------------------
    h0 = _val(nt, (X - 200, -1200), 0.5)
    C_gt0 = _cmp(nt, (X + 200, -100), "FLOAT", "GREATER_THAN", C, h0)
    C_ge2 = _cmp(nt, (X + 200, -160), "FLOAT", "GREATER_THAN", C, _val(nt, (X - 100, -220), 1.5))
    S2 = _dot(nt, (X + 200, 60), S, S)
    X += 400
    Slen = _math(nt, (X, 60), "SQRT", _math(nt, (X - 160, 120), "MAXIMUM", S2, EPS))
    Sun = _scale(nt, (X + 200, 0), S, _math(nt, (X + 200, -60), "DIVIDE", ONE,
                                            _math(nt, (X - 0, -120), "MAXIMUM", Slen, EPS)))
    ev_ge2 = _scale(nt, (X + 400, 120), S,
                    _math(nt, (X + 400, 40), "DIVIDE", TWO,
                          _math(nt, (X + 240, -20), "MAXIMUM", S2, EPS)))
    FLun = _normv(nt, (X + 200, 260), FL)
    dcos = _math(nt, (X + 400, 320), "MAXIMUM",
                 _math(nt, (X + 400, 260), "ABSOLUTE", _dot(nt, (X + 200, 200), Sun, FLun)), EPS)
    ev_eq1 = _scale(nt, (X + 600, 240), FLun, _math(nt, (X + 600, 320), "DIVIDE", ONE, dcos))
    X += 900
    tvec_even = _sw(nt, (X, 60), "VECTOR", C_ge2, false=ev_eq1, true=ev_ge2)
    tvec_plain = _sw(nt, (X + 200, 60), "VECTOR", EVEN, false=Sun, true=tvec_even)
    tvec_plain = _sw(nt, (X + 400, 60), "VECTOR", C_gt0, false=VZERO, true=tvec_plain)

    # ---------------- edge rail (bmo_inset.cc lines 932..956) -------------
    # bmesh takes the branch when the vertex carries >= 2 tagged edges, the
    # first two belong to *different* faces, and those two faces share an edge
    # through the vertex.  It detects that with `l_other_a->v == l_other_b->v`,
    # i.e. the far ends of each face's *other* edge at v agree.  The offset then
    # runs along that shared edge instead of between the two edge tangents.
    #
    # Corner-domain translation.  A region-boundary edge is registered at the
    # corner that starts it (`contrib`) or, when the face loop traverses it the
    # other way, as `contrib_prev` of that corner.  So for a corner c = (f, v):
    #
    #   contrib_any(c) = A(c) + B(c)   how many of f's two edges at v are
    #                                  region-boundary edges
    #   solo(c)        = contrib_any(c) == 1   -> f's *other* edge at v is the
    #                                  candidate shared edge
    #   d_far(c)       = A ? prev_vertex - v : next_vertex - v
    #                                  direction to that edge's far end
    #
    # The two corners at v are the two faces of one shared edge exactly when
    # both are solo and their `d_far` coincide.  So the branch fires when every
    # solo corner at v has the same `d_far`, and a sum-only test can express
    # that as zero variance:
    #   n = sum(solo),  S = sum(d_far),  Q = sum(|d_far|^2)
    #   Q - |S|^2 / n == 0   <=>   all `d_far` equal
    # `d_far` is measured from the vertex, so "equal" is bitwise equal and the
    # variance lands on exactly 0 for n == 2 (the common case).
    XR = X0 + 6200
    e_prev = eoc.outputs["Previous Edge Index"]
    solo_f = _cmp(nt, (XR, 60), "FLOAT", "EQUAL", contrib_any, ONE)
    far_v = _sw(nt, (XR, 200), "VECTOR", contrib_b, false=pv_n, true=pv_p)
    d_far = _vm(nt, (XR + 200, 200), "SUBTRACT", far_v, pv)
    solo_one = _sw(nt, (XR + 200, 60), "FLOAT", solo_f, false=ZERO, true=ONE)
    # NB: these three are consumed by corner-domain maths only, so they must be
    # the raw Accumulate Field outputs (which are already corner fields, constant
    # per vertex).  Wrapping them in `pt()` would re-sample them through the
    # *point*-domain `CornersOfVertex` lookup and, evaluated in the corner
    # domain, read a stranger vertex's total.
    n_solo = _acc(nt, (XR + 460, -60), "CORNER", "FLOAT", vert_i, solo_one)
    s_dv = _acc(nt, (XR + 460, 80), "CORNER", "FLOAT_VECTOR", vert_i,
                _scale(nt, (XR + 260, 200), d_far, solo_one))
    q_dv = _acc(nt, (XR + 460, 240), "CORNER", "FLOAT", vert_i,
                _math(nt, (XR + 260, 360), "MULTIPLY", solo_one,
                      _dot(nt, (XR + 120, 360), d_far, d_far)))
    var_far = _math(nt, (XR + 680, 360), "SUBTRACT", q_dv,
                    _math(nt, (XR + 680, 480), "DIVIDE",
                          _dot(nt, (XR + 560, 480), s_dv, s_dv),
                          _math(nt, (XR + 560, 600), "MAXIMUM", n_solo, ONE)))
    # relative threshold, so the test keeps working for meshes far from the
    # origin or very small (an absolute epsilon would not be scale invariant)
    var_thr = _math(nt, (XR + 680, 540), "MULTIPLY", q_dv,
                    _val(nt, (XR + 560, 660), 1e-6))
    trig_b = _bool(nt, (XR + 900, 160), "AND", solo_f,
                   _bool(nt, (XR + 900, 40), "AND",
                         _cmp(nt, (XR + 700, 40), "FLOAT", "GREATER_THAN",
                              n_solo, vs1_5),
                         _cmp(nt, (XR + 700, 540), "FLOAT", "LESS_THAN",
                              var_far, var_thr)))

    rail_c = _sw(nt, (XR + 1100, 360), "VECTOR", trig_b, false=VZERO,
                 true=_normv(nt, (XR + 1100, 460), d_far))
    RAIL_S_v = _acc(nt, (XR + 1320, 360), "CORNER", "FLOAT_VECTOR", vert_i, rail_c)
    RAIL_N_v = _acc(nt, (XR + 1320, 540), "CORNER", "FLOAT", vert_i,
                    _sw(nt, (XR + 1100, 540), "FLOAT", trig_b, false=ZERO, true=ONE))

    RAIL_S = pt((XR + 1540, 360), "FLOAT_VECTOR", RAIL_S_v)
    RAIL_N = pt((XR + 1540, 540), "FLOAT", RAIL_N_v)
    rail_on = _cmp(nt, (XR + 1740, 540), "FLOAT", "GREATER_THAN", RAIL_N, h0)
    rail_dir = _normv(nt, (XR + 1740, 360), RAIL_S)
    rd_c = _fai(nt, (XR + 1940, 360), "POINT", "FLOAT_VECTOR", rail_dir, vert_i)

    # `even` scales by the larger of the two edge angles: bmesh compares
    # |tvec - no|^2 (== 2 - 2*dot for unit vectors) and keeps the bigger one,
    # so the pick is the edge with the smaller *signed* dot.  Taken together
    # with `min(a, sum - a)` that is the minimum signed dot of the two tagged
    # edges.
    #
    # The corner domain registers a tagged edge at the corner that *starts* it,
    # so at a vertex whose two tagged edges are not consecutive in the same
    # face loop the second one only shows up as `contrib_prev` of another
    # corner.  It must be measured against the tangent of *its own* edge
    # (`no_prev`), exactly like S_v / L_v do above.
    marked_f = _cmp(nt, (XR + 1940, 60), "FLOAT", "GREATER_THAN", contrib_any, h0)
    dot_t = _dot(nt, (XR + 1940, 180), rd_c, no_this)
    dot_p = _dot(nt, (XR + 1940, 300), rd_c, no_prev)
    adot_v = _math(nt, (XR + 2140, 180), "ADD",
                   _math(nt, (XR + 2090, 120), "MULTIPLY", dot_t, contrib),
                   _math(nt, (XR + 2090, 240), "MULTIPLY", dot_p, contrib_pf))
    sum_adot_v = _acc(nt, (XR + 2340, 180), "CORNER", "FLOAT", vert_i, adot_v)
    # corner-domain consumer -> use the accumulate output directly (see above)
    sum_adot_c = sum_adot_v
    mn = _math(nt, (XR + 2760, 180), "MINIMUM", adot_v,
               _math(nt, (XR + 2640, 100), "SUBTRACT", sum_adot_c, adot_v))
    # only the corners that own one of the tagged edges may vote (their count
    # is C, which the division below normalises by)
    mn = _sw(nt, (XR + 2840, 180), "FLOAT", marked_f, false=ZERO, true=mn)
    mn_total_v = _acc(nt, (XR + 3040, 180), "CORNER", "FLOAT", vert_i, mn)
    min_adot = _math(nt, (XR + 3260, 180), "ABSOLUTE",
                     _math(nt, (XR + 3260, 100), "DIVIDE",
                           pt((XR + 3160, 180), "FLOAT", mn_total_v),
                           _math(nt, (XR + 3160, 100), "MAXIMUM", C, ONE)))
    # shell_v3v3_normalized_to_dist(): |dot| < SMALL_NUMBER -> 1.0 else 1/|dot|
    # (ref/math_geom_inline.cc:133).  A plain MAXIMUM(min_adot, EPS) clamp would
    # invert the *other* way round and blow the reciprocal up to ~1e8.
    scale_rail = _sw(nt, (XR + 3460, 180), "FLOAT",
                     _cmp(nt, (XR + 3360, 100), "FLOAT", "LESS_THAN", min_adot, EPS),
                     false=_math(nt, (XR + 3460, 260), "DIVIDE", ONE,
                                 _math(nt, (XR + 3460, 340), "MAXIMUM", min_adot, EPS)),
                     true=ONE)
    even_scale = _sw(nt, (XR + 3660, 180), "FLOAT", EVEN, false=ONE, true=scale_rail)
    rail_tvec = _scale(nt, (XR + 3860, 180), rail_dir, even_scale)
    use_rail = _bool(nt, (XR + 3860, 60), "AND", ERAIL, rail_on)
    tvec = _sw(nt, (XR + 4080, 60), "VECTOR", use_rail,
               false=tvec_plain, true=rail_tvec)

    relT = _sw(nt, (X + 200, 340), "FLOAT", C_ge2,
               false=L, true=_math(nt, (X, 340), "DIVIDE", L, TWO))
    relT = _sw(nt, (X + 400, 340), "FLOAT", REL, false=ONE, true=relT)
    DISP_T = _scale(nt, (X + 600, 60), tvec, _math(nt, (X + 600, 200), "MULTIPLY", THK, relT))

    # ---------------- depth ------------------------------------------------
    vno_orig = nrmf.outputs["Normal"]
    Nlen = _math(nt, (X - 300, 520), "SQRT",
                 _math(nt, (X - 460, 580), "MAXIMUM", _dot(nt, (X - 640, 460), N, N), EPS))
    Nu = _scale(nt, (X - 100, 460), N,
                _math(nt, (X - 100, 400), "DIVIDE", ONE,
                      _math(nt, (X - 260, 340), "MAXIMUM", Nlen, EPS)))
    vno = _sw(nt, (X + 100, 460), "VECTOR", C_gt0, false=vno_orig, true=Nu)
    vno = _sw(nt, (X + 300, 460), "VECTOR", OUT, false=vno, true=vno_orig)

    # fan mask for the shell factor
    fanm = _sw(nt, (X + 300, 700), "FLOAT", OUT, false=regf, true=ONE)
    vno_c = _fai(nt, (X + 500, 700), "POINT", "FLOAT_VECTOR", vno, vert_i)
    cosang = _math(nt, (X + 500, 860), "MULTIPLY",
                   _dot(nt, (X + 500, 800), dir_prev, dir_next), ONE)
    cosang = _math(nt, (X + 700, 860), "MAXIMUM", cosang, _val(nt, (X + 500, 980), -1.0))
    cosang = _math(nt, (X + 700, 940), "MINIMUM", cosang, _val(nt, (X + 500, 1040), 1.0))
    ang = _math(nt, (X + 880, 860), "ARCCOSINE", cosang)
    sinv = _math(nt, (X + 700, 700), "ABSOLUTE", _dot(nt, (X + 500, 640), vno_c, fno_c))
    sdist = _sw(nt, (X + 900, 700), "FLOAT",
                _cmp(nt, (X + 880, 640), "FLOAT", "LESS_THAN", sinv, _val(nt, (X + 700, 560), 1e-5)),
                false=_math(nt, (X + 880, 720), "DIVIDE", ONE,
                            _math(nt, (X + 880, 780), "MAXIMUM", sinv, EPS)),
                true=ONE)
    SH_a = _acc(nt, (X + 1100, 860), "CORNER", "FLOAT", vert_i,
                _math(nt, (X + 1100, 800), "MULTIPLY", ang,
                      _math(nt, (X + 1100, 920), "MULTIPLY", sdist, fanm)))
    SH_b = _acc(nt, (X + 1100, 1040), "CORNER", "FLOAT", vert_i,
                _math(nt, (X + 1100, 980), "MULTIPLY", ang, fanm))
    X += 1500
    sha = pt((X, 860), "FLOAT", SH_a)
    shb = pt((X, 1040), "FLOAT", SH_b)
    sf = _sw(nt, (X + 300, 940), "FLOAT",
             _cmp(nt, (X + 200, 880), "FLOAT", "LESS_THAN", shb, EPS),
             false=_math(nt, (X + 200, 1000), "DIVIDE", sha,
                         _math(nt, (X + 200, 1060), "MAXIMUM", shb, EPS)),
             true=ONE)

    # relative length for depth
    #   C >  0 -> mean length of the incident tagged edges        (L / C)
    #   C == 0 -> mean length over *all* incident edges, but only when the mesh
    #            actually contains a tagged edge; otherwise the lazy
    #            "length propagation" fallback (bmo_inset.cc 552..667) has
    #            nothing to seed from and yields 0 (= no depth at all).
    avgall = _math(nt, (X + 200, 640), "DIVIDE", LENA,
                   _math(nt, (X + 200, 700), "MAXIMUM", CNTA, EPS))
    avgall = _sw(nt, (X + 400, 640), "FLOAT", ANYTAG, false=ZERO, true=avgall)
    relD = _sw(nt, (X + 600, 640), "FLOAT", C_gt0, false=avgall,
               true=_math(nt, (X + 400, 540), "DIVIDE", L,
                          _math(nt, (X + 400, 460), "MAXIMUM", C, EPS)))
    relD = _sw(nt, (X + 800, 640), "FLOAT", REL, false=ONE, true=relD)
    sfm = _sw(nt, (X + 800, 900), "FLOAT", EVEN, false=ONE, true=sf)
    DISP_D = _scale(nt, (X + 800, 700), vno,
                    _math(nt, (X + 800, 840), "MULTIPLY",
                          _math(nt, (X + 700, 780), "MULTIPLY", DEP, relD), sfm))

    # which copy receives what
    #   Outset     : the unmoved copy keeps the depth; the moved copy does not.
    #                Vertices never touched by a tagged edge are not split by
    #                the operator at all, so *both* copies must end up at the
    #                exact same place (they get welded further down).
    #   not Outset : the moved (inset) copy carries the depth.
    DISP_TOP = _vm(nt, (X + 1300, 460), "ADD", DISP_T,
                   _sw(nt, (X + 1200, 560), "VECTOR", OUT, false=DISP_D,
                       true=_sw(nt, (X + 1100, 660), "VECTOR", C_gt0,
                                false=_sw(nt, (X + 1000, 760), "VECTOR", IS_SEL,
                                          false=VZERO, true=DISP_D),
                                true=VZERO)))
    zeroC = _cmp(nt, (X + 900, 200), "FLOAT", "GREATER_THAN", C, h0)
    # depth is applied to the vertices of the *selection* faces (`op->slots_in
    # "faces"`) once the operator is done:
    #   not Outset -> the face pointer now points at the inset face (= the Top
    #                 copy), so the unmoved copy keeps no depth at all.
    #   Outset     -> the face pointer still holds the original vertices, so the
    #                 unmoved copy carries the depth and the moved copy does not.
    DISP_ORIG = _sw(nt, (X + 1100, 200), "VECTOR", OUT, true=DISP_D,
                    false=_sw(nt, (X + 1000, 120), "VECTOR", zeroC,
                              false=DISP_D, true=VZERO))
    SELOUT = _sw(nt, (X + 1100, 20), "FLOAT", IS_SEL, false=ZERO, true=ONE)

    # ---------------- extrude ---------------------------------------------
    X += 1500
    # bmesh re-hooks the loop of *every selected* face at a split vertex, not
    # only the ones carrying a tagged edge (bmo_inset.cc 867/1094: the vertex is
    # separated first, then each selected fan is displaced).  A selected face can
    # reach a tagged vertex through two *untagged* edges - the folded "needle"
    # corner produced by a V-notch / spike pushed into an edge.  Such a face
    # carries no tagged edge of its own, but the operator still moves it, so it
    # has to join the extruded region.  Leaving it out welds it to the old
    # vertex and lets the walls along its two untagged edges survive as stray
    # triangles (visible as a flap hanging off the notch).
    # Provably safe to widen: both endpoints of a tagged edge have C > 0, so
    # after the widening every remaining region-boundary edge has C == 0 at both
    # ends and its wall degenerates away in the weld pass below.
    cvt = _sw(nt, (X - 1400, 160), "FLOAT",
              _fai(nt, (X - 1600, 160), "POINT", "BOOLEAN", C_gt0, vert_i),
              false=ZERO, true=ONE)
    vtf_n = _acc(nt, (X - 1100, 160), "CORNER", "FLOAT", face_i, cvt)
    face_vtagged = _cmp(nt, (X - 800, 200), "FLOAT", "GREATER_THAN",
                        _fai(nt, (X - 800, 100), "CORNER", "FLOAT", vtf_n, anyf_c), h0f)
    # Individual mode insets every selected face on its own (Boundary / Outset
    # do not apply there), otherwise only faces touching a tagged vertex.
    ex_sel = _sw(nt, (X, 260), "BOOLEAN", IND,
                 false=_bool(nt, (X - 100, 160), "AND", reg_face, face_vtagged),
                 true=SEL)
    g0 = _store(nt, (X, 0), geo, PFX + "dtop", "FLOAT_VECTOR", "POINT", DISP_TOP)
    g0 = _store(nt, (X, -140), g0, PFX + "dorig", "FLOAT_VECTOR", "POINT", DISP_ORIG)
    g0 = _store(nt, (X, -280), g0, PFX + "issel", "FLOAT", "POINT", SELOUT)
    g0 = _store(nt, (X, -420), g0, PFX + "c", "FLOAT", "POINT", C)

    ex = _n(nt, "GeometryNodeExtrudeMesh", (X + 300, -140))
    ex.inputs["Offset Scale"].default_value = 0.0
    _l(nt, g0, ex.inputs["Mesh"])
    _l(nt, ex_sel, ex.inputs["Selection"])
    _l(nt, IND, ex.inputs["Individual"])
    gm = ex.outputs["Mesh"]
    top_f = ex.outputs["Top"]
    side_f = ex.outputs["Side"]

    topk = _sw(nt, (X + 500, -400), "FLOAT", top_f, false=ZERO, true=ONE)
    topn = _acc(nt, (X + 700, -340), "CORNER", "FLOAT", vert_i, topk)
    is_top = _cmp(nt, (X + 900, -340), "FLOAT", "GREATER_THAN", topn,
                  _val(nt, (X + 700, -460), 0.5))

    dtop = _named(nt, (X + 700, -560), PFX + "dtop", "FLOAT_VECTOR")
    dorg = _named(nt, (X + 700, -660), PFX + "dorig", "FLOAT_VECTOR")
    isel = _cmp(nt, (X + 700, -760), "FLOAT", "GREATER_THAN",
                _named(nt, (X + 500, -760), PFX + "issel", "FLOAT"),
                _val(nt, (X + 500, -860), 0.5))

    X += 1100
    p1 = _setpos(nt, (X, -140), gm, is_top, dtop)
    keep_orig = _bool(nt, (X - 200, -300), "AND",
                      _bool(nt, (X - 400, -300), "NOT", is_top), isel)
    p2 = _setpos(nt, (X, -420), p1, keep_orig, dorg)

    # ---------------- weld rim copies the operator never splits ------------
    # bmesh only duplicates a vertex when a *tagged* edge touches it.  With
    # Boundary off the mesh-boundary edges of the region are not tagged, so
    # those rim vertices stay shared with their neighbours, while `Extrude Mesh`
    # always duplicates the whole rim.  The copies sit exactly on top of each
    # other (all displacements above agreed on that), so they can be welded.
    cval = _named(nt, (X + 300, -900), PFX + "c", "FLOAT")
    msel = _cmp(nt, (X + 500, -900), "FLOAT", "LESS_THAN", cval,
                _val(nt, (X + 300, -1000), 0.5))
    msel = _bool(nt, (X + 700, -900), "AND",
                 _bool(nt, (X + 700, -1000), "NOT", IND), msel)
    mrg = _n(nt, "GeometryNodeMergeByDistance", (X + 900, -420))
    mrg.inputs["Distance"].default_value = 1e-6
    _l(nt, p2, mrg.inputs["Geometry"])
    _l(nt, msel, mrg.inputs["Selection"])
    p2 = mrg.outputs["Geometry"]

    # ---------------- individual mode (computed on the extruded mesh) ------
    Xi = X
    Yi = 1200
    tpv_n = _fai(nt, (Xi, Yi), "CORNER", "FLOAT_VECTOR", posf.outputs["Position"], ci_n)
    tpv_p = _fai(nt, (Xi, Yi - 120), "CORNER", "FLOAT_VECTOR", posf.outputs["Position"], ci_p)
    tfn = _fai(nt, (Xi, Yi - 240), "FACE", "FLOAT_VECTOR", nrmf.outputs["Normal"], face_i)
    tno_o = _normv(nt, (Xi + 300, Yi), _vm(nt, (Xi + 200, Yi), "CROSS_PRODUCT", tfn,
                                           _vm(nt, (Xi + 100, Yi - 40), "SUBTRACT", tpv_n, posf.outputs["Position"])))
    tno_i = _normv(nt, (Xi + 300, Yi - 120), _vm(nt, (Xi + 200, Yi - 120), "CROSS_PRODUCT", tfn,
                                                  _vm(nt, (Xi + 100, Yi - 160), "SUBTRACT", posf.outputs["Position"], tpv_p)))
    tS = _vm(nt, (Xi + 500, Yi - 60), "ADD", tno_o, tno_i)
    tS2 = _dot(nt, (Xi + 700, Yi - 60), tS, tS)
    tSun = _normv(nt, (Xi + 700, Yi - 160), tS)
    tlen = _math(nt, (Xi + 700, Yi - 260), "MULTIPLY",
                 _math(nt, (Xi + 500, Yi - 320), "ADD",
                       _len(nt, (Xi + 500, Yi - 260),
                            _vm(nt, (Xi + 300, Yi - 300), "SUBTRACT", tpv_n, posf.outputs["Position"])),
                       _len(nt, (Xi + 500, Yi - 380),
                            _vm(nt, (Xi + 300, Yi - 420), "SUBTRACT", posf.outputs["Position"], tpv_p))), _val(nt, (Xi + 500, Yi - 440), 0.5))
    trel = _sw(nt, (Xi + 900, Yi - 260), "FLOAT", REL, false=ONE, true=tlen)
    ttev = _scale(nt, (Xi + 900, Yi - 60), tS,
                  _math(nt, (Xi + 900, Yi - 160), "DIVIDE", TWO,
                        _math(nt, (Xi + 900, Yi - 220), "MAXIMUM", tS2, EPS)))
    tt = _sw(nt, (Xi + 1100, Yi - 60), "VECTOR", EVEN, false=tSun, true=ttev)
    tdisp = _vm(nt, (Xi + 1300, Yi - 60), "ADD", _scale(nt, (Xi + 1100, Yi - 300), tt,
                                                        _math(nt, (Xi + 1100, Yi - 380), "MULTIPLY", THK, trel)),
                _scale(nt, (Xi + 1100, Yi - 460), tfn,
                       _math(nt, (Xi + 1100, Yi - 540), "MULTIPLY", DEP, trel)))
    tsel = _sw(nt, (Xi + 900, Yi - 560), "FLOAT",
               _bool(nt, (Xi + 700, Yi - 560), "AND", top_f, _bool(nt, (Xi + 500, Yi - 620), "NOT", OUT)),
               false=ZERO, true=ONE)
    tacc = _acc(nt, (Xi + 1500, Yi - 340), "CORNER", "FLOAT_VECTOR", vert_i,
                _scale(nt, (Xi + 1500, Yi - 420), tdisp, tsel))
    tlift = _fai(nt, (Xi + 1700, Yi - 340), "CORNER", "FLOAT_VECTOR", tacc, anyc)
    pind = _setpos(nt, (Xi + 1900, Yi - 340), gm, is_top, tlift)

    # ---------------- switch, cleanup, outputs -----------------------------
    Xs = Xi + 2300
    final = _sw(nt, (Xs, 0), "GEOMETRY", IND, false=p2, true=pind)
    for nm in (PFX + "dtop", PFX + "dorig", PFX + "issel", PFX + "c"):
        final = _rm(nt, (Xs + 260, 0), final, nm)

    og = _n(nt, "NodeGroupOutput", (Xs + 600, 0))
    _l(nt, final, og.inputs["Geometry"])
    _l(nt, top_f, og.inputs["Inset Faces"])
    _l(nt, side_f, og.inputs["Ring"])
    return nt


def get_or_create_group(name=GROUP_NAME):
    """Return the shared "Inset Faces" group, building or refreshing it.

    A group saved by an older release has a different node layout, so it is
    rebuilt *in place* (same node-group ID) - that way the modifiers already
    using it pick the new graph up instead of being left on the old one.
    """
    tag = "%d.%d.%d" % VERSION
    ng = bpy.data.node_groups.get(name)
    if ng is not None and ng.bl_idname != "GeometryNodeTree":
        bpy.data.node_groups.remove(ng)      # wrong ID type -> replace it
        ng = None
    if ng is None:
        ng = build(name)
    elif ng.get(_VER_KEY) != tag:
        build(name, ng)
    ng[_VER_KEY] = tag
    return ng
