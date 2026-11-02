# -*- coding: utf-8 -*-
"""Solidify double shell, matching Blender 5.2 modifier defaults.

Simple: true input vertex normals, turning-angle Even Thickness compensation,
full double skin and boundary rims. Complex: default Constraints solver, no
Even toggle effect (as in native). Disconnected vertex face fans are split by
CORNER connectivity with source edge ordering and logical direction preserved.
Complex inputs requiring native radial sector splitting, short-edge merging,
singularity handling return the original mesh with Warning=True, not a Simple
substitute. Complex keeps a mesh with no faces unchanged; when faces exist it
removes loose edges/points before rebuilding. Arbitrary Complex topology is not covered.
Vertex Group is an effective POINT weight field, default 1 (no group).
EDGE attributes use Simple type defaults or Complex native boundary-edge identity;
Complex transfers vertex crease to edge crease and removes the vertex layer.
Non-trivial attribute types and sector/singularity attributes are not covered.
"""
import bpy
from .extrude import _Graph, _interface, _point_selection, MIN_BLENDER, is_supported
from .solidify_complex import build_complex
from .solidify_attributes import prepare_attributes, finish_attributes
from .solidify_topology import build_topology

GROUP_NAME = 'Solidify'
VERSION = (0, 1, 2)


def _simple_fields(g, geometry):
    idx = g.node('GeometryNodeInputIndex').outputs[0]
    pos = g.node('GeometryNodeInputPosition').outputs[0]
    no = g.node('GeometryNodeInputNormal').outputs[0]
    geo, base = g.capture(geometry, 'FACE', normal=('VECTOR', no))
    face = g.node('GeometryNodeFaceOfCorner')
    vert = g.node('GeometryNodeVertexOfCorner')
    first = g.node('GeometryNodeCornersOfVertex')
    g.link(idx, face.inputs['Corner Index'])
    g.link(idx, vert.inputs['Corner Index'])
    g.link(idx, first.inputs['Vertex Index'])
    vi, ci = vert.outputs['Vertex Index'], first.outputs['Corner Index']
    fn = g.at('FACE', 'FLOAT_VECTOR', base['normal'], face.outputs['Face Index'])
    directions = []
    for step in (-1, 1):
        c = g.node('GeometryNodeOffsetCornerInFace')
        g.link(idx, c.inputs['Corner Index'])
        c.inputs['Offset'].default_value = step
        p = g.at('CORNER', 'FLOAT_VECTOR', pos, c.outputs['Corner Index'])
        directions.append(g.norm(g.vec('SUBTRACT', p, pos)))
    dot = g.dot(*directions)
    m = g.math('MINIMUM', 1, g.math('ABSOLUTE', dot))
    poly = g.math('ADD', .077980478, g.math('MULTIPLY', -.02164095, m))
    poly = g.math('ADD', -.213300989, g.math('MULTIPLY', poly, m))
    poly = g.math('ADD', 1.5707963267, g.math('MULTIPLY', poly, m))
    angle = g.math('MULTIPLY', g.math('SQRT', g.math('SUBTRACT', 1, m)), poly)
    angle = g.switch('FLOAT', g.math('LESS_THAN', dot, 0), angle, g.math('SUBTRACT', 3.141592653589793, angle))
    weighted = g.accum('CORNER', 'FLOAT_VECTOR', vi, g.scale(fn, angle))
    normal = g.norm(g.at('CORNER', 'FLOAT_VECTOR', weighted, ci))
    has_faces = g.math('GREATER_THAN', first.outputs['Total'], 0)
    normal = g.switch('VECTOR', has_faces, g.norm(pos), normal)
    nc = g.at('POINT', 'FLOAT_VECTOR', normal, vi)
    absdot = g.math('ABSOLUTE', g.dot(nc, fn))
    reciprocal = g.switch('FLOAT', g.math('LESS_THAN', absdot, 1e-8), g.math('DIVIDE', 1, absdot), 1)
    # Native Simple Even uses turning angle: prev-v versus v-next, not BMesh inner angle.
    incoming, outgoing = directions[0], g.scale(directions[1], -1)
    d = g.dot(incoming, outgoing)
    minus = g.length(g.vec('SUBTRACT', incoming, outgoing))
    plus = g.length(g.vec('ADD', incoming, outgoing))
    chord = g.switch('FLOAT', g.math('LESS_THAN', d, 0), minus, plus)
    beta = g.math('MULTIPLY', 2, g.math('ARCSINE', g.math('MINIMUM', 1, g.math('MULTIPLY', chord, .5))))
    beta = g.switch('FLOAT', g.math('LESS_THAN', d, 0), beta, g.math('SUBTRACT', 3.141592653589793, beta))
    beta = g.math('MAXIMUM', beta, 1.1920928955078125e-7)
    a = g.at('CORNER', 'FLOAT', g.accum('CORNER', 'FLOAT', vi, g.math('MULTIPLY', beta, reciprocal)), ci)
    b = g.at('CORNER', 'FLOAT', g.accum('CORNER', 'FLOAT', vi, beta), ci)
    shell = g.switch('FLOAT', has_faces, 0, g.math('DIVIDE', a, b))
    geo, fields = g.capture(geo, 'POINT', normal=('VECTOR', normal), shell=('FLOAT', shell), has_faces=('BOOLEAN', has_faces))
    return geo, fields


def _shell_mesh(g, original, positive, negative, weight, flip_winding=False, complex_mode=False, topology=None):
    original, attribute_data = prepare_attributes(g, original, topology)
    idx = g.node('GeometryNodeInputIndex').outputs[0]
    pos = g.node('GeometryNodeInputPosition').outputs[0]
    original, f = g.capture(original, 'POINT', source_id=('INT', idx), rest=('VECTOR', pos),
                          positive=('VECTOR', positive), negative=('VECTOR', negative), weight=('FLOAT', weight))
    # Extrude all faces at zero offset: retains lower rim vertices, supplies upper skin.
    ex = g.node('GeometryNodeExtrudeMesh', mode='FACES')
    ex.inputs['Individual'].default_value = False
    ex.inputs['Offset'].default_value = (0, 0, 0)
    ex.inputs['Offset Scale'].default_value = 0
    g.link(original, ex.inputs['Mesh'])
    ex.inputs['Selection'].default_value = True
    ex_geo, marks = g.capture(ex.outputs['Mesh'], 'FACE', top=('BOOLEAN', ex.outputs['Top']), rim=('BOOLEAN', ex.outputs['Side']))
    ex_geo, point_marks = g.capture(ex_geo, 'POINT', upper=('BOOLEAN', _point_selection(g, marks['top'])))
    upper = point_marks['upper']
    def encode(geometry, layer):
        bit = int(layer) if isinstance(layer, bool) else g.switch('INT', layer, 0, 1)
        key = g.math('ADD', g.math('MULTIPLY', f['source_id'], 2), bit)
        position = g.combine((g.math('MODULO', key, 512),
                              g.math('MODULO', g.math('FLOOR', g.math('DIVIDE', key, 512)), 512),
                              g.math('FLOOR', g.math('DIVIDE', key, 262144))))
        return g.setpos(geometry, position)
    # Extrude does not duplicate wire edges onto the upper face endpoints. Remove
    # its retained wires and supply a complete upper wire copy with layer=1.
    if not complex_mode:
        neighbors = g.node('GeometryNodeInputMeshEdgeNeighbors').outputs['Face Count']
        wire_selection = g.math('COMPARE', neighbors, 0)
        wires = g.separate(original, 'EDGE', wire_selection)
        edge_degree = g.node('GeometryNodeEdgesOfVertex')
        g.link(idx, edge_degree.inputs['Vertex Index'])
        isolated = g.separate(original, 'POINT', g.math('COMPARE', edge_degree.outputs['Total'], 0))
        delete_wire = g.node('GeometryNodeDeleteGeometry', domain='EDGE', mode='ALL')
        g.link(ex_geo, delete_wire.inputs['Geometry'])
        g.link(wire_selection, delete_wire.inputs['Selection'])
        degree = g.node('GeometryNodeCornersOfVertex')
        g.link(idx, degree.inputs['Vertex Index'])
        delete_loose = g.node('GeometryNodeDeleteGeometry', domain='POINT', mode='ALL')
        g.link(delete_wire.outputs['Geometry'], delete_loose.inputs['Geometry'])
        g.link(g.math('COMPARE', degree.outputs['Total'], 0), delete_loose.inputs['Selection'])
        ex_geo = delete_loose.outputs['Geometry']
    lower_flip = g.node('GeometryNodeFlipFaces')
    g.link(original, lower_flip.inputs['Mesh'])
    join = g.node('GeometryNodeJoinGeometry')
    g.link(encode(ex_geo, upper), join.inputs['Geometry'])
    g.link(encode(lower_flip.outputs['Mesh'], False), join.inputs['Geometry'])
    if not complex_mode:
        g.link(encode(wires, True), join.inputs['Geometry'])
        g.link(encode(isolated, True), join.inputs['Geometry'])
    merge = g.node('GeometryNodeMergeByDistance')
    merge.inputs['Distance'].default_value = .1
    g.link(join.outputs['Geometry'], merge.inputs['Geometry'])
    # Temporary integer-lattice coordinates also carry layer identity; freeze
    # anonymously before restoring real coordinates, never use a private name.
    layer = g.math('GREATER_THAN', g.math('MODULO', g.split(pos)[0], 2), .5)
    geo, layer_data = g.capture(merge.outputs['Geometry'], 'POINT', upper=('BOOLEAN', layer))
    upper = layer_data['upper']
    move = g.switch('VECTOR', upper, f['negative'], f['positive'])
    geo = g.setpos(geo, g.vec('ADD', f['rest'], g.scale(move, f['weight'])))
    flip = g.node('GeometryNodeFlipFaces')
    g.link(geo, flip.inputs['Mesh'])
    g.link(flip_winding, flip.inputs['Selection'])
    geo, identity = g.capture(flip.outputs['Mesh'], 'POINT',
                              source_id=('INT', f['source_id']), upper=('BOOLEAN', upper))
    return finish_attributes(g, geo, original, attribute_data,
                             identity['source_id'], identity['upper'], complex_mode)


def build(group_name=GROUP_NAME, nt=None):
    if not is_supported():
        raise RuntimeError('Solidify requires Blender 5.2+')
    if nt is None:
        nt = bpy.data.node_groups.new(group_name, 'GeometryNodeTree')
    else:
        nt.nodes.clear()
    inputs = [('Geometry', 'Geometry', None), ('Mode', 'Menu', None), ('Thickness', 'Float', .01),
              ('Offset', 'Float', -1.0), ('Even Thickness', 'Bool', False), ('Vertex Group', 'Float', 1.0)]
    sockets = _interface(nt, inputs, [('Geometry', 'Geometry'), ('Warning', 'Bool')])
    mode = sockets[('Mode', 'INPUT')]
    mode.force_non_field = True
    mode.description = 'Simple / Complex (native default Constraints)'
    sockets[('Thickness', 'INPUT')].subtype = 'DISTANCE'
    sockets[('Thickness', 'INPUT')].force_non_field = True
    sockets[('Offset', 'INPUT')].min_value, sockets[('Offset', 'INPUT')].max_value = -1, 1
    sockets[('Offset', 'INPUT')].force_non_field = True
    sockets[('Vertex Group', 'INPUT')].min_value, sockets[('Vertex Group', 'INPUT')].max_value = 0, 1
    sockets[('Vertex Group', 'INPUT')].description = 'Effective point-domain weight, 1 when no group. Connect Named Attribute float weights; use Exists to select 1 for missing groups'
    sockets[('Even Thickness', 'INPUT')].description = 'Simple shell compensation; ignored in Complex as in the native Constraints mode'
    sockets[('Warning', 'OUTPUT')].description = 'Unsupported Complex radial sectors, short-edge merge, singularities or oversized fans: original mesh returned, never silently replaced by Simple'
    nt.description = 'Solidify: Simple and Complex Constraints with disconnected vertex fans split. Other controls use modifier defaults. Warning marks unsupported radial/merge/singularity topology'
    g = _Graph(nt)
    inp = g.node('NodeGroupInput').outputs
    menu = g.node('GeometryNodeMenuSwitch', data_type='BOOLEAN')
    menu.enum_items.clear()
    menu.enum_items.new('Simple')
    menu.enum_items.new('Complex')
    menu.inputs['Simple'].default_value = False
    menu.inputs['Complex'].default_value = True
    g.link(inp['Mode'], menu.inputs['Menu'])
    bpy.context.view_layer.update()
    mode.default_value = 'Simple'
    complex_mode = menu.outputs['Output']
    simple_geo, fields = _simple_fields(g, inp['Geometry'])
    half = g.math('MULTIPLY', inp['Thickness'], .5)
    a = g.math('MULTIPLY', half, g.math('ADD', inp['Offset'], 1))
    b = g.math('MULTIPLY', half, g.math('SUBTRACT', inp['Offset'], 1))
    factor = g.switch('FLOAT', inp['Even Thickness'], 1, fields['shell'])
    plus = g.scale(fields['normal'], g.math('MULTIPLY', g.math('MAXIMUM', a, b), factor))
    minus = g.scale(fields['normal'], g.math('MULTIPLY', g.math('MINIMUM', a, b), factor))
    weight = g.math('MINIMUM', 1, g.math('MAXIMUM', 0, inp['Vertex Group']))
    simple = _shell_mesh(g, simple_geo, plus, minus, weight)
    # Native Complex's no-face early return is global, not per connected island.
    size = g.node('GeometryNodeAttributeDomainSize', component='MESH')
    g.link(inp['Geometry'], size.inputs['Geometry'])
    has_faces = g.math('GREATER_THAN', size.outputs['Face Count'], 0)
    # Freeze user fields before filtering so Index-dependent weights keep input identity.
    complex_source, frozen = g.capture(inp['Geometry'], 'POINT', effective_weight=('FLOAT', weight))
    neighbors = g.node('GeometryNodeInputMeshEdgeNeighbors').outputs['Face Count']
    delete_edges = g.node('GeometryNodeDeleteGeometry', domain='EDGE', mode='ALL')
    g.link(complex_source, delete_edges.inputs['Geometry'])
    g.link(g.math('COMPARE', neighbors, 0), delete_edges.inputs['Selection'])
    corners = g.node('GeometryNodeCornersOfVertex')
    index = g.node('GeometryNodeInputIndex').outputs[0]
    g.link(index, corners.inputs['Vertex Index'])
    delete_points = g.node('GeometryNodeDeleteGeometry', domain='POINT', mode='ALL')
    g.link(delete_edges.outputs['Geometry'], delete_points.inputs['Geometry'])
    g.link(g.math('COMPARE', corners.outputs['Total'], 0), delete_points.inputs['Selection'])
    complex_source, topology = build_topology(g, delete_points.outputs['Geometry'])
    complex_geo, plus, minus, warning = build_complex(g, complex_source, inp['Thickness'], inp['Offset'], topology)
    warning = g.boolean('OR', warning, topology['warning'])
    reverse = g.boolean('NOT', g.math('GREATER_THAN', inp['Thickness'], 0))
    complex_mesh = _shell_mesh(g, complex_geo, plus, minus, frozen['effective_weight'], reverse, True, topology)
    warning = g.boolean('AND', has_faces, warning)
    usable = g.boolean('AND', has_faces, g.boolean('NOT', warning))
    complex_mesh = g.switch('GEOMETRY', usable, inp['Geometry'], complex_mesh)
    geometry = g.switch('GEOMETRY', complex_mode, simple, complex_mesh)
    output = g.node('NodeGroupOutput')
    g.link(geometry, output.inputs['Geometry'])
    g.link(g.boolean('AND', complex_mode, warning), output.inputs['Warning'])
    nt['bi1mcs_version'] = '.'.join(map(str, VERSION))
    nt['bi1mcs_tool'] = GROUP_NAME
    return nt


def get_or_create_group(name=GROUP_NAME):
    nt = bpy.data.node_groups.get(name)
    if nt is None:
        return build(name)
    if nt.bl_idname != 'GeometryNodeTree':
        raise TypeError('Existing group is not a GeometryNodeTree')
    if nt.get('bi1mcs_version') != '.'.join(map(str, VERSION)):
        return build(name, nt)
    return nt
