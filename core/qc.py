import shlex, re
from simpleeval import simple_eval
from pathlib import Path
from typing import Optional
from utils import Logger
from core import vrd as vrd_module
from core import flex_controllers
from core.bone_animations import read_dmx_bone_animation, frames_quat_to_euler, frames_rotation_to_degrees, read_smd_bone_animation, apply_world_scale


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


_CMP_RE = re.compile(r'^\s*([^\s"]+|"[^"]+")\s*(==|!=|>=|<=|>|<)\s*([^\s"]+|"[^"]+")\s*$')

def _evaluate_condition(expression: str, variables: dict, is_ifdef: bool) -> bool:
    expression = expression.strip()
    if is_ifdef:
        return expression in variables

    for or_part in expression.split('||'):
        if all(_eval_and_term(t.strip(), variables) for t in or_part.split('&&') if t.strip()):
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


#
# Processor
#

ORANGE = "\033[38;5;208m"
RED    = "\033[91m"
RESET  = "\033[0m"


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
        current_scale: float = 1.0
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

    def _log(self, level: str, color: str, msg: str):
        if self.logger:
            getattr(self.logger, level)(f"{color}{msg}{RESET}")

    def _warn(self, msg: str): self._log("error",  ORANGE, msg)
    def _err(self,  msg: str): self._log("error",  RED,    msg)
    def _info(self, msg: str): self._log("info",   ORANGE, msg)

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
                self._err(f"ERROR Line {line_num}: Undefined variable '${name}$'")
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
            self._warn(f"WARNING Line {line_num}: $pushd without a path")
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
            self._warn(f"WARNING Line {line_num}: $popd without matching $pushd")
        else:
            self.pushd_stack.pop()
        return original_line

    def _eval_fileexist(self, parts: list, line_num: int, base_dir: Path) -> bool:
        if len(parts) < 2:
            self._warn(f"WARNING Line {line_num}: $iffileexist without a path")
            return False

        raw, err = self._substitute_variables(parts[1], line_num)
        if err:
            self._warn(f"WARNING Line {line_num}: Undefined variable in $iffileexist path")

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
            return f"// WARNING Line {line_num}: Malformed $definevariable: {line}\n"
        try:
            name = parts[1]
            raw, err = self._substitute_variables(" ".join(parts[2:]), line_num)
            if err:
                return f"// ERROR Line {line_num}: Undefined variable in expression for $definevariable: {line.rstrip()}\n"

            if name in self.macro_args_override:
                self._warn(f"WARNING Line {line_num}: Cannot define variable '{name}' - shadowed by macro argument")
                return f"// WARNING Line {line_num}: Variable '{name}' shadowed by macro argument, ignoring\n"
            if name in self.json_vars:
                return f"// Overridden by JSON config: {line}\n"
            if name in self.defined_vars:
                self._warn(f"WARNING Line {line_num}: Variable '{name}' already defined, ignoring redefinition")
                return f"// WARNING Line {line_num}: Variable '{name}' already defined, ignoring\n"

            self.variables[name] = self._eval_value(raw)
            self.defined_vars.add(name)
            return None
        except Exception as e:
            return f"// WARNING Line {line_num}: Failed to parse $definevariable: {line} ({e})\n"

    def _handle_redefine_variable(self, parts: list, line_num: int, line: str) -> Optional[str]:
        if len(parts) < 3:
            return f"// WARNING Line {line_num}: Malformed $redefinevariable: {line}\n"
        try:
            name = parts[1]
            raw, err = self._substitute_variables(" ".join(parts[2:]), line_num)
            if err:
                return f"// ERROR Line {line_num}: Undefined variable in expression for $redefinevariable: {line.rstrip()}\n"

            if name in self.macro_args_override:
                self._err(f"ERROR Line {line_num}: Cannot redefine macro argument '{name}'")
                return f"// ERROR Line {line_num}: Cannot redefine macro argument '{name}'\n"
            if name not in self.defined_vars:
                self._err(f"ERROR Line {line_num}: Cannot redefine undefined variable '{name}'")
                return f"// ERROR Line {line_num}: Cannot redefine undefined variable '{name}'\n"

            self.variables[name] = self._eval_value(raw)
            self._info(f"INFO Line {line_num}: Variable '{name}' redefined to '{self.variables[name]}'")
            return None
        except Exception as e:
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

    def _handle_include(self, original_line: str, parts: list, line_num: int,
                        base_dir: Path, include_stack: set, processed_line: str) -> str:
        if len(parts) < 2:
            return f"// WARNING Line {line_num}: $include without path: {original_line.rstrip()}\n"

        include_file, err = self._substitute_variables(parts[1], line_num)
        if err:
            self._warn(f"WARNING Line {line_num}: Undefined variable in $include path, using literal")
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
                _current_scale=self.current_scale
            )
            return header + nested + "\n"
        except Exception as e:
            msg = f"ERROR Line {line_num}: Failed to process include '{include_file}': {e}"
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
                self._warn(f"WARNING Line {line_num}: Macro '{macro_name}' expects argument '{arg_name}' but none provided")

        processor = QCProcessor(
            self.variables.copy(), self.macros, self.logger,
            macro_args_override=arg_mapping,
            include_dirs=self.include_dirs,
            root_dir=self.root_dir,
            current_scale=self.current_scale
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
        
        if depth == 0:
            formatted_line = stripped
        else:
            formatted_line = line.rstrip()
            
        depth += line.count('{') - line.count('}')
        if depth < 0:
            depth = 0
            
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
    _current_scale: float = 1.0
) -> str:

    _include_stack = _include_stack or set()
    _variables     = _variables     or {}
    _macros        = _macros        or {}
    _defined_vars  = _defined_vars  if _defined_vars is not None else set(_variables)
    _pushd_stack   = list(_pushd_stack) if _pushd_stack is not None else []

    try:
        resolved = qc_path.resolve(strict=True)
    except FileNotFoundError:
        return f"// ERROR: $include or qc file not found: {qc_path.as_posix()}\n"
    except Exception as e:
        return f"// ERROR: Failed to resolve path '{qc_path.as_posix()}': {e}\n"

    if resolved in _include_stack:
        return f"// ERROR: Circular $include detected! '{resolved.as_posix()}' is already in the include stack.\n"

    _include_stack.add(resolved)
    _root_dir = _root_dir or resolved.parent

    processor = QCProcessor(_variables, _macros, logger, include_dirs=include_dirs or [], root_dir=_root_dir, current_scale=_current_scale)
    processor.defined_vars    = _defined_vars
    processor.pushd_stack     = _pushd_stack
    processor.vrd_name_counts = _vrd_name_counts if _vrd_name_counts is not None else {}

    output_lines  = []
    current_macro = None
    macro_lines   = []

    with resolved.open("r", encoding="utf-8", errors="ignore") as f:
        all_lines = f.readlines()

    i = 0
    new_bonemerge = set()

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
                output_lines.append(f"// WARNING Line {line_num}: {command} missing driver bone name\n")
                continue
            driver_bone = parts[1].strip('"')
            block, i = vrd_module._parse_driverbone_block(all_lines, i)
            if block and block["pose"] and block["target_bones"]:
                pose_stem = Path(block["pose"]).stem.lower()
                vrd_name = re.sub(r'[^\w]', '_', f"{pose_stem}_{driver_bone.lower()}")
                count = processor.vrd_name_counts.get(vrd_name, 0)
                processor.vrd_name_counts[vrd_name] = count + 1
                if count > 0:
                    vrd_name = f"{vrd_name}_{count}"
                try:
                    pose_base = processor.pushd_stack[-1] if processor.pushd_stack else _root_dir
                    vrd_module.generate_vrd(
                        driver_bone, block["pose"], block["triggers"],
                        block["target_bones"], pose_base, _root_dir, vrd_name, processor.current_scale,
                        logger=logger
                    )
                    for target_bone in block["target_bones"]:
                        if target_bone in new_bonemerge:
                            continue
                        output_lines.append(f'$bonemerge "{target_bone}"\n')
                        new_bonemerge.add(target_bone)
                    output_lines.append(f'// VRD Scale: {processor.current_scale}"\n')
                    output_lines.append(f'$proceduralbones "vrds/{vrd_name}.vrd"\n')
                except Exception as e:
                    output_lines.append(f"// ERROR Line {line_num}: Failed to generate VRD for '{driver_bone}': {e}\n")
            else:
                output_lines.append(f"// WARNING Line {line_num}: {command} block incomplete, skipped\n")
            continue

        if command == "$definemacro":
            _parse_definemacro(stripped, parts, line_num, output_lines)
            no_cont = stripped[:-2].strip() if stripped.endswith("\\\\") else stripped
            macro_parts = processor._parse_command(no_cont)
            if len(macro_parts) >= 2:
                current_macro = {'name': macro_parts[1], 'args': macro_parts[2:]}
                macro_lines   = []
            else:
                output_lines.append(f"// WARNING Line {line_num}: Malformed $definemacro: {stripped}\n")
            continue

        if command == "$model":
            block_lines = [line]
            if "{" in line:
                depth = line.count("{") - line.count("}")
                while depth > 0 and i < len(all_lines):
                    next_line, _ = processor._substitute_variables(all_lines[i], line_num + len(block_lines))
                    block_lines.append(next_line)
                    depth += next_line.count("{") - next_line.count("}")
                    i += 1

            block_content = "".join(block_lines)
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
                    output_lines.append(f"// WARNING Line {line_num}: Could not resolve DMX '{dmx_raw}'\n")
                    output_lines.append(block_content)
            else:
                output_lines.append(block_content)
            continue

        if command == "$defineskeleton":
            if len(parts) < 3:
                output_lines.append(f"// WARNING Line {line_num}: $defineskeleton requires a DMX path and frame index\n")
                continue

            dmx_raw = parts[1].strip('"')
            try:
                frame_idx = int(parts[2])
            except ValueError:
                output_lines.append(f"// WARNING Line {line_num}: $defineskeleton frame index must be an integer\n")
                continue

            target_bones = []
            
            rest_of_line = stripped[len(parts[0]):].strip()
            rest_of_line = rest_of_line[len(parts[1]):].strip()
            rest_of_line = rest_of_line[len(parts[2]):].strip()

            if "{" in rest_of_line:
                content = rest_of_line[rest_of_line.find("{")+1:].strip()
                
                while i < len(all_lines):
                    if "}" in content:
                        closing_idx = content.find("}")
                        final_tokens = content[:closing_idx].strip()
                        if final_tokens:
                            for token in processor._parse_command(final_tokens):
                                target_bones.append(token.strip('"'))
                        break
                    
                    if content:
                        for token in processor._parse_command(content):
                            target_bones.append(token.strip('"'))
                    
                    content = all_lines[i].strip()
                    i += 1

            dmx_path = processor._resolve_dmx_path(dmx_raw, resolved.parent)
            if not dmx_path:
                output_lines.append(f"// WARNING Line {line_num}: could not resolve DMX '{dmx_raw}'\n")
                continue

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
                    frame = frames[frame_idx]
                    bone_map = {bt.bone_name: bt for bt in frame}

                    if not target_bones:
                        bones_to_write = list(bone_map.keys())
                    else:
                        bones_to_write = target_bones

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
                            output_lines.append(f"// WARNING: bone '{bone_name}' not found\n")
            except Exception as e:
                output_lines.append(f"// ERROR: {e}\n")
            
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