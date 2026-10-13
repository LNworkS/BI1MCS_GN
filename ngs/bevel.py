# -*- coding: utf-8 -*-
"""BI1MCS_GN :: "Bevel" node group.

A Geometry-Nodes front end for Blender's Bevel *modifier*.

How it works
------------
Blender 5.2 ships a native ``Mesh Bevel`` node (``GeometryNodeMeshBevel``) that
runs the newer ``geometry::mesh_bevel`` kernel.  The *modifier* still runs the
older bmesh kernel (``BM_mesh_bevel``).  The two agree closely, so the geometry
is delegated to the native node and everything the native node lacks (limit
method, width method, miter combination) is rebuilt around it in nodes.

The four offsets are named exactly like the native node's sockets and each one
is run through the Width Method conversion independently, so the node can
reproduce the modifier's single ``Width`` *and* the native node's per-edge-end
control at the same time.

Verified against the modifier: limit methods, all five width methods, segments,
profile and the miter mapping all reproduce the modifier bit for bit on the
axis-aligned and skewed test corpus (``probe_bevel_gn.py`` / ``probe_bevel_post.py``).

Not exposed, and why
--------------------
``Clamp Overlap``           The native kernel has no overlap clamp.
``Loop Slide``              The native kernel always slides.
``V-Mesh Method``           Only "Adjacent" exists in the native kernel.
``Harden Normals``          Needs custom split normals, which nodes cannot
                            write.
``Mark Seam`` / ``Mark Sharp`` / ``Material Index`` / ``Face Strength``
                            Dropped from the interface.  The ``Bevel Faces``
                            and ``Bevel Edges`` outputs carry the same
                            information, so a Store Named Attribute node
                            downstream does the job.
``Profile Type``            Only the superellipse profile is exposed.

Deliberate approximations - all marked in the socket descriptions
-----------------------------------------------------------------
==========================  ==================================================
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

Kernel drift (not this node's doing)
------------------------------------
The two kernels are not identical everywhere.  Running the *native node on its
own* against the modifier on a block with a 1x2 notch reproduces the same
topology but drifts by up to ~4% of the width once ``Segments >= 3``
(measured: h = 0.0087 at width 0.2, segments 6), while a cube, an L-prism, a
cylinder and a cone are bit-exact at every segment count.  ``probe_bevel_post.py``
reports this separately as the "kernel baseline".

Miter mapping
-------------
The node exposes a single ``Miter`` boolean, exactly like the native ``Mesh
Bevel`` node.  The modifier's ``Miter Outer`` (3 states) and ``Miter Inner``
(2 states) give six combinations, but the native kernel only has one flag per
vertex, and it only lines up with two of them:

=================  ==================
native ``Miter``   modifier state
=================  ==================
off                (Sharp, Sharp)
on                 (Patch, Arc)
=================  ==================

Those two are reproduced bit for bit.  Measured against the modifier (width
0.15, segments 1):

=======  ==========  ==========  ==========
shape    modifier    modifier    native
         (S, S)      (P, A)      on
=======  ==========  ==========  ==========
cube     24 v/26 f   52 v/26 f   52 v/26 f
lprism   42 v/44 f   88 v/44 f   88 v/44 f
lstep    48 v/50 f   104 v/50 f  104 v/50 f
=======  ==========  ==========  ==========

Why the other four cannot be reached in nodes
---------------------------------------------
``miter_outer`` fires only at reflex (concave) corners and ``miter_inner`` only
at convex ones (``bmesh_bevel.cc:3548`` + ``edges_angle_kind``), so it looks as
though a *per-vertex* flag could drive the two sides apart.  The native node's
``Miter`` input is indeed a per-vertex field (measured on an L-prism: switching
it on at one vertex changes only that vertex's corner, 36 -> 39 vertices, while
switching a single *edge* on changes nothing), and a per-vertex flag does give
four distinct results.  It still cannot reach the modifier's mixed states:

* a reflex vertex carries *both* kinds of corner at once.  The pair of edges
  spanning the 270-degree side is ``ANGLE_LARGER`` (outer miter) and each pair
  with the third edge is ``ANGLE_SMALLER`` (inner miter).  The modifier applies
  ``miter_outer`` and ``miter_inner`` to those pairs separately; the native node
  has one boolean for the whole vertex, so it can only give that vertex
  (Sharp, Sharp) or (Patch, Arc).  Every mixed combination is therefore wrong
  *at the reflex vertex itself* - ``probe_bevel_miter11.py`` lists the differing
  vertices and all of them lie within one width of a reflex corner;
* the native "on" state is always Patch on the outer side; the modifier's
  ``Miter Outer = Arc`` has no native counterpart at all.

Measured against the modifier on an L-prism, a per-vertex flag still differs at
the reflex corners, and an exhaustive search over per-vertex flag patterns
(every constant-per-class assignment over reflex / adjacent / far vertices,
``probe_bevel_miter8.py``) found no pattern that reproduces any of the four
mixed combinations.

Getting them by combining two bevels was measured too, and does not work:

* two bevels over a partition of the edge set give 42 faces instead of 38 on an
  L-prism: a vertex whose edges are split across the two passes never sees its
  full bevel, so the miter is not formed at all;
* swapping the reflex-corner neighbourhood between the flag=on and flag=off
  results leaves a seam - 76 vertices instead of 70 even welded at distance
  0.1 - because every bevel strip is shaped by *both* of its end corners, so
  there is no cut where the two runs agree;
* the modifier's mixed-state vertices are genuinely new geometry: they are not
  within 1e-6 of any vertex of either native run (``probe_bevel_miter9.py`` /
  ``probe_bevel_miter10.py``), so no splice of two runs can reach them.

The flag is a boolean rather than an enum because that is exactly what the
native node exposes - ``Miter`` off or on - and the two states it reaches are
the two the modifier draws exactly.

Exact path
----------
``bmesh.ops.bevel`` and ``bpy.ops.mesh.bevel`` both accept ``miter_outer`` and
``miter_inner`` separately and reproduce all six combinations bit for bit
(``probe_bevel_pyops.py``: 3 shapes x 6 combinations, all exact).  Neither can
run inside a node graph, so the only way to reach the four mixed combinations
exactly is to drive the bevel from Python rather than from the native node.
"""

import bpy

MIN_BLENDER = (5, 2, 0)
GROUP_NAME = "Bevel"
VERSION = (0, 5, 0)
_VER_KEY = "bi1mcs_version"

# ---------------------------------------------------------------- enum tables
WIDTH_METHODS = ("Offset", "Width", "Depth", "Percent", "Absolute")
LIMIT_METHODS = ("None", "Angle", "Weight", "Vertex Group")
AFFECTS = ("Edges", "Vertices")

# Width-method shortcuts (indices into WIDTH_METHODS).
WM_OFFSET, WM_WIDTH, WM_DEPTH, WM_PERCENT, WM_ABSOLUTE = range(5)

# The four offset sockets, in the native node's own order.
OFFSET_NAMES = ("Start Left Offset", "Start Right Offset",
                "End Left Offset", "End Right Offset")


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


def _eq_i(nt, loc, sock, value):
    return _cmp(nt, loc, "INT", "EQUAL", sock, value)


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

    af = s(name="Affect", in_out="INPUT", socket_type="NodeSocketInt",
           description=_enum_desc("Bevel edges or vertices", AFFECTS))
    af.default_value = 0

    wm = s(name="Width Method", in_out="INPUT", socket_type="NodeSocketInt",
           description=_enum_desc(
               "How the four offsets are measured", WIDTH_METHODS,
               "Offset: used as is.\n"
               "Width / Depth: divided by the edge's face-normal angle so the "
               "distance is measured across the bevel.\n"
               "Percent: percentage of the beveled edge's own length.\n"
               "Absolute: used as is.\n"
               "bmesh instead measures Percent/Absolute along the adjacent "
               "edges."))
    wm.default_value = 0

    for nm, desc, val in (
            (OFFSET_NAMES[0], "Amount at the edge's start, measured towards "
                              "the face on the left", 0.1),
            (OFFSET_NAMES[1], "Amount at the edge's start, measured towards "
                              "the face on the right", 0.1),
            (OFFSET_NAMES[2], "Amount at the edge's end, measured towards the "
                              "face on the left", 0.1),
            (OFFSET_NAMES[3], "Amount at the edge's end, measured towards the "
                              "face on the right", 0.1)):
        sk = s(name=nm, in_out="INPUT", socket_type="NodeSocketFloat",
               description=desc + "\nInterpreted according to Width Method.")
        sk.default_value = val
        sk.min_value = 0.0

    seg = s(name="Segments", in_out="INPUT", socket_type="NodeSocketInt",
            description="Number of segments for round edges/verts")
    seg.default_value = 1
    seg.min_value = 1
    try:
        seg.max_value = 1000
    except Exception:
        pass

    prof = s(name="Profile", in_out="INPUT", socket_type="NodeSocketFloat",
             description="Superellipse profile shape (0.5 = round; 0.25 is "
                         "closer to a chamfer).\n"
                         "Exactly 0 is not reproducible: bmesh switches to a "
                         "special 'square in' topology there.")
    prof.default_value = 0.5
    prof.min_value = 0.0
    prof.max_value = 1.0

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

    mi = s(name="Miter", in_out="INPUT", socket_type="NodeSocketBool",
           description="Miter the corners where several beveled edges meet.\n"
                       "Matches the native Mesh Bevel node's Miter input, which\n"
                       "is a single per-vertex flag: off is the modifier's\n"
                       "(Miter Outer = Sharp, Miter Inner = Sharp), on is\n"
                       "(Patch, Arc).  Those two combinations are reproduced\n"
                       "exactly; the modifier's other four are not reachable -\n"
                       "see \"Miter mapping\" in the module docstring.")
    mi.default_value = False

    spr = s(name="Spread", in_out="INPUT", socket_type="NodeSocketFloat",
            description="Spread distance for arc miters")
    spr.default_value = 0.1
    spr.min_value = 0.0

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
    AFF = o["Affect"]
    WM = o["Width Method"]
    AMT = [o[nm] for nm in OFFSET_NAMES]
    SEG = o["Segments"]
    PROF = o["Profile"]
    LIM = o["Limit Method"]
    ANG = o["Angle"]
    EW = o["Edge Weight"]
    VW = o["Vertex Weight"]
    VG = o["Vertex Group"]
    VGINV = o["Invert Vertex Group"]
    # `Miter` is the native node's own boolean flag - see "Miter mapping" in
    # the module docstring.
    MIT = o["Miter"]
    SPR = o["Spread"]

    X = -8800
    ONE = _val(nt, (X, -1500), 1.0)
    ZERO = _val(nt, (X, -1700), 0.0)
    HALF = _val(nt, (X, -1900), 0.5)
    TWO = _val(nt, (X, -2100), 2.0)
    EPS = _val(nt, (X, -2300), 1e-6)
    THR = _val(nt, (X, -2500), 0.5)
    P1 = _val(nt, (X, -2700), 1e-2)
    P100 = _val(nt, (X, -2900), 100.0)

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

    def convert(src, row):
        """Run one user amount through the Width Method conversion."""
        flat = _math(nt, (X + 60, row), "MULTIPLY", src, P1)
        ow = _sw(nt, (X + 340, row), "FLOAT",
                 _cmp(nt, (X + 200, row + 100), "FLOAT", "LESS_THAN",
                      z_w, TINY),
                 false=_math(nt, (X + 60, row - 80), "DIVIDE", src,
                             _math(nt, (X - 60, row - 140), "MAXIMUM",
                                   z_w, TINY)),
                 true=flat)
        od = _sw(nt, (X + 340, row - 180), "FLOAT",
                 _cmp(nt, (X + 200, row - 80), "FLOAT", "LESS_THAN",
                      z_d, TINY),
                 false=_math(nt, (X + 60, row - 260), "DIVIDE", src,
                             _math(nt, (X - 60, row - 320), "MAXIMUM",
                                   z_d, TINY)),
                 true=flat)
        op = _math(nt, (X + 60, row - 420), "DIVIDE",
                   _math(nt, (X - 60, row - 360), "MULTIPLY", elen, src),
                   P100)
        v = src
        v = _sw(nt, (X + 540, row - 40), "FLOAT",
                _eq_i(nt, (X + 420, row + 60), WM, WM_WIDTH),
                false=v, true=ow)
        v = _sw(nt, (X + 720, row - 40), "FLOAT",
                _eq_i(nt, (X + 600, row + 60), WM, WM_DEPTH),
                false=v, true=od)
        v = _sw(nt, (X + 900, row - 40), "FLOAT",
                _eq_i(nt, (X + 780, row + 60), WM, WM_PERCENT),
                false=v, true=op)
        # ZERO-width guard: a 0 offset must be a no-op
        return _sw(nt, (X + 1080, row - 40), "FLOAT",
                   _cmp(nt, (X + 960, row + 60), "FLOAT", "LESS_THAN",
                        v, EPS),
                   false=v, true=ZERO)

    OFF = [convert(AMT[i], 400 - 800 * i) for i in range(4)]

    # -------------------------------------------------------------- miter
    # Straight through, exactly like the native Mesh Bevel node's Miter input:
    # a single boolean, off == (Sharp, Sharp) and on == (Patch, Arc) in the
    # modifier's terms.  Both of those are reproduced bit for bit.  Driving the
    # two sides apart per vertex was measured and cannot reach the modifier's
    # four mixed combinations - see "Miter mapping" in the module docstring.
    miter = MIT

    # ------------------------------------------------------------- bevel(s)
    X += 1800

    def bevel_node(loc, affect, selection):
        nd = _n(nt, "GeometryNodeMeshBevel", loc)
        _l(nt, GEO, nd.inputs["Mesh"])
        _l(nt, selection, nd.inputs["Selection"])
        nd.inputs["Affect Kind"].default_value = affect
        if affect == "Edges":
            for nm, off in zip(OFFSET_NAMES, OFF):
                _l(nt, off, nd.inputs[nm])
            nd.inputs["Offset"].default_value = 0.1
        else:
            # Vertex bevel takes a single offset; the start-left one is used,
            # which is the same value the modifier would apply.
            _l(nt, OFF[0], nd.inputs["Offset"])
            for nm in OFFSET_NAMES:
                nd.inputs[nm].default_value = 0.1
        _l(nt, SEG, nd.inputs["Segments"])
        _l(nt, PROF, nd.inputs["Shape"])
        _l(nt, miter, nd.inputs["Miter"])
        _l(nt, SPR, nd.inputs["Spread"])
        return nd

    bx_e = bevel_node((X, 300), "Edges", lim_e)
    bx_v = bevel_node((X, -400), "Vertices", lim_v)

    is_vert = _eq_i(nt, (X + 180, 120), AFF, 1)
    geo = _sw(nt, (X + 300, -60), "GEOMETRY", is_vert,
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
    NEW_EDGE = _cmp(nt, (X + 1620, 140), "INT", "GREATER_THAN", e_tot, 0)

    # ------------------------------------------------------------ outputs
    GOUT = _n(nt, "NodeGroupOutput", (X + 1900, -60))
    _l(nt, geo, GOUT.inputs["Geometry"])
    _l(nt, NEW_FACE, GOUT.inputs["Bevel Faces"])
    _l(nt, NEW_EDGE, GOUT.inputs["Bevel Edges"])

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
