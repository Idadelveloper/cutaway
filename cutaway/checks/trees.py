"""Binary search tree law checks. Probes per frame (arrays allowed by the contract):
node_keys (number[]), left_index (number[], -1 = none), right_index (number[], -1 = none), root_index,
inorder_keys (number[]), tree_height (nodes on the longest root-to-leaf path; empty tree = 0),
node_count, search_key, search_path (number[]: keys visited so far), search_found (0/1),
balanced (0/1: AVL mode on).
"""
from __future__ import annotations

from ..schemas import Finding, Law, Trace
from .common import Ids
from .lawkit import _f, _missing

TREE_PROBES = ["node_keys", "left_index", "right_index", "root_index"]


def _tree(fr: dict):
    p = (fr or {}).get("probes") or {}
    if any(p.get(k) is None for k in TREE_PROBES):
        return None
    keys = list(p["node_keys"])
    left = [int(x) for x in p["left_index"]]
    right = [int(x) for x in p["right_index"]]
    root = int(p["root_index"])
    return keys, left, right, root, p


def _walk(keys, left, right, root):
    """In-order keys, height, and an error string (bad index / cycle / order) if the structure is broken."""
    n = len(keys)
    if len(left) != n or len(right) != n:
        return None, None, f"array lengths differ: keys {n}, left {len(left)}, right {len(right)}"
    if root == -1:
        return [], 0, None if n == 0 else f"root is -1 but there are {n} nodes"
    seen, order = set(), []

    def rec(i, lo, hi):
        if i == -1:
            return 0
        if not 0 <= i < n:
            raise ValueError(f"index {i} out of range")
        if i in seen:
            raise ValueError(f"node {i} reached twice (cycle or shared child)")
        seen.add(i)
        k = keys[i]
        if not (lo < k < hi):
            raise ValueError(f"key {k} at node {i} violates bounds ({lo}, {hi})")
        hl = rec(left[i], lo, k)
        order.append(k)
        hr = rec(right[i], k, hi)
        return 1 + max(hl, hr)

    try:
        h = rec(root, float("-inf"), float("inf"))
    except ValueError as e:
        return None, None, str(e)
    if len(seen) != n:
        return order, h, f"{n - len(seen)} nodes are not reachable from the root"
    return order, h, None


def _each(trs, fn):
    """Apply fn(frame, sweep, i) -> message|None to every frame; return the first failure."""
    for sweep, tr in trs.items():
        for i, fr in enumerate(tr.frames):
            msg = fn(fr, sweep, i)
            if msg:
                return sweep, i, msg
    return None


def bst_order(trs: dict[str, Trace], law: Law, fig: str, ids: Ids) -> list[Finding]:
    if all(_tree(fr) is None for tr in trs.values() for fr in tr.frames):
        return _missing(ids, fig, law, TREE_PROBES)

    def chk(fr, sweep, i):
        t = _tree(fr)
        if t is None:
            return None
        keys, left, right, root, p = t
        order, _, err = _walk(keys, left, right, root)
        if err:
            return err
        if p.get("inorder_keys") is not None and list(p["inorder_keys"]) != order:
            return f"inorder_keys {list(p['inorder_keys'])[:8]} != actual in-order {order[:8]}"
        return None

    bad = _each(trs, chk)
    if bad:
        sweep, i, msg = bad
        return [_f(ids, fig, law, "high", f"not a valid binary search tree: {msg}", f"{sweep} frame {i}",
                   "keep left subtree < node < right subtree after every insert/rotation", time=f"frame {i}")]
    return []


def tree_height(trs: dict[str, Trace], law: Law, fig: str, ids: Ids) -> list[Finding]:
    def chk(fr, sweep, i):
        t = _tree(fr)
        if t is None or (fr.get("probes") or {}).get("tree_height") is None:
            return None
        keys, left, right, root, p = t
        _, h, err = _walk(keys, left, right, root)
        if err is None and int(p["tree_height"]) != h:
            return f"tree_height {p['tree_height']} but the structure has height {h}"
        cnt = p.get("node_count")
        if cnt is not None and int(cnt) != len(keys):
            return f"node_count {cnt} but {len(keys)} nodes"
        return None

    bad = _each(trs, chk)
    if bad:
        sweep, i, msg = bad
        return [_f(ids, fig, law, "high", msg, f"{sweep} frame {i}", "compute height/count from the actual tree",
                   time=f"frame {i}")]
    return []


def search_path(trs: dict[str, Trace], law: Law, fig: str, ids: Ids) -> list[Finding]:
    """search_path must be a prefix of the true comparison path; search_found must match membership."""
    checked = 0

    def chk(fr, sweep, i):
        nonlocal checked
        t = _tree(fr)
        p = (fr or {}).get("probes") or {}
        if t is None or p.get("search_path") is None or p.get("search_key") is None:
            return None
        checked += 1
        keys, left, right, root, _ = t
        target, path = p["search_key"], list(p["search_path"])
        true, j = [], root
        while j != -1 and 0 <= j < len(keys):
            true.append(keys[j])
            if keys[j] == target:
                break
            j = left[j] if target < keys[j] else right[j]
        if path != true[:len(path)]:
            return f"search for {target} visited {path} but the comparison path is {true}"
        found = p.get("search_found")
        if found is not None and len(path) == len(true) and bool(found) != (target in keys):
            return f"search_found={found} but {target} {'is' if target in keys else 'is not'} in the tree"
        return None

    bad = _each(trs, chk)
    if not checked:
        return _missing(ids, fig, law, TREE_PROBES + ["search_key", "search_path"])
    if bad:
        sweep, i, msg = bad
        return [_f(ids, fig, law, "high", msg, f"{sweep} frame {i}",
                   "go left when the target is smaller, right when larger, stop when equal", time=f"frame {i}")]
    return []


def avl_balance(trs: dict[str, Trace], law: Law, fig: str, ids: Ids) -> list[Finding]:
    """When balanced (AVL) mode is on, every node's subtree heights differ by at most 1."""
    def chk(fr, sweep, i):
        t = _tree(fr)
        if t is None or not (fr.get("probes") or {}).get("balanced"):
            return None
        keys, left, right, root, _ = t

        def h(j):
            if j == -1:
                return 0, None
            hl, el = h(left[j])
            hr, er = h(right[j])
            err = el or er or (f"node {keys[j]}: left height {hl}, right height {hr}" if abs(hl - hr) > 1 else None)
            return 1 + max(hl, hr), err

        try:
            return h(root)[1]
        except (IndexError, RecursionError):
            return "broken structure"

    bad = _each(trs, chk)
    if bad:
        sweep, i, msg = bad
        return [_f(ids, fig, law, "high", f"AVL mode on but tree is unbalanced: {msg}", f"{sweep} frame {i}",
                   "rotate after each insert so |h(left) - h(right)| <= 1", time=f"frame {i}")]
    return []


CHECKS = {f.__name__: f for f in (bst_order, tree_height, search_path, avl_balance)}
