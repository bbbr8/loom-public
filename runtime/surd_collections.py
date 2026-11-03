"""Bounded collection kernels; language values and callbacks enter through ports.

No interpreter imports, file I/O, source rewriting, or numerical conversion.
"""

FUNCTIONS = frozenset({"group_by", "unique_by", "partition", "scan", "flat_map", "chunk", "flatten"})
MAX_ITEMS = 1_000_000
MAX_VISITS = 2_000_000
MAX_DEPTH = 32


class CollectionError(ValueError):
    def __init__(self, message, limit=False):
        super().__init__(message)
        self.limit = limit


class Traversal:
    """Shared traversal allowance for keys and accumulator snapshots in one call."""
    def __init__(self, scalar, checkpoint):
        self.scalar, self.checkpoint, self.visits = scalar, checkpoint, 0

    def visit(self, depth):
        self.checkpoint()
        self.visits += 1
        if self.visits > MAX_VISITS or depth > MAX_DEPTH:
            raise CollectionError("collection structural traversal limit exceeded", limit=True)

    def walk(self, value, key=False, path=None, depth=0):
        self.visit(depth)
        if not isinstance(value, (list, dict)):
            return self.scalar(value) if key else value
        path = set() if path is None else path
        if id(value) in path:
            raise CollectionError("collection keys and scan snapshots cannot contain cycles")
        path.add(id(value))
        try:
            if isinstance(value, list):
                items = [self.walk(x, key, path, depth+1) for x in value]
                return ("list", tuple(items)) if key else items
            if key:
                return ("map", frozenset((self.walk(k, True, path, depth+1),
                                          self.walk(v, True, path, depth+1)) for k, v in value.items()))
            return {k: self.walk(v, False, path, depth+1) for k, v in value.items()}
        finally:
            path.remove(id(value))


def apply(name, values, *, invoke=None, scalar=None, truth=None, checkpoint=lambda: None,
          start=None, size=None):
    """Return fresh outer containers; retain input item identities except scan snapshots."""
    if name not in FUNCTIONS:
        raise CollectionError("unknown collection function: " + str(name))
    if not isinstance(values, list):
        raise CollectionError(name + " needs a list")
    if len(values) > MAX_ITEMS:
        raise CollectionError("collection input exceeds item limit", limit=True)
    # Freeze iteration length before invoking callbacks which may mutate inputs.
    xs = list(values)
    traversal = Traversal(scalar, checkpoint)
    out = []

    def append(target, item):
        if len(target) >= MAX_ITEMS:
            raise CollectionError("collection output exceeds item limit", limit=True)
        target.append(item)

    if name in ("group_by", "unique_by"):
        slots = {}
        for item in xs:
            checkpoint()
            key = invoke(item)
            token = traversal.walk(key, key=True)
            if token not in slots:
                slots[token] = len(out)
                append(out, {"key": traversal.walk(key), "items": []} if name == "group_by" else item)
            if name == "group_by":
                append(out[slots[token]]["items"], item)
        return out
    if name == "partition":
        rest = []
        for item in xs:
            checkpoint()
            append(out if truth(invoke(item)) else rest, item)
        return {"matched": out, "rest": rest}
    if name == "scan":
        acc = start
        append(out, traversal.walk(acc))
        for item in xs:
            checkpoint()
            acc = invoke(acc, item)
            append(out, traversal.walk(acc))
        return out
    if name == "chunk":
        if type(size) is not int or size < 1:
            raise CollectionError("chunk size must be a positive whole number")
        for offset in range(0, len(xs), size):
            checkpoint()
            append(out, xs[offset:offset+size])
        return out
    for item in xs:
        checkpoint()
        part = invoke(item) if name == "flat_map" else item
        if name == "flat_map" and not isinstance(part, list):
            raise CollectionError("flat_map callback must return a list")
        if isinstance(part, list):
            if len(out) + len(part) > MAX_ITEMS:
                raise CollectionError("collection output exceeds item limit", limit=True)
            for child in part:
                checkpoint()
                out.append(child)
        else:
            append(out, part)
    return out
