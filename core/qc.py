import shlex, re
from simpleeval import simple_eval
from pathlib import Path
from typing import Optional
from utils import Logger
from core import vrd as vrd_module
from core import flex_controllers, datamodel
from core.bone_animations import read_dmx_bone_animation, frames_quat_to_euler, frames_rotation_to_degrees, read_smd_bone_animation, apply_world_scale

ORANGE = "\033[38;5;208m"
RED    = "\033[91m"
RESET  = "\033[0m"

_CMP_RE = re.compile(r'^\s*([^\s"]+|"[^"]+")\s*(==|!=|>=|<=|>|<)\s*([^\s"]+|"[^"]+")\s*$')


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------

def _get_value(val_str: str, variables: dict, allow_literal: bool = False):
    val_str  = val_str.strip()
    unquoted = val_str.strip('"')
    if unquoted in variables:
        return variables[unquoted]
    if val_str.startswith('"') and val_str.endswith('"'):
        return unquoted
    try:
        float(val_str)
        return val_str
    except (ValueError, TypeError):
        pass
    return val_str if allow_literal else None


def _compare(left_str: str, op: str, right_str: str, variables: dict) -> bool:
    left  = _get_value(left_str, variables)
    right = _get_value(right_str, variables, allow_literal=True)
    try:
        l, r = float(left), float(right)
        return {"==": l == r, "!=": l != r, ">": l > r, "<": l < r, ">=": l >= r, "<=": l <= r}.get(op, False)
    except (ValueError, TypeError):
        if op == "==": return str(left) == str(right)
        if op == "!=": return str(left) != str(right)
    return False


def _eval_and_term(term: str, variables: dict) -> bool:
    m = _CMP_RE.match(term)
    if m:
        return _compare(m.group(1), m.group(2), m.group(3), variables)
    val = _get_value(term, variables)
    return val is not None and str(val).strip() not in ("0", "", "false")


def _evaluate_condition(expression: str, variables: dict, is_ifdef: bool) -> bool:
    expression = expression.strip()
    for or_part in expression.split("||"):
        terms = [t.strip() for t in or_part.split("&&") if t.strip()]
        if is_ifdef:
            if all(t in variables for t in terms):
                return True
        elif all(_eval_and_term(t, variables) for t in terms):
            return True
    return False


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class QCReturnException(Exception):
    pass

class QCCompileError(Exception):
    pass


# ---------------------------------------------------------------------------
# Processor
# ---------------------------------------------------------------------------

class QCProcessor:
    COMMENT_OUT_COMMANDS = {"$msg", "$echo"}

    def __init__(
        self,
        variables: dict       = None,
        macros: dict          = None,
        logger                = None,
        macro_args_override: dict = None,
        include_dirs: list    = None,
        root_dir: Path        = None,
        current_scale: float  = 1.0,
        compiler: str         = None,
        vrd_prefix: str       = None,
    ):
        self.variables           = variables if variables is not None else {}
        self.macros              = macros    if macros    is not None else {}
        self.logger: Logger      = logger
        self.macro_args_override = macro_args_override or {}
        self.include_dirs        = include_dirs or []
        self.root_dir            = root_dir
        self.if_stack            = []
        self.output_lines        = []
        self.json_vars           = set(self.variables)
        self.defined_vars        = set(self.variables)
        self.pushd_stack         = []
        self.current_scale       = current_scale
        self.compiler            = compiler
        self.vrd_prefix          = vrd_prefix
        self.vrd_name_counts     = {}

    def _log(self, level: str, color: str, msg: str):
        if self.logger:
            getattr(self.logger, level)(f"{color}{msg}{RESET}")

    def _warn(self, msg: str): self._log("warn",  ORANGE, msg)
    def _err(self,  msg: str): self._log("error", RED,    msg)
    def _info(self, msg: str): self._log("info",  ORANGE, msg)

    def _parse_command(self, line: str) -> list:
        try:
            return shlex.split(line)
        except ValueError:
            return []

    def _substitute_variables(self, line: str, line_num: int = None) -> tuple[str, bool]:
        has_error = False

        def replace(match):
            nonlocal has_error
            name = match.group(1)
            if name in self.macro_args_override:
                return str(self.macro_args_override[name])
            if name in self.variables:
                return str(self.variables[name])
            has_error = True
            if line_num:
                self._err(f"Line {line_num}: Undefined variable '${name}$'")
            return match.group(0)

        return re.sub(r'\$(\w+)\$', replace, line), has_error

    def _effective_vars(self) -> dict:
        return {**self.variables, **self.macro_args_override}

    # ------------------------------------------------------------------
    # Conditional handling
    # ------------------------------------------------------------------

    def _handle_conditional(self, command: str, parts: list, line_num: int,
                             is_skipping: bool, base_dir: Path = None) -> bool:
        if command in ("$if", "$ifdef", "$iffileexist"):
            if is_skipping:
                self.if_stack.append((False, False, command))
            elif command == "$iffileexist":
                result = self._eval_fileexist(parts, line_num, base_dir)
                self.if_stack.append((result, result, command))
            else:
                expr, err = self._substitute_variables(" ".join(parts[1:]), line_num)
                if err:
                    self.if_stack.append((False, False, command))
                else:
                    result = _evaluate_condition(expr, self._effective_vars(), command == "$ifdef")
                    self.if_stack.append((result, result, command))
            return True

        if command == "$elif":
            return self._handle_elif(parts, line_num, base_dir)
        if command == "$else":
            return self._handle_else(line_num)
        if command == "$endif":
            if not self.if_stack:
                self.output_lines.append(f"// ERROR Line {line_num}: $endif without $if\n")
            else:
                self.if_stack.pop()
            return True

        return False

    def _stack_error(self, keyword: str, line_num: int) -> bool:
        self.output_lines.append(f"// ERROR Line {line_num}: {keyword} without $if\n")
        return True

    def _handle_elif(self, parts: list, line_num: int, base_dir: Path = None) -> bool:
        if not self.if_stack:
            return self._stack_error("$elif", line_num)
        _, taken, kind = self.if_stack[-1]
        parent_skip = len(self.if_stack) > 1 and not self.if_stack[-2][0]
        if parent_skip or taken:
            self.if_stack[-1] = (False, True, kind)
        elif kind == "$iffileexist":
            result = self._eval_fileexist(parts, line_num, base_dir)
            self.if_stack[-1] = (result, result, kind)
        else:
            expr, err = self._substitute_variables(" ".join(parts[1:]), line_num)
            if err:
                self.if_stack[-1] = (False, False, kind)
            else:
                result = _evaluate_condition(expr, self._effective_vars(), is_ifdef=False)
                self.if_stack[-1] = (result, result, kind)
        return True

    def _handle_else(self, line_num: int) -> bool:
        if not self.if_stack:
            return self._stack_error("$else", line_num)
        _, taken, kind = self.if_stack[-1]
        parent_skip = len(self.if_stack) > 1 and not self.if_stack[-2][0]
        self.if_stack[-1] = (False, True, kind) if (parent_skip or taken) else (True, True, kind)
        return True

    def _eval_fileexist(self, parts: list, line_num: int, base_dir: Path) -> bool:
        if len(parts) < 2:
            self._warn(f"Line {line_num}: $iffileexist without a path")
            return False

        raw, err = self._substitute_variables(parts[1], line_num)
        if err:
            self._warn(f"Line {line_num}: Undefined variable in $iffileexist path")

        file_path  = Path(raw.strip().strip('"'))
        is_qc_file = file_path.suffix.lower() in (".qc", ".qci")

        if self.pushd_stack and not is_qc_file:
            return (self.pushd_stack[-1] / file_path).resolve().exists()

        resolve_base = self.root_dir or base_dir
        target = (resolve_base / file_path).resolve() if resolve_base else file_path.resolve()

        if not target.exists() and base_dir and resolve_base != base_dir:
            if (base_dir / file_path).resolve().exists():
                return True

        return target.exists()

    # ------------------------------------------------------------------
    # $pushd / $popd
    # ------------------------------------------------------------------

    def _handle_pushd(self, parts: list, line_num: int, base_dir: Path, original_line: str) -> str:
        if len(parts) < 2:
            self._warn(f"Line {line_num}: $pushd without a path")
            return original_line
        raw, _ = self._substitute_variables(parts[1], line_num)
        dir_path = Path(raw.strip().strip('"'))
        if not dir_path.is_absolute():
            current  = self.pushd_stack[-1] if self.pushd_stack else (self.root_dir or base_dir)
            dir_path = current / dir_path
        self.pushd_stack.append(dir_path)
        return original_line

    def _handle_popd(self, line_num: int, original_line: str) -> str:
        if not self.pushd_stack:
            self._warn(f"Line {line_num}: $popd without matching $pushd")
        else:
            self.pushd_stack.pop()
        return original_line

    # ------------------------------------------------------------------
    # Variable definition
    # ------------------------------------------------------------------

    def _eval_value(self, value_str: str) -> str:
        try:
            return str(simple_eval(value_str))
        except Exception:
            return value_str

    def _handle_define_variable(self, parts: list, line_num: int, line: str) -> Optional[str]:
        if len(parts) < 3:
            self._warn(f"Line {line_num}: Malformed $definevariable: {line}")
            return f"// WARNING Line {line_num}: Malformed $definevariable: {line}\n"
        try:
            name     = parts[1]
            raw, err = self._substitute_variables(" ".join(parts[2:]), line_num)
            if err:
                return f"// ERROR Line {line_num}: Undefined variable in expression for $definevariable: {line.rstrip()}\n"

            if name in self.macro_args_override:
                self._warn(f"Line {line_num}: Cannot define variable '{name}' - shadowed by macro argument")
                return f"// WARNING Line {line_num}: Variable '{name}' shadowed by macro argument, ignoring\n"
            if name in self.json_vars:
                return f"// Overridden by JSON config: {line}\n"
            if name in self.defined_vars:
                self._warn(f"Line {line_num}: Variable '{name}' already defined, ignoring redefinition")
                return f"// WARNING Line {line_num}: Variable '{name}' already defined, ignoring\n"

            self.variables[name] = self._eval_value(raw)
            self.defined_vars.add(name)
            return None
        except Exception as e:
            self._warn(f"Line {line_num}: Failed to parse $definevariable: {line} ({e})")
            return f"// WARNING Line {line_num}: Failed to parse $definevariable: {line} ({e})\n"

    def _handle_redefine_variable(self, parts: list, line_num: int, line: str) -> Optional[str]:
        if len(parts) < 3:
            self._warn(f"Line {line_num}: Malformed $redefinevariable: {line}")
            return f"// WARNING Line {line_num}: Malformed $redefinevariable: {line}\n"
        try:
            name     = parts[1]
            raw, err = self._substitute_variables(" ".join(parts[2:]), line_num)
            if err:
                return f"// ERROR Line {line_num}: Undefined variable in expression for $redefinevariable: {line.rstrip()}\n"

            if name in self.macro_args_override:
                self._err(f"Line {line_num}: Cannot redefine macro argument '{name}'")
                return f"// ERROR Line {line_num}: Cannot redefine macro argument '{name}'\n"
            if name not in self.defined_vars:
                self._err(f"Line {line_num}: Cannot redefine undefined variable '{name}'")
                return f"// ERROR Line {line_num}: Cannot redefine undefined variable '{name}'\n"

            self.variables[name] = self._eval_value(raw)
            self._info(f"Line {line_num}: Variable '{name}' redefined to '{self.variables[name]}'")
            return None
        except Exception as e:
            self._warn(f"Line {line_num}: Failed to parse $redefinevariable: {line} ({e})")
            return f"// WARNING Line {line_num}: Failed to parse $redefinevariable: {line} ({e})\n"

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def _resolve_include(self, include_file: str, base_dir: Path) -> tuple[Path, bool]:
        resolve_base = self.root_dir or base_dir
        target = (resolve_base / include_file).resolve()
        if target.exists():
            return target, False

        filename = Path(include_file).name
        for d in self.include_dirs:
            d = Path(d)
            if not d.is_absolute():
                d = (resolve_base / d).resolve()
            candidate = (d / filename).resolve()
            if candidate.exists():
                return candidate, True

        return target, False

    def _resolve_mesh_path(self, raw: str, base_dir: Path) -> Optional[Path]:
        """Resolve a mesh path, trying .dmx then .smd if no extension is given."""
        paths_to_check = [raw] if Path(raw).suffix.lower() in (".dmx", ".smd") else [raw + ".dmx", raw + ".smd"]

        for p_str in paths_to_check:
            p = Path(p_str)
            candidates = [
                self.pushd_stack[-1] / p if self.pushd_stack else None,
                (self.root_dir / p)       if self.root_dir   else None,
                base_dir / p
            ]
            for c in candidates:
                if c and c.exists():
                    return c

        return None

    # ------------------------------------------------------------------
    # DMX editing — unified helper
    # ------------------------------------------------------------------

    def _make_edited_dmx(
        self,
        dmx_path: Path,
        vis_changes: list,
        del_names: list,
        strip_flex: bool = False,
    ) -> Path:
        """
        Apply visibility changes, mesh deletions, and/or flex-rule stripping to
        *dmx_path* in a single load-edit-write pass.

        The output filename uses a CRC-32 hex suffix derived from the combined
        edit set so that identical edits always produce the same file and
        different combinations never collide.  The file is written alongside
        the source DMX.
        """
        import zlib
        key_parts: list[str] = []
        if strip_flex:
            key_parts.append("norules")
        if vis_changes:
            key_parts.append("vis:" + ",".join(f"{n}={v}" for n, v in vis_changes))
        if del_names:
            key_parts.append("del:" + ",".join(del_names))
        crc      = zlib.crc32("|".join(key_parts).encode()) & 0xFFFFFFFF
        out_path = dmx_path.parent / f"{dmx_path.stem}_{crc:08x}.dmx"

        # ---- sniff original encoding / version --------------------------
        orig_enc, orig_ver = "keyvalues2", 1
        try:
            with open(dmx_path, "rb") as fh:
                hdr = b""
                while not hdr.endswith(b">"):
                    ch = fh.read(1)
                    if not ch:
                        break
                    hdr += ch
            hdr_str = hdr.decode("ascii", errors="ignore")
            m = re.findall(datamodel.header_format_regex, hdr_str)
            if m:
                orig_enc, orig_ver = m[0][0], int(m[0][1])
            else:
                m = re.findall(datamodel.header_proto2_regex, hdr_str)
                if m:
                    orig_enc, orig_ver = "binary_proto", int(m[0][0])
        except Exception:
            pass

        dm       = datamodel.load(str(dmx_path))
        mesh_map = {e.name: e for e in dm.elements if e.type == "DmeMesh"}

        # ---- visibility changes -----------------------------------------
        for mesh_name, visible in vis_changes:
            elem = mesh_map.get(mesh_name)
            if elem is not None:
                elem["visible"] = bool(visible)
            elif self.logger:
                self.logger.warn(
                    f"visiblemesh: DmeMesh '{mesh_name}' not found in '{dmx_path.name}'"
                )

        # ---- mesh deletions ---------------------------------------------
        if del_names:
            to_delete: set = set()
            for mesh_name in del_names:
                mesh_elem = mesh_map.get(mesh_name)
                if mesh_elem is None:
                    if self.logger:
                        self.logger.warn(
                            f"removemesh: DmeMesh '{mesh_name}' not found in '{dmx_path.name}'"
                        )
                    continue
                to_delete.add(mesh_elem)
                for e in dm.elements:
                    if e.type == "DmeDag" and e.get("shape") == mesh_elem:
                        to_delete.add(e)
                        transform = e.get("transform")
                        if isinstance(transform, datamodel.Element):
                            to_delete.add(transform)
                    elif e.name == mesh_name and e.type in ("DmeDag", "DmeTransform"):
                        to_delete.add(e)
            for e in to_delete:
                if e in dm.elements:
                    dm.elements.remove(e)
            for parent in dm.elements:
                for attr_key in list(parent.keys()):
                    val = parent[attr_key]
                    if isinstance(val, datamodel.Element) and val in to_delete:
                        del parent[attr_key]
                    elif isinstance(val, datamodel._ElementArray):
                        for e in to_delete:
                            while e in val:
                                val.remove(e)

        # ---- strip flex rules (noautodmxrules 2) ------------------------
        if strip_flex:
            REMOVE_TYPES = {"DmeCombinationInputControl", "DmeCombinationDominationRule"}
            flex_delete  = {e for e in dm.elements if e.type in REMOVE_TYPES}
            for e in flex_delete:
                if self.logger:
                    self.logger.info(f"noautodmxrules 2: Removing {e.type} '{e.name}'")
                dm.elements.remove(e)
            for parent in dm.elements:
                for attr_key in list(parent.keys()):
                    val = parent[attr_key]
                    if isinstance(val, datamodel.Element) and val in flex_delete:
                        del parent[attr_key]
                    elif isinstance(val, datamodel._ElementArray):
                        for e in flex_delete:
                            while e in val:
                                val.remove(e)

        dm.write(str(out_path), orig_enc, orig_ver)
        if self.logger:
            self.logger.info(f"dmx edit: wrote '{out_path.name}'")
        return out_path

    def _strip_block(self, text: str, keyword: str) -> tuple[Optional[str], str]:
        """
        Find the first ``keyword { ... }`` in *text*, extract its content,
        remove it from *text*, and return ``(content, remaining_text)``.
        Returns ``(None, text)`` unchanged if the keyword is not found.
        """
        m = re.search(r'(?<!\w)' + re.escape(keyword) + r'(?!\w)', text, re.IGNORECASE)
        if not m:
            return None, text
        after = text[m.end():]
        brace_pos = after.find("{")
        if brace_pos == -1:
            return None, text
        depth = close_pos = 0
        for ci, ch in enumerate(after):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    close_pos = ci
                    break
        if depth != 0:
            return None, text
        content   = after[brace_pos + 1 : close_pos].strip()
        remaining = text[: m.start()] + after[close_pos + 1 :]
        return content, remaining

    # ------------------------------------------------------------------
    # Bodygroup handler
    # ------------------------------------------------------------------

    def _parse_mesh_vis_tokens(self, content: str) -> list[tuple[str, int]]:
        """Parse '"name1" 0 "name2" "name3" 1' into [(name1,0),(name2,1),(name3,1)]."""
        tokens = self._parse_command(content.strip())
        result, pending = [], []
        for tok in tokens:
            t = tok.strip('"')
            if t in ("0", "1"):
                for name in pending:
                    result.append((name, int(t)))
                pending = []
            else:
                pending.append(t)
        return result

    def _parse_del_tokens(self, content: str) -> list[str]:
        """Parse mesh names from a removemesh { } block content."""
        return [t.strip('"') for t in self._parse_command(content.strip()) if t.strip('"')]

    def _extract_block_content(self, text: str, keyword: str) -> str | None:
        """
        Find 'keyword { ... }' in text (word-boundary safe) and return the
        content between the outer braces, or None if not found.
        """
        m = re.search(r'(?<!\w)' + re.escape(keyword) + r'(?!\w)', text, re.IGNORECASE)
        if not m:
            return None
        after = text[m.end():]
        brace_pos = after.find("{")
        if brace_pos == -1:
            return None
        depth = close_pos = 0
        for ci, ch in enumerate(after):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    close_pos = ci
                    break
        return after[brace_pos + 1:close_pos].strip() if depth == 0 else None

    def _process_bodygroup_studio_lines(self, block_lines: list, base_dir: Path, line_num: int) -> list:
        """
        Scan bodygroup block lines for studio lines carrying visiblemesh{} and/or
        removemesh{} blocks.  Both keywords may appear together on the same studio
        line.  Plain studio/blank lines are passed through unchanged.
        """
        out = []
        i = 0
        while i < len(block_lines):
            raw = block_lines[i]
            i += 1
            stripped = raw.strip()

            if not stripped or stripped.startswith("//"):
                out.append(raw)
                continue

            tokens = self._parse_command(stripped)
            if not tokens or tokens[0].lower() != "studio" or len(tokens) < 2:
                out.append(raw)
                continue

            lc = [t.lower() for t in tokens]
            has_vis = "visiblemesh" in lc
            has_del = "removemesh"  in lc
            if not has_vis and not has_del:
                out.append(raw)
                continue

            studio_path_raw = tokens[1].strip('"')

            # Build the full text of everything after "studio <path>", collecting
            # continuation lines until all opened braces are balanced.
            rest  = " ".join(tokens[2:])
            depth = rest.count("{") - rest.count("}")
            while depth > 0 and i < len(block_lines):
                nxt   = block_lines[i].strip()
                i    += 1
                rest += " " + nxt
                depth += nxt.count("{") - nxt.count("}")

            vis_changes = []
            del_names   = []

            if has_vis:
                content = self._extract_block_content(rest, "visiblemesh")
                if content is not None:
                    vis_changes = self._parse_mesh_vis_tokens(content)

            if has_del:
                content = self._extract_block_content(rest, "removemesh")
                if content is not None:
                    del_names = self._parse_del_tokens(content)

            if not vis_changes and not del_names:
                out.append(f'studio "{studio_path_raw}"\n')
                continue

            dmx_path = self._resolve_mesh_path(studio_path_raw, base_dir)
            if not dmx_path:
                if self.logger:
                    self.logger.warn(
                        f"Line {line_num}: bodygroup studio: cannot resolve '{studio_path_raw}'"
                    )
                out.append(raw)
                continue

            if dmx_path.suffix.lower() != ".dmx":
                if self.logger:
                    self.logger.warn(
                        f"Line {line_num}: bodygroup studio: '{studio_path_raw}' is not a DMX, skipping edit blocks"
                    )
                out.append(f'studio "{studio_path_raw}"\n')
                continue

            try:
                out_path = self._make_edited_dmx(dmx_path, vis_changes, del_names)
            except Exception as e:
                raise QCCompileError(
                    f"Line {line_num}: bodygroup studio: failed for '{studio_path_raw}': {e}"
                )

            orig_dir = str(Path(studio_path_raw).parent)
            rel_path = (
                out_path.name if orig_dir == "."
                else f"{orig_dir}/{out_path.name}".replace("\\", "/")
            )
            out.append(f'studio "{rel_path}"\n')

        return out

    # ------------------------------------------------------------------
    # Block collection
    # ------------------------------------------------------------------

    def _collect_block(self, all_lines: list, i: int, depth: int, base_dir: Path) -> tuple[list[str], int]:
        """Collect lines of a { } block until depth reaches 0, processing conditionals."""
        block_lines = []
        while depth > 0 and i < len(all_lines):
            raw_line    = all_lines[i]
            inner_parts = self._parse_command(raw_line.strip())
            inner_cmd   = inner_parts[0].lower() if inner_parts else ""
            i += 1

            is_skipping = bool(self.if_stack) and not self.if_stack[-1][0]
            if self._handle_conditional(inner_cmd, inner_parts, i, is_skipping, base_dir):
                continue
            if is_skipping:
                continue

            substituted, _ = self._substitute_variables(raw_line, i)
            depth += substituted.count("{") - substituted.count("}")
            block_lines.append(substituted)

        return block_lines, i

    def _collect_block_tokens(self, all_lines: list, i: int, stripped: str, base_dir: Path) -> tuple[list[str], int]:
        """
        Given a stripped line and current read index, collect all quoted tokens
        from the following { } block. Returns (token_list, new_i).
        If no opening brace is found, returns ([], i) unchanged.
        """
        brace_line = stripped
        if "{" not in brace_line and i < len(all_lines):
            brace_line = all_lines[i].strip()
            if "{" in brace_line:
                i += 1

        if "{" not in brace_line:
            return [], i

        after_open = brace_line[brace_line.find("{") + 1:].strip()
        depth      = 1 + after_open.count("{") - after_open.count("}")
        tokens     = []

        pre_close = after_open[:after_open.find("}")] if "}" in after_open else after_open
        for tok in self._parse_command(pre_close):
            tokens.append(tok.strip('"'))

        if depth > 0:
            inner_lines, i = self._collect_block(all_lines, i, depth, base_dir)
            for bl in inner_lines:
                if bl.strip().startswith("//"):
                    continue
                for tok in self._parse_command(bl.strip()):
                    if tok != "}":
                        tokens.append(tok.strip('"'))

        return tokens, i

    # ------------------------------------------------------------------
    # VRD / driver bone block parsers (moved from vrd.py)
    # ------------------------------------------------------------------

    def _parse_driverbone_block(self, all_lines: list, start: int) -> tuple[dict | None, int]:
        result = {"pose": None, "restpose": None, "triggers": [], "target_bones": []}
        i = start - 1

        while i < len(all_lines) and "{" not in all_lines[i]:
            i += 1
        if i >= len(all_lines):
            return None, i
        i += 1

        saved_if_stack = self.if_stack
        self.if_stack  = []

        while i < len(all_lines):
            raw = all_lines[i].strip()
            i += 1

            if raw == "}":
                break
            if not raw or raw.startswith("//"):
                continue

            raw_parts = self._parse_command(raw)
            raw_cmd   = raw_parts[0].lower() if raw_parts else ""

            is_skipping = bool(self.if_stack) and not self.if_stack[-1][0]
            if self._handle_conditional(raw_cmd, raw_parts, i, is_skipping):
                continue
            if is_skipping:
                continue

            line, _  = self._substitute_variables(raw, i)
            tokens   = self._parse_command(line)
            if not tokens:
                continue

            if tokens[0].lower() == "pose":
                result["pose"] = tokens[1]
                continue

            if tokens[0].lower() == "restpose":
                fp = tokens[1] if len(tokens) > 1 else None
                fi = int(tokens[2]) if len(tokens) > 2 else 0
                result["restpose"] = (fp, fi)
                continue

            j = 0
            while j < len(tokens):
                tok = tokens[j]
                if tok.lower() == "trigger":
                    j += 1
                    continue
                if j + 1 < len(tokens):
                    try:
                        angle = float(tok)
                        frame = int(tokens[j + 1])
                        result["triggers"].append((angle, frame))
                        j += 2
                        continue
                    except ValueError:
                        pass
                result["target_bones"].append(tok.strip('"'))
                j += 1

        self.if_stack = saved_if_stack
        return result, i

    def _parse_driverlookatbone_block(self, all_lines: list, start: int) -> tuple[dict | None, int]:
        result = {
            "pose": None, "frame": 0,
            "aimvector": (0.0, 0.0, 0.0), "upvector": (0.0, 0.0, 0.0),
            "location":  (0.0, 0.0, 0.0), "rotation": (0.0, 0.0, 0.0),
            "helper_bones": []
        }
        i = start - 1

        while i < len(all_lines) and "{" not in all_lines[i]:
            i += 1
        if i >= len(all_lines):
            return None, i
        i += 1

        while i < len(all_lines):
            line = all_lines[i].strip()
            i += 1

            if line == "}":
                break
            if not line or line.startswith("//"):
                continue

            tokens  = shlex.split(line)
            if not tokens:
                continue
            keyword = tokens[0].lower()

            if keyword == "pose" and len(tokens) >= 2:
                result["pose"] = tokens[1]
            elif keyword == "frame" and len(tokens) >= 2:
                result["frame"] = int(tokens[1])
            elif keyword == "aimvector" and len(tokens) >= 4:
                result["aimvector"] = (float(tokens[1]), float(tokens[2]), float(tokens[3]))
            elif keyword == "upvector" and len(tokens) >= 4:
                result["upvector"] = (float(tokens[1]), float(tokens[2]), float(tokens[3]))
            elif keyword in ("posoffset", "location") and len(tokens) >= 4:
                result["location"] = (float(tokens[1]), float(tokens[2]), float(tokens[3]))
            elif keyword in ("rotoffset", "rotation") and len(tokens) >= 4:
                result["rotation"] = (float(tokens[1]), float(tokens[2]), float(tokens[3]))
            else:
                for tok in tokens:
                    result["helper_bones"].append(tok.strip('"'))

        return result, i

    # ------------------------------------------------------------------
    # Include / macro expansion
    # ------------------------------------------------------------------

    def _handle_include(self, original_line: str, parts: list, line_num: int,
                        base_dir: Path, include_stack: set, processed_line: str) -> str:
        if len(parts) < 2:
            self._warn(f"Line {line_num}: $include without path: {original_line.rstrip()}")
            return f"// WARNING Line {line_num}: $include without path: {original_line.rstrip()}\n"

        include_file, err = self._substitute_variables(parts[1], line_num)
        if err:
            self._warn(f"Line {line_num}: Undefined variable in $include path, using literal")
            include_file = parts[1]

        target, from_dirs = self._resolve_include(include_file, base_dir)

        if self.logger:
            if from_dirs:
                self.logger.info(f"(includedirs): {target.name}")
                self.logger.debug(f"(includedirs) full path: {target}")
            else:
                self.logger.info(f"(local): {target.name}")
                self.logger.debug(f"(local) full path: {target}")

        if_file_exist = "iffileexist" in [p.lower() for p in parts[2:]]

        if not target.exists():
            if if_file_exist:
                self._warn(f"Line {line_num}: Optional include not found, skipping: {include_file}")
                return f"// WARNING Line {line_num}: Optional include not found, skipping: {include_file}\n"
            msg = f"Include file not found at line {line_num}: {include_file}"
            if self.logger: self.logger.error(msg)
            raise FileNotFoundError(msg)

        if target in include_stack:
            self._warn(f"Line {line_num}: Circular include detected: {include_file}")
            return f"// WARNING Line {line_num}: Circular include detected: {include_file}\n"

        header = (
            f"\n// NOTE: Original path '{include_file}' not found, using includedirs: {target}\n"
            if from_dirs else "\n"
        )

        try:
            nested = process_qc_file(
                target,
                _include_stack=include_stack.copy(),
                _variables=self.variables,
                _macros=self.macros,
                logger=self.logger,
                _defined_vars=self.defined_vars,
                include_dirs=self.include_dirs,
                _root_dir=self.root_dir,
                _pushd_stack=self.pushd_stack,
                _vrd_name_counts=self.vrd_name_counts,
                _current_scale=self.current_scale,
                compiler=self.compiler,
                vrd_prefix=self.vrd_prefix,
            )
            return header + nested + "\n"
        except Exception as e:
            msg = f"Line {line_num}: Failed to process include '{include_file}': {e}"
            if self.logger: self.logger.error(msg)
            return f"// {msg}\n"

    def _handle_macro_expansion(self, active_command: str, parts: list,
                                 base_dir: Path, include_stack: set, line_num: int) -> str:
        macro_name = active_command[1:]
        macro_def  = self.macros[macro_name]
        provided   = parts[1:]

        arg_mapping = {}
        for idx, arg_name in enumerate(macro_def["args"]):
            if idx < len(provided):
                arg_mapping[arg_name] = provided[idx]
            else:
                self._warn(f"Line {line_num}: Macro '{macro_name}' expects argument '{arg_name}' but none provided")

        processor              = QCProcessor(self.variables.copy(), self.macros, self.logger,
                                             macro_args_override=arg_mapping, include_dirs=self.include_dirs,
                                             root_dir=self.root_dir, current_scale=self.current_scale,
                                             compiler=self.compiler)
        processor.defined_vars = self.defined_vars.copy()
        processor.pushd_stack  = list(self.pushd_stack)
        return processor.process_content("\n".join(macro_def["body"]) + "\n", base_dir, include_stack.copy())

    # ------------------------------------------------------------------
    # Line-level processing (used by process_content / macro expansion)
    # ------------------------------------------------------------------

    def process_line(self, line: str, line_num: int, base_dir: Path, include_stack: set) -> Optional[str]:
        stripped = line.strip()
        parts    = self._parse_command(stripped)
        command  = parts[0].lower() if parts else ""
        is_skipping = bool(self.if_stack) and not self.if_stack[-1][0]

        if self._handle_conditional(command, parts, line_num, is_skipping, base_dir):
            return None
        if is_skipping:
            return None
        if command == "$return":
            raise QCReturnException()
        if command == "$pushd":
            return self._handle_pushd(parts, line_num, base_dir, line)
        if command == "$popd":
            return self._handle_popd(line_num, line)

        # $definevariable and $redefinevariable are handled here on the raw parts
        # so that variable substitution happens only on the value expression.
        if command == "$definevariable":
            return self._handle_define_variable(parts, line_num, stripped)
        if command == "$redefinevariable":
            return self._handle_redefine_variable(parts, line_num, stripped)

        processed, has_error = self._substitute_variables(line, line_num)
        if has_error:
            return f"// ERROR Line {line_num}: Undefined variable in line: {line.rstrip()}\n"

        active_parts   = self._parse_command(processed.strip())
        active_command = active_parts[0].lower() if active_parts else ""

        if active_command == "$scale" and len(active_parts) >= 2:
            try:
                self.current_scale = float(active_parts[1])
            except ValueError:
                pass

        if active_command == "$eyeposition" and self.current_scale != 1.0:
            if len(active_parts) >= 4:
                try:
                    x = float(active_parts[1]) * self.current_scale
                    y = float(active_parts[2]) * self.current_scale
                    z = float(active_parts[3]) * self.current_scale
                    active_parts[1] = f"{x:g}"
                    active_parts[2] = f"{y:g}"
                    active_parts[3] = f"{z:g}"
                    processed = " ".join(f'"{t}"' if " " in t else t for t in active_parts) + "\n"
                except ValueError:
                    pass

        if active_command == "$include":
            return self._handle_include(line, active_parts, line_num, base_dir, include_stack, processed.strip())

        if active_command.startswith("$") and active_command[1:] in self.macros:
            return self._handle_macro_expansion(active_command, active_parts, base_dir, include_stack, line_num)

        if active_command in self.COMMENT_OUT_COMMANDS:
            self._info(processed.strip())
            return "// " + processed

        #if active_command == "$modelname" and len(active_parts) >= 2:
        #    lowered = active_parts[1].lower()
        #    return f'$modelname "{lowered}"\n'

        return processed

    def process_content(self, content: str, base_dir: Path, include_stack: set) -> str:
        self.output_lines = []
        self.if_stack     = []
        for line_num, line in enumerate(content.splitlines(True), 1):
            result = self.process_line(line, line_num, base_dir, include_stack)
            if result is not None:
                self.output_lines.append(result)
        return "".join(self.output_lines)

    # ------------------------------------------------------------------
    # Main file processing loop
    # ------------------------------------------------------------------

    def process_file(self, resolved: Path, all_lines: list, include_stack: set) -> list[str]:
        """
        Process all lines of a QC file. Special block commands are handled inline here.
        Returns the list of output line strings (unformatted).
        """
        output_lines          = []
        current_macro         = None
        macro_lines           = []
        new_bonemerge         = set()
        new_lookat_attachments = {}
        base_dir              = resolved.parent

        i = 0
        while i < len(all_lines):
            raw_line = all_lines[i]
            line_num = i + 1
            i += 1

            stripped  = raw_line.strip()
            raw_parts = self._parse_command(stripped)
            raw_cmd   = raw_parts[0].lower() if raw_parts else ""

            # Macro body collection
            if current_macro is not None:
                body_line = raw_line.rstrip()
                if body_line.endswith("\\\\"):
                    macro_lines.append(body_line[:-2].rstrip())
                else:
                    macro_lines.append(body_line)
                    self.macros[current_macro["name"]] = {"args": current_macro["args"], "body": macro_lines}
                    current_macro = None
                    macro_lines   = []
                continue

            is_skipping = bool(self.if_stack) and not self.if_stack[-1][0]
            if self._handle_conditional(raw_cmd, raw_parts, line_num, is_skipping, base_dir):
                continue
            if is_skipping:
                continue

            # $definemacro — collected over following lines ending with \\
            if raw_cmd == "$definemacro":
                no_cont     = stripped[:-2].strip() if stripped.endswith("\\\\") else stripped
                macro_parts = self._parse_command(no_cont)
                if len(macro_parts) >= 2:
                    current_macro = {"name": macro_parts[1], "args": macro_parts[2:]}
                    macro_lines   = []
                else:
                    if self.logger: self.logger.warn(f"Line {line_num}: Malformed $definemacro: {stripped}")
                    output_lines.append(f"// WARNING Line {line_num}: Malformed $definemacro: {stripped}\n")
                continue

            # Variable definition commands are handled before global substitution so that
            # expressions like "1/$ReScale$" are substituted only on the value portion and
            # correctly evaluated even when the surrounding line has no other variable refs.
            if raw_cmd == "$definevariable":
                result = self._handle_define_variable(raw_parts, line_num, stripped)
                if result: output_lines.append(result)
                continue

            if raw_cmd == "$redefinevariable":
                result = self._handle_redefine_variable(raw_parts, line_num, stripped)
                if result: output_lines.append(result)
                continue

            # Global variable substitution for all remaining commands
            line, has_sub_error = self._substitute_variables(raw_line, line_num)
            if has_sub_error:
                output_lines.append(f"// ERROR Line {line_num}: Undefined variable in line: {raw_line.rstrip()}\n")
                output_lines.append(line)
                continue

            stripped = line.strip()
            parts    = self._parse_command(stripped)
            command  = parts[0].lower() if parts else ""

            if command == "$scale" and len(parts) >= 2:
                try:
                    self.current_scale = float(parts[1])
                except ValueError:
                    pass

            # ----------------------------------------------------------
            # VRD / driver bone commands
            # ----------------------------------------------------------

            if command in ("$nekodriverbone", "$driverbone"):
                if len(parts) < 2:
                    raise QCCompileError(f"Line {line_num}: {command} missing driver bone name")

                driver_bone = parts[1].strip('"')
                block, i    = self._parse_driverbone_block(all_lines, i)

                if not block or not block["pose"] or not block["target_bones"]:
                    raise QCCompileError(
                        f"Line {line_num}: {command} '{driver_bone}' block is incomplete (missing pose or target bones)"
                    )

                pose_stem = Path(block["pose"]).stem.lower()
                _vrd_base = f"{self.vrd_prefix}_{pose_stem}_{driver_bone.lower()}" if self.vrd_prefix else f"{pose_stem}_{driver_bone.lower()}"
                vrd_name  = re.sub(r'[^\w]', '_', _vrd_base)
                count     = self.vrd_name_counts.get(vrd_name, 0)
                self.vrd_name_counts[vrd_name] = count + 1
                if count > 0:
                    vrd_name = f"{vrd_name}_{count}"

                pose_base = self.pushd_stack[-1] if self.pushd_stack else self.root_dir
                try:
                    restpose = block.get("restpose")
                    vrd_module.generate_vrd(
                        driver_bone, block["pose"], block["triggers"], block["target_bones"],
                        pose_base, self.root_dir, vrd_name, self.current_scale, logger=self.logger,
                        restpose_path=restpose[0] if restpose else None,
                        restpose_frame=restpose[1] if restpose else 0,
                    )
                except Exception as e:
                    raise QCCompileError(f"Line {line_num}: Failed to generate VRD for '{driver_bone}': {e}")

                for target_bone in block["target_bones"]:
                    if target_bone not in new_bonemerge:
                        output_lines.append(f'$bonemerge "{target_bone}"\n')
                        new_bonemerge.add(target_bone)
                output_lines.append(f'// VRD Scale: {self.current_scale}"\n')
                output_lines.append(f'$proceduralbones "vrds/{vrd_name}.vrd"\n')
                continue

            if command == "$driverlookatbone":
                if len(parts) < 2:
                    raise QCCompileError(f"Line {line_num}: $driverlookatbone missing bone name")

                target_bone = parts[1].strip('"')
                block, i    = self._parse_driverlookatbone_block(all_lines, i)

                if not block or not block["pose"] or not block["helper_bones"]:
                    raise QCCompileError(
                        f"Line {line_num}: $driverlookatbone '{target_bone}' block is incomplete (missing pose or helper bones)"
                    )

                pose_stem       = Path(block["pose"]).stem.lower()
                _vrd_base       = f"{self.vrd_prefix}_lookat_{pose_stem}_{target_bone.lower()}" if self.vrd_prefix else f"lookat_{pose_stem}_{target_bone.lower()}"
                vrd_name        = re.sub(r'[^\w]', '_', _vrd_base)
                count           = self.vrd_name_counts.get(vrd_name, 0)
                self.vrd_name_counts[vrd_name] = count + 1
                if count > 0:
                    vrd_name = f"{vrd_name}_{count}"

                pose_base       = self.pushd_stack[-1] if self.pushd_stack else self.root_dir
                stripped_target = target_bone.split(".")[-1]
                loc, rot        = block["location"], block["rotation"]
                existing        = new_lookat_attachments.get(stripped_target, [])
                attachment_name = next((n for l, r, n in existing if l == loc and r == rot), None)

                if attachment_name is None:
                    base            = f"{stripped_target}_lookattarget"
                    attachment_name = base if not existing else f"{base}_{len(existing)}"
                    existing.append((loc, rot, attachment_name))
                    new_lookat_attachments[stripped_target] = existing
                    pos_str = " ".join(f"{v:g}" for v in loc)
                    rot_str = " ".join(f"{v:g}" for v in rot)
                    output_lines.append(f'$attachment "{attachment_name}" "{target_bone}" {pos_str} rotate {rot_str}\n')

                try:
                    vrd_module.generate_lookat_vrd(
                        target_bone, attachment_name, block["frame"], block["aimvector"], block["upvector"],
                        block["helper_bones"], block["pose"], pose_base, self.root_dir, vrd_name,
                        self.current_scale, logger=self.logger
                    )
                except Exception as e:
                    raise QCCompileError(f"Line {line_num}: Failed to generate lookat VRD for '{target_bone}': {e}")

                for helper_bone in block["helper_bones"]:
                    if helper_bone not in new_bonemerge:
                        output_lines.append(f'$bonemerge "{helper_bone}"\n')
                        new_bonemerge.add(helper_bone)
                output_lines.append(f'// VRD Scale: {self.current_scale}\n')
                output_lines.append(f'$proceduralbones "vrds/{vrd_name}.vrd"\n')
                continue

            # ----------------------------------------------------------
            # $model — block collection + scale + flex controller injection
            # ----------------------------------------------------------

            if command == "$model":
                block_lines = [line]
                if "{" in line:
                    inner_lines, i = self._collect_block(all_lines, i, line.count("{") - line.count("}"), base_dir)
                    block_lines.extend(inner_lines)
                block_content = "".join(block_lines)

                _noauto_m = re.search(r'(?i)\bnoautodmxrules(?:\s+(\d+))?', block_content)
                if _noauto_m:
                    _noauto_val = int(_noauto_m.group(1)) if _noauto_m.group(1) else 1
                    noautodmxrules_mode = min(max(_noauto_val, 1), 2)
                else:
                    noautodmxrules_mode = 0

                if noautodmxrules_mode == 1:
                    block_content = re.sub(r'(?i)(\bnoautodmxrules)\s+\d+', r'\1', block_content)
                elif noautodmxrules_mode == 2:
                    block_content = re.sub(r'(?i)[ \t]*\bnoautodmxrules(?:\s+\d+)?[ \t]*\n?', '', block_content)

                # Extract removemesh / visiblemesh from block before studiomdl sees it.
                # Both must be stripped regardless of whether the mesh is a DMX.
                rm_content,   block_content = self._strip_block(block_content, "removemesh")
                vm_content,   block_content = self._strip_block(block_content, "visiblemesh")
                vis_changes_m = self._parse_mesh_vis_tokens(vm_content) if vm_content is not None else []
                del_names_m   = self._parse_del_tokens(rm_content)      if rm_content is not None else []

                sub_parts = self._parse_command(line.strip())

                # Declare these so the flex-controller block below can reference them
                # even when len(sub_parts) < 3.
                is_dmx         = False
                is_smd         = False
                dmx_path       = None
                final_mesh_path = None

                if len(sub_parts) >= 3:
                    mesh_raw = sub_parts[2].strip('"')
                    dmx_path = self._resolve_mesh_path(mesh_raw, base_dir)

                    if not dmx_path:
                        raise QCCompileError(f"Line {line_num}: $model could not resolve '{mesh_raw}'")

                    is_dmx = dmx_path.suffix.lower() == ".dmx"
                    is_smd = dmx_path.suffix.lower() == ".smd"

                    final_mesh_path = mesh_raw
                    if not Path(mesh_raw).suffix.lower() in (".dmx", ".smd"):
                        final_mesh_path += dmx_path.suffix

                    # Combined DMX edit: noautodmxrules 2 + removemesh + visiblemesh
                    # All three are handled in a single load-edit-write pass via _make_edited_dmx.
                    needs_dmx_edit = (noautodmxrules_mode == 2 or vis_changes_m or del_names_m)
                    if needs_dmx_edit and is_dmx:
                        try:
                            out_path = self._make_edited_dmx(
                                dmx_path, vis_changes_m, del_names_m,
                                strip_flex=(noautodmxrules_mode == 2),
                            )
                            orig_dir        = str(Path(mesh_raw).parent)
                            final_mesh_path = (
                                out_path.name if orig_dir == "."
                                else f"{orig_dir}/{out_path.name}".replace("\\", "/")
                            )
                            dmx_path = out_path   # flex-controller injection reads the edited file
                        except Exception as e:
                            raise QCCompileError(f"Line {line_num}: $model mesh edit failed: {e}")
                    elif needs_dmx_edit and not is_dmx:
                        self._warn(
                            f"Line {line_num}: $model removemesh/visiblemesh requires a DMX mesh, "
                            f"skipping edits for '{mesh_raw}'"
                        )

                # Scaling
                if self.current_scale != 1.0 and self.compiler != "nekomdl":
                    scaled_lines = []
                    for bl in block_content.splitlines(True):
                        btokens  = self._parse_command(bl.strip())
                        bkw      = btokens[0].lower() if btokens else ""
                        xyz_index = {"mouth": 4, "spherenormals": 2, "eyeball": 3}.get(bkw)
                        if xyz_index and len(btokens) >= xyz_index + 3:
                            try:
                                x = float(btokens[xyz_index])     * self.current_scale
                                y = float(btokens[xyz_index + 1]) * self.current_scale
                                z = float(btokens[xyz_index + 2]) * self.current_scale
                                btokens[xyz_index]     = f"{x:g}"
                                btokens[xyz_index + 1] = f"{y:g}"
                                btokens[xyz_index + 2] = f"{z:g}"
                                if bkw == "eyeball":
                                    try:
                                        btokens[-1] = f"{float(btokens[-1]) * self.current_scale:g}"
                                    except ValueError:
                                        pass
                                bl = " ".join(f'"{t}"' if " " in t else t for t in btokens) + "\n"
                            except ValueError:
                                pass
                        scaled_lines.append(bl)
                    block_content = "".join(scaled_lines)

                # Flex Controllers
                if len(sub_parts) >= 3 and is_dmx:
                    res_content, errs, count = flex_controllers.inject_flex_controllers_from_dmx(block_content, dmx_path)

                    if final_mesh_path != mesh_raw:
                        res_content = res_content.replace(f'"{mesh_raw}"', f'"{final_mesh_path}"', 1)

                    for err in errs:
                        output_lines.append(f"// ERROR Line {line_num}: {err}\n")
                    if count > 0 and self.logger:
                        self.logger.info(f"Constructed {count} flex controllers from {dmx_path.name}")
                    output_lines.append(res_content)
                else:
                    output_lines.append(block_content)
                continue

            # ----------------------------------------------------------
            # $defineskeletonhierarchy — emit $hierarchy for each bone
            # ----------------------------------------------------------

            if command in ("$defineskeletonhierarchy", "$defineskeletonheirarchy"):
                if len(parts) < 2:
                    if self.logger: self.logger.warn(f"Line {line_num}: {command} requires a DMX path")
                    output_lines.append(f"// WARNING Line {line_num}: {command} requires a DMX path\n")
                    continue

                dmx_raw      = parts[1].strip('"')
                target_bones, i = self._collect_block_tokens(all_lines, i, stripped, base_dir)
                dmx_path     = self._resolve_mesh_path(dmx_raw, base_dir)

                if not dmx_path:
                    raise QCCompileError(f"Line {line_num}: {command} could not resolve '{dmx_raw}'")

                try:
                    ext    = dmx_path.suffix.lower()
                    frames = (read_dmx_bone_animation(str(dmx_path)) if ext == ".dmx"
                              else read_smd_bone_animation(str(dmx_path)) if ext == ".smd"
                              else None)
                    if frames is None:
                        continue

                    if frames:
                        bone_map       = {bt.bone_name: bt for bt in frames[0]}
                        bones_to_write = target_bones if target_bones else list(bone_map.keys())
                        for bone_name in bones_to_write:
                            bt = bone_map.get(bone_name)
                            if bt:
                                parent = f'"{bt.parent_name}"' if bt.parent_name else '""'
                                output_lines.append(f'$hierarchy "{bone_name}" {parent}\n')
                            else:
                                if self.logger: self.logger.warn(f"Line {line_num}: bone '{bone_name}' not found in '{dmx_raw}'")
                                output_lines.append(f"// WARNING: bone '{bone_name}' not found\n")
                    else:
                        if self.logger: self.logger.warn(f"Line {line_num}: no frames found in '{dmx_raw}'")
                        output_lines.append(f"// WARNING: no frames found in '{dmx_raw}'\n")
                except Exception as e:
                    raise QCCompileError(f"Line {line_num}: Failed to read '{dmx_raw}': {e}")
                continue

            # ----------------------------------------------------------
            # $defineskeleton — emit $definebone for each bone at a frame
            # ----------------------------------------------------------

            if command == "$defineskeleton":
                if len(parts) < 3:
                    if self.logger: self.logger.warn(f"Line {line_num}: $defineskeleton requires a DMX path and frame index")
                    output_lines.append(f"// WARNING Line {line_num}: $defineskeleton requires a DMX path and frame index\n")
                    continue

                dmx_raw = parts[1].strip('"')
                try:
                    frame_idx = int(parts[2])
                except ValueError:
                    if self.logger: self.logger.warn(f"Line {line_num}: $defineskeleton frame index must be an integer")
                    output_lines.append(f"// WARNING Line {line_num}: $defineskeleton frame index must be an integer\n")
                    continue

                target_bones, i = self._collect_block_tokens(all_lines, i, stripped, base_dir)
                dmx_path        = self._resolve_mesh_path(dmx_raw, base_dir)

                if not dmx_path:
                    raise QCCompileError(f"Line {line_num}: $defineskeleton could not resolve '{dmx_raw}'")

                try:
                    ext    = dmx_path.suffix.lower()
                    frames = (frames_rotation_to_degrees(frames_quat_to_euler(read_dmx_bone_animation(str(dmx_path))))
                              if ext == ".dmx" else
                              frames_rotation_to_degrees(read_smd_bone_animation(str(dmx_path)))
                              if ext == ".smd" else None)
                    if frames is None:
                        continue

                    if self.current_scale != 1.0:
                        frames = apply_world_scale(frames, self.current_scale)

                    if frame_idx < len(frames):
                        bone_map       = {bt.bone_name: bt for bt in frames[frame_idx]}
                        bones_to_write = target_bones if target_bones else list(bone_map.keys())
                        for bone_name in bones_to_write:
                            bt = bone_map.get(bone_name)
                            if bt:
                                x, y, z    = bt.location
                                rx, ry, rz = bt.rotation
                                parent     = f'"{bt.parent_name}"' if bt.parent_name else '""'
                                output_lines.append(
                                    f'$definebone "{bone_name}" {parent} '
                                    f'{x:.6f} {y:.6f} {z:.6f} '
                                    f'{rx:.6f} {ry:.6f} {rz:.6f} '
                                    f'0 0 0 0 0 0\n'
                                )
                            else:
                                if self.logger: self.logger.warn(f"Line {line_num}: bone '{bone_name}' not found in '{dmx_raw}'")
                                output_lines.append(f"// WARNING: bone '{bone_name}' not found\n")
                    else:
                        if self.logger: self.logger.warn(f"Line {line_num}: frame index {frame_idx} out of range in '{dmx_raw}'")
                        output_lines.append(f"// WARNING: frame index {frame_idx} out of range in '{dmx_raw}'\n")
                except QCCompileError:
                    raise
                except Exception as e:
                    raise QCCompileError(f"Line {line_num}: Failed to read '{dmx_raw}': {e}")
                continue

            # ----------------------------------------------------------
            # $body — optional removemesh / visiblemesh edit blocks
            # ----------------------------------------------------------

            if command == "$body":
                lc_parts = [p.lower() for p in parts]
                has_vis  = "visiblemesh" in lc_parts
                has_del  = "removemesh"  in lc_parts

                if (has_vis or has_del) and len(parts) >= 3:
                    body_name = parts[1].strip('"')
                    mesh_raw  = parts[2].strip('"')

                    # Collect everything after the mesh path token, spanning continuation lines
                    # until brace depth is balanced.
                    rest  = " ".join(parts[3:])
                    depth = rest.count("{") - rest.count("}")
                    while depth > 0 and i < len(all_lines):
                        nxt   = all_lines[i].strip()
                        i    += 1
                        rest += " " + nxt
                        depth += nxt.count("{") - nxt.count("}")

                    vis_changes, del_names = [], []
                    if has_vis:
                        cnt = self._extract_block_content(rest, "visiblemesh")
                        if cnt is not None:
                            vis_changes = self._parse_mesh_vis_tokens(cnt)
                    if has_del:
                        cnt = self._extract_block_content(rest, "removemesh")
                        if cnt is not None:
                            del_names = self._parse_del_tokens(cnt)

                    dmx_path = self._resolve_mesh_path(mesh_raw, base_dir)
                    if not dmx_path:
                        raise QCCompileError(f"Line {line_num}: $body could not resolve '{mesh_raw}'")

                    # Normalise mesh path extension
                    file_str = mesh_raw
                    if not Path(mesh_raw).suffix.lower() in (".dmx", ".smd"):
                        file_str += dmx_path.suffix

                    if dmx_path.suffix.lower() != ".dmx":
                        self._warn(
                            f"Line {line_num}: $body removemesh/visiblemesh requires a DMX mesh, "
                            f"skipping edits for '{mesh_raw}'"
                        )
                        output_lines.append(f'$body "{body_name}" "{file_str}"\n')
                        continue

                    try:
                        out_path = self._make_edited_dmx(dmx_path, vis_changes, del_names)
                    except Exception as e:
                        raise QCCompileError(
                            f"Line {line_num}: $body mesh edit failed for '{mesh_raw}': {e}"
                        )

                    orig_dir = str(Path(mesh_raw).parent)
                    rel_path = (
                        out_path.name if orig_dir == "."
                        else f"{orig_dir}/{out_path.name}".replace("\\", "/")
                    )
                    output_lines.append(f'$body "{body_name}" "{rel_path}"\n')
                    continue
                # No edit blocks — fall through to passthrough below.

            # ----------------------------------------------------------
            # $rendermeshlist — expand into $body lines
            # ----------------------------------------------------------

            if command == "$rendermeshlist":
                replace_rules  = []
                # Each entry: (mesh_name, vis_changes, del_names)
                mesh_entries: list[tuple[str, list, list]] = []
                variants       = []
                ignore_missing = False

                brace_line = stripped
                if "{" not in brace_line and i < len(all_lines):
                    brace_line = all_lines[i].strip()
                    if "{" in brace_line:
                        i += 1

                if "{" not in brace_line:
                    if self.logger: self.logger.warn(f"Line {line_num}: $rendermeshlist missing opening brace")
                    output_lines.append(f"// WARNING Line {line_num}: $rendermeshlist missing opening brace\n")
                    continue

                after_open = brace_line[brace_line.find("{") + 1:].strip()
                depth      = 1 + after_open.count("{") - after_open.count("}")

                # Handle tokens on the same line as the opening brace.
                # These are treated as plain directives / mesh names (no multi-line blocks here).
                if after_open:
                    pre_close = after_open[:after_open.find("}")] if "}" in after_open else after_open
                    for tok in self._parse_command(pre_close):
                        t = tok.strip('"')
                        if not t or t == "}":
                            continue
                        tl = t.lower()
                        if tl == "ignore_missing":
                            # handled as keyword=value on its own line; treat bare presence as true
                            ignore_missing = True
                        elif tl not in ("replace", "suffix", "prefix", "visiblemesh", "removemesh"):
                            mesh_entries.append((t, [], []))

                if depth > 0:
                    inner_lines, i = self._collect_block(all_lines, i, depth, base_dir)

                    j = 0
                    while j < len(inner_lines):
                        bl         = inner_lines[j]
                        stripped_bl = bl.strip()
                        j += 1

                        if not stripped_bl or stripped_bl.startswith("//") or stripped_bl == "}":
                            continue

                        toks    = self._parse_command(stripped_bl)
                        if not toks:
                            continue
                        keyword = toks[0].lower()

                        if keyword in ("suffix", "prefix") and len(toks) >= 2:
                            variants.append((keyword, toks[1].strip('"')))
                            continue
                        if keyword == "replace" and len(toks) >= 2:
                            replace_rules.append((toks[1], toks[2] if len(toks) >= 3 else ""))
                            continue
                        if keyword == "ignore_missing" and len(toks) >= 2:
                            ignore_missing = toks[1].strip() not in ("0", "false")
                            continue

                        # Mesh entry: first token is the name; subsequent tokens may include
                        # removemesh / visiblemesh block keywords.
                        mesh_name = toks[0].strip('"')
                        lc_toks   = [t.lower() for t in toks]
                        has_vis   = "visiblemesh" in lc_toks
                        has_del   = "removemesh"  in lc_toks

                        if has_vis or has_del:
                            # Collect rest of entry, gathering continuation lines for multi-line blocks.
                            rest  = " ".join(toks[1:])
                            depth2 = rest.count("{") - rest.count("}")
                            while depth2 > 0 and j < len(inner_lines):
                                nxt    = inner_lines[j].strip()
                                j     += 1
                                rest  += " " + nxt
                                depth2 += nxt.count("{") - nxt.count("}")

                            vis_changes, del_names = [], []
                            if has_vis:
                                cnt = self._extract_block_content(rest, "visiblemesh")
                                if cnt is not None:
                                    vis_changes = self._parse_mesh_vis_tokens(cnt)
                            if has_del:
                                cnt = self._extract_block_content(rest, "removemesh")
                                if cnt is not None:
                                    del_names = self._parse_del_tokens(cnt)
                            mesh_entries.append((mesh_name, vis_changes, del_names))
                        else:
                            # Plain line: all tokens are mesh names (skip stray "}")
                            for tok in toks:
                                t = tok.strip('"')
                                if t and t != "}":
                                    mesh_entries.append((t, [], []))

                for mesh_name, vis_changes, del_names in mesh_entries:
                    body_name = mesh_name
                    for pattern, replacement in replace_rules:
                        body_name = re.sub(pattern, replacement, body_name)

                    dmx_path = self._resolve_mesh_path(mesh_name, base_dir)
                    if dmx_path:
                        file_str = mesh_name if Path(mesh_name).suffix.lower() in (".dmx", ".smd") else mesh_name + dmx_path.suffix
                    else:
                        file_str = mesh_name + ".dmx" if not Path(mesh_name).suffix else mesh_name

                    # Apply removemesh / visiblemesh edits if present
                    if (vis_changes or del_names) and dmx_path:
                        if dmx_path.suffix.lower() != ".dmx":
                            self._warn(
                                f"Line {line_num}: $rendermeshlist removemesh/visiblemesh requires a DMX mesh, "
                                f"skipping edits for '{mesh_name}'"
                            )
                        else:
                            try:
                                out_path = self._make_edited_dmx(dmx_path, vis_changes, del_names)
                                orig_dir = str(Path(file_str).parent)
                                file_str = (
                                    out_path.name if orig_dir == "."
                                    else f"{orig_dir}/{out_path.name}".replace("\\", "/")
                                )
                            except Exception as e:
                                raise QCCompileError(
                                    f"Line {line_num}: $rendermeshlist mesh edit failed for '{mesh_name}': {e}"
                                )

                    if not dmx_path:
                        if ignore_missing:
                            if self.logger: self.logger.warn(f"Line {line_num}: $rendermeshlist '{mesh_name}' not found, skipping")
                            output_lines.append(f"// WARNING: '{mesh_name}' not found\n")
                            output_lines.append(f'// $body "{body_name}" "{file_str}"\n')
                            continue
                        else:
                            if self.logger: self.logger.warn(f"Line {line_num}: $rendermeshlist could not resolve '{mesh_name}'")
                            output_lines.append(f"// WARNING Line {line_num}: $rendermeshlist could not resolve '{mesh_name}'\n")

                    output_lines.append(f'$body "{body_name}" "{file_str}"\n')

                    # Variants intentionally do not inherit removemesh / visiblemesh edits.
                    for vtype, vstring in variants:
                        p = Path(file_str)
                        if vtype == "suffix":
                            var_body = body_name + vstring
                            var_file = str(p.with_name(p.stem + vstring + p.suffix)).replace("\\", "/")
                        else:
                            var_body = vstring + body_name
                            var_file = str(p.with_name(vstring + p.stem + p.suffix)).replace("\\", "/")

                        if ignore_missing and not self._resolve_mesh_path(var_file, base_dir):
                            if self.logger: self.logger.warn(f"Line {line_num}: $rendermeshlist variant '{var_file}' not found, skipping")
                            output_lines.append(f"// WARNING: variant '{var_file}' not found\n")
                            output_lines.append(f'// $body "{var_body}" "{var_file}"\n')
                        else:
                            output_lines.append(f'$body "{var_body}" "{var_file}"\n')

                continue

            # ----------------------------------------------------------
            # $bodygroup — handle studio ... visiblemesh { } / removemesh { } sub-blocks
            # ----------------------------------------------------------

            if command == "$bodygroup":
                if "{" in line:
                    brace_line = line
                elif i < len(all_lines) and "{" in all_lines[i]:
                    brace_line = all_lines[i]
                    i += 1
                else:
                    output_lines.append(line)
                    continue

                depth = brace_line.count("{") - brace_line.count("}")
                if depth > 0:
                    inner_lines, i = self._collect_block(all_lines, i, depth, base_dir)
                else:
                    inner_lines = []

                new_body = self._process_bodygroup_studio_lines(inner_lines, base_dir, line_num)

                output_lines.append(line)
                if "{" not in line:
                    output_lines.append(brace_line if brace_line.endswith("\n") else brace_line + "\n")
                output_lines.extend(new_body)
                continue

            # ----------------------------------------------------------
            # Passthrough to inline command handler
            # ----------------------------------------------------------

            result = self.process_line(line, line_num, base_dir, include_stack)
            if result is not None:
                output_lines.append(result)

        # Flush any error lines written to self.output_lines by _stack_error
        output_lines.extend(self.output_lines)
        self.output_lines = []
        return output_lines


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _format_qc_output(text: str) -> str:
    lines  = text.splitlines()
    result = []
    depth  = 0
    consecutive_newlines = 0

    for line in lines:
        stripped = line.strip()

        if not stripped:
            consecutive_newlines += 1
            if consecutive_newlines <= 1:
                result.append("")
            continue

        consecutive_newlines = 0
        net = stripped.count("{") - stripped.count("}")

        if net < 0:
            depth = max(0, depth + net)

        result.append(stripped if depth == 0 else "\t" * depth + stripped)

        if net > 0:
            depth += net

    return "\n".join(result).strip() + "\n"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def process_qc_file(
    qc_path: Path,
    _include_stack: set   = None,
    _variables: dict      = None,
    _macros: dict         = None,
    logger                = None,
    _defined_vars: set    = None,
    include_dirs: list    = None,
    _root_dir: Path       = None,
    _pushd_stack: list    = None,
    _vrd_name_counts: dict = None,
    _current_scale: float = 1.0,
    compiler: str         = None,
    vrd_prefix: str       = None,
) -> str:

    _include_stack = _include_stack or set()
    _variables     = _variables     or {}
    _macros        = _macros        or {}
    _defined_vars  = _defined_vars  if _defined_vars is not None else set(_variables)
    _pushd_stack   = list(_pushd_stack) if _pushd_stack is not None else []

    try:
        resolved = qc_path.resolve(strict=True)
    except FileNotFoundError:
        raise QCCompileError(f"QC file not found: {qc_path.as_posix()}")
    except Exception as e:
        raise QCCompileError(f"Failed to resolve path '{qc_path.as_posix()}': {e}")

    if resolved in _include_stack:
        raise QCCompileError(f"Circular $include detected: '{resolved.as_posix()}' is already in the include stack.")

    _include_stack.add(resolved)
    _root_dir = _root_dir or resolved.parent

    processor = QCProcessor(_variables, _macros, logger,
                            include_dirs=include_dirs or [],
                            root_dir=_root_dir,
                            current_scale=_current_scale,
                            compiler=compiler,
                            vrd_prefix=vrd_prefix)

    processor.defined_vars  = _defined_vars
    processor.pushd_stack   = _pushd_stack
    processor.vrd_name_counts = _vrd_name_counts if _vrd_name_counts is not None else {}

    with resolved.open("r", encoding="utf-8", errors="ignore") as f:
        all_lines = f.readlines()

    output_lines = processor.process_file(resolved, all_lines, _include_stack)
    _include_stack.remove(resolved)

    return _format_qc_output("".join(output_lines))


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def qc_read_includes(qc_path: Path) -> list[Path]:
    if not qc_path.exists():
        return []

    visited, includes = set(), []

    def scan(path: Path):
        if path in visited or not path.exists():
            return
        visited.add(path)
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line.lower().startswith("$include"):
                    continue
                raw    = line.split(None, 1)[1].strip().strip('"')
                target = (path.parent / raw).resolve()
                if target.exists():
                    includes.append(target)
                    scan(target)

    scan(qc_path)
    return includes


def qc_read_materials(qc_path: Path, dumped_materials: list[str] | None = None) -> list[str]:
    if not qc_path.exists():
        return dumped_materials or []

    dumped_materials = dumped_materials or []

    def tokenize_quoted(raw: str) -> list[str]:
        tokens, buf, in_q = [], [], False
        for ch in raw:
            if ch == '"':
                if in_q and buf:
                    tokens.append("".join(buf).strip().replace("\\", "/"))
                    buf.clear()
                in_q = not in_q
            elif ch.isspace() and not in_q:
                if buf:
                    tokens.append("".join(buf).strip().replace("\\", "/"))
                    buf.clear()
            else:
                buf.append(ch)
        if buf:
            tokens.append("".join(buf).strip().replace("\\", "/"))
        return tokens

    def parse_renamematerial(path: Path) -> dict[str, str]:
        mapping = {}
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line.lower().startswith("$renamematerial"):
                    continue
                args = tokenize_quoted(line[len("$renamematerial"):].strip())
                if len(args) == 2:
                    mapping[args[0]] = args[1]
        return mapping

    def parse_cdmaterials(path: Path) -> list[str]:
        found = []
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line.lower().startswith("$cdmaterials"):
                    continue
                raw = line.split(None, 1)[1] if " " in line else ""
                found.extend(tokenize_quoted(raw))
        return found

    def parse_texturegroup(path: Path) -> list[str]:
        mats  = []
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        i     = 0
        while i < len(lines):
            if lines[i].strip().lower().startswith("$texturegroup") and "skinfamilies" in lines[i].lower():
                while i < len(lines) and "{" not in lines[i]:
                    i += 1
                i += 1
                while i < len(lines) and "}" not in lines[i]:
                    mats.extend(tokenize_quoted(lines[i].strip()))
                    i += 1
            else:
                i += 1
        return mats

    includes    = qc_read_includes(qc_path)
    all_paths   = [qc_path, *includes]
    rename_map  = {}
    cdmats, texmats = [], []

    for p in all_paths:
        rename_map.update(parse_renamematerial(p))
        cdmats.extend(parse_cdmaterials(p))
        texmats.extend(parse_texturegroup(p))

    renamed_dumps = [rename_map.get(m, m) for m in dumped_materials]
    all_materials = renamed_dumps + [rename_map.get(m, m) for m in texmats]

    if not cdmats:
        return renamed_dumps + all_materials

    return renamed_dumps + [
        f"{base.rstrip('/')}/{mat}"
        for base in cdmats
        for mat in all_materials
    ]