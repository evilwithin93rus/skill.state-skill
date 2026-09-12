#!/usr/bin/env python3
"""
Deterministic warehouse simulation for benchmarking SKILL.state against baselines.

Models the paper's Warehouse environment (SkillExecBench Environment 1):
- N shelves (default 40), each holding zero or one item
- Actions: Store <item> <shelf>, Ship <item> <shelf>, Move <item> <old> <new>, DONE
- Generates deterministic task sequences with drift and noise injection
- Tracks ground-truth state on disk; used as observe_cmd / action_cmd for
  skillstate.py run_loop

Usage:
  python benchmarks/warehouse_env.py init      -- write initial state
  python benchmarks/warehouse_env.py observe   -- print latest observation
  python benchmarks/warehouse_env.py act "Ship item_12 shelf_42"  -- execute action
  python benchmarks/warehouse_env.py task-sequence -- print all tasks (for baselines)
"""

from __future__ import annotations

import json
import os
import sys
import random
import hashlib

SEED = 42
random.seed(SEED)

SHELF_COUNT = 40
INITIAL_ITEMS = 18

WORKDIR = os.path.join(os.path.dirname(__file__), ".bench_state")
STATE_PATH = os.path.join(WORKDIR, "ground_truth.json")
TASK_PATH = os.path.join(WORKDIR, "tasks.json")
STEP_PATH = os.path.join(WORKDIR, "step.txt")
HISTORY_PATH = os.path.join(WORKDIR, "history.jsonl")

SKILLSTATE_INSTRUCTIONS = """You are a warehouse robot. 40 shelves: shelf_0 to shelf_39. Each shelf holds 0 or 1 item.

Your state has: inventory (shelf->item), maintenance (shelf->flag), pending_intake (item->"waiting").

Actions: Ship <item> <shelf> | Store <item> <shelf> | Move <item> <old> <new> | DONE

Rules:
- Verify items are on correct shelves before acting.
- Observation beats memory: if observation says something changed, patch state immediately.
- Delete finished entries with null.
- Ignore BACKGROUND TELEMETRY noise.
- When observation says "All tasks complete", action DONE.

Respond with reasoning, then one ```json block with exactly keys "state_patch" and "action". Set keys to null to delete them. Include only keys you are changing."""


def _hash_task(task_idx):
    h = hashlib.md5(f"task-{task_idx}-seed-{SEED}".encode()).hexdigest()
    return int(h, 16)


def generate_tasks(seed=None):
    """Generate a deterministic sequence of warehouse operations. Returns list of dicts."""
    if seed is not None:
        random.seed(seed)
    else:
        random.seed(SEED)
    shelves = list(range(SHELF_COUNT))
    items_in_stock = list(range(1, INITIAL_ITEMS + 1))
    item_counter = INITIAL_ITEMS + 1
    occupied = {}  # shelf -> item_id

    for i in range(INITIAL_ITEMS):
        shelf = shelves[i]
        item = items_in_stock[i]
        occupied[shelf] = item

    tasks = []
    for step_i in range(55):
        available_occupied = [s for s, it in occupied.items() if not str(s).startswith("maint_")]
        available_empty = [s for s in shelves if s not in occupied and not str(s).startswith("maint_")]

        rng = _hash_task(step_i)
        task_type_r = rng % 100

        observation_parts = []
        task = {"step": step_i}

        if step_i >= 53:
            observation_parts.append("All tasks complete. No further work pending.")
            task["type"] = "done"
            task["observation"] = " ".join(observation_parts)
            tasks.append(task)
            break

        if task_type_r < 40 and available_occupied:
            shelf = available_occupied[rng % len(available_occupied)]
            item = occupied[shelf]
            observation_parts.append(f"Customer ordered item_{item:02d}.")
            task["type"] = "ship"
            task["item"] = f"item_{item:02d}"
            task["shelf"] = f"shelf_{shelf}"
            del occupied[shelf]

        elif task_type_r < 65 and available_empty:
            shelf = available_empty[rng % len(available_empty)]
            new_item = item_counter
            item_counter += 1
            observation_parts.append(f"New shipment arrived containing item_{new_item:02d}.")
            task["type"] = "store"
            task["item"] = f"item_{new_item:02d}"
            task["shelf"] = f"shelf_{shelf}"

        elif task_type_r < 80 and available_occupied and available_empty:
            old_shelf = available_occupied[rng % len(available_occupied)]
            new_shelf = available_empty[(rng // 7) % len(available_empty)]
            item = occupied[old_shelf]
            observation_parts.append(
                f"Maintenance team relocated item_{item:02d} from shelf_{old_shelf} "
                f"to shelf_{new_shelf}."
            )
            task["type"] = "move_drift"
            task["item"] = f"item_{item:02d}"
            task["old_shelf"] = f"shelf_{old_shelf}"
            task["new_shelf"] = f"shelf_{new_shelf}"
            del occupied[old_shelf]
            occupied[new_shelf] = item

        else:
            if available_occupied:
                shelf = available_occupied[rng % len(available_occupied)]
                item = occupied[shelf]
                observation_parts.append(f"Customer ordered item_{item:02d}.")
                task["type"] = "ship"
                task["item"] = f"item_{item:02d}"
                task["shelf"] = f"shelf_{shelf}"
                del occupied[shelf]
            elif available_empty:
                shelf = available_empty[rng % len(available_empty)]
                new_item = item_counter
                item_counter += 1
                observation_parts.append(f"New shipment arrived containing item_{new_item:02d}.")
                task["type"] = "store"
                task["item"] = f"item_{new_item:02d}"
                task["shelf"] = f"shelf_{shelf}"

        if rng % 7 == 0:
            maint_shelf = shelves[rng % len(shelves)]
            if maint_shelf in occupied:
                observation_parts.append(
                    f"Alert: shelf_{maint_shelf} reports degraded performance. "
                    f"Flag for maintenance after current operation."
                )
                task["maintenance_flag"] = f"shelf_{maint_shelf}"

        if rng % 5 == 0:
            noise_lines = [
                "--- BACKGROUND TELEMETRY ---",
                f"[Robot] Battery: {40 + (rng % 60)}%, "
                f"Temperature: {(rng % 30) + 20}C, CPU Load: {10 + (rng % 80)}%",
                f"[Sensor] Humidity: {20 + (rng % 60)}%, "
                f"Temp: {(rng % 15) + 18:.1f}C, CO2: {350 + (rng % 300)} ppm",
            ]
            observation_parts.append("\n".join(noise_lines))

        task["observation"] = "\n".join(observation_parts)
        tasks.append(task)

    return tasks


def init_environment(force=False):
    os.makedirs(WORKDIR, exist_ok=True)
    if not force and os.path.exists(STEP_PATH):
        return
    tasks = generate_tasks()
    with open(TASK_PATH, "w", encoding="utf-8") as f:
        json.dump(tasks, f, separators=(",", ":"), ensure_ascii=False)
    with open(STEP_PATH, "w", encoding="utf-8") as f:
        f.write("0")
    shelves = {}
    for i in range(INITIAL_ITEMS):
        shelves[f"shelf_{i}"] = f"item_{i + 1:02d}"
    state = {"inventory": shelves, "maintenance": {}, "pending_intake": {}}
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, separators=(",", ":"), ensure_ascii=False)

    instructions = SKILLSTATE_INSTRUCTIONS
    inst_path = os.path.join(WORKDIR, "instructions.txt")
    with open(inst_path, "w", encoding="utf-8") as f:
        f.write(instructions)

    schema_path = os.path.join(WORKDIR, "schema.json")
    with open(schema_path, "w", encoding="utf-8") as f:
        json.dump({
            "inventory": {"*": "str?"},
            "maintenance": {"*": "str?"},
            "pending_intake": {"*": "str?"},
        }, f, separators=(",", ":"), ensure_ascii=False)

    return tasks


def observe():
    init_environment()
    with open(STEP_PATH, encoding="utf-8") as f:
        step = int(f.read().strip())
    with open(TASK_PATH, encoding="utf-8") as f:
        tasks = json.load(f)
    if step >= len(tasks):
        print("All tasks complete. No further work pending.")
        return
    print(tasks[step]["observation"])


def act(action_str):
    init_environment()
    with open(STEP_PATH, encoding="utf-8") as f:
        step = int(f.read().strip())
    with open(TASK_PATH, encoding="utf-8") as f:
        tasks = json.load(f)
    with open(STATE_PATH, encoding="utf-8") as f:
        state = json.load(f)

    parts = action_str.strip().split()
    verb = parts[0] if parts else ""

    task = tasks[step] if step < len(tasks) else None

    if verb == "DONE":
        result = "Episode complete. Final state saved."
    elif verb == "Ship":
        if len(parts) < 3:
            result = "Error: Ship requires <item> <shelf>."
        else:
            item, shelf = parts[1], parts[2]
            if shelf in state["inventory"] and state["inventory"][shelf] == item:
                del state["inventory"][shelf]
                result = f"Success: Shipped {item} from {shelf}."
            else:
                current = state["inventory"].get(shelf, "empty")
                result = f"Error: {shelf} holds {current}, not {item}."
    elif verb == "Store":
        if len(parts) < 3:
            result = "Error: Store requires <item> <shelf>."
        else:
            item, shelf = parts[1], parts[2]
            if shelf in state["inventory"]:
                result = f"Error: {shelf} is occupied by {state['inventory'][shelf]}."
            else:
                state["inventory"][shelf] = item
                result = f"Success: Stored {item} on {shelf}."
    elif verb == "Move":
        if len(parts) < 4:
            result = "Error: Move requires <item> <old_shelf> <new_shelf>."
        else:
            item, old_shelf, new_shelf = parts[1], parts[2], parts[3]
            if old_shelf not in state["inventory"] or state["inventory"][old_shelf] != item:
                current = state["inventory"].get(old_shelf, "empty")
                result = f"Error: {old_shelf} holds {current}, not {item}."
            elif new_shelf in state["inventory"]:
                result = f"Error: {new_shelf} is occupied by {state['inventory'][new_shelf]}."
            else:
                del state["inventory"][old_shelf]
                state["inventory"][new_shelf] = item
                result = f"Success: Moved {item} from {old_shelf} to {new_shelf}."
    else:
        result = f"Error: Unknown action '{verb}'. Valid: Ship, Store, Move, DONE."

    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, separators=(",", ":"), ensure_ascii=False)

    entry = {"step": step, "action": action_str, "result": result}
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    next_step = step + 1
    with open(STEP_PATH, "w", encoding="utf-8") as f:
        f.write(str(next_step))

    print(result)


def load_tasks():
    init_environment()
    with open(TASK_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_history():
    if not os.path.exists(HISTORY_PATH):
        return []
    entries = []
    with open(HISTORY_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def read_file(path):
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: warehouse_env.py init|observe|act <action>|task-sequence")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "init":
        tasks = init_environment(force=True)
        print(f"Initialized {len(tasks)} tasks in {WORKDIR}")
    elif cmd == "observe":
        observe()
    elif cmd == "act":
        act(" ".join(sys.argv[2:]))
    elif cmd == "task-sequence":
        tasks = load_tasks()
        print(json.dumps(tasks, indent=2, ensure_ascii=False))
    elif cmd == "history":
        print(json.dumps(load_history(), indent=2, ensure_ascii=False))
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)