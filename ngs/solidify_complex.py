# -*- coding: utf-8 -*-
"""纯 GN Solidify Complex / Constraints 的可定向流形顶点求解器。

固定参照 MOD_solidify_nonmanifold_fbe6228777e7.cc:1454-1664。
入口 build_complex(g, geometry, thickness, offset) 返回原拓扑的捕获几何、
POINT 正侧位移、POINT 负侧位移、非 field Warning。没有图内 Python。
默认约定：Constraints、merge=1e-4、boundary=NONE；不消费 Even。
负厚度绕序、顶点组权重（须在 NZ 之后应用）、双壳和边界桥由调用方处理。

入口支持单一顶点面扇的可定向流形，最多 64 个相邻面；调用方先拆分不连通面扇。重复区按原生有序平面
队列逐对扫描、交换与合并，不以 Simple 或顶点平均法线代替 Constraints。
Warning 标识短边合并、非流形边、共享边同向、分裂面扇、松散元素、退化面、
超出求解规模和原生可能拆分的反折扇。不实现 Complex 的非流形拓扑：原生
一个源点可能产生超过两个副本，此接口不能交给双壳拓扑冒充完整支持。
边界修正、平面面权重、厚度 clamp 不属于此入口的契约。
"""
from .extrude import _Graph

MAX_VALENCE = 64
MERGE_THRESHOLD = 1e-4
PLANE_THRESHOLD = 0.98
FLOAT_EPSILON = 1.1920928955078125e-7
DAMPING = 5e-5
DEFAULT_THICKNESS_MODE = 'CONSTRAINTS'
DEFAULT_BOUNDARY_MODE = 'NONE'


def _dot(g, a, b):
    x, y = g.split(a), g.split(b)
    terms = [g.math('MULTIPLY', u, v) for u, v in zip(x, y)]
    return g.math('ADD', g.math('ADD', terms[0], terms[1]), terms[2])


def _normalize(g, vector):
    squared = _dot(g, vector, vector)
    factor = g.math('DIVIDE', 1, g.math('SQRT', squared))
    factor = g.switch('FLOAT', g.math('GREATER_THAN', squared, 1e-35), 0, factor)
    return g.scale(vector, factor)


def _eq(g, a, b):
    n = g.node('FunctionNodeCompare', data_type='INT', operation='EQUAL')
    g.link(a, n.inputs['A'])
    g.link(b, n.inputs['B'])
    return n.outputs['Result']


def _all(g, *values):
    result = values[0]
    for value in values[1:]:
        result = g.boolean('AND', result, value)
    return result


def _any(g, *values):
    result = values[0]
    for value in values[1:]:
        result = g.boolean('OR', result, value)
    return result


def _corner(g, vertex, rank=0, weights=None):
    n = g.node('GeometryNodeCornersOfVertex')
    g.link(vertex, n.inputs['Vertex Index'])
    g.link(rank, n.inputs['Sort Index'])
    if weights is not None:
        g.link(weights, n.inputs['Weights'])
    return n.outputs['Corner Index'], n.outputs['Total']


def _edge_corners(g, edge, rank=0):
    n = g.node('GeometryNodeCornersOfEdge')
    g.link(edge, n.inputs['Edge Index'])
    g.link(rank, n.inputs['Sort Index'])
    return n.outputs['Corner Index']


def _offset_corner(g, corner, offset):
    n = g.node('GeometryNodeOffsetCornerInFace')
    g.link(corner, n.inputs['Corner Index'])
    g.link(offset, n.inputs['Offset'])
    return n.outputs['Corner Index']


def _repeat(g, iterations, geometry, **fields):
    end = g.node('GeometryNodeRepeatOutput')
    for name, (dtype, _) in fields.items():
        end.repeat_items.new(dtype, name)
    start = g.node('GeometryNodeRepeatInput')
    start.pair_with_output(end)
    g.link(iterations, start.inputs['Iterations'])
    g.link(geometry, start.inputs['Geometry'])
    for name, (_, value) in fields.items():
        g.link(value, start.inputs[name])
    return start, end


def _finish(g, end, geometry, domain, **fields):
    geometry, frozen = g.capture(geometry, domain, **fields)
    g.link(geometry, end.inputs['Geometry'])
    for name, value in frozen.items():
        g.link(value, end.inputs[name])
    return end.outputs['Geometry'], {name: end.outputs[name] for name in fields}


def _sample(g, geometry, domain, dtype, field, index):
    n = g.node('GeometryNodeSampleIndex', domain=domain, data_type=dtype)
    n.clamp = False
    g.link(geometry, n.inputs['Geometry'])
    g.link(field, n.inputs['Value'])
    g.link(index, n.inputs['Index'])
    return n.outputs['Value']


def _plane(g, geometry, normal, vertex, rank, index):
    corner, _ = _corner(g, vertex, rank, index)
    return _sample(g, geometry, 'CORNER', 'FLOAT_VECTOR', normal, corner)


def _scan_pairs(g, geometry, normal, count, active, index, maximum):
    # 与源码双重循环同序；best + FLT_EPSILON 不可用排序取最大值代替。
    limit = g.math('MINIMUM', MAX_VALENCE, g.stat(
        geometry, 'POINT', g.switch('FLOAT', active, 0, count)))
    loops = g.math('MULTIPLY', limit, limit)
    begin, end = _repeat(g, loops, geometry,
                         best=('FLOAT', -1.0 if maximum else 2.0),
                         a=('INT', 0), b=('INT', 0))
    geo = begin.outputs['Geometry']
    iteration = begin.outputs['Iteration']
    k = g.math('FLOOR', g.math('DIVIDE', iteration, limit))
    m = g.math('MODULO', iteration, limit)
    n0 = _plane(g, geo, normal, index, k, index)
    n1 = _plane(g, geo, normal, index, m, index)
    p = _dot(g, n0, n1)
    valid = _all(g, active, g.math('LESS_THAN', k, m),
                 g.math('LESS_THAN', m, count))
    threshold = g.math('ADD', begin.outputs['best'], FLOAT_EPSILON) if maximum else begin.outputs['best']
    better = g.math('GREATER_THAN' if maximum else 'LESS_THAN', p, threshold)
    replace = g.boolean('AND', valid, better)
    return _finish(g, end, geo, 'POINT',
                   best=('FLOAT', g.switch('FLOAT', replace, begin.outputs['best'], p)),
                   a=('INT', g.switch('INT', replace, begin.outputs['a'], k)),
                   b=('INT', g.switch('INT', replace, begin.outputs['b'], m)))


def _native_start(g, geometry, count, closed, first, outgoing, incoming,
                  next_fan, previous_fan, vertex, edge_first, edge_end, index, back):
    # 源码 977-1094：每次扫描原边索引，可能向队首插入；闭扇首边不是固定 min。
    left = _sample(g, geometry, 'CORNER', 'INT', previous_fan, first)
    right = first
    if back:
        left, right = right, left
    rounds = g.math('MAXIMUM', 0, g.math('SUBTRACT', g.math('MINIMUM',
        MAX_VALENCE, g.stat(geometry, 'POINT', g.switch('FLOAT', closed, 0, count))), 1))
    begin, end = _repeat(g, rounds, geometry, left=('INT', left), right=('INT', right), start=('INT', right))
    geo = begin.outputs['Geometry']
    left, right = begin.outputs['left'], begin.outputs['right']
    logical_out, logical_in = (incoming, outgoing) if back else (outgoing, incoming)
    left_edge = _sample(g, geo, 'CORNER', 'INT', logical_out, left)
    right_edge = _sample(g, geo, 'CORNER', 'INT', logical_in, right)
    loop_start = _sample(g, geo, 'CORNER', 'INT', vertex,
                         _offset_corner(g, left, -1)) if back else index
    face_zero = _eq(g, loop_start, g.at('EDGE', 'INT', edge_first if back else edge_end, left_edge))
    prepend = _any(g, g.math('LESS_THAN', left_edge, right_edge),
                   _all(g, _eq(g, left_edge, right_edge), face_zero))
    walking = _all(g, closed, g.math('LESS_THAN', begin.outputs['Iteration'], g.math('SUBTRACT', count, 1)))
    new_left = _sample(g, geo, 'CORNER', 'INT', next_fan if back else previous_fan, left)
    new_right = _sample(g, geo, 'CORNER', 'INT', previous_fan if back else next_fan, right)
    return _finish(g, end, geo, 'POINT',
                   left=('INT', g.switch('INT', _all(g, walking, prepend), left, new_left)),
                   right=('INT', g.switch('INT', _all(g, walking, g.boolean('NOT', prepend)), right, new_right)),
                   start=('INT', g.switch('INT', _all(g, walking, prepend), begin.outputs['start'], left)))


def _solve(g, geometry, queue_normal, count, slot, vertex, index, offset):
    active = g.math('GREATER_THAN', count, 2)
    geo, pair = _scan_pairs(g, geometry, queue_normal, count, active, index, False)
    a = g.at('POINT', 'INT', pair['a'], vertex)
    b = g.at('POINT', 'INT', pair['b'], vertex)
    # 原生先交换 min_n0 与 0，再交换原 min_n1 与 1。
    second = g.switch('INT', _eq(g, slot, 1), slot, b)
    second = g.switch('INT', _eq(g, slot, b), second, 1)
    selected = g.switch('INT', _eq(g, second, 0), second, a)
    selected = g.switch('INT', _eq(g, second, a), selected, 0)
    selected = g.switch('INT', g.at('POINT', 'BOOLEAN', active, vertex), slot, selected)
    reordered = _plane(g, geo, queue_normal, vertex, selected, index)
    geo, f = g.capture(geo, 'CORNER', normal=('VECTOR', reordered))
    normal = f['normal']

    # 第三平面：最小 max(dot(n0,nk), dot(n1,nk))；相等时取最后一个。
    limit = g.math('MINIMUM', MAX_VALENCE, g.stat(geo, 'POINT', count))
    start, end = _repeat(g, limit, geo, best=('FLOAT', 1.0), third=('INT', 2))
    work = start.outputs['Geometry']
    k = start.outputs['Iteration']
    nk = _plane(g, work, normal, index, k, index)
    n0 = _plane(g, work, normal, index, 0, index)
    n1 = _plane(g, work, normal, index, 1, index)
    p = g.math('MAXIMUM', _dot(g, n0, nk), _dot(g, n1, nk))
    replace = _all(g, active, g.math('GREATER_THAN', k, 1),
                   g.math('LESS_THAN', k, count),
                   g.boolean('NOT', g.math('GREATER_THAN', p, start.outputs['best'])))
    geo, third = _finish(g, end, work, 'POINT',
                        best=('FLOAT', g.switch('FLOAT', replace, start.outputs['best'], p)),
                        third=('INT', g.switch('INT', replace, start.outputs['third'], k)))
    t = g.at('POINT', 'INT', third['third'], vertex)
    selected = g.switch('INT', _eq(g, slot, 2), slot, t)
    selected = g.switch('INT', _eq(g, slot, t), selected, 2)
    selected = g.switch('INT', g.at('POINT', 'BOOLEAN', active, vertex), slot, selected)
    geo, f = g.capture(geo, 'CORNER', normal=('VECTOR', _plane(g, geo, normal, vertex, selected, index)))
    normal = f['normal']

    # 每轮找最相似的一对，平均归一化，并真正 memmove 删除后一平面。
    rounds = g.math('MAXIMUM', 0, g.math('SUBTRACT', limit, 2))
    begin, end = _repeat(g, rounds, geo, normal=('VECTOR', normal),
                         count=('INT', count), active=('BOOLEAN', active))
    work = begin.outputs['Geometry']
    normal = begin.outputs['normal']
    count = begin.outputs['count']
    active = _all(g, begin.outputs['active'], g.math('GREATER_THAN', count, 2))
    work, pair = _scan_pairs(g, work, normal, count, active, index, True)
    merge = _all(g, active, g.boolean('NOT', g.math('LESS_THAN', pair['best'], PLANE_THRESHOLD)))
    a, b = pair['a'], pair['b']
    na = _plane(g, work, normal, index, a, index)
    nb = _plane(g, work, normal, index, b, index)
    average = _normalize(g, g.vec('ADD', na, nb))
    bc = g.at('POINT', 'INT', b, vertex)
    source_slot = g.switch('INT', g.boolean('NOT', g.math('LESS_THAN', slot, bc)), slot,
                           g.math('ADD', slot, 1))
    moved = _plane(g, work, normal, vertex, source_slot, index)
    merged = g.switch('VECTOR', _eq(g, slot, g.at('POINT', 'INT', a, vertex)), moved,
                      g.at('POINT', 'FLOAT_VECTOR', average, vertex))
    normal = g.switch('VECTOR', g.at('POINT', 'BOOLEAN', merge, vertex), normal, merged)
    work, f = g.capture(work, 'CORNER', normal=('VECTOR', normal))
    g.link(f['normal'], end.inputs['normal'])
    work, f = g.capture(work, 'POINT',
                       count=('INT', g.switch('INT', merge, count, g.math('SUBTRACT', count, 1))),
                       active=('BOOLEAN', merge))
    g.link(work, end.inputs['Geometry'])
    g.link(f['count'], end.inputs['count'])
    g.link(f['active'], end.inputs['active'])
    geo = end.outputs['Geometry']
    normal, count = end.outputs['normal'], end.outputs['count']
    n0 = _plane(g, geo, normal, index, 0, index)
    n1 = _plane(g, geo, normal, index, 1, index)
    n2 = _plane(g, geo, normal, index, 2, index)
    q = _dot(g, n0, n1)
    d = g.math('SUBTRACT', 1, g.math('MULTIPLY', q, q))
    stop = g.math('SUBTRACT', .999, g.math('MULTIPLY', .05, g.math('ABSOLUTE', offset)))
    regular = _all(g, g.math('GREATER_THAN', d, 10 * FLOAT_EPSILON),
                   g.math('LESS_THAN', q, stop))
    factor = g.switch('FLOAT', regular,
                      g.math('DIVIDE', 1, g.math('ADD', 1, g.math('ABSOLUTE', q))),
                      g.math('DIVIDE', g.math('SUBTRACT', 1, q), d))
    two = g.scale(g.vec('ADD', n0, n1), factor)
    move = _normalize(g, g.vec('CROSS_PRODUCT', n0, n1))
    third_d = _dot(g, n2, move)
    correction = g.scale(move, g.math('DIVIDE', g.math('SUBTRACT', 1, _dot(g, n2, two)), third_d))
    three = g.switch('VECTOR', g.math('GREATER_THAN', g.math('ABSOLUTE', third_d), .02),
                     two, g.vec('ADD', two, correction))

    # >3: (sum(nn^T) + 5e-5 I)^-1 sum(n)，列向量余子式求逆。
    nc = g.switch('VECTOR', g.math('LESS_THAN', slot, g.at('POINT', 'INT', count, vertex)),
                  (0, 0, 0), normal)
    xyz = g.split(nc)
    first, _ = _corner(g, index, 0, index)
    rows = []
    for axis in range(3):
        total = g.accum('CORNER', 'FLOAT_VECTOR', vertex, g.scale(nc, xyz[axis]))
        row = g.at('CORNER', 'FLOAT_VECTOR', total, first)
        rows.append(g.vec('ADD', row, g.combine(tuple(DAMPING if i == axis else 0 for i in range(3)))))
    rhs = g.at('CORNER', 'FLOAT_VECTOR', g.accum('CORNER', 'FLOAT_VECTOR', vertex, nc), first)
    c0 = g.vec('CROSS_PRODUCT', rows[1], rows[2])
    c1 = g.vec('CROSS_PRODUCT', rows[2], rows[0])
    c2 = g.vec('CROSS_PRODUCT', rows[0], rows[1])
    determinant = _dot(g, rows[0], c0)
    ls = g.scale(g.combine((_dot(g, c0, rhs), _dot(g, c1, rhs), _dot(g, c2, rhs))),
                 g.math('DIVIDE', 1, determinant))
    result = g.switch('VECTOR', _eq(g, count, 1), two, n0)
    result = g.switch('VECTOR', _eq(g, count, 3), result, three)
    result = g.switch('VECTOR', g.math('GREATER_THAN', count, 3), result, ls)
    result = g.switch('VECTOR', _eq(g, count, 0), result, (0, 0, 0))
    bad = _any(g, _all(g, g.math('GREATER_THAN', count, 1), g.math('LESS_THAN', q, -.999)),
               _all(g, g.math('GREATER_THAN', count, 3),
                    g.boolean('NOT', g.math('GREATER_THAN', determinant, 0))))
    return geo, result, bad


def build_complex(g, geometry, thickness, offset, topology=None):
    """构建 Constraints，不建双壳；输出位移已在原 POINT 域冻结。"""
    if not isinstance(g, _Graph):
        raise TypeError('build_complex 需要 extrude._Graph 或兼容子类')
    index = g.node('GeometryNodeInputIndex').outputs[0]
    normal = g.node('GeometryNodeInputNormal').outputs[0]
    position = g.node('GeometryNodeInputPosition').outputs[0]
    geo, base = g.capture(geometry, 'FACE', normal=('VECTOR', normal))
    face = g.node('GeometryNodeFaceOfCorner')
    vert = g.node('GeometryNodeVertexOfCorner')
    edges = g.node('GeometryNodeEdgesOfCorner')
    for node in (face, vert, edges):
        g.link(index, node.inputs['Corner Index'])
    vertex = vert.outputs['Vertex Index']
    outgoing, incoming = edges.outputs['Next Edge Index'], edges.outputs['Previous Edge Index']
    face_count = g.node('GeometryNodeInputMeshEdgeNeighbors').outputs['Face Count']
    edge_vertices = g.node('GeometryNodeInputMeshEdgeVertices')
    edge_end = topology['last_vertex'] if topology else edge_vertices.outputs['Vertex Index 2']
    edge_first = topology['first_vertex'] if topology else edge_vertices.outputs['Vertex Index 1']
    previous = _offset_corner(g, index, -1)
    c0 = _edge_corners(g, incoming, 0)
    c1 = _edge_corners(g, incoming, 1)
    next_fan = g.switch('INT', _eq(g, c0, previous), c0, c1)
    next_fan = g.switch('INT', _eq(g, g.at('EDGE', 'INT', face_count, incoming), 1), next_fan, -1)
    o0, o1 = _edge_corners(g, outgoing, 0), _edge_corners(g, outgoing, 1)
    other = g.switch('INT', _eq(g, o0, index), o0, o1)
    previous_fan = _offset_corner(g, other, 1)
    previous_fan = g.switch('INT', _eq(g, g.at('EDGE', 'INT', face_count, outgoing), 1), previous_fan, -1)
    boundary_start = _eq(g, g.at('EDGE', 'INT', face_count, outgoing), 1)
    acc = g.node('GeometryNodeAccumulateField', domain='CORNER', data_type='INT')
    g.link(vertex, acc.inputs['Group ID'])
    acc.inputs['Value'].default_value = 1
    slot = g.math('SUBTRACT', acc.outputs['Leading'], 1)
    geo, corner_data = g.capture(geo, 'CORNER',
        vertex=('INT', vertex), outgoing=('INT', outgoing), incoming=('INT', incoming),
        normal=('VECTOR', g.at('FACE', 'FLOAT_VECTOR', base['normal'], face.outputs['Face Index'])),
        next_fan=('INT', next_fan), previous_fan=('INT', previous_fan), slot=('INT', slot))
    vertex, outgoing, incoming, next_fan, previous_fan, slot = (corner_data[k] for k in
        ('vertex', 'outgoing', 'incoming', 'next_fan', 'previous_fan', 'slot'))
    first, valence = _corner(g, index, 0, index)
    boundary_count = g.at('CORNER', 'INT', g.accum('CORNER', 'INT', vertex,
                         g.switch('INT', boundary_start, 0, 1)), first)
    boundary_corner = g.at('CORNER', 'INT', g.accum('CORNER', 'INT', vertex,
                          g.switch('INT', boundary_start, 0, index)), first)
    minimum_edge = g.node('GeometryNodeEdgesOfVertex')
    g.link(index, minimum_edge.inputs['Vertex Index'])
    g.link(index, minimum_edge.inputs['Weights'])
    e = minimum_edge.outputs['Edge Index']
    a, b = _edge_corners(g, e, 0), _edge_corners(g, e, 1)
    start_corner = g.switch('INT', _eq(g, g.at('CORNER', 'INT', vertex, a), index), b, a)
    start_corner = g.switch('INT', g.math('GREATER_THAN', boundary_count, 0), start_corner, boundary_corner)
    geo, points = g.capture(geo, 'POINT', count=('INT', valence),
                           start=('INT', start_corner), boundary=('INT', boundary_count),
                           thickness=('FLOAT', thickness), offset=('FLOAT', offset))
    count, start_corner = points['count'], points['start']
    limit = g.math('MINIMUM', MAX_VALENCE, g.stat(geo, 'POINT', count))
    begin, end = _repeat(g, limit, geo, cursor=('INT', start_corner), rank=('INT', -1))
    current = begin.outputs['cursor']
    walking = g.math('LESS_THAN', begin.outputs['Iteration'], count)
    hit = _all(g, _eq(g, index, g.at('POINT', 'INT', current, vertex)),
               g.at('POINT', 'BOOLEAN', walking, vertex))
    work, rank_data = g.capture(begin.outputs['Geometry'], 'CORNER',
                               rank=('INT', g.switch('INT', hit, begin.outputs['rank'], begin.outputs['Iteration'])))
    g.link(rank_data['rank'], end.inputs['rank'])
    next_cursor = _sample(g, work, 'CORNER', 'INT', next_fan, current)
    next_cursor = g.switch('INT', g.math('LESS_THAN', current, 0), next_cursor, -1)
    next_cursor = g.switch('INT', walking, current, next_cursor)
    work, cursor_data = g.capture(work, 'POINT', cursor=('INT', next_cursor))
    g.link(work, end.inputs['Geometry'])
    g.link(cursor_data['cursor'], end.inputs['cursor'])
    geo = end.outputs['Geometry']
    rank = end.outputs['rank']
    visited = g.at('CORNER', 'INT', g.accum('CORNER', 'INT', vertex,
                       g.switch('INT', g.math('LESS_THAN', rank, 0), 1, 0)), first)

    # 原生每隔一条 edge 入队，内部 edge 的 faces[0]/faces[1] 顺序优先。
    displacements = []
    solver_bad = []
    for back in (False, True):
        closed = _eq(g, points['boundary'], 0)
        start_geo, native = _native_start(g, geo, count, closed, start_corner,
            outgoing, incoming, next_fan, previous_fan, vertex, edge_first, edge_end, index, back)
        native_rank = _sample(g, start_geo, 'CORNER', 'INT', rank, native['start'])
        start_geo, anchor_data = g.capture(start_geo, 'POINT', anchor=('INT', native_rank))
        native_rank = _sample(g, start_geo, 'POINT', 'INT', anchor_data['anchor'], index)
        anchor = g.at('POINT', 'INT', native_rank, vertex)
        total = g.at('POINT', 'INT', count, vertex)
        shifted = g.math('SUBTRACT', anchor, rank) if back else g.math('SUBTRACT', rank, anchor)
        shifted = g.math('MODULO', g.math('ADD', shifted, total), total)
        r = g.math('SUBTRACT', g.math('SUBTRACT', total, 1), rank) if back else rank
        r = g.switch('FLOAT', g.at('POINT', 'BOOLEAN', closed, vertex), r, shifted)
        use_outgoing = _eq(g, g.math('MODULO', r, 2), 0)
        last_closed = _all(
            g.at('POINT', 'BOOLEAN', _eq(g, points['boundary'], 0), vertex),
            _eq(g, r, g.math('SUBTRACT', g.at('POINT', 'INT', count, vertex), 1)))
        logical_out, logical_in = (incoming, outgoing) if back else (outgoing, incoming)
        chosen = g.switch('INT', use_outgoing, logical_in, logical_out)
        chosen = g.switch('INT', last_closed, chosen, logical_in)
        edge_rank = g.switch('FLOAT', use_outgoing, g.math('ADD', r, 1), r)
        edge_rank = g.switch('FLOAT', last_closed, edge_rank, 0)
        previous_vertex = g.at('CORNER', 'INT', vertex, previous)
        start_vertex = g.switch('INT', _eq(g, chosen, outgoing), previous_vertex, vertex)
        target_end = g.at('EDGE', 'INT', edge_first if back else edge_end, chosen)
        face_zero = _eq(g, start_vertex, target_end)
        internal = _eq(g, g.at('EDGE', 'INT', face_count, chosen), 2)
        weight = g.math('ADD', edge_rank,
                        g.switch('FLOAT', _all(g, internal, g.boolean('NOT', face_zero)), 0, 1))
        source, _ = _corner(g, vertex, slot, weight)
        queue = _sample(g, geo, 'CORNER', 'FLOAT_VECTOR', corner_data['normal'], source)
        work, queue_data = g.capture(geo, 'CORNER', queue=('VECTOR', queue))
        work, unit, bad = _solve(g, work, queue_data['queue'], count, slot, vertex, index, points['offset'])
        h_front = g.math('MULTIPLY', g.math('MULTIPLY', g.math('ADD', points['offset'], 1), .5), points['thickness'])
        h = g.math('SUBTRACT', h_front, g.math('MULTIPLY', points['thickness'], points['offset'])) if back else h_front
        h = g.switch('FLOAT', g.math('LESS_THAN', h, 0), g.math('MAXIMUM', h, 1e-5),
                     g.math('MINIMUM', h, -1e-5))
        if back:
            h = g.math('MULTIPLY', h, -1)
        work, frozen = g.capture(work, 'POINT', displacement=('VECTOR', g.scale(unit, h)), bad=('BOOLEAN', bad))
        # 只从相同索引的冻结源几何取值；下游壳复制不重新求法线/面扇。
        displacements.append(_sample(g, work, 'POINT', 'FLOAT_VECTOR', frozen['displacement'], index))
        solver_bad.append(g.stat(work, 'POINT', g.switch('FLOAT', frozen['bad'], 0, 1)))

    edge_length = g.length(g.vec('SUBTRACT', edge_vertices.outputs['Position 1'], edge_vertices.outputs['Position 2']))
    ec0, ec1 = _edge_corners(g, index, 0), _edge_corners(g, index, 1)
    same_direction = _all(g, _eq(g, face_count, 2),
                         _eq(g, g.at('CORNER', 'INT', vertex, ec0), g.at('CORNER', 'INT', vertex, ec1)))
    edge_bad = _any(g, g.math('GREATER_THAN', face_count, 2), _eq(g, face_count, 0),
                    same_direction, g.boolean('NOT', g.math('GREATER_THAN', edge_length, MERGE_THRESHOLD)))
    point_bad = _any(g, g.math('GREATER_THAN', count, MAX_VALENCE),
                     g.boolean('NOT', _eq(g, visited, count)),
                     g.math('GREATER_THAN', points['boundary'], 1), _eq(g, count, 0))
    area = g.node('GeometryNodeInputMeshFaceArea').outputs['Area']
    face_bad = g.boolean('NOT', g.math('GREATER_THAN', area, 1e-12))
    size = g.node('GeometryNodeAttributeDomainSize', component='MESH')
    g.link(geo, size.inputs['Geometry'])
    size_bad = _any(g, *(g.boolean('NOT', g.math('LESS_THAN', size.outputs[name], 16777216))
                        for name in ('Point Count', 'Edge Count', 'Face Corner Count')))
    warning = g.math('GREATER_THAN', g.math('MAXIMUM',
        g.math('MAXIMUM', g.stat(geo, 'EDGE', g.switch('FLOAT', edge_bad, 0, 1)),
               g.stat(geo, 'POINT', g.switch('FLOAT', point_bad, 0, 1))),
        g.math('MAXIMUM', g.stat(geo, 'FACE', g.switch('FLOAT', face_bad, 0, 1)),
               g.math('MAXIMUM', *solver_bad))), 0)
    warning = g.boolean('OR', warning, size_bad)
    geo, output = g.capture(geo, 'POINT', positive=('VECTOR', displacements[0]),
                            negative=('VECTOR', displacements[1]), source_position=('VECTOR', position))
    return geo, output['positive'], output['negative'], warning
