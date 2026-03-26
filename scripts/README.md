# Scripts

This directory contains utility scripts for SmartFTA-Dola.

## `generate_fta_stress.py`

Generate fault-tree JSON data for editor stress testing.

### Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

### Usage

Run from project root (`SmartFTA-Dola/`):

```bash
uv run scripts/generate_fta_stress.py --top 3 --basic 220 --max-depth 6 --edges 350 --basic-early-rate 0.28 --seed 42 --output frontend/data/stress-medium.json
```

### Main options

- `--edges` (required): total number of edges. In strict tree mode, this must equal `intermediate + basic` (or `basic` when `max-depth=2`).
- `--top` (required): number of top events (`type=1`).
- `--basic` (required): number of basic events (`type=3`).
- `--max-depth` (optional): maximum depth from top to basic (default `3`, minimum `2`).
- `--intermediate` (optional): number of intermediate events (`type=2`). If omitted, auto-derived.
- `--basic-early-rate` (optional): ratio (`0~0.9`) for placing basic events in earlier levels (L2/L3/...) instead of only deepest level.
- `--seed` (optional): random seed, default `42`.
- `--output` (optional): output JSON path, default `data/stress-flow.json`.

### Notes

- Generated JSON follows the new frontend business format (`meta`, `logic`, `visual`).
- `meta.name` is auto-derived from the output filename stem (for example `stress-medium.json` -> `stress-medium`).
- Script enforces basic validity:
  - adjacent levels are connected (each level has inbound/outbound coverage),
  - no duplicate edges.
- Generated graph is acyclic by construction (only connects from level `i` to `i+1`).
- Strict tree constraint: every non-top node has exactly one parent.
- Branches do not need to always reach the deepest level; some branches terminate earlier by reaching a basic event at upper levels.
- If requested `--edges` exceeds graph capacity for given counts, script exits with an error.
