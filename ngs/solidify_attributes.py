# -*- coding: utf-8 -*-
"""Blender 5.2 Solidify 默认属性的纯 GN 构建助手。

适用可定向单扇双壳（调用方先拆不连通面扇）、默认 rim=True/Constraints/Boundary NONE。
不负责非流形扇拆分、短边合并；壳层身份完全匿名，不占用用户属性名。
调用者须把 prepare 返回的几何送入 shell，并传入输出 POINT 域的匿名
source_id、upper；所有 capture 的几何链必须传播到最终输出。
complex_mode 是构建期 Python bool，不是运行时字段。
"""


def _equal(g, a, b):
    n = g.node('FunctionNodeCompare', data_type='INT', operation='EQUAL')
    g.link(a, n.inputs['A'])
    g.link(b, n.inputs['B'])
    return n.outputs['Result']


def _named(g, name):
    n = g.node('GeometryNodeInputNamedAttribute', data_type='FLOAT')
    n.inputs['Name'].default_value = name
    return n


def _incident(g, vertex, weights):
    n = g.node('GeometryNodeEdgesOfVertex')
    g.link(vertex, n.inputs['Vertex Index'])
    g.link(weights, n.inputs['Weights'])
    return n


def prepare_attributes(g, geometry, topology=None):
    """返回捕获几何和 EDGE/POINT 匿名字段及非 field 存在性 socket。"""
    index = g.node('GeometryNodeInputIndex').outputs[0]
    names = ('crease_vert', 'crease_edge', 'bevel_weight_vert', 'bevel_weight_edge')
    attrs = {name: _named(g, name) for name in names}
    # SampleIndex 的几何上下文冻结 Exists，避免在输出上误判新建层为输入层。
    data = {name + '_exists': g.sample(geometry, 'BOOLEAN', n.outputs['Exists'], 0)
            for name, n in attrs.items()}
    geometry, edge_data = g.capture(geometry, 'EDGE', source_edge=('INT', index))
    data.update(edge_data)
    count = g.node('GeometryNodeInputMeshEdgeNeighbors').outputs['Face Count']
    boundary = _equal(g, count, 1)
    internal = g.math('GREATER_THAN', count, 1)
    minimum = _incident(g, index, index)
    seed = minimum.outputs['Edge Index']
    edge_vertices = g.node('GeometryNodeInputMeshEdgeVertices')
    first_face = g.node('GeometryNodeFaceOfCorner')
    g.link(index, first_face.inputs['Corner Index'])
    first_corner = g.node('GeometryNodeCornersOfEdge')
    g.link(seed, first_corner.inputs['Edge Index'])
    g.link(first_face.outputs['Face Index'], first_corner.inputs['Weights'])
    corner_vertex = g.node('GeometryNodeVertexOfCorner')
    g.link(first_corner.outputs['Corner Index'], corner_vertex.inputs['Corner Index'])
    logical_first = topology['first_vertex'] if topology else edge_vertices.outputs['Vertex Index 1']
    direction = _equal(g, g.at('EDGE', 'INT', logical_first, seed),
                       corner_vertex.outputs['Vertex Index'])
    seed_boundary = g.at('EDGE', 'BOOLEAN', boundary, seed)
    choose_incoming = g.boolean('XNOR', direction, seed_boundary)
    corner_edges = g.node('GeometryNodeEdgesOfCorner')
    g.link(index, corner_edges.inputs['Corner Index'])
    sides = []
    for output in ('Previous Edge Index', 'Next Edge Index'):
        edge = corner_edges.outputs[output]
        is_boundary = g.at('EDGE', 'BOOLEAN', boundary, edge)
        corner = g.node('GeometryNodeCornersOfVertex')
        g.link(index, corner.inputs['Vertex Index'])
        g.link(g.switch('FLOAT', is_boundary, 1, 0), corner.inputs['Weights'])
        sides.append(g.at('CORNER', 'INT', edge, corner.outputs['Corner Index']))
    incoming, outgoing = sides
    has_boundary_edge = _incident(g, index, g.switch('FLOAT', boundary, 1, 0))
    has_boundary = g.at('EDGE', 'BOOLEAN', boundary, has_boundary_edge.outputs['Edge Index'])
    connector_source = g.switch('INT', choose_incoming, outgoing, incoming)
    connector_source = g.switch('INT', has_boundary, -1, connector_source)
    # 单扇边界点恰有两条 boundary；内部点的这些值不用于连接边。
    has_internal = g.math('GREATER_THAN', minimum.outputs['Total'], 2)
    inherited = {}
    for name in ('crease_edge', 'bevel_weight_edge'):
        value = attrs[name].outputs['Attribute']
        greatest = _incident(g, index, g.switch('FLOAT', internal, 1e30, g.math('MULTIPLY', value, -1)))
        max_internal = g.at('EDGE', 'FLOAT', value, greatest.outputs['Edge Index'])
        max_internal = g.switch('FLOAT', has_internal, 0, max_internal)
        boundary_min = g.math('MINIMUM', g.at('EDGE', 'FLOAT', value, incoming),
                              g.at('EDGE', 'FLOAT', value, outgoing))
        if name == 'crease_edge':
            inherited[name] = g.switch('FLOAT', has_internal, boundary_min, max_internal)
        else:
            inherited[name] = g.switch('FLOAT', g.math('GREATER_THAN', boundary_min, 0),
                                       max_internal, g.math('MINIMUM', max_internal, boundary_min))
    crease = g.math('MAXIMUM', attrs['crease_vert'].outputs['Attribute'], inherited['crease_edge'])
    bevel = g.math('MAXIMUM', attrs['bevel_weight_vert'].outputs['Attribute'], inherited['bevel_weight_edge'])
    geometry, point_data = g.capture(
        geometry, 'POINT', connector_source=('INT', connector_source),
        rim_crease=('FLOAT', crease), rim_bevel=('FLOAT', bevel),
        vertex_bevel=('FLOAT', attrs['bevel_weight_vert'].outputs['Attribute']))
    data.update(point_data)
    return geometry, data


def _transfer_edges(g, target, source, target_edge_id, source_edge_id=None, defaults=False):
    n = g.node('GeometryNodeTransferAttributes')
    index = g.node('GeometryNodeInputIndex').outputs[0]
    g.link(target, n.inputs['Target'])
    g.link(source, n.inputs['Source'])
    n.inputs['Exclude Names'].default_value = True
    n.inputs['Attribute Names'].default_value = ''
    # ID socket 使用 implicit Index；只改 default_value 不会覆盖隐式字段。
    # 常量 -1 必须从显式 INT socket 连入，才能禁止其他域的匹配传输。
    negative = g.node('FunctionNodeIntegerMath', operation='ADD')
    negative.inputs[0].default_value = -1
    minus_one = negative.outputs[0]
    for domain in ('Point', 'Edge', 'Face', 'Corner', 'Curve', 'Instance'):
        g.link(minus_one if defaults else index, n.inputs['Target ' + domain + ' ID'])
        g.link(index if defaults else minus_one, n.inputs['Source ' + domain + ' ID'])
    if not defaults:
        if isinstance(target_edge_id, int):
            constant = g.node('FunctionNodeIntegerMath', operation='ADD')
            constant.inputs[0].default_value = target_edge_id
            target_edge_id = constant.outputs[0]
        g.link(target_edge_id, n.inputs['Target Edge ID'])
        g.link(index if source_edge_id is None else source_edge_id, n.inputs['Source Edge ID'])
    return n.outputs['Target']


def _white_colors(g, donor, source):
    # 动态枚举仅 EDGE 颜色层；不能把 Transfer 的透明黑称作 native 白默认。
    for kind, menu in (('FLOAT_COLOR', 'Color'), ('BYTE_COLOR', 'Byte Color')):
        names = g.node('GeometryNodeGetAttributeNames')
        g.link(source, names.inputs['Geometry'])
        names.inputs['Filter Domain'].default_value = True
        names.inputs['Domain'].default_value = 'Edge'
        names.inputs['Filter Data Type'].default_value = True
        names.inputs['Data Type'].default_value = menu
        length = g.node('GeometryNodeListLength', data_type='STRING')
        g.link(names.outputs['Names'], length.inputs['List'])
        end = g.node('GeometryNodeRepeatOutput')
        end.repeat_items.clear()
        end.repeat_items.new('GEOMETRY', 'Geometry')
        start = g.node('GeometryNodeRepeatInput')
        start.pair_with_output(end)
        g.link(length.outputs['Length'], start.inputs['Iterations'])
        g.link(donor, start.inputs['Geometry'])
        item = g.node('GeometryNodeListGetItem', socket_type='STRING', structure_type='SINGLE')
        g.link(names.outputs['Names'], item.inputs['List'])
        g.link(start.outputs['Iteration'], item.inputs['Index'])
        store = g.node('GeometryNodeStoreNamedAttribute', domain='EDGE', data_type=kind)
        g.link(start.outputs['Geometry'], store.inputs['Geometry'])
        g.link(item.outputs['Value'], store.inputs['Name'])
        store.inputs['Value'].default_value = (1, 1, 1, 1)
        g.link(store.outputs['Geometry'], end.inputs['Geometry'])
        donor = end.outputs['Geometry']
    return donor


def _store_connector(g, geometry, name, selection, value, exists):
    n = g.node('GeometryNodeStoreNamedAttribute', domain='EDGE', data_type='FLOAT')
    n.inputs['Name'].default_value = name
    g.link(geometry, n.inputs['Geometry'])
    g.link(selection, n.inputs['Selection'])
    g.link(value, n.inputs['Value'])
    return g.switch('GEOMETRY', exists, geometry, n.outputs['Geometry'])


def finish_attributes(g, geometry, source_geometry, data, source_id, upper, complex_mode=False):
    """恢复两皮旧 EDGE 及新连接默认值；不会按真实位置分类零厚度边。"""
    if type(complex_mode) is not bool:
        raise TypeError('complex_mode 必须是构建期 Python bool')
    edge = g.node('GeometryNodeInputMeshEdgeVertices')
    a, b = edge.outputs['Vertex Index 1'], edge.outputs['Vertex Index 2']
    sa, sb = g.at('POINT', 'INT', source_id, a), g.at('POINT', 'INT', source_id, b)
    ua, ub = g.at('POINT', 'BOOLEAN', upper, a), g.at('POINT', 'BOOLEAN', upper, b)
    connector = g.boolean('AND', _equal(g, sa, sb), g.boolean('XOR', ua, ub))
    # 捕获输出 EDGE 身份，后续 Transfer/Repeat/Store 都沿此几何链传播。
    geometry, output = g.capture(geometry, 'EDGE', connector=('BOOLEAN', connector), point_id=('INT', sa),
                                 old_edge=('INT', data['source_edge']))
    connector, sa = output['connector'], output['point_id']
    if complex_mode:
        source = g.sample(source_geometry, 'INT', data['connector_source'], sa)
        target = g.switch('INT', connector, output['old_edge'], source)
        geometry = _transfer_edges(g, geometry, source_geometry, target)
    else:
        line = g.node('GeometryNodeMeshLine')
        line.inputs['Count'].default_value = 2
        donor = _transfer_edges(g, line.outputs['Mesh'], source_geometry, None, defaults=True)
        donor = _white_colors(g, donor, source_geometry)
        # 供体仅有边 ID 0；旧边用 ID 1（非负且不匹配），保留已有值。
        geometry = _transfer_edges(g, geometry, donor, g.switch('INT', connector, 1, 0))
        size = g.node('GeometryNodeAttributeDomainSize', component='MESH')
        g.link(source_geometry, size.inputs['Geometry'])
        # 连接用不匹配的非负 ID，保留前一级供体写入的默认值。
        geometry = _transfer_edges(g, geometry, source_geometry,
                                   g.switch('INT', connector, output['old_edge'], size.outputs['Edge Count']))
    crease_exists = data['crease_edge_exists']
    if complex_mode:
        crease_exists = g.boolean('OR', crease_exists, data['crease_vert_exists'])
    bevel_exists = g.boolean('OR', data['bevel_weight_edge_exists'], data['bevel_weight_vert_exists'])
    crease = g.sample(source_geometry, 'FLOAT', data['rim_crease'], sa) if complex_mode else 0
    bevel = g.sample(source_geometry, 'FLOAT', data['rim_bevel' if complex_mode else 'vertex_bevel'], sa)
    geometry = _store_connector(g, geometry, 'crease_edge', connector, crease, crease_exists)
    geometry = _store_connector(g, geometry, 'bevel_weight_edge', connector, bevel, bevel_exists)
    if complex_mode:
        remove = g.node('GeometryNodeRemoveAttribute')
        remove.inputs['Name'].default_value = 'crease_vert'
        g.link(geometry, remove.inputs['Geometry'])
        geometry = remove.outputs['Geometry']
    return geometry
