from pathlib import Path
from core import datamodel

CATEGORY_ORDER = ["eyes", "eyelid", "brow", "mouth", "cheek", "misc"]

EYES_DIRECTION_ORDER = [
    "eyes_up", "eyes_down", "eyes_left", "eyes_right",
    "look_up", "look_down", "look_left", "look_right",
]

def _category_sort_key(flexgroup: str) -> tuple[int, str]:
    fg_lower = flexgroup.lower()
    for i, cat in enumerate(CATEGORY_ORDER):
        if fg_lower == cat:
            return (i, fg_lower)
    return (len(CATEGORY_ORDER), fg_lower)


def _controller_sort_key(flexgroup: str, control_name: str) -> tuple:
    cn_lower = control_name.lower()
    try:
        direction_index = EYES_DIRECTION_ORDER.index(cn_lower)
        return (_category_sort_key(flexgroup), -1, direction_index)
    except ValueError:
        return (_category_sort_key(flexgroup), 0, cn_lower)
    

def inject_flex_controllers_from_dmx(block_content: str, dmx_path: Path) -> tuple[str, list[str], int]:
    errors = []
    try:
        dm = datamodel.load(str(dmx_path))
    except Exception as e:
        return block_content, [f"Failed to load DMX '{dmx_path}': {e}"], 0

    combination_operator = dm.root.get("combinationOperator")
    if not combination_operator:
        return block_content, errors, 0

    controls = combination_operator.get("controls") or []
    if not controls:
        return block_content, errors, 0

    entries = []

    for control in controls:
        flexgroup = control.get("flexgroup") or ""
        if not flexgroup:
            continue

        raw_names = list(control.get("rawControlNames") or [])
        if not raw_names:
            continue

        f_min = control.get("flexMin", 0.0)
        f_max = control.get("flexMax", 1.0)

        entries.append((flexgroup, f"{f_min:g}", f"{f_max:g}", control.name, raw_names[0]))

    if not entries:
        return block_content, errors, 0

    entries.sort(key=lambda e: _controller_sort_key(e[0], e[3]))

    flex_controllers = [
        f"\tflexcontroller {fg} range {mn} {mx} {cn}"
        for fg, mn, mx, cn, _ in entries
    ]
    flex_mappings = [
        f"\t%{rn} = {cn}"
        for _, _, _, cn, rn in entries
    ]

    close = block_content.rfind("}")
    if close == -1:
        return block_content, ["$model block has no closing '}', cannot inject flex controllers"], 0

    base_body = block_content[:close].rstrip()
    injected_lines = [""] + flex_controllers + [""] + flex_mappings
    injected = base_body + "\n" + "\n".join(injected_lines) + "\n" + block_content[close:]

    return injected, errors, len(flex_controllers)