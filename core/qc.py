import shlex, re
from simpleeval import simple_eval
from pathlib import Path
from typing import Optional
from utils import Logger
from core import vrd as vrd_module
from core import flex_controllers
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
        compiler: str         = None
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

    def _resolve_dmx_path(self, raw_path: str, base_dir: Path) -> Optional[Path]:
        p          = Path(raw_path)
        candidates = [
            self.pushd_stack[-1] / p if self.pushd_stack else None,
            (self.root_dir / p)       if self.root_dir   else None,
            base_dir / p
        ]
        for c in candidates:
            if c and c.exists():
                return c
        return None

    def _resolve_mesh_path(self, raw: str, base_dir: Path) -> Optional[Path]:
        """Resolve a mesh path, trying .dmx then .smd if no extension is given."""
        if Path(raw).suffix.lower() in (".dmx", ".smd"):
            return self._resolve_dmx_path(raw, base_dir)
        for ext in (".dmx", ".smd"):
            p = self._resolve_dmx_path(raw + ext, base_dir)
            if p:
                return p
        return None

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
                for tok in self._parse_command(bl.strip()):
                    if tok != "}":
                        tokens.append(tok.strip('"'))

        return tokens, i

    # ------------------------------------------------------------------
    # VRD / driver bone block parsers (moved from vrd.py)
    # ------------------------------------------------------------------

    def _parse_driverbone_block(self, all_lines: list, start: int) -> tuple[dict | None, int]:
        result = {"pose": None, "triggers": [], "target_bones": []}
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

            tokens = shlex.split(line)
            if not tokens:
                continue

            if tokens[0].lower() == "pose":
                result["pose"] = tokens[1]
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

        if not target.exists():
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
                compiler=self.compiler
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
                vrd_name  = re.sub(r'[^\w]', '_', f"{pose_stem}_{driver_bone.lower()}")
                count     = self.vrd_name_counts.get(vrd_name, 0)
                self.vrd_name_counts[vrd_name] = count + 1
                if count > 0:
                    vrd_name = f"{vrd_name}_{count}"

                pose_base = self.pushd_stack[-1] if self.pushd_stack else self.root_dir
                try:
                    vrd_module.generate_vrd(
                        driver_bone, block["pose"], block["triggers"], block["target_bones"],
                        pose_base, self.root_dir, vrd_name, self.current_scale, logger=self.logger
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
                vrd_name        = re.sub(r'[^\w]', '_', f"lookat_{pose_stem}_{target_bone.lower()}")
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

                sub_parts = self._parse_command(line.strip())
                if len(sub_parts) >= 3 and sub_parts[2].lower().endswith(".dmx"):
                    dmx_raw  = sub_parts[2].strip('"')
                    dmx_path = self._resolve_dmx_path(dmx_raw, base_dir)
                    if dmx_path:
                        res_content, errs, count = flex_controllers.inject_flex_controllers_from_dmx(block_content, dmx_path)
                        for err in errs:
                            output_lines.append(f"// ERROR Line {line_num}: {err}\n")
                        if count > 0 and self.logger:
                            self.logger.info(f"Constructed {count} flex controllers from {dmx_path.name}")
                        output_lines.append(res_content)
                    else:
                        if self.logger: self.logger.warn(f"Line {line_num}: Could not resolve DMX '{dmx_raw}' for $model, flex controllers skipped")
                        output_lines.append(f"// WARNING Line {line_num}: Could not resolve DMX '{dmx_raw}'\n")
                        output_lines.append(block_content)
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
            # $rendermeshlist — expand into $body lines
            # ----------------------------------------------------------

            if command == "$rendermeshlist":
                replace_rules  = []
                mesh_names     = []
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

                def parse_rendermesh_tokens(tokens):
                    nonlocal ignore_missing
                    if not tokens:
                        return
                    keyword = tokens[0].lower()
                    if keyword == "replace" and len(tokens) >= 2:
                        replace_rules.append((tokens[1], tokens[2] if len(tokens) >= 3 else ""))
                    elif keyword == "ignore_missing" and len(tokens) >= 2:
                        ignore_missing = tokens[1].strip() not in ("0", "false")
                    else:
                        for token in tokens:
                            if token != "}":
                                mesh_names.append(token.strip('"'))

                if after_open:
                    pre_close = after_open[:after_open.find("}")] if "}" in after_open else after_open
                    parse_rendermesh_tokens(self._parse_command(pre_close))

                if depth > 0:
                    inner_lines, i = self._collect_block(all_lines, i, depth, base_dir)
                    for bl in inner_lines:
                        toks = self._parse_command(bl.strip())
                        if toks and toks[0].lower() in ("suffix", "prefix") and len(toks) >= 2:
                            variants.append((toks[0].lower(), toks[1].strip('"')))
                        else:
                            parse_rendermesh_tokens(toks)

                for mesh_name in mesh_names:
                    body_name = mesh_name
                    for pattern, replacement in replace_rules:
                        body_name = re.sub(pattern, replacement, body_name)

                    if Path(mesh_name).suffix.lower() in (".dmx", ".smd"):
                        file_str = mesh_name
                        dmx_path = self._resolve_dmx_path(mesh_name, base_dir)
                    else:
                        dmx_path = self._resolve_dmx_path(mesh_name + ".dmx", base_dir)
                        if dmx_path:
                            file_str = mesh_name + ".dmx"
                        else:
                            dmx_path = self._resolve_dmx_path(mesh_name + ".smd", base_dir)
                            file_str = mesh_name + ".smd" if dmx_path else mesh_name + ".dmx"

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

                    for vtype, vstring in variants:
                        p = Path(file_str)
                        if vtype == "suffix":
                            var_body = body_name + vstring
                            var_file = str(p.with_name(p.stem + vstring + p.suffix)).replace("\\", "/")
                        else:
                            var_body = vstring + body_name
                            var_file = str(p.with_name(vstring + p.stem + p.suffix)).replace("\\", "/")

                        if ignore_missing and not self._resolve_dmx_path(var_file, base_dir):
                            if self.logger: self.logger.warn(f"Line {line_num}: $rendermeshlist variant '{var_file}' not found, skipping")
                            output_lines.append(f"// WARNING: variant '{var_file}' not found\n")
                            output_lines.append(f'// $body "{var_body}" "{var_file}"\n')
                        else:
                            output_lines.append(f'$body "{var_body}" "{var_file}"\n')

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
    compiler: str         = None
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

    processor              = QCProcessor(_variables, _macros, logger,
                                         include_dirs=include_dirs or [],
                                         root_dir=_root_dir,
                                         current_scale=_current_scale,
                                         compiler=compiler)
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