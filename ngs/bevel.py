# -*- coding: utf-8 -*-
"""BI1MCS_GN :: "Bevel" node group.

A Geometry-Nodes front end for Blender's Bevel, exposing the whole parameter
surface of the Bevel *modifier* on a single node.

How it works
------------
Blender 5.2 ships a native ``Mesh Bevel`` node (``GeometryNodeMeshBevel``) that
runs the newer ``geometry::mesh_bevel`` kernel.  The *modifier* still runs the
older bmesh kernel (``BM_mesh_bevel``).  The two agree closely, so the geometry
is delegated to the native node and everything the native node lacks (limit
method, width method, marking, material, face strength) is rebuilt around it in
nodes.

Verified against the modifier (see ``probe_bevel_gn.py``): limit methods, all
five width methods, segments, profile, miter and the post-processing flags all
reproduce the modifier bit for bit on the test corpus.

Deliberate approximations - all marked in the socket descriptions
-----------------------------------------------------------------
==========================  ==================================================
``Loop Slide``              The native kernel always slides; the switch is
                            accepted but has no effect.
``V-Mesh Method``           Only "Adjacent" exists in the native kernel;
                            "Cutoff" behaves like "Adjacent".
``Clamp Overlap``           No counterpart in the native kernel.
``Harden Normals``          Needs custom split normals, which nodes cannot
                            write; ignored.
``Miter Outer``             The native kernel has one ``Miter`` flag and it
                            corresponds to the modifier's *Miter Inner*
                            (Sharp = off, Arc = on).  ``Miter Outer`` is
                            accepted for parameter compatibility and ignored.
``Width Method: Percent``   bmesh measures the percentage along the *adjacent*
                            edges; here a percentage of the beveled edge's own
                            length is used.
``Width Method: Absolute``  bmesh measures along the adjacent edges; here the
                            value is used directly as the offset.
``Profile = 0``             bmesh switches to a special "square in" topology;
                            the native kernel always emits the generic
                            superellipse, so any value > 0 is reproduced
                            exactly and 0 falls back to the generic shape.
==========================  ==================================================
"""

import bpy

MIN_BLENDER = (5, 2, 0)
GROUP_NAME = "Bevel"
VERSION = (0, 1, 0)
_VER_KEY = "bi1mcs_version"

# ---------------------------------------------------------------- enum tables
WIDTH_METHODS = ("Offset", "Width", "Depth", "Percent", "Absolute")
LIMIT_METHODS = ("None", "Angle", "Weight", "Vertex Group")
AFFECTS = ("Edges", "Vertices")
MITER_OUTER = ("Sharp", "Patch", "Arc")
MITER_INNER = ("Sharp", "Arc")
FACE_STRENGTH = ("None", "New", "Affected", "All")
PROFILE_TYPES = ("Superellipse", "Custom")

# Raw values the bmesh kernel writes into the ``__mod_weightednormals_faceweight``
# face attribute (bmesh_bevel.cc:3136, FKind -> FACE_STRENGTH_*).
FS_WEAK = -16384      # F_VERT - faces built around a vertex
FS_MEDIUM = 0         # F_EDGE - faces along a beveled edge
FS_STRONG = 16384     # F_RECON / F_ORIG - everything else

# Width-method shortcuts (indices into WIDTH_METHODS).
WM_OFFSET, WM_WIDTH, WM_DEPTH, WM_PERCENT, WM_ABSOLUTE = range(5)


def is_supported():
    return tuple(bpy.app.version) >= MIN_BLENDER


def _enum_desc(title, names, extra=""):
    body = " / ".join("%d = %s" % (i, n) for i, n in enumerate(names))
    return "%s:  %s%s" % (title, body, ("\n" + extra) if extra else "")


# ------------------------------------------------------------------- helpers
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
    return nd.outputs["Vector" if op != "DISTANCE" else "Value"]


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


def _or(nt, loc, a, b):
    return _bool(nt, loc, "OR", a, b)


def _not(nt, loc, a):
    return _bool(nt, loc, "NOT", a)


def _sw(nt, loc, itype, cond, false, true):
    nd = _n(nt, "GeometryNodeSwitch", loc, input_type=itype)
    _l(nt, cond, nd.inputs["Switch"])
    for s, v in (("False", false), ("True", true)):
        if v is None:
            continue
        if _sock(v):
            _l(nt, v, nd.inputs[s])
        else:
            nd.inputs[s].default_value = v
    return nd.outputs["Output"]


def _val(nt, loc, v):
    nd = _n(nt, "ShaderNodeValue", loc)
    nd.outputs[0].default_value = float(v)
    return nd.outputs[0]


def _fai(nt, loc, domain, dtype, value, index):
    """Fetch *value* (evaluated on *domain*) at the index given in the
    *current* domain.  Both domains must be chosen with care - the index is
    evaluated where the node output is consumed."""
    nd = _n(nt, "GeometryNodeFieldAtIndex", loc, domain=domain, data_type=dtype)
    _l(nt, value, nd.inputs["Value"])
    _l(nt, index, nd.inputs["Index"])
    return nd.outputs["Value"]


def _named(nt, loc, name, dtype):
    nd = _n(nt, "GeometryNodeInputNamedAttribute", loc, data_type=dtype)
    if _sock(name):
        _l(nt, name, nd.inputs["Name"])
    else:
        nd.inputs["Name"].default_value = name
    return nd.outputs["Attribute"]


def _store(nt, loc, geo, name, dtype, domain, value):
    nd = _n(nt, "GeometryNodeStoreNamedAttribute", loc, data_type=dtype,
            domain=domain)
    nd.inputs["Name"].default_value = name
    _l(nt, geo, nd.inputs["Geometry"])
    _l(nt, value, nd.inputs["Value"])
    return nd.outputs["Geometry"]


def _eq_i(nt, loc, sock, value):
    return _cmp(nt, loc, "INT", "EQUAL", sock, value)


def _ge_i(nt, loc, sock, value):
    return _cmp(nt, loc, "INT", "GREATER_EQUAL", sock, value)


# --------------------------------------------------------------------- build
def build(group_name=GROUP_NAME, nt=None):
    """Create (or rebuild) the "Bevel" node group and return it."""
    if nt is None:
        nt = bpy.data.node_groups.new(group_name, "GeometryNodeTree")
    nt.nodes.clear()
    nt.interface.clear()

    # ---------------------------------------------------------- interface
    s = nt.interface.new_socket

    s(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")

    sel = s(name="Selection", in_out="INPUT", socket_type="NodeSocketBool",
            description="Extra selection on top of Limit Method")
    sel.default_value = True
    for attr, val in (("default_input", "FIELD"), ("structure_type", "AUTO")):
        try:
            setattr(sel, attr, val)
        except Exception:
            pass

    amt = s(name="Amount", in_out="INPUT", socket_type="NodeSocketFloat",
            description="Bevel amount (the modifier's Width)")
    amt.default_value = 0.1
    amt.min_value = 0.0

    wm = s(name="Width Method", in_out="INPUT", socket_type="NodeSocketInt",
           description=_enum_desc(
               "How Amount is measured", WIDTH_METHODS,
               "Percent: percentage of the beveled edge's own length.\n"
               "Absolute: Amount used directly as the offset.\n"
               "bmesh instead measures both along the adjacent edges."))
    wm.default_value = 0

    pct = s(name="Percent", in_out="INPUT", socket_type="NodeSocketFloat",
            description="Bevel amount for the Percent width method")
    pct.default_value = 0.1
    pct.min_value = 0.0

    seg = s(name="Segments", in_out="INPUT", socket_type="NodeSocketInt",
            description="Number of segments for round edges/verts")
    seg.default_value = 1
    seg.min_value = 1
    try:
        seg.max_value = 1000
    except Exception:
        pass

    prof = s(name="Profile", in_out="INPUT", socket_type="NodeSocketFloat",
             description="Profile shape (0.5 = round); "
                         "used when no Custom Profile is supplied.\n"
                         "Exactly 0 is not reproducible: bmesh switches to a "
                         "special 'square in' topology there.")
    prof.default_value = 0.5
    prof.min_value = 0.0
    prof.max_value = 1.0

    s(name="Profile Type", in_out="INPUT", socket_type="NodeSocketInt",
      description=_enum_desc("Profile source", PROFILE_TYPES))
    s(name="Custom Profile", in_out="INPUT", socket_type="NodeSocketGeometry",
      description="Curve sampled for a custom profile: XY plane, "
                  "(0,1,0) -> (1,0,0)")

    af = s(name="Affect", in_out="INPUT", socket_type="NodeSocketInt",
           description=_enum_desc("Bevel edges or vertices", AFFECTS))
    af.default_value = 0

    lim = s(name="Limit Method", in_out="INPUT", socket_type="NodeSocketInt",
            description=_enum_desc("Which elements get beveled", LIMIT_METHODS,
                                   "Angle works on the angle between the two "
                                   "adjacent face normals."))
    lim.default_value = 1

    ang = s(name="Angle", in_out="INPUT", socket_type="NodeSocketFloat",
            description="Angle above which to bevel edges (radians)")
    ang.default_value = 0.523599
    ang.min_value = 0.0

    s(name="Edge Weight", in_out="INPUT", socket_type="NodeSocketString",
      description="Float attribute holding the per-edge weight "
                  "(Limit Method = Weight); edges with weight 0 are skipped")
    s(name="Vertex Weight", in_out="INPUT", socket_type="NodeSocketString",
      description="Float attribute holding the per-vertex weight "
                  "(Limit Method = Weight, Affect = Vertices); weight 0 is "
                  "skipped")
    s(name="Vertex Group", in_out="INPUT", socket_type="NodeSocketString",
      description="Weight attribute used by Limit Method = Vertex Group; an "
                  "edge is beveled when both of its vertices are >= 0.5")

    inv = s(name="Invert Vertex Group", in_out="INPUT",
            socket_type="NodeSocketBool")
    inv.default_value = False

    clamp = s(name="Clamp Overlap", in_out="INPUT", socket_type="NodeSocketBool",
              description="NOT SUPPORTED - the native bevel kernel has no "
                          "overlap clamp; the switch is accepted and ignored")
    clamp.default_value = True

    slide = s(name="Loop Slide", in_out="INPUT", socket_type="NodeSocketBool",
              description="NOT SUPPORTED - the native bevel kernel always "
                          "slides along edges; the switch is accepted and "
                          "ignored")
    slide.default_value = True

    s(name="Miter Outer", in_out="INPUT", socket_type="NodeSocketInt",
      description=_enum_desc(
          "Pattern for outside miters", MITER_OUTER,
          "NOT SUPPORTED - the native bevel kernel has a single miter flag "
          "and it corresponds to Miter Inner.  This switch is accepted and "
          "ignored."))
    s(name="Miter Inner", in_out="INPUT", socket_type="NodeSocketInt",
      description=_enum_desc(
          "Pattern for inside miters", MITER_INNER,
          "Drives the native kernel's Miter flag: Sharp = off, Arc = on."))

    spr = s(name="Spread", in_out="INPUT", socket_type="NodeSocketFloat",
            description="Spread distance for arc miters")
    spr.default_value = 0.1
    spr.min_value = 0.0

    for nm, desc in (("Mark Seam", "Mark the generated edges as UV seams"),
                     ("Mark Sharp", "Mark the generated edges as sharp"),
                     ("Harden Normals",
                      "NOT SUPPORTED - needs custom split normals, which "
                      "geometry nodes cannot write")):
        sk = s(name=nm, in_out="INPUT", socket_type="NodeSocketBool",
               description=desc)
        sk.default_value = False

    mat = s(name="Material Index", in_out="INPUT", socket_type="NodeSocketInt",
            description="Material index of the generated faces, -1 to keep "
                        "the surrounding index")
    mat.default_value = -1
    mat.min_value = -1

    s(name="Face Strength", in_out="INPUT", socket_type="NodeSocketInt",
      description=_enum_desc(
          "Whether/which faces get a face strength", FACE_STRENGTH,
          "Written to __mod_weightednormals_faceweight, exactly like the "
          "modifier does."))

    s(name="V-Mesh Method", in_out="INPUT", socket_type="NodeSocketInt",
      description=_enum_desc("How the mesh at intersections is created",
                             ("Adjacent", "Cutoff"),
                             "Only 'Adjacent' exists in the native kernel; "
                             "'Cutoff' behaves the same."))

    s(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    s(name="Bevel Faces", in_out="OUTPUT", socket_type="NodeSocketBool",
      description="Faces generated by the bevel (field, face domain)")
    s(name="Bevel Edges", in_out="OUTPUT", socket_type="NodeSocketBool",
      description="Edges generated by the bevel: edges with a generated face "
                  "on either side (field, edge domain)")

    # ------------------------------------------------------------ sources
    gi = _n(nt, "NodeGroupInput", (-9000, 0))
    o = gi.outputs
    GEO = o["Geometry"]
    SEL = o["Selection"]
    AMT = o["Amount"]
    WM = o["Width Method"]
    PCT = o["Percent"]
    SEG = o["Segments"]
    PROF = o["Profile"]
    PTYPE = o["Profile Type"]
    CPROF = o["Custom Profile"]
    AFF = o["Affect"]
    LIM = o["Limit Method"]
    ANG = o["Angle"]
    EW = o["Edge Weight"]
    VW = o["Vertex Weight"]
    VG = o["Vertex Group"]
    VGINV = o["Invert Vertex Group"]
    MO = o["Miter Outer"]
    MI = o["Miter Inner"]
    SPR = o["Spread"]
    MSEAM = o["Mark Seam"]
    MSHARP = o["Mark Sharp"]
    MAT = o["Material Index"]
    FSTR = o["Face Strength"]

    X = -8800
    ONE = _val(nt, (X, -1500), 1.0)
    ZERO = _val(nt, (X, -1700), 0.0)
    HALF = _val(nt, (X, -1900), 0.5)
    TWO = _val(nt, (X, -2100), 2.0)
    EPS = _val(nt, (X, -2300), 1e-6)
    THR = _val(nt, (X, -2500), 0.5)
    P100 = _val(nt, (X, -2700), 100.0)

    # ---------------------------------------------- per-edge measurements
    X += 600
    eang = _n(nt, "GeometryNodeInputMeshEdgeAngle", (X, 400)).outputs[
        "Unsigned Angle"]
    enf = _n(nt, "GeometryNodeInputMeshEdgeNeighbors", (X, 200)).outputs[
        "Face Count"]
    ev = _n(nt, "GeometryNodeInputMeshEdgeVertices", (X, 0))
    EV1 = ev.outputs["Vertex Index 1"]
    EV2 = ev.outputs["Vertex Index 2"]
    manifold = _cmp(nt, (X + 200, 200), "INT", "EQUAL", enf, 2)

    # edge length, used by the Percent width method
    elen = _vm(nt, (X + 440, -240), "DISTANCE",
               ev.outputs["Position 1"], ev.outputs["Position 2"])

    # ------------------------------------------------ limit method (edges)
    # angle: dot(n1, n2) < cos(angle)  <=>  face-normal angle > angle
    X += 500
    ang_thr = _math(nt, (X, 400), "ADD", ANG,
                    _val(nt, (X - 200, 300), 1.75e-7))
    m_ang = _bool(nt, (X + 260, 380), "AND", manifold,
                  _cmp(nt, (X + 60, 460), "FLOAT", "GREATER_THAN", eang,
                       ang_thr))

    ew_attr = _named(nt, (X - 100, 120), EW, "FLOAT")
    m_wgt = _bool(nt, (X + 260, 80), "AND", manifold,
                  _cmp(nt, (X + 60, 160), "FLOAT", "NOT_EQUAL", ew_attr, 0.0))

    vg_attr_e = _named(nt, (X - 300, -220), VG, "FLOAT")
    w1 = _fai(nt, (X - 100, -200), "POINT", "FLOAT", vg_attr_e, EV1)
    w2 = _fai(nt, (X - 100, -340), "POINT", "FLOAT", vg_attr_e, EV2)
    w1 = _sw(nt, (X + 60, -200), "FLOAT", VGINV, false=w1,
             true=_math(nt, (X - 100, -420), "SUBTRACT", ONE, w1))
    w2 = _sw(nt, (X + 60, -340), "FLOAT", VGINV, false=w2,
             true=_math(nt, (X - 100, -500), "SUBTRACT", ONE, w2))
    m_vg = _bool(nt, (X + 380, -260), "AND", manifold,
                 _bool(nt, (X + 280, -320), "AND",
                       _cmp(nt, (X + 160, -200), "FLOAT", "GREATER_EQUAL",
                            w1, THR),
                       _cmp(nt, (X + 160, -340), "FLOAT", "GREATER_EQUAL",
                            w2, THR)))

    # Cascade of switches instead of an Index Switch (a custom node group
    # cannot declare enum sockets).  Level k forwards its own result as the
    # "otherwise" branch so the lower values keep falling through.
    lim_e = ONE
    lim_e = _sw(nt, (X + 700, -60), "BOOLEAN", _eq_i(nt, (X + 580, 220), LIM, 1),
                false=lim_e, true=m_ang)
    lim_e = _sw(nt, (X + 900, -60), "BOOLEAN", _eq_i(nt, (X + 780, 220), LIM, 2),
                false=lim_e, true=m_wgt)
    lim_e = _sw(nt, (X + 1100, -60), "BOOLEAN",
                _eq_i(nt, (X + 980, 220), LIM, 3), false=lim_e, true=m_vg)
    lim_e = _bool(nt, (X + 1320, -60), "AND", lim_e, SEL)

    # ---------------------------------------------- limit method (vertices)
    vw_attr = _named(nt, (X - 100, -700), VW, "FLOAT")
    v_wgt = _cmp(nt, (X + 60, -700), "FLOAT", "NOT_EQUAL", vw_attr, 0.0)
    vg_attr_p = _named(nt, (X - 300, -900), VG, "FLOAT")
    vg_w = _sw(nt, (X + 60, -940), "FLOAT", VGINV, false=vg_attr_p,
               true=_math(nt, (X - 100, -1020), "SUBTRACT", ONE, vg_attr_p))
    v_vg = _cmp(nt, (X + 160, -940), "FLOAT", "GREATER_EQUAL", vg_w, THR)
    # Vertex bevel (MOD_bevel.cc:174): only "Weight" filters; with Angle /
    # Vertex Group / None the lookup falls back to the mesh deform layer, which
    # is 1.0 when no group is set - so those select everything.
    lim_v = _sw(nt, (X + 380, -760), "BOOLEAN",
                _eq_i(nt, (X + 260, -640), LIM, 2), false=ONE, true=v_wgt)
    lim_v = _sw(nt, (X + 620, -760), "BOOLEAN",
                _eq_i(nt, (X + 500, -640), LIM, 3), false=lim_v, true=v_vg)
    lim_v = _bool(nt, (X + 860, -760), "AND", lim_v, SEL)

    # ---------------------------------------------------- width conversion
    # `eang` is the *unsigned* angle between the two face normals (phi).  bmesh
    # measures the edge angle as the supplement, theta = PI - phi
    # (bmesh_bevel.cc:6589), and feeds that to the width conversion:
    #   WIDTH: z = |2 sin(theta/2)| = |2 cos(phi/2)|
    #   DEPTH: z = |  cos(theta/2)| = |  sin(phi/2)|
    # The two coincide at phi = 90 deg, which is why axis-aligned boxes cannot
    # tell them apart.
    X += 1600
    half = _math(nt, (X, 400), "MULTIPLY", eang, HALF)
    z_w = _math(nt, (X + 220, 400), "MULTIPLY",
                _math(nt, (X + 100, 320), "ABSOLUTE",
                      _math(nt, (X, 460), "COSINE", half)), TWO)
    z_d = _math(nt, (X + 220, 200), "ABSOLUTE",
                _math(nt, (X, 260), "SINE", half))
    # bmesh guards a vanishing sin/cos with BEVEL_EPSILON and falls back to 1%
    # of the amount (bmesh_bevel.cc:6991) - flat meshes hit this.
    TINY = _val(nt, (X + 300, 620), 1e-6)
    FLAT = _math(nt, (X + 440, 620), "MULTIPLY", AMT,
                 _val(nt, (X + 300, 720), 0.01))
    o_w = _sw(nt, (X + 700, 400), "FLOAT",
              _cmp(nt, (X + 560, 500), "FLOAT", "LESS_THAN", z_w, TINY),
              false=_math(nt, (X + 440, 400), "DIVIDE", AMT,
                          _math(nt, (X + 340, 480), "MAXIMUM", z_w, TINY)),
              true=FLAT)
    o_d = _sw(nt, (X + 700, 200), "FLOAT",
              _cmp(nt, (X + 560, 300), "FLOAT", "LESS_THAN", z_d, TINY),
              false=_math(nt, (X + 440, 200), "DIVIDE", AMT,
                          _math(nt, (X + 340, 280), "MAXIMUM", z_d, TINY)),
              true=FLAT)
    o_p = _math(nt, (X + 440, 0), "DIVIDE",
                _math(nt, (X + 220, 60), "MULTIPLY", elen, PCT), P100)

    # Same cascade rule as above: each level passes its result on.
    off = AMT
    off = _sw(nt, (X + 900, -120), "FLOAT",
              _eq_i(nt, (X + 780, 120), WM, WM_WIDTH), false=off, true=o_w)
    off = _sw(nt, (X + 1100, -120), "FLOAT",
              _eq_i(nt, (X + 980, 120), WM, WM_DEPTH), false=off, true=o_d)
    off = _sw(nt, (X + 1300, -120), "FLOAT",
              _eq_i(nt, (X + 1180, 120), WM, WM_PERCENT), false=off, true=o_p)
    off = _sw(nt, (X + 1500, -120), "FLOAT",
              _eq_i(nt, (X + 1380, 120), WM, WM_ABSOLUTE), false=off, true=AMT)
    # ZERO-width guard: a 0 offset must be a no-op
    off = _sw(nt, (X + 1500, -320), "FLOAT",
              _cmp(nt, (X + 1380, -220), "FLOAT", "LESS_THAN", off, EPS),
              false=off, true=ZERO)

    # -------------------------------------------------------------- miter
    # The native node exposes a single Miter flag.  Measured against the
    # modifier over every combination of Miter Outer/Inner it maps exactly
    # onto the *inner* miter: Sharp -> off, Arc -> on.  Miter Outer has no
    # counterpart, so it is accepted and ignored.
    miter = _eq_i(nt, (X + 1300, -620), MI, 1)

    # ------------------------------------------------------------- bevel(s)
    X += 1800

    def bevel_node(loc, affect, selection, offsets):
        nd = _n(nt, "GeometryNodeMeshBevel", loc)
        _l(nt, GEO, nd.inputs["Mesh"])
        _l(nt, selection, nd.inputs["Selection"])
        nd.inputs["Affect Kind"].default_value = affect
        if affect == "Edges":
            for nm in ("Start Left Offset", "Start Right Offset",
                       "End Left Offset", "End Right Offset"):
                _l(nt, offsets, nd.inputs[nm])
            nd.inputs["Offset"].default_value = 0.1
        else:
            _l(nt, offsets, nd.inputs["Offset"])
            for nm in ("Start Left Offset", "Start Right Offset",
                       "End Left Offset", "End Right Offset"):
                nd.inputs[nm].default_value = 0.1
        _l(nt, SEG, nd.inputs["Segments"])
        _l(nt, PROF, nd.inputs["Shape"])
        _l(nt, miter, nd.inputs["Miter"])
        _l(nt, SPR, nd.inputs["Spread"])
        return nd

    bx_e = bevel_node((X, 300), "Edges", lim_e, off)
    bx_v = bevel_node((X, -400), "Vertices", lim_v, off)

    # custom profile: only wired in when Profile Type == 1
    psel = _eq_i(nt, (X - 200, -700), PTYPE, 1)
    pgeo = _sw(nt, (X - 40, -780), "GEOMETRY", psel, false=None, true=CPROF)
    _l(nt, pgeo, bx_e.inputs["Profile"])
    _l(nt, pgeo, bx_v.inputs["Profile"])

    is_vert = _eq_i(nt, (X + 180, 120), AFF, 1)
    BEVEL = _sw(nt, (X + 300, -60), "GEOMETRY", is_vert,
                false=bx_e.outputs["Mesh"], true=bx_v.outputs["Mesh"])

    # ------------------------------------------------------- selections
    # The Selections panel outputs are anonymous attributes; pick the pair
    # belonging to the kernel that actually ran.
    def pick(name, itype="BOOLEAN"):
        return _sw(nt, (X + 300, 520), itype, is_vert,
                   false=bx_e.outputs[name], true=bx_v.outputs[name])

    VFACE = pick("Vertex Face")
    EFACE = pick("Edge Face")
    OEDGE = pick("Outer Edge")
    MEDGE = pick("Mid Edge")

    NEW_FACE = _or(nt, (X + 560, 520), VFACE, EFACE)
    NEW_EDGE = _or(nt, (X + 560, 380), OEDGE, MEDGE)

    # Every edge the bevel touched: an edge that has at least one generated
    # face on either side.  The Selections panel has no name for that, so it is
    # assembled from the corner topology - accumulate "is my face generated"
    # over the corners of each edge, then read the total back per edge.
    c_edge = _n(nt, "GeometryNodeEdgesOfCorner",
                (X + 560, 260)).outputs["Next Edge Index"]
    c_face = _n(nt, "GeometryNodeFaceOfCorner",
                (X + 560, 140)).outputs["Face Index"]
    c_hit = _fai(nt, (X + 760, 200), "FACE", "BOOLEAN", NEW_FACE, c_face)
    c_val = _sw(nt, (X + 960, 200), "INT", c_hit, false=0, true=1)
    acc = _n(nt, "GeometryNodeAccumulateField", (X + 1180, 200),
             data_type="INT", domain="CORNER")
    _l(nt, c_val, acc.inputs["Value"])
    _l(nt, c_edge, acc.inputs["Group ID"])
    e_corner = _n(nt, "GeometryNodeCornersOfEdge",
                  (X + 1180, 20)).outputs["Corner Index"]
    e_tot = _fai(nt, (X + 1400, 140), "CORNER", "INT",
                 acc.outputs["Total"], e_corner)
    NEW_EDGE_ALL = _cmp(nt, (X + 1620, 140), "INT", "GREATER_THAN", e_tot, 0)

    # ----------------------------------------------------- post-processing
    geo = BEVEL

    # material index of the generated faces
    mat_ok = _cmp(nt, (X + 560, 120), "INT", "GREATER_EQUAL", MAT, 0)
    mat_prev = _named(nt, (X + 560, 0), "material_index", "INT")
    mat_val = _sw(nt, (X + 760, 60), "INT", NEW_FACE, false=mat_prev, true=MAT)
    mat_geo = _store(nt, (X + 980, 60), geo, "material_index", "INT", "FACE",
                     mat_val)
    geo = _sw(nt, (X + 1200, 60), "GEOMETRY", mat_ok, false=geo, true=mat_geo)

    # face strength (__mod_weightednormals_faceweight), exactly the values the
    # bmesh kernel writes per FKind
    recon = _not(nt, (X + 560, -180), NEW_FACE)
    big = _bool(nt, (X + 760, -180), "AND",
                _ge_i(nt, (X + 620, -300), FSTR, 2), recon)
    fs = _sw(nt, (X + 980, -180), "INT", big, false=0, true=FS_STRONG)
    fs = _sw(nt, (X + 1200, -180), "INT",
             _bool(nt, (X + 1060, -400), "AND",
                   _ge_i(nt, (X + 920, -520), FSTR, 1), VFACE),
             false=fs, true=FS_WEAK)
    fs_geo = _store(nt, (X + 1420, -180), geo, "__mod_weightednormals_faceweight",
                    "INT", "FACE", fs)
    geo = _sw(nt, (X + 1640, -180), "GEOMETRY",
              _eq_i(nt, (X + 1500, -420), FSTR, 0), false=fs_geo, true=geo)

    # seam / sharp on the generated edges
    for nm, flag, attr in (("seam", MSEAM, "uv_seam"),
                           ("sharp", MSHARP, "sharp_edge")):
        prev = _named(nt, (X + 560, -700), attr, "BOOLEAN")
        val = _or(nt, (X + 760, -700), prev, NEW_EDGE)
        stored = _store(nt, (X + 980, -700), geo, attr, "BOOLEAN", "EDGE", val)
        geo = _sw(nt, (X + 1200, -700), "GEOMETRY", flag, false=geo,
                  true=stored)

    # ------------------------------------------------------------ outputs
    GOUT = _n(nt, "NodeGroupOutput", (X + 1900, -60))
    _l(nt, geo, GOUT.inputs["Geometry"])
    _l(nt, NEW_FACE, GOUT.inputs["Bevel Faces"])
    _l(nt, NEW_EDGE_ALL, GOUT.inputs["Bevel Edges"])

    return nt


def get_or_create_group(name=GROUP_NAME):
    """Return the shared "Bevel" group, building or refreshing it.

    A group saved by an older release has a different node layout, so it is
    rebuilt *in place* (same node-group ID) - that way the modifiers already
    using it pick the new graph up instead of being left on the old one.
    """
    tag = "%d.%d.%d" % VERSION
    ng = bpy.data.node_groups.get(name)
    if ng is not None and ng.bl_idname != "GeometryNodeTree":
        bpy.data.node_groups.remove(ng)
        ng = None
    if ng is None:
        ng = build(name)
    elif ng.get(_VER_KEY) != tag:
        build(name, ng)
    ng[_VER_KEY] = tag
    return ng
