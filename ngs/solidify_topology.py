# -*- coding: utf-8 -*-
"""Solidify Complex 的纯 GN 顶点面扇拆分与源边逻辑方向。

调用方须先冻结 POINT 权重并过滤松散元素；无面输入在主图旁路。
返回几何保留各域源属性，first_vertex/last_vertex 是该几何 EDGE 域
匿名 INT：索引指向实际新顶点，但方向对齐输入边的存储端点顺序。
几何实端点不重写；求解器及属性准备须显式消费这两个逻辑端点。
warning 是非字段 Boolean；径向边 >2、64 轮残差和角规模越界告警。
Python 仅用于构建节点图，几何求值全部由 GN 完成。
"""

MAX_ROUNDS = 64
MAX_CORNERS = 16777216


def _int(g, operation, a, b=0):
    node = g.node('FunctionNodeIntegerMath', operation=operation)
    g.link(a, node.inputs[0])
    g.link(b, node.inputs[1])
    return node.outputs[0]


def _equal(g, a, b):
    node = g.node('FunctionNodeCompare', data_type='INT', operation='EQUAL')
    g.link(a, node.inputs['A'])
    g.link(b, node.inputs['B'])
    return node.outputs['Result']


def _sample(g, geometry, domain, data_type, value, index):
    node = g.node('GeometryNodeSampleIndex', domain=domain, data_type=data_type)
    node.clamp = False
    g.link(geometry, node.inputs['Geometry'])
    g.link(value, node.inputs['Value'])
    g.link(index, node.inputs['Index'])
    return node.outputs['Value']


def _offset_corner(g, corner, step):
    node = g.node('GeometryNodeOffsetCornerInFace')
    g.link(corner, node.inputs['Corner Index'])
    node.inputs['Offset'].default_value = step
    return node.outputs['Corner Index']


def _edge_corner(g, edge, rank):
    node = g.node('GeometryNodeCornersOfEdge')
    g.link(edge, node.inputs['Edge Index'])
    node.inputs['Sort Index'].default_value = rank
    return node.outputs['Corner Index']


def build_topology(g, geometry):
    """返回 (几何, {first_vertex, last_vertex, warning})，不建双壳。"""
    index = g.node('GeometryNodeInputIndex').outputs[0]
    position = g.node('GeometryNodeInputPosition').outputs[0]
    source, point = g.capture(geometry, 'POINT', pid=('INT', index), rest=('VECTOR', position))
    old_vertices = g.node('GeometryNodeInputMeshEdgeVertices')
    source, edge = g.capture(source, 'EDGE', eid=('INT', index),
                             old_first=('INT', old_vertices.outputs['Vertex Index 1']))
    source, face = g.capture(source, 'FACE', fid=('INT', index))
    source, corner = g.capture(source, 'CORNER', cid=('INT', index))

    vertices = g.node('GeometryNodeVertexOfCorner')
    g.link(index, vertices.inputs['Corner Index'])
    vertex = vertices.outputs['Vertex Index']
    edges = g.node('GeometryNodeEdgesOfCorner')
    g.link(index, edges.inputs['Corner Index'])
    face_count = g.node('GeometryNodeInputMeshEdgeNeighbors').outputs['Face Count']
    neighbors = []
    for name, own_corner in (('Next Edge Index', index),
                             ('Previous Edge Index', _offset_corner(g, index, -1))):
        edge_index = edges.outputs[name]
        c0, c1 = (_edge_corner(g, edge_index, rank) for rank in (0, 1))
        other = g.switch('INT', _equal(g, c0, own_corner), c0, c1)
        other_vertex = g.at('CORNER', 'INT', vertex, other)
        same_point = g.switch('INT', _equal(g, other_vertex, vertex),
                              _offset_corner(g, other, 1), other)
        neighbors.append(g.switch('INT', _equal(g, g.at('EDGE', 'INT', face_count, edge_index), 2),
                                  index, same_point))
    geometry, adjacency = g.capture(source, 'CORNER', label=('INT', index),
                                     left=('INT', neighbors[0]), right=('INT', neighbors[1]))
    end = g.node('GeometryNodeRepeatOutput')
    end.repeat_items.new('INT', 'Label')
    begin = g.node('GeometryNodeRepeatInput')
    begin.pair_with_output(end)
    begin.inputs['Iterations'].default_value = MAX_ROUNDS
    g.link(geometry, begin.inputs['Geometry'])
    g.link(adjacency['label'], begin.inputs['Label'])
    work, label = begin.outputs['Geometry'], begin.outputs['Label']
    left, right = (_sample(g, work, 'CORNER', 'INT', label, adjacency[name])
                   for name in ('left', 'right'))
    reduced = _int(g, 'MINIMUM', _int(g, 'MINIMUM', label, left), right)
    work, updated = g.capture(work, 'CORNER', label=('INT', reduced))
    g.link(work, end.inputs['Geometry'])
    g.link(updated['label'], end.inputs['Label'])
    geometry, label = end.outputs['Geometry'], end.outputs['Label']

    left, right = (_sample(g, geometry, 'CORNER', 'INT', label, adjacency[name])
                   for name in ('left', 'right'))
    residual = g.boolean('NOT', _equal(g, label,
        _int(g, 'MINIMUM', label, _int(g, 'MINIMUM', left, right))))
    not_converged = g.math('GREATER_THAN', g.stat(geometry, 'CORNER',
        g.switch('FLOAT', residual, 0, 1)), 0)
    radial = g.math('GREATER_THAN', g.stat(source, 'EDGE', face_count), 2)
    size = g.node('GeometryNodeAttributeDomainSize', component='MESH')
    g.link(source, size.inputs['Geometry'])
    too_large = g.boolean('NOT', g.math('LESS_THAN', size.outputs['Face Corner Count'], MAX_CORNERS))
    warning = g.boolean('OR', not_converged, g.boolean('OR', radial, too_large))

    split = g.node('GeometryNodeSplitEdges')
    g.link(geometry, split.inputs['Mesh'])
    split.inputs['Selection'].default_value = True
    corners = g.node('GeometryNodeCornersOfVertex')
    g.link(index, corners.inputs['Vertex Index'])
    fan = g.at('CORNER', 'INT', label, corners.outputs['Corner Index'])
    geometry, split_data = g.capture(split.outputs['Mesh'], 'POINT', fan=('INT', fan))
    key = split_data['fan']
    lattice = g.combine((_int(g, 'FLOORED_MODULO', key, 512),
                         _int(g, 'FLOORED_MODULO', _int(g, 'DIVIDE', key, 512), 512),
                         _int(g, 'DIVIDE', key, 262144)))
    merge = g.node('GeometryNodeMergeByDistance')
    merge.inputs['Distance'].default_value = .1
    g.link(g.setpos(geometry, lattice), merge.inputs['Geometry'])

    # 实坐标只恢复位置；属性按各域源 ID 复制，不靠近邻或插值。
    transfer = g.node('GeometryNodeTransferAttributes')
    g.link(merge.outputs['Geometry'], transfer.inputs['Target'])
    g.link(source, transfer.inputs['Source'])
    transfer.inputs['Exclude Names'].default_value = True
    transfer.inputs['Attribute Names'].default_value = ''
    identities = {'Point': point['pid'], 'Edge': edge['eid'],
                  'Face': face['fid'], 'Corner': corner['cid']}
    minus_one = _int(g, 'ADD', -1)
    for domain in ('Point', 'Edge', 'Face', 'Corner', 'Curve', 'Instance'):
        g.link(identities.get(domain, minus_one), transfer.inputs['Target ' + domain + ' ID'])
        g.link(index if domain in identities else minus_one, transfer.inputs['Source ' + domain + ' ID'])
    geometry = g.setpos(transfer.outputs['Target'],
        _sample(g, source, 'POINT', 'FLOAT_VECTOR', point['rest'], point['pid']))
    sort = g.node('GeometryNodeSortElements', domain='EDGE')
    g.link(geometry, sort.inputs['Geometry'])
    g.link(edge['eid'], sort.inputs['Sort Weight'])

    # Merge/Sort 可改变边实端点方向；只校正逻辑索引，不修改几何。
    actual = g.node('GeometryNodeInputMeshEdgeVertices')
    first, last = actual.outputs['Vertex Index 1'], actual.outputs['Vertex Index 2']
    aligned = _equal(g, g.at('POINT', 'INT', point['pid'], first), edge['old_first'])
    geometry, logical = g.capture(sort.outputs['Geometry'], 'EDGE',
        first_vertex=('INT', g.switch('INT', aligned, last, first)),
        last_vertex=('INT', g.switch('INT', aligned, first, last)))
    return geometry, {'first_vertex': logical['first_vertex'],
                      'last_vertex': logical['last_vertex'], 'warning': warning}
