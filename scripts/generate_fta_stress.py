#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Generate random fault-tree JSON for stress testing.

Example:
  uv run scripts/generate_fta_stress.py --edges 1200 --top 3 --basic 400 --max-depth 5 --output data/stress-1200.json
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Node:
    node_id: str
    name: str
    node_type: str  # 1=top,2=intermediate,3=basic
    gate: str
    x: int
    y: int


def _distribute_intermediate(inter: int, max_depth: int) -> list[int]:
    internal_levels = max_depth - 2
    if internal_levels <= 0:
        return []
    buckets = [0] * internal_levels
    if inter <= 0:
        return buckets

    # Keep intermediate levels contiguous from top to bottom.
    active_levels = min(inter, internal_levels)
    for i in range(active_levels):
        buckets[i] = 1
    remaining = inter - active_levels
    for i in range(remaining):
        buckets[i % active_levels] += 1
    return buckets


def _distribute_basic_by_depth(
    top: int,
    basic: int,
    max_depth: int,
    inter_level_counts: list[int],
    basic_early_rate: float,
) -> list[int]:
    # Non-top depths are 1..(max_depth-1)
    depth_count = max_depth - 1
    buckets = [0] * depth_count
    if basic <= 0:
        return buckets

    # Reachability: depth 1 is always reachable from top; deeper depths require
    # at least one intermediate node in the previous depth.
    reachable_depths = [1]
    for depth in range(2, depth_count + 1):
        prev_inter = inter_level_counts[depth - 2] if depth - 2 < len(inter_level_counts) else 0
        if prev_inter <= 0:
            break
        reachable_depths.append(depth)

    deepest_reachable = reachable_depths[-1]
    deepest_index = deepest_reachable - 1
    early_depths = [d for d in reachable_depths if d != deepest_reachable]

    # Reserve minimum basics so parent levels can fan out and avoid
    # creating intermediate leaves due to lack of children.
    min_basic = [0] * depth_count
    for depth in reachable_depths:
        inter_here = inter_level_counts[depth - 1] if depth - 1 < len(inter_level_counts) else 0
        if depth == 1:
            required = max(0, top - inter_here)
        else:
            inter_prev = inter_level_counts[depth - 2] if depth - 2 < len(inter_level_counts) else 0
            required = max(0, inter_prev - inter_here)
        min_basic[depth - 1] = required

    min_required = sum(min_basic)
    if min_required > basic:
        raise ValueError(
            "Not enough basic events to keep all parent intermediate nodes connected. "
            "Increase --basic or reduce --intermediate/--max-depth."
        )

    for i, value in enumerate(min_basic):
        buckets[i] = value

    remaining = basic - min_required
    early_target = int(round(basic * basic_early_rate))
    early_target = max(0, min(early_target, remaining))
    buckets[deepest_index] += remaining - early_target

    if early_target > 0 and early_depths:
        for i in range(early_target):
            depth = early_depths[i % len(early_depths)]
            buckets[depth - 1] += 1

    return buckets


def _calc_intermediate_count(
    top: int,
    basic: int,
    edges: int,
    max_depth: int,
    user_value: int | None,
) -> int:
    if user_value is not None:
        inter = max(0, user_value)
        if max_depth <= 2 and inter > 0:
            raise ValueError("--intermediate must be 0 when --max-depth=2.")
        expected_edges = inter + basic
        if edges != expected_edges:
            raise ValueError(
                f"For strict tree mode, --edges must equal intermediate+basic "
                f"(expected {expected_edges}, got {edges})."
            )
        return inter

    if max_depth <= 2:
        if edges != basic:
            raise ValueError(
                f"For --max-depth=2, --edges must equal --basic ({basic})."
            )
        return 0

    inter = edges - basic
    if inter < 0:
        raise ValueError(
            f"For strict tree mode, --edges must be >= --basic ({basic})."
        )
    return inter


def _build_nodes(
    top: int,
    inter: int,
    basic: int,
    max_depth: int,
    basic_early_rate: float,
    x_gap: int,
    y_gap: int,
) -> tuple[list[Node], list[str], list[list[str]], list[list[str]]]:
    nodes: list[Node] = []
    top_ids: list[str] = []
    inter_level_ids: list[list[str]] = []
    basic_level_ids: list[list[str]] = []

    cursor = 1

    for i in range(top):
        nid = f"n{cursor}"
        cursor += 1
        top_ids.append(nid)
        nodes.append(Node(nid, f"Top Event {i + 1}", "1", "2", 100, 80 + i * y_gap))

    buckets = _distribute_intermediate(inter, max_depth)
    inter_cursor = 1
    for level, count in enumerate(buckets, start=1):
        level_ids: list[str] = []
        for i in range(count):
            nid = f"n{cursor}"
            cursor += 1
            level_ids.append(nid)
            nodes.append(
                Node(
                    nid,
                    f"Intermediate L{level} Event {inter_cursor}",
                    "2",
                    "2",
                    100 + x_gap * level,
                    80 + i * y_gap,
                )
            )
            inter_cursor += 1
        inter_level_ids.append(level_ids)

    basic_depth_counts = _distribute_basic_by_depth(
        top=top,
        basic=basic,
        max_depth=max_depth,
        inter_level_counts=buckets,
        basic_early_rate=basic_early_rate,
    )
    basic_cursor = 1
    for depth, count in enumerate(basic_depth_counts, start=1):
        depth_ids: list[str] = []
        for i in range(count):
            nid = f"n{cursor}"
            cursor += 1
            depth_ids.append(nid)
            nodes.append(
                Node(
                    nid,
                    f"Basic L{depth} Event {basic_cursor}",
                    "3",
                    "2",
                    100 + x_gap * depth,
                    80 + i * y_gap,
                )
            )
            basic_cursor += 1
        basic_level_ids.append(depth_ids)

    return nodes, top_ids, inter_level_ids, basic_level_ids


def _generate_edges(
    top_ids: list[str],
    inter_level_ids: list[list[str]],
    basic_level_ids: list[list[str]],
    edges: int,
    seed: int,
) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    if not top_ids:
        return []

    expected_edges = sum(len(level) for level in inter_level_ids) + sum(
        len(level) for level in basic_level_ids
    )
    if edges != expected_edges:
        raise ValueError(
            f"For strict tree mode, --edges must equal total non-top nodes "
            f"({expected_edges}); got {edges}."
        )

    result: list[tuple[str, str]] = []
    depth_count = len(basic_level_ids)
    for depth in range(1, depth_count + 1):
        # Parent candidates at this depth transition:
        # depth=1 -> top events; deeper -> previous depth intermediate nodes only.
        if depth == 1:
            parent_level = list(top_ids)
        else:
            parent_level = list(inter_level_ids[depth - 2]) if depth - 2 < len(inter_level_ids) else []

        child_level = []
        if depth - 1 < len(inter_level_ids):
            child_level.extend(inter_level_ids[depth - 1])
        child_level.extend(basic_level_ids[depth - 1])
        if not parent_level or not child_level:
            continue

        rng.shuffle(parent_level)
        rng.shuffle(child_level)

        # Single-parent constraint + fan-out distribution:
        # every child gets exactly one parent, and parents can have multiple children.
        parent_count = len(parent_level)
        child_count = len(child_level)
        base = child_count // parent_count
        extra = child_count % parent_count

        cursor = 0
        for i, parent in enumerate(parent_level):
            take = base + (1 if i < extra else 0)
            for _ in range(take):
                child = child_level[cursor]
                cursor += 1
                result.append((parent, child))

    return sorted(result)


def _legacy_node_type_to_new(value: str) -> str:
    return {"1": "TOP", "2": "INTERMEDIATE", "3": "BASIC"}.get(value, "INTERMEDIATE")


def _legacy_gate_to_new(value: str) -> str:
    return {"1": "AND", "2": "OR"}.get(value, "OR")


def _to_business_json(
    nodes: list[Node],
    edges: list[tuple[str, str]],
    flow_name: str,
) -> dict:
    logic_nodes = [
        {
            "nodeId": n.node_id,
            "nodeType": _legacy_node_type_to_new(n.node_type),
            "name": n.name,
            "probability": None,
        }
        for n in nodes
    ]

    child_ids = {dst for _, dst in edges}
    top_candidates = [n.node_id for n in nodes if n.node_id not in child_ids]
    top_node_id = top_candidates[0] if top_candidates else (nodes[0].node_id if nodes else None)

    children_by_parent: dict[str, list[str]] = {}
    node_map = {n.node_id: n for n in nodes}
    for src, dst in edges:
        children_by_parent.setdefault(src, []).append(dst)

    logic_gates = []
    for idx, (parent_id, child_list) in enumerate(children_by_parent.items(), start=1):
        parent = node_map.get(parent_id)
        gate_type = _legacy_gate_to_new(parent.gate if parent else "2")
        logic_gates.append(
            {
                "gateId": f"gate-{idx:04d}",
                "gateType": gate_type,
                "parentNodeId": parent_id,
                "childNodeIds": child_list,
            }
        )

    return {
        "meta": {
            "ftaId": f"FTA-STRESS-{flow_name.upper()}",
            "name": flow_name,
            "topNodeId": top_node_id,
        },
        "logic": {
            "nodes": logic_nodes,
            "gates": logic_gates,
        },
        "visual": {
            "canvas": {
                "width": 1920,
                "height": 1080,
            },
            "nodePositions": [
                {
                    "nodeId": n.node_id,
                    "x": n.x,
                    "y": n.y,
                }
                for n in nodes
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate stress-test fault tree JSON.")
    parser.add_argument("--edges", type=int, required=True, help="Total number of edges.")
    parser.add_argument("--top", type=int, required=True, help="Number of top events (type=1).")
    parser.add_argument("--basic", type=int, required=True, help="Number of basic events (type=3).")
    parser.add_argument(
        "--max-depth",
        type=int,
        default=3,
        help="Max tree depth from top to basic events (>=2).",
    )
    parser.add_argument(
        "--intermediate",
        type=int,
        default=None,
        help="Number of intermediate events (type=2). Auto-derived when omitted.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument(
        "--basic-early-rate",
        type=float,
        default=0.2,
        help="Ratio (0~0.9) of basic events placed in earlier levels instead of the deepest level.",
    )
    parser.add_argument("--x-gap", type=int, default=420, help="Horizontal gap between layers.")
    parser.add_argument("--y-gap", type=int, default=90, help="Vertical gap within a layer.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/stress-flow.json"),
        help="Output JSON path.",
    )

    args = parser.parse_args()

    if args.edges <= 0:
        raise SystemExit("--edges must be > 0")
    if args.top <= 0:
        raise SystemExit("--top must be > 0")
    if args.basic <= 0:
        raise SystemExit("--basic must be > 0")
    if args.max_depth < 2:
        raise SystemExit("--max-depth must be >= 2")
    if args.basic_early_rate < 0 or args.basic_early_rate > 0.9:
        raise SystemExit("--basic-early-rate must be within [0, 0.9].")

    try:
        inter = _calc_intermediate_count(
            args.top,
            args.basic,
            args.edges,
            args.max_depth,
            args.intermediate,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    nodes, top_ids, inter_level_ids, basic_level_ids = _build_nodes(
        top=args.top,
        inter=inter,
        basic=args.basic,
        max_depth=args.max_depth,
        basic_early_rate=args.basic_early_rate,
        x_gap=args.x_gap,
        y_gap=args.y_gap,
    )
    edges = _generate_edges(
        top_ids,
        inter_level_ids,
        basic_level_ids,
        args.edges,
        args.seed,
    )
    flow_name = args.output.stem or "stress-flow"
    payload = _to_business_json(nodes, edges, flow_name=flow_name)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        "Generated:",
        (
            f"nodes={len(nodes)} "
            f"(top={len(top_ids)}, inter={sum(len(x) for x in inter_level_ids)}, basic={sum(len(x) for x in basic_level_ids)})"
        ),
        f"maxDepth={args.max_depth}",
        f"edges={len(edges)}",
        f"output={args.output}",
    )


if __name__ == "__main__":
    main()
