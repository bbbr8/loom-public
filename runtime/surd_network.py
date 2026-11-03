"""surd_network — exact floor..ceiling of one class of money across a network of accounts (Surd net_bounds).

Pipes are taken in the order given: {from, to, cents}. Each account's total balance follows the record; when an account
pays out more than it holds, the shortfall is that account's own money (the class, when the account is one of the
class's sources). Every allocation of the class's money that keeps each account's class money between 0 and its
balance is one feasible flow in a time-expanded network, so the least (most) class money the picked pipes can carry
is a min-cost flow with cost +1 (-1) on them. Integer cents throughout: no floating point, no tolerance.

Pure module (stdlib only). surd_ext wraps it as the Surd built-in net_bounds(pipes, fn, sources).
"""
import heapq
from collections import defaultdict


class _Graph:
    def __init__(self):
        self.to, self.cap, self.cost, self.adj = [], [], [], []

    def node(self):
        self.adj.append([])
        return len(self.adj) - 1

    def arc(self, u, v, cap, cost=0):
        if cap <= 0:
            return
        for a, b, c, w in ((u, v, cap, cost), (v, u, 0, -cost)):
            self.adj[a].append(len(self.to))
            self.to.append(b); self.cap.append(c); self.cost.append(w)


def build(pipes, picked, sources):
    """pipes: [(from, to, cents)] in order; picked: set of pipe indexes; sources: set of account names.
    Returns (graph, S, K, supply, target_arcs, total_balance_check)."""
    g = _Graph()
    S = g.node()
    T = defaultdict(int)
    cur = {}
    supply = 0
    targets = []
    for i, (f, t, a) in enumerate(pipes):
        s = a - T[f] if a > T[f] else 0
        inj = s if f in sources else 0
        T[f] += s - a
        T[t] += a
        x = g.node()
        if inj:
            g.arc(S, x, inj)
            supply += inj
        if f in cur:
            g.arc(cur[f], x, 1 << 62)
        fo = g.node()
        g.arc(x, fo, T[f])                    # what the source keeps: at most its balance after the pipe
        ti = g.node()
        if t in cur:
            g.arc(cur[t], ti, 1 << 62)
        if a < 0:
            raise ValueError(f"pipe {i} has a negative amount")
        if a > 0:                             # a $0.00 pipe carries nothing and gets no arc
            targets.append((i, len(g.to)))
            g.arc(x, ti, a)                   # the pipe: at most its amount of class money
        to_ = g.node()
        g.arc(ti, to_, T[t])                  # what the receiver holds: at most its balance after the pipe
        cur[f], cur[t] = fo, to_
    K = g.node()
    for n in cur.values():
        g.arc(n, K, 1 << 62)
    pipe_arc = {i: ai for i, ai in targets}
    return g, S, K, supply, pipe_arc


def _min_cost_flow(g, S, K, need):
    """primal-dual: exact potentials on the DAG, then Dijkstra phases with a Dinic blocking flow on admissible arcs."""
    n = len(g.adj)
    INF = float("inf")
    pot = [INF] * n
    pot[S] = 0
    for u in range(n):                         # nodes were created in time order: a topological order
        if pot[u] == INF:
            continue
        for e in g.adj[u]:
            if g.cap[e] > 0 and pot[u] + g.cost[e] < pot[g.to[e]]:
                pot[g.to[e]] = pot[u] + g.cost[e]
    flow = cost = 0
    while flow < need:
        dist = [INF] * n
        dist[S] = 0
        pq = [(0, S)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            for e in g.adj[u]:
                if g.cap[e] <= 0:
                    continue
                v = g.to[e]
                nd = d + g.cost[e] + pot[u] - pot[v]
                if nd < dist[v]:
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        if dist[K] == INF:
            break
        for u in range(n):
            if dist[u] < INF:
                pot[u] += dist[u]
        # Dinic on arcs with zero reduced cost
        def admissible(e, u):
            return g.cap[e] > 0 and dist[u] < INF and g.cost[e] + pot[u] - pot[g.to[e]] == 0
        while flow < need:
            level = [-1] * n
            level[S] = 0
            q = [S]
            for u in q:
                for e in g.adj[u]:
                    v = g.to[e]
                    if level[v] < 0 and admissible(e, u):
                        level[v] = level[u] + 1
                        q.append(v)
            if level[K] < 0:
                break
            it = [0] * n
            while flow < need:
                r = _iter_dfs(g, S, K, level, it, admissible, need - flow)
                if not r:
                    break
                flow += r
    for e in range(0, len(g.to), 2):
        cost += g.cost[e] * g.cap[e ^ 1]       # flow on arc e = residual capacity of its reverse
    return flow, cost


def _iter_dfs(g, S, K, level, it, admissible, limit):
    """one augmenting path in the level graph (iterative, so long account chains never hit the recursion limit)"""
    stack, arcs = [S], []
    while stack:
        u = stack[-1]
        if u == K:
            f = min([limit] + [g.cap[e] for e in arcs])
            for e in arcs:
                g.cap[e] -= f
                g.cap[e ^ 1] += f
            return f
        advanced = False
        while it[u] < len(g.adj[u]):
            e = g.adj[u][it[u]]
            v = g.to[e]
            if level[v] == level[u] + 1 and admissible(e, u):
                stack.append(v); arcs.append(e)
                advanced = True
                break
            it[u] += 1
        if not advanced:
            stack.pop()
            if arcs:
                e = arcs.pop()
                it[stack[-1]] += 1
            if not stack:
                return 0
    return 0


def bounds(pipes, picked, sources):
    """exact (floor, ceiling, class_in) in cents for the class money carried by the picked pipes, together."""
    out = []
    class_in = None
    for sign in (1, -1):
        g, S, K, supply, pipe_arc = build(pipes, picked, sources)
        for i in picked:
            if i not in pipe_arc:
                continue
            g.cost[pipe_arc[i]] = sign
            g.cost[pipe_arc[i] ^ 1] = -sign
        flow, cost = _min_cost_flow(g, S, K, supply)
        if flow != supply:
            raise ValueError(f"network infeasible: placed {flow} of {supply} cents")
        out.append(sign * cost)
        class_in = supply
    return out[0], out[1], class_in
