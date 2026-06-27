import re
from typing import Dict, List

# Maps lowercase VMT parameter names to their minimum required dx bucket.
# Values: 90 (>=dx90), 92 (>=DX90_20b / SM3).
# Parameters absent from this dict are valid at all DX levels and stay at the root block.
VMT_DXLEVEL_PARAMS: Dict[str, int] = {
    # ---- DX90 (Shader Model 2.0b / ps_2_b) ------------------------------------
    # Phong specular - requires ps_2_b or higher
    "$phong":                         90,
    "$phongboost":                    90,
    "$phongexponent":                 90,
    "$phongexponenttexture":          90,
    "$phongfresnelranges":            90,
    "$phongtint":                     90,
    "$phongalbedotint":               90,
    "$phongalbedoboost":              90,
    "$phongdisablehalflambert":       90,
    "$phongwarptexture":              90,
    # Phong mask sources
    "$normalmapalphaphongmask":       90,
    "$basemapalphaphongmask":         90,
    # Rim lighting (depends on phong system)
    "$rimlight":                      90,
    "$rimlightexponent":              90,
    "$rimlightboost":                 90,
    "$rimlightmask":                  90,
    "$rimmask":                       90,
    # Environment map masking (separate mask texture or fresnel)
    "$envmapmask":                    90,
    "$envmapfresnel":                 90,
    # Self-illumination masking and fresnel variants
    "$selfillummask":                 90,
    "$selfillumtexture":              90,
    "$selfillumfresnel":              90,
    "$selfillumfresnelminmaxexp":     90,
    # Second bump map UV transform
    "$bumptransform2":                90,
    # Second detail layer
    "$detail2":                       90,
    "$detailscale2":                  90,
    "$detailblendfactor2":            90,
    "$detailblendmode2":              90,
    "$detailtint2":                   90,
    "$detailframe2":                  90,
    "$blendmodulatetexture":          90,
    # Emissive blend (L4D2 / CS:GO character shader)
    "$emissiveblendenabled":          90,
    "$emissiveblendstrength":         90,
    "$emissiveblendtint":             90,
    "$emissiveblendscrollvector":     90,
    "$emissiveblendbasetexture":      90,
    "$emissiveblendtexture":          90,
    "$emissiveblendflowtexture":      90,
    # CS:GO / Strata advanced textures
    "$mraotexture":                   90,
    "$paintsplatnormalmap":           90,
    "$paintsplatbubblelayout":        90,
    "$paintsplatbubble":              90,
    "$paintenvmap":                   90,
    "$emissiontexture":               90,
    "$emissiontexture2":              90,

    # ---- DX90_20b (Shader Model 3.0 / dxlevel 92 or 95) -----------------------
    # Diffuse warp - requires SM3
    "$lightwarptexture":              92,
}

# Ordered dx buckets: (level_int, output_block_name)
_DX_BUCKETS = [
    (90, ">=DX90"),
    (92, ">=DX90_20b"),
]

# Maps a parsed dx level string (lowercase) to its bucket level int.
_DX_LEVEL_MAP: Dict[str, int] = {
    "dx9":  90,   # legacy alias
    "dx90": 90,
    "dx90_20b": 92,
    "dx95": 92,   # fold invalid >=dx95 into SM3 bucket
    "dx100": 92,
}

_DX_BLOCK_RE = re.compile(r'^\s*"?(>=|<=|>|<)?\s*(dx\d+(?:_\w+)?)"?\s*$', re.IGNORECASE)
_KV_RE = re.compile(r'\s*(\$\w+)\s+', re.IGNORECASE)
# Identifier-only line (no $, no operators) - marks the start of a named block like Proxies.
_NAMED_BLOCK_RE = re.compile(r'^[A-Za-z_]\w*\s*$')

# Maps a "group enable" param to the list of key prefixes suppressed when it equals zero.
# When the enable param is zero/false, the enable param itself and all params whose lowercase
# key starts with any listed prefix are dropped from the output.
# e.g. "$phong 0" -> drop $phong + every $phong* param.
# e.g. "$emissiveblendenabled 0" -> drop $emissiveblendenabled + every $emissiveblend* param.
VMT_GROUP_ENABLES: Dict[str, List[str]] = {
    "$phong":               ["$phong"],
    "$rimlight":            ["$rimlight", "$rimmask"],
    "$emissiveblendenabled":["$emissiveblend"],
    "$selfillum":           ["$selfillum"],
    "$detail":              ["$detail"],
    "$alphatest":              ["$alphatest", "$alphatestreference", "$allowalphatocoverage"],
    "$nodecal":              ["$nodecal"],
    "$halflambert":              ["$halflambert"],
    "$nocull":              ["$nocull"],
    "$translucent":              ["$translucent"],
    "$envmapfresnel":              ["$envmapfresnel"],
}

_ZERO_VALUE_RE = re.compile(r'^"?0(\.0*)?"?$')


def _find_comment_start(line: str) -> int:
    """Return the index of a // comment, ignoring // inside quoted strings. -1 if none."""
    in_string = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            in_string = not in_string
        elif not in_string and ch == '/' and i + 1 < len(line) and line[i + 1] == '/':
            return i
        i += 1
    return -1


def strip_vmt_comments(content: str) -> str:
    """Remove // comments from VMT content and collapse consecutive blank lines to one."""
    out = []
    prev_blank = False
    for line in content.splitlines():
        idx = _find_comment_start(line)
        if idx >= 0:
            line = line[:idx].rstrip()
        is_blank = not line.strip()
        if is_blank and prev_blank:
            continue
        out.append(line)
        prev_blank = is_blank
    return '\n'.join(out)


def _collect_suppressed_prefixes(lines: list) -> List[str]:
    """Scan lines for zero-valued group-enable params and return the list of key prefixes
    that should be suppressed. A prefix in the returned list means: drop any param whose
    lowercase key starts with that prefix."""
    suppressed: List[str] = []
    kv_full = re.compile(r'\s*(\$\w+)\s+(.*)', re.IGNORECASE)
    for line in lines:
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        m = kv_full.match(s)
        if not m:
            continue
        key = m.group(1).lower()
        val = m.group(2).strip().strip('"')
        if key in VMT_GROUP_ENABLES and _ZERO_VALUE_RE.match(val):
            suppressed.extend(VMT_GROUP_ENABLES[key])
    return suppressed


def _is_suppressed(key_lower: str, suppressed_prefixes: List[str]) -> bool:
    """Return True if key_lower starts with any suppressed prefix."""
    return any(key_lower.startswith(p) for p in suppressed_prefixes)


def _normalize_kv(line: str, leading: str) -> str:
    """Rewrite a KV line with normalized single-space between key and value."""
    m = re.match(r'\s*(\$\w+)\s+(.*)', line.rstrip(), re.IGNORECASE)
    if m:
        return f"{leading}{m.group(1)} {m.group(2).rstrip()}"
    return line


def _buckets_for_level(level: int) -> list:
    """Return all bucket level ints that should contain a param with the given min_level.
    A param must appear in its minimum bucket AND all higher buckets so that each
    dx block is self-contained (matching the Valve convention of duplicating params upward)."""
    return [bucket_level for bucket_level, _ in _DX_BUCKETS if bucket_level >= level]


def reorganize_vmt_by_dxlevel(content: str, reorganize_dxlevel: bool = True,
                               clean_disabled_groups: bool = True) -> str:
    """
    Strips // comments, then optionally reorganizes and cleans a VMT.

    reorganize_dxlevel:
        Move params in VMT_DXLEVEL_PARAMS into >=dxNN blocks, duplicated into all qualifying
        buckets so each block is self-contained. Existing >=dxNN blocks are merged.
        Existing <dxNN fallback blocks are preserved as-is.
    clean_disabled_groups:
        Drop params whose group-enable (VMT_GROUP_ENABLES) is set to 0.
    """
    content = strip_vmt_comments(content)
    lines = content.splitlines()

    # Find root opening and closing brace
    root_open_idx = -1
    root_close_idx = -1
    depth = 0
    for i, line in enumerate(lines):
        for ch in line:
            if ch == "{":
                if depth == 0:
                    root_open_idx = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and root_open_idx != -1:
                    root_close_idx = i
                    break
        if root_close_idx != -1:
            break

    if root_open_idx == -1 or root_close_idx == -1:
        return content

    preamble_lines = lines[:root_open_idx + 1]
    suffix_lines = lines[root_close_idx:]
    root_content = lines[root_open_idx + 1 : root_close_idx]

    # Detect indent style from the first indented root-level line
    indent = "\t"
    for line in root_content:
        if line.startswith("\t"):
            indent = "\t"
            break
        if line.startswith("    "):
            indent = "    "
            break

    # Collect suppressed groups only when cleaning is enabled.
    if clean_disabled_groups:
        all_kv_lines: List[str] = list(root_content)
        _scan_idx = 0
        _scan_lines = root_content
        while _scan_idx < len(_scan_lines):
            _s = _scan_lines[_scan_idx].strip()
            _dm = _DX_BLOCK_RE.match(_s)
            if _dm and (_dm.group(1) or ">=") == ">=":
                _scan_idx += 1
                if _scan_idx < len(_scan_lines) and _scan_lines[_scan_idx].strip() == "{":
                    _scan_idx += 1
                _d = 1
                while _scan_idx < len(_scan_lines) and _d > 0:
                    _bl = _scan_lines[_scan_idx]
                    _bs = _bl.strip()
                    _d += _bs.count("{") - _bs.count("}")
                    if _d > 0:
                        all_kv_lines.append(_bl)
                    _scan_idx += 1
                continue
            _scan_idx += 1
        suppressed = _collect_suppressed_prefixes(all_kv_lines)
    else:
        suppressed = []

    # Parse root content into buckets keyed by bucket level int
    universal_items = []
    dx_buckets: Dict[int, list] = {lvl: [] for lvl, _ in _DX_BUCKETS}
    preserve_blocks = []   # (header_line, inner_lines) for <dxNN fallback blocks
    proxies_blocks = []    # (header_line, inner_lines) for Proxies and similar named blocks

    idx = 0
    while idx < len(root_content):
        line = root_content[idx]
        stripped = line.strip()

        # Detect a dx-level block header
        dx_m = _DX_BLOCK_RE.match(stripped)
        if dx_m:
            op = dx_m.group(1) or ">="
            level_key = dx_m.group(2).lower()
            block_header = line
            idx += 1
            if idx < len(root_content) and root_content[idx].strip() == "{":
                idx += 1
            inner = []
            d = 1
            while idx < len(root_content) and d > 0:
                bl = root_content[idx]
                bs = bl.strip()
                d += bs.count("{") - bs.count("}")
                if d > 0:
                    inner.append(bl)
                idx += 1
            if op in ("<", "<=", ">") or not reorganize_dxlevel:
                preserve_blocks.append((block_header, inner))
            elif op == ">=":
                base = _DX_LEVEL_MAP.get(level_key, 90)
                normalized = []
                for l in inner:
                    ls = l.strip()
                    km = _KV_RE.match(ls) if ls else None
                    if km and _is_suppressed(km.group(1).lower(), suppressed):
                        continue
                    normalized.append(_normalize_kv(l, indent * 2) if km else l)
                for bucket in _buckets_for_level(base):
                    dx_buckets[bucket].extend(normalized)
            else:
                preserve_blocks.append((block_header, inner))
            continue

        # Detect a named block (e.g. Proxies) - identifier on its own line followed by {
        if (not stripped.startswith("//") and _NAMED_BLOCK_RE.match(stripped)
                and idx + 1 < len(root_content)
                and root_content[idx + 1].strip() == "{"):
            block_header = line
            idx += 2  # skip header and opening {
            inner = []
            d = 1
            while idx < len(root_content) and d > 0:
                bl = root_content[idx]
                bs = bl.strip()
                d += bs.count("{") - bs.count("}")
                if d > 0:
                    inner.append(bl)
                idx += 1
            proxies_blocks.append((block_header, inner))
            continue

        # Regular key-value line
        kv_m = _KV_RE.match(stripped) if stripped and not stripped.startswith("//") else None
        if kv_m:
            key = kv_m.group(1).lower()
            if _is_suppressed(key, suppressed):
                idx += 1
                continue
            min_level = VMT_DXLEVEL_PARAMS.get(key)
            if min_level is None or not reorganize_dxlevel:
                universal_items.append(_normalize_kv(line, indent))
            else:
                for bucket in _buckets_for_level(min_level):
                    dx_buckets[bucket].append(_normalize_kv(line, indent * 2))
        else:
            universal_items.append(line)

        idx += 1

    # Strip leading and trailing blank lines from universal_items, and collapse internal
    # consecutive blank lines (these appear when comment lines between params are removed).
    while universal_items and not universal_items[0].strip():
        universal_items.pop(0)
    while universal_items and not universal_items[-1].strip():
        universal_items.pop()
    collapsed: list = []
    prev_blank = False
    for line in universal_items:
        is_blank = not line.strip()
        if is_blank and prev_blank:
            continue
        collapsed.append(line)
        prev_blank = is_blank
    universal_items = collapsed

    # Reconstruct
    out = list(preamble_lines)
    out.extend(universal_items)

    for header, inner in preserve_blocks:
        if out and out[-1].strip():
            out.append("")
        out.append(header)
        out.append(f"{indent}{{")
        out.extend(inner)
        out.append(f"{indent}}}")

    for bucket_level, block_name in _DX_BUCKETS:
        items = dx_buckets[bucket_level]
        if items:
            out.append("")
            out.append(f'{indent}"{block_name}"')
            out.append(f"{indent}{{")
            out.extend(items)
            out.append(f"{indent}}}")

    # Named blocks (Proxies etc.) always go last
    for header, inner in proxies_blocks:
        out.append("")
        out.append(header)
        out.append(f"{indent}{{")
        out.extend(inner)
        out.append(f"{indent}}}")

    out.extend(suffix_lines)

    # Final pass: collapse any remaining consecutive blank lines
    final_out = []
    prev_blank = False
    for line in out:
        is_blank = not line.strip()
        if is_blank and prev_blank:
            continue
        final_out.append(line)
        prev_blank = is_blank

    return "\n".join(final_out)
