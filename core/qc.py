import shlex, re
from simpleeval import simple_eval
from pathlib import Path
from typing import Optional
from utils import Logger
from core import vrd as vrd_module
from core import flex_controllers
from core.bone_animations import read_dmx_bone_animation, frames_quat_to_euler, frames_rotation_to_degrees, read_smd_bone_animation, apply_world_scale

# TODO: This entirety is utterly retarded, refine how qc command are handled
# Maybe process all $include first then start again at line 0 then parse all qc command

# NOTE: Parsing cdmaterials just check every possibility which isn't efficient

#
# Variables
#

ORANGE = "\033[38;5;208m"
RED    = "\033[91m"
RESET  = "\033[0m"

_CMP_RE = re.compile(r'^\s*([^\s"]+|"[^"]+")\s*(==|!=|>=|<=|>|<)\s*([^\s"]+|"[^"]+")\s*$')

#
# Condition evaluation
#

def _get_value(val_str: str, variables: dict, allow_literal: bool = False):
    val_str = val_str.strip()
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
    left = _get_value(left_str, variables)
    right = _get_value(right_str, variables, allow_literal=True)
    try:
        l, r = float(left), float(right)
        return {
            '==': l == r, '!=': l != r,
            '>':  l > r,  '<':  l < r,
            '>=': l >= r, '<=': l <= r,
        }.get(op, False)
    except (ValueError, TypeError):
        if op == '==': return str(left) == str(right)
        if op == '!=': return str(left) != str(right)
    return False


def _evaluate_condition(expression: str, variables: dict, is_ifdef: bool) -> bool:
    expression = expression.strip()
    for or_part in expression.split('||'):
        terms = [t.strip() for t in or_part.split('&&') if t.strip()]
        if is_ifdef:
            if all(t in variables for t in terms):
                return True
        elif all(_eval_and_term(t, variables) for t in terms):
            return True
    return False


def _eval_and_term(term: str, variables: dict) -> bool:
    m = _CMP_RE.match(term)
    if m:
        return _compare(m.group(1), m.group(2), m.group(3), variables)
    val = _get_value(term, variables)
    return val is not None and str(val).strip() not in ("0", "", "false")


#
# Exceptions
#

class QCReturnException(Exception):
    pass

class QCCompileError(Exception):
    pass


#
# Processor
#


class QCProcessor:
    COMMENT_OUT_COMMANDS = {"$msg", "$echo"}

    def __init__(
        self,
        variables: dict = None,
        macros: dict = None,
        logger=None,
        macro_args_override: dict = None,
        include_dirs: list = None,
        root_dir: Path = None,
        current_scale: float = 1.0,
        compiler: str = None
    ):
        self.variables          = variables if variables is not None else {}
        self.macros             = macros    if macros    is not None else {}
        self.logger: Logger     = logger
        self.macro_args_override = macro_args_override or {}
        self.include_dirs       = include_dirs or []
        self.root_dir           = root_dir
        self.if_stack           = []
        self.output_lines       = []
        self.json_vars          = set(self.variables)
        self.defined_vars       = set(self.variables)
        self.pushd_stack        = []
        self.current_scale      = current_scale
        self.compiler           = compiler

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

    def process_line(self, line: str, line_num: int, base_dir: Path, include_stack: set) -> Optional[str]:
        stripped      = line.strip()
        parts         = self._parse_command(stripped)
        command       = parts[0].lower() if parts else ""
        is_skipping   = bool(self.if_stack) and not self.if_stack[-1][0]

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

        if active_command == "$include":
            return self._handle_include(line, active_parts, line_num, base_dir, include_stack, processed.strip())

        if active_command.startswith('$') and active_command[1:] in self.macros:
            return self._handle_macro_expansion(active_command, active_parts, base_dir, include_stack, line_num)

        if active_command in self.COMMENT_OUT_COMMANDS:
            self._info(processed.strip())
            return "// " + processed

        return processed

    def _handle_conditional(self, command: str, parts: list, line_num: int, is_skipping: bool, base_dir: Path = None) -> bool:
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

    def _pushd_path(self) -> Optional[Path]:
        return self.pushd_stack[-1] if self.pushd_stack else None

    def _handle_pushd(self, parts: list, line_num: int, base_dir: Path, original_line: str) -> str:
        if len(parts) < 2:
            self._warn(f"Line {line_num}: $pushd without a path")
            return original_line
        raw, _ = self._substitute_variables(parts[1], line_num)
        dir_path = Path(raw.strip().strip('"'))
        if not dir_path.is_absolute():
            current = self._pushd_path() or self.root_dir or base_dir
            dir_path = current / dir_path
        self.pushd_stack.append(dir_path)
        return original_line

    def _handle_popd(self, line_num: int, original_line: str) -> str:
        if not self.pushd_stack:
            self._warn(f"Line {line_num}: $popd without matching $pushd")
        else:
            self.pushd_stack.pop()
        return original_line

    def _eval_fileexist(self, parts: list, line_num: int, base_dir: Path) -> bool:
        if len(parts) < 2:
            self._warn(f"Line {line_num}: $iffileexist without a path")
            return False

        raw, err = self._substitute_variables(parts[1], line_num)
        if err:
            self._warn(f"Line {line_num}: Undefined variable in $iffileexist path")

        file_path = Path(raw.strip().strip('"'))
        is_qc_file = file_path.suffix.lower() in (".qc", ".qci")

        if self.pushd_stack and not is_qc_file:
            return (self.pushd_stack[-1] / file_path).resolve().exists()

        resolve_base = self.root_dir or base_dir
        target = (resolve_base / file_path).resolve() if resolve_base else file_path.resolve()

        if not target.exists() and base_dir and resolve_base != base_dir:
            if (base_dir / file_path).resolve().exists():
                return True

        return target.exists()

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
            name = parts[1]
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
            name = parts[1]
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
        p = Path(raw_path)
        candidates = [self.pushd_stack[-1] / p if self.pushd_stack else None,
                    (self.root_dir / p) if self.root_dir else None,
                    base_dir / p]
        for c in candidates:
            if c and c.exists():
                return c
        return None

    def _collect_block(self, all_lines: list, i: int, depth: int, base_dir: Path) -> tuple[list[str], int]:
        """Collect lines of a { } block until depth reaches 0.
        Conditionals are evaluated and variable substitution is applied.
        The closing '}' line is included in the returned lines."""
        block_lines = []
        while depth > 0 and i < len(all_lines):
            raw_line = all_lines[i]
            inner_parts = self._parse_command(raw_line.strip())
            inner_cmd = inner_parts[0].lower() if inner_parts else ""
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

        header = f"\n// NOTE: Original path '{include_file}' not found, using includedirs: {target}\n" if from_dirs else "\n"

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
        for i, arg_name in enumerate(macro_def['args']):
            if i < len(provided):
                arg_mapping[arg_name] = provided[i]
            else:
                self._warn(f"Line {line_num}: Macro '{macro_name}' expects argument '{arg_name}' but none provided")

        processor = QCProcessor(
            self.variables.copy(), self.macros, self.logger,
            macro_args_override=arg_mapping,
            include_dirs=self.include_dirs,
            root_dir=self.root_dir,
            current_scale=self.current_scale,
            compiler=self.compiler
        )
        processor.defined_vars = self.defined_vars.copy()
        processor.pushd_stack  = list(self.pushd_stack)
        return processor.process_content('\n'.join(macro_def['body']) + '\n', base_dir, include_stack.copy())

    def process_content(self, content: str, base_dir: Path, include_stack: set) -> str:
        self.output_lines = []
        self.if_stack     = []
        for line_num, line in enumerate(content.splitlines(True), 1):
            result = self.process_line(line, line_num, base_dir, include_stack)
            if result is not None:
                self.output_lines.append(result)
        return "".join(self.output_lines)


def _format_qc_output(text: str) -> str:
    lines = text.splitlines()
    result = []
    depth = 0
    consecutive_newlines = 0

    for line in lines:
        stripped = line.strip()

        if not stripped:
            consecutive_newlines += 1
            if consecutive_newlines <= 1:
                result.append("")
            continue

        consecutive_newlines = 0

        net = stripped.count('{') - stripped.count('}')
        if net < 0:
            depth = max(0, depth + net)

        formatted_line = stripped if depth == 0 else '\t' * depth + stripped

        if net > 0:
            depth += net

        result.append(formatted_line)

    return "\n".join(result).strip() + "\n"


def process_qc_file(
    qc_path: Path,
    _include_stack: set = None,
    _variables: dict = None,
    _macros: dict = None,
    logger=None,
    _defined_vars: set = None,
    include_dirs: list = None,
    _root_dir: Path = None,
    _pushd_stack: list = None,
    _vrd_name_counts: dict = None,
    _current_scale: float = 1.0,
    compiler: str = None
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

    processor = QCProcessor(_variables, _macros,
                            logger, include_dirs=include_dirs or [],
                            root_dir=_root_dir,
                            current_scale=_current_scale,
                            compiler=compiler)
    
    processor.defined_vars    = _defined_vars
    processor.pushd_stack     = _pushd_stack
    processor.vrd_name_counts = _vrd_name_counts if _vrd_name_counts is not None else {}

    output_lines  = []
    current_macro = None
    macro_lines   = []

    with resolved.open("r", encoding="utf-8", errors="ignore") as f:
        all_lines = f.readlines()

    i = 0
    new_bonemerge          = set()
    new_lookat_attachments = {}

    while i < len(all_lines):
        line     = all_lines[i]
        line_num = i + 1
        stripped = line.strip()
        parts    = processor._parse_command(stripped)
        command  = parts[0].lower() if parts else ""
        i += 1

        if current_macro is not None:
            body_line = line.rstrip()
            if body_line.endswith("\\\\"):
                macro_lines.append(body_line[:-2].rstrip())
            else:
                macro_lines.append(body_line)
                _macros[current_macro['name']] = {'args': current_macro['args'], 'body': macro_lines}
                current_macro = None
                macro_lines   = []
            continue

        is_skipping = bool(processor.if_stack) and not processor.if_stack[-1][0]

        if processor._handle_conditional(command, parts, line_num, is_skipping, resolved.parent):
            continue
        if is_skipping:
            continue

        line, has_sub_error = processor._substitute_variables(line, line_num)
        if has_sub_error:
            output_lines.append(f"// ERROR Line {line_num}: Undefined variable in line: {all_lines[i - 1].rstrip()}\n")
            output_lines.append(line)
            continue

        stripped = line.strip()
        parts    = processor._parse_command(stripped)
        command  = parts[0].lower() if parts else ""

        if command in ("$nekodriverbone", "$driverbone"):
            if len(parts) < 2:
                msg = f"Line {line_num}: {command} missing driver bone name"
                if logger: logger.error(msg)
                raise QCCompileError(msg)

            driver_bone = parts[1].strip('"')
            block, i = vrd_module._parse_driverbone_block(all_lines, i)

            if not block or not block["pose"] or not block["target_bones"]:
                msg = f"Line {line_num}: {command} '{driver_bone}' block is incomplete (missing pose or target bones)"
                if logger: logger.error(msg)
                raise QCCompileError(msg)

            pose_stem = Path(block["pose"]).stem.lower()
            vrd_name  = re.sub(r'[^\w]', '_', f"{pose_stem}_{driver_bone.lower()}")
            count = processor.vrd_name_counts.get(vrd_name, 0)
            processor.vrd_name_counts[vrd_name] = count + 1
            if count > 0:
                vrd_name = f"{vrd_name}_{count}"

            pose_base = processor.pushd_stack[-1] if processor.pushd_stack else _root_dir
            try:
                vrd_module.generate_vrd(
                    driver_bone, block["pose"], block["triggers"],
                    block["target_bones"], pose_base, _root_dir, vrd_name, processor.current_scale,
                    logger=logger
                )
            except Exception as e:
                msg = f"Line {line_num}: Failed to generate VRD for '{driver_bone}': {e}"
                if logger: logger.error(msg)
                raise QCCompileError(msg)

            for target_bone in block["target_bones"]:
                if target_bone in new_bonemerge:
                    continue
                output_lines.append(f'$bonemerge "{target_bone}"\n')
                new_bonemerge.add(target_bone)
            output_lines.append(f'// VRD Scale: {processor.current_scale}"\n')
            output_lines.append(f'$proceduralbones "vrds/{vrd_name}.vrd"\n')
            continue

        if command == "$driverlookatbone":
            if len(parts) < 2:
                msg = f"Line {line_num}: $driverlookatbone missing bone name"
                if logger: logger.error(msg)
                raise QCCompileError(msg)

            target_bone = parts[1].strip('"')
            block, i = vrd_module._parse_driverlookatbone_block(all_lines, i)

            if not block or not block["pose"] or not block["helper_bones"]:
                msg = f"Line {line_num}: $driverlookatbone '{target_bone}' block is incomplete (missing pose or helper bones)"
                if logger: logger.error(msg)
                raise QCCompileError(msg)

            pose_stem = Path(block["pose"]).stem.lower()
            vrd_name  = re.sub(r'[^\w]', '_', f"lookat_{pose_stem}_{target_bone.lower()}")
            count = processor.vrd_name_counts.get(vrd_name, 0)
            processor.vrd_name_counts[vrd_name] = count + 1
            if count > 0:
                vrd_name = f"{vrd_name}_{count}"

            pose_base = processor.pushd_stack[-1] if processor.pushd_stack else _root_dir
            stripped_target = target_bone.split('.')[-1]
            loc, rot = block["location"], block["rotation"]
            existing = new_lookat_attachments.get(stripped_target, [])
            attachment_name = next((n for l, r, n in existing if l == loc and r == rot), None)
            if attachment_name is None:
                base = f"{stripped_target}_lookattarget"
                attachment_name = base if not existing else f"{base}_{len(existing)}"
                existing.append((loc, rot, attachment_name))
                new_lookat_attachments[stripped_target] = existing
                pos_str = " ".join(f"{v:g}" for v in loc)
                rot_str = " ".join(f"{v:g}" for v in rot)
                output_lines.append(f'$attachment "{attachment_name}" "{target_bone}" {pos_str} rotate {rot_str}\n')

            try:
                vrd_module.generate_lookat_vrd(
                    target_bone, attachment_name, block["frame"], block["aimvector"], block["upvector"],
                    block["helper_bones"], block["pose"], pose_base, _root_dir, vrd_name,
                    processor.current_scale, logger=logger
                )
            except Exception as e:
                msg = f"Line {line_num}: Failed to generate lookat VRD for '{target_bone}': {e}"
                if logger: logger.error(msg)
                raise QCCompileError(msg)

            for helper_bone in block["helper_bones"]:
                if helper_bone not in new_bonemerge:
                    output_lines.append(f'$bonemerge "{helper_bone}"\n')
                    new_bonemerge.add(helper_bone)
            output_lines.append(f'// VRD Scale: {processor.current_scale}\n')
            output_lines.append(f'$proceduralbones "vrds/{vrd_name}.vrd"\n')
            continue

        if command == "$definemacro":
            _parse_definemacro(stripped, parts, line_num, output_lines)
            no_cont = stripped[:-2].strip() if stripped.endswith("\\\\") else stripped
            macro_parts = processor._parse_command(no_cont)
            if len(macro_parts) >= 2:
                current_macro = {'name': macro_parts[1], 'args': macro_parts[2:]}
                macro_lines   = []
            else:
                if logger: logger.warn(f"Line {line_num}: Malformed $definemacro: {stripped}")
                output_lines.append(f"// WARNING Line {line_num}: Malformed $definemacro: {stripped}\n")
            continue

        if command == "$model":
            block_lines = [line]
            if "{" in line:
                inner_lines, i = processor._collect_block(
                    all_lines, i, line.count("{") - line.count("}"), resolved.parent
                )
                block_lines.extend(inner_lines)

            block_content = "".join(block_lines)

            if processor.current_scale != 1.0 and compiler != 'nekomdl':
                scaled_lines = []
                for bl in block_content.splitlines(True):
                    btokens = processor._parse_command(bl.strip())
                    bkw = btokens[0].lower() if btokens else ""
                    # mouth  <int> <flex> <bone> X Y Z
                    # spherenormals <mat> X Y Z
                    # eyeball <n> <bone> X Y Z <mat> ...
                    xyz_index = {"mouth": 4, "spherenormals": 2, "eyeball": 3}.get(bkw)
                    if xyz_index and len(btokens) >= xyz_index + 3:
                        try:
                            x = float(btokens[xyz_index])     * processor.current_scale
                            y = float(btokens[xyz_index + 1]) * processor.current_scale
                            z = float(btokens[xyz_index + 2]) * processor.current_scale
                            btokens[xyz_index]     = f"{x:g}"
                            btokens[xyz_index + 1] = f"{y:g}"
                            btokens[xyz_index + 2] = f"{z:g}"
                            if bkw == "eyeball":
                                try:
                                    btokens[-1] = f"{float(btokens[-1]) * processor.current_scale:g}"
                                except ValueError:
                                    pass
                            bl = " ".join(f'"{t}"' if " " in t else t for t in btokens) + "\n"
                        except ValueError:
                            pass
                    scaled_lines.append(bl)
                block_content = "".join(scaled_lines)

            sub_parts = processor._parse_command(line.strip())

            if len(sub_parts) >= 3 and sub_parts[2].lower().endswith(".dmx"):
                dmx_raw  = sub_parts[2].strip('"')
                dmx_path = processor._resolve_dmx_path(dmx_raw, resolved.parent)
                if dmx_path:
                    res_content, errs, count = flex_controllers.inject_flex_controllers_from_dmx(block_content, dmx_path)
                    for err in errs:
                        output_lines.append(f"// ERROR Line {line_num}: {err}\n")
                    if count > 0 and logger:
                        logger.info(f"Constructed {count} flex controllers from {dmx_path.name}")
                    output_lines.append(res_content)
                else:
                    if logger: logger.warn(f"Line {line_num}: Could not resolve DMX '{dmx_raw}' for $model, flex controllers skipped")
                    output_lines.append(f"// WARNING Line {line_num}: Could not resolve DMX '{dmx_raw}'\n")
                    output_lines.append(block_content)
            else:
                output_lines.append(block_content)
            continue

        if command in ("$defineskeletonhierarchy", "$defineskeletonheirarchy"):
            if len(parts) < 2:
                if logger: logger.warn(f"Line {line_num}: {command} requires a DMX path")
                output_lines.append(f"// WARNING Line {line_num}: {command} requires a DMX path\n")
                continue

            dmx_raw = parts[1].strip('"')
            target_bones = []

            brace_line = stripped
            if "{" not in brace_line and i < len(all_lines):
                brace_line = all_lines[i].strip()
                if "{" in brace_line:
                    i += 1

            if "{" in brace_line:
                after_open = brace_line[brace_line.find("{") + 1:].strip()
                depth = 1 + after_open.count("{") - after_open.count("}")
                pre_close = after_open[:after_open.find("}")] if "}" in after_open else after_open
                for token in processor._parse_command(pre_close):
                    target_bones.append(token.strip('"'))

                if depth > 0:
                    inner_lines, i = processor._collect_block(all_lines, i, depth, resolved.parent)
                    for bl in inner_lines:
                        for token in processor._parse_command(bl.strip()):
                            if token != "}":
                                target_bones.append(token.strip('"'))

            dmx_path = None
            if Path(dmx_raw).suffix.lower() in (".dmx", ".smd"):
                dmx_path = processor._resolve_dmx_path(dmx_raw, resolved.parent)
            else:
                dmx_path = processor._resolve_dmx_path(dmx_raw + ".dmx", resolved.parent)
                if not dmx_path:
                    dmx_path = processor._resolve_dmx_path(dmx_raw + ".smd", resolved.parent)

            if not dmx_path:
                msg = f"Line {line_num}: {command} could not resolve '{dmx_raw}'"
                if logger: logger.error(msg)
                raise QCCompileError(msg)

            try:
                ext = dmx_path.suffix.lower()
                if ext == ".dmx":
                    frames = read_dmx_bone_animation(str(dmx_path))
                elif ext == ".smd":
                    frames = read_smd_bone_animation(str(dmx_path))
                else:
                    continue

                if frames:
                    frame    = frames[0]
                    bone_map = {bt.bone_name: bt for bt in frame}
                    bones_to_write = target_bones if target_bones else list(bone_map.keys())

                    for bone_name in bones_to_write:
                        bt = bone_map.get(bone_name)
                        if bt:
                            parent = f'"{bt.parent_name}"' if bt.parent_name else '""'
                            output_lines.append(f'$hierarchy "{bone_name}" {parent}\n')
                        else:
                            if logger: logger.warn(f"Line {line_num}: bone '{bone_name}' not found in '{dmx_raw}'")
                            output_lines.append(f"// WARNING: bone '{bone_name}' not found\n")
                else:
                    if logger: logger.warn(f"Line {line_num}: no frames found in '{dmx_raw}'")
                    output_lines.append(f"// WARNING: no frames found in '{dmx_raw}'\n")
            except Exception as e:
                msg = f"Line {line_num}: Failed to read '{dmx_raw}': {e}"
                if logger: logger.error(msg)
                raise QCCompileError(msg)

            continue

        if command == "$defineskeleton":
            if len(parts) < 3:
                if logger: logger.warn(f"Line {line_num}: $defineskeleton requires a DMX path and frame index")
                output_lines.append(f"// WARNING Line {line_num}: $defineskeleton requires a DMX path and frame index\n")
                continue

            dmx_raw = parts[1].strip('"')
            try:
                frame_idx = int(parts[2])
            except ValueError:
                if logger: logger.warn(f"Line {line_num}: $defineskeleton frame index must be an integer")
                output_lines.append(f"// WARNING Line {line_num}: $defineskeleton frame index must be an integer\n")
                continue

            target_bones = []

            brace_line = stripped
            if "{" not in brace_line and i < len(all_lines):
                brace_line = all_lines[i].strip()
                if "{" in brace_line:
                    i += 1

            if "{" in brace_line:
                after_open = brace_line[brace_line.find("{") + 1:].strip()
                depth = 1 + after_open.count("{") - after_open.count("}")
                pre_close = after_open[:after_open.find("}")] if "}" in after_open else after_open
                for token in processor._parse_command(pre_close):
                    target_bones.append(token.strip('"'))

                if depth > 0:
                    inner_lines, i = processor._collect_block(all_lines, i, depth, resolved.parent)
                    for bl in inner_lines:
                        for token in processor._parse_command(bl.strip()):
                            if token != "}":
                                target_bones.append(token.strip('"'))

            dmx_path = None
            if Path(dmx_raw).suffix.lower() in (".dmx", ".smd"):
                dmx_path = processor._resolve_dmx_path(dmx_raw, resolved.parent)
            else:
                dmx_path = processor._resolve_dmx_path(dmx_raw + ".dmx", resolved.parent)
                if not dmx_path:
                    dmx_path = processor._resolve_dmx_path(dmx_raw + ".smd", resolved.parent)

            if not dmx_path:
                msg = f"Line {line_num}: $defineskeleton could not resolve '{dmx_raw}'"
                if logger: logger.error(msg)
                raise QCCompileError(msg)

            try:
                ext = dmx_path.suffix.lower()
                if ext == ".dmx":
                    frames = read_dmx_bone_animation(str(dmx_path))
                    frames = frames_quat_to_euler(frames)
                elif ext == ".smd":
                    frames = read_smd_bone_animation(str(dmx_path))
                else:
                    continue

                frames = frames_rotation_to_degrees(frames)

                if processor.current_scale != 1.0:
                    frames = apply_world_scale(frames, processor.current_scale)

                if frame_idx < len(frames):
                    frame    = frames[frame_idx]
                    bone_map = {bt.bone_name: bt for bt in frame}
                    bones_to_write = target_bones if target_bones else list(bone_map.keys())

                    for bone_name in bones_to_write:
                        bt = bone_map.get(bone_name)
                        if bt:
                            x, y, z = bt.location
                            rx, ry, rz = bt.rotation
                            parent = f'"{bt.parent_name}"' if bt.parent_name else '""'
                            output_lines.append(
                                f'$definebone "{bone_name}" {parent} '
                                f'{x:.6f} {y:.6f} {z:.6f} '
                                f'{rx:.6f} {ry:.6f} {rz:.6f} '
                                f'0 0 0 0 0 0\n'
                            )
                        else:
                            if logger: logger.warn(f"Line {line_num}: bone '{bone_name}' not found in '{dmx_raw}'")
                            output_lines.append(f"// WARNING: bone '{bone_name}' not found\n")
                else:
                    if logger: logger.warn(f"Line {line_num}: frame index {frame_idx} out of range in '{dmx_raw}'")
                    output_lines.append(f"// WARNING: frame index {frame_idx} out of range in '{dmx_raw}'\n")
            except QCCompileError:
                raise
            except Exception as e:
                msg = f"Line {line_num}: Failed to read '{dmx_raw}': {e}"
                if logger: logger.error(msg)
                raise QCCompileError(msg)

            continue

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
                if logger: logger.warn(f"Line {line_num}: $rendermeshlist missing opening brace")
                output_lines.append(f"// WARNING Line {line_num}: $rendermeshlist missing opening brace\n")
                continue

            after_open = brace_line[brace_line.find("{") + 1:].strip()
            depth = 1 + after_open.count("{") - after_open.count("}")

            def parse_rendermesh_tokens(tokens):
                if not tokens:
                    return
                keyword = tokens[0].lower()
                if keyword == "replace" and len(tokens) >= 2:
                    pattern     = tokens[1]
                    replacement = tokens[2] if len(tokens) >= 3 else ""
                    replace_rules.append((pattern, replacement))
                elif keyword == "ignore_missing" and len(tokens) >= 2:
                    nonlocal ignore_missing
                    ignore_missing = tokens[1].strip() not in ("0", "false")
                else:
                    for token in tokens:
                        if token != "}":
                            mesh_names.append(token.strip('"'))

            if after_open:
                pre_close = after_open[:after_open.find("}")] if "}" in after_open else after_open
                parse_rendermesh_tokens(processor._parse_command(pre_close))

            if depth > 0:
                inner_lines, i = processor._collect_block(all_lines, i, depth, resolved.parent)
                for bl in inner_lines:
                    toks = processor._parse_command(bl.strip())
                    if toks and toks[0].lower() in ("suffix", "prefix") and len(toks) >= 2:
                        variants.append((toks[0].lower(), toks[1].strip('"')))
                    else:
                        parse_rendermesh_tokens(toks)

            for mesh_name in mesh_names:
                body_name = mesh_name
                for pattern, replacement in replace_rules:
                    body_name = re.sub(pattern, replacement, body_name)

                raw_path = mesh_name
                if Path(raw_path).suffix.lower() in (".dmx", ".smd"):
                    file_str = raw_path
                    dmx_path = processor._resolve_dmx_path(raw_path, resolved.parent)
                else:
                    dmx_path = processor._resolve_dmx_path(raw_path + ".dmx", resolved.parent)
                    if dmx_path:
                        file_str = raw_path + ".dmx"
                    else:
                        dmx_path = processor._resolve_dmx_path(raw_path + ".smd", resolved.parent)
                        file_str = raw_path + ".smd" if dmx_path else raw_path + ".dmx"

                if not dmx_path:
                    if ignore_missing:
                        if logger: logger.warn(f"Line {line_num}: $rendermeshlist '{mesh_name}' not found, skipping")
                        output_lines.append(f"// WARNING: '{mesh_name}' not found\n")
                        output_lines.append(f'// $body "{body_name}" "{file_str}"\n')
                        continue
                    else:
                        if logger: logger.warn(f"Line {line_num}: $rendermeshlist could not resolve '{mesh_name}'")
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

                    if ignore_missing and not processor._resolve_dmx_path(var_file, resolved.parent):
                        if logger: logger.warn(f"Line {line_num}: $rendermeshlist variant '{var_file}' not found, skipping")
                        output_lines.append(f"// WARNING: variant '{var_file}' not found\n")
                        output_lines.append(f'// $body "{var_body}" "{var_file}"\n')
                    else:
                        output_lines.append(f'$body "{var_body}" "{var_file}"\n')

            continue

        result = processor.process_line(line, line_num, resolved.parent, _include_stack)
        if result is not None:
            output_lines.append(result)

    output_lines.extend(processor.output_lines)
    _include_stack.remove(resolved)

    return _format_qc_output("".join(output_lines))


def _parse_definemacro(stripped: str, parts: list, line_num: int, output_lines: list):
    pass


#
# Read helpers
#

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

    includes = qc_read_includes(qc_path)
    all_paths = [qc_path, *includes]

    rename_map = {}
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