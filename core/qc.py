import shlex
import re
from simpleeval import simple_eval
from pathlib import Path
from typing import Optional
from utils import PrefixedLogger

def _evaluate_condition(expression: str, variables: dict, is_ifdef: bool) -> bool:
    """Safely evaluates a QC conditional expression."""
    expression = expression.strip()

    if is_ifdef:
        return expression in variables

    def get_value(val_str: str, is_literal_ok: bool = False):
        val_str = val_str.strip()
        unquoted = val_str.strip('"')

        if unquoted in variables:
            return variables[unquoted]

        # It's not a variable. Is it a quoted string literal?
        if val_str.startswith('"') and val_str.endswith('"'):
            return unquoted

        # Is it a number literal?
        try:
            float(val_str)
            return val_str
        except (ValueError, TypeError):
            pass

        if is_literal_ok:
            return val_str

        # It is an unquoted, non-numeric literal. Treat as an undefined variable.
        return None

    def compare(left_str, op, right_str):
        left = get_value(left_str)
        right = get_value(right_str, is_literal_ok=True)
        
        try:
            left_num, right_num = float(left), float(right)
            if op == '==': return left_num == right_num
            if op == '!=': return left_num != right_num
            if op == '>': return left_num > right_num
            if op == '<': return left_num < right_num
            if op == '>=': return left_num >= right_num
            if op == '<=': return left_num <= right_num
        except (ValueError, TypeError):
            if op == '==': return str(left) == str(right)
            if op == '!=': return str(left) != str(right)
        
        return False

    for or_part in expression.split('||'):
        is_and_term_true = True
        for and_part in or_part.split('&&'):
            and_part = and_part.strip()
            
            match = re.match(r'^\s*([^\s"]+|"[^"]+")\s*(==|!=|>=|<=|>|<)\s*([^\s"]+|"[^"]+")\s*$', and_part)
            if match:
                left, op, right = match.groups()
                if not compare(left, op, right):
                    is_and_term_true = False
                    break
            elif and_part:
                val = get_value(and_part)
                if val is None or str(val).strip() in ("0", "", "false"):
                    is_and_term_true = False
                    break
        
        if is_and_term_true:
            return True

    return False

class QCReturnException(Exception):
    """Raised when a $return is encountered."""
    pass

class QCProcessor:
    COMMENT_OUT_COMMANDS = {"$msg", "$echo"}
    
    ORANGE = "\033[38;5;208m"
    RED = "\033[91m"
    RESET = '\033[0m'
    
    def __init__(self, variables: dict = None, macros: dict = None, logger=None, macro_args_override: dict = None, include_dirs: list = None, root_dir: Path = None):
        self.variables = variables if variables is not None else {}
        self.macros = macros if macros is not None else {}
        self.logger : PrefixedLogger = logger
        self.if_stack = []
        self.output_lines = []
        self.json_vars = set(self.variables.keys())
        self.defined_vars = set(self.variables.keys())
        self.macro_args_override = macro_args_override if macro_args_override is not None else {}
        self.include_dirs = include_dirs if include_dirs is not None else []
        self.root_dir = root_dir
        
    def process_line(self, line: str, line_num: int, base_dir: Path, include_stack: set) -> Optional[str]:
        stripped_line = line.strip()
        command_parts = self._parse_command(stripped_line)
        command = command_parts[0].lower() if command_parts else ""
        
        is_skipping = self.if_stack and not self.if_stack[-1][0]
        
        if self._handle_conditional(command, command_parts, line_num, is_skipping):
            return None
        
        if is_skipping:
            return None
        
        if command == "$return":
            raise QCReturnException()
        
        if command == "$definevariable":
            return self._handle_define_variable(command_parts, line_num, stripped_line)
        
        if command == "$redefinevariable":
            return self._handle_redefine_variable(command_parts, line_num, stripped_line)
        
        processed_line, has_error = self._substitute_variables(line, line_num)
        if has_error:
            return f"// ERROR Line {line_num}: Undefined variable in line: {line.rstrip()}\n"
        
        active_command_parts = self._parse_command(processed_line.strip())
        active_command = active_command_parts[0].lower() if active_command_parts else ""
        
        if active_command == "$include":
            return self._handle_include(line, active_command_parts, line_num, base_dir, include_stack, processed_line.strip())
        
        elif active_command.startswith('$') and active_command[1:] in self.macros:
            return self._handle_macro_expansion(active_command, active_command_parts, base_dir, include_stack, line_num)
        
        elif active_command in self.COMMENT_OUT_COMMANDS:
            if self.logger:
                message = processed_line.strip()
                colored_message = f"{self.ORANGE}{message}{self.RESET}"
                self.logger.info(colored_message)
            return "// " + processed_line
        
        return processed_line
    
    def _parse_command(self, line: str) -> list:
        try:
            return shlex.split(line)
        except ValueError:
            return []
    
    def _substitute_variables(self, line: str, line_num: int = None) -> tuple[str, bool]:
        result = line
        has_error = False
        
        var_pattern = re.compile(r'\$(\w+)\$')
        
        def replace_var(match):
            nonlocal has_error
            var_name = match.group(1)
            
            if var_name in self.macro_args_override:
                return str(self.macro_args_override[var_name])
            elif var_name in self.variables:
                return str(self.variables[var_name])
            else:
                has_error = True
                if self.logger and line_num:
                    error_msg = f"{self.RED}ERROR Line {line_num}: Undefined variable '${var_name}$'{self.RESET}"
                    self.logger.error(error_msg)
                return match.group(0)
        
        result = var_pattern.sub(replace_var, result)
        return result, has_error
    
    def _handle_conditional(self, command: str, command_parts: list, line_num: int, is_skipping: bool) -> bool:
        if command in ("$if", "$ifdef"):
            parent_is_skipping = is_skipping
            if parent_is_skipping:
                self.if_stack.append((False, False))
            else:
                condition_str = " ".join(command_parts[1:])
                condition_str, has_error = self._substitute_variables(condition_str, line_num)
                if has_error:
                    self.if_stack.append((False, False))
                else:
                    effective_vars = {**self.variables, **self.macro_args_override}
                    result = _evaluate_condition(condition_str, effective_vars, is_ifdef=(command == "$ifdef"))
                    self.if_stack.append((result, result))
            return True
        
        elif command == "$elif":
            if not self.if_stack:
                self.output_lines.append(f"// ERROR Line {line_num}: $elif without $if\n")
                return True
            
            _, branch_taken = self.if_stack[-1]
            parent_is_skipping = len(self.if_stack) > 1 and not self.if_stack[-2][0]
            
            if parent_is_skipping or branch_taken:
                self.if_stack[-1] = (False, True)
            else:
                condition_str = " ".join(command_parts[1:])
                condition_str, has_error = self._substitute_variables(condition_str, line_num)
                if has_error:
                    self.if_stack[-1] = (False, False)
                else:
                    effective_vars = {**self.variables, **self.macro_args_override}
                    result = _evaluate_condition(condition_str, effective_vars, is_ifdef=False)
                    self.if_stack[-1] = (result, result)
            return True
        
        elif command == "$else":
            if not self.if_stack:
                self.output_lines.append(f"// ERROR Line {line_num}: $else without $if\n")
                return True
            
            _, branch_taken = self.if_stack[-1]
            parent_is_skipping = len(self.if_stack) > 1 and not self.if_stack[-2][0]
            
            if parent_is_skipping or branch_taken:
                self.if_stack[-1] = (False, True)
            else:
                self.if_stack[-1] = (True, True)
            return True
        
        elif command == "$endif":
            if not self.if_stack:
                self.output_lines.append(f"// ERROR Line {line_num}: $endif without $if\n")
                return True
            self.if_stack.pop()
            return True
        
        return False
    
    def _handle_define_variable(self, command_parts: list, line_num: int, line: str) -> Optional[str]:
        try:
            if len(command_parts) >= 3:
                var_name = command_parts[1]
                value_str = " ".join(command_parts[2:])

                value_str, has_error = self._substitute_variables(value_str, line_num)
                if has_error:
                    return f"// ERROR Line {line_num}: Undefined variable in expression for $definevariable: {line.rstrip()}\n"
                
                try:
                    # Use simple_eval to safely evaluate the expression.
                    var_value = str(simple_eval(value_str))
                except Exception:
                    # If simple_eval fails, use the string value as is.
                    var_value = value_str

                if var_name in self.macro_args_override:
                    if self.logger:
                        warning_msg = f"{self.ORANGE}WARNING Line {line_num}: Cannot define variable '{var_name}' - shadowed by macro argument{self.RESET}"
                        self.logger.error(warning_msg)
                    return f"// WARNING Line {line_num}: Variable '{var_name}' shadowed by macro argument, ignoring\n"

                if var_name in self.json_vars:
                    return f"// Overridden by JSON config: {line}\n"

                if var_name in self.defined_vars:
                    if self.logger:
                        warning_msg = f"{self.ORANGE}WARNING Line {line_num}: Variable '{var_name}' already defined, ignoring redefinition{self.RESET}"
                        self.logger.error(warning_msg)
                    return f"// WARNING Line {line_num}: Variable '{var_name}' already defined, ignoring\n"
                else:
                    self.variables[var_name] = var_value
                    self.defined_vars.add(var_name)
                    return None
            else:
                return f"// WARNING Line {line_num}: Malformed $definevariable: {line}\n"
        except Exception as e:
            return f"// WARNING Line {line_num}: Failed to parse $definevariable: {line} ({e})\n"
    
    def _handle_redefine_variable(self, command_parts: list, line_num: int, line: str) -> Optional[str]:
        try:
            if len(command_parts) >= 3:
                var_name = command_parts[1]
                value_str = " ".join(command_parts[2:])

                value_str, has_error = self._substitute_variables(value_str, line_num)
                if has_error:
                    return f"// ERROR Line {line_num}: Undefined variable in expression for $redefinevariable: {line.rstrip()}\n"
                
                try:
                    # Use simple_eval to safely evaluate the expression.
                    var_value = str(simple_eval(value_str))
                except Exception:
                    # If simple_eval fails, use the string value as is.
                    var_value = value_str

                if var_name in self.macro_args_override:
                    if self.logger:
                        error_msg = f"{self.RED}ERROR Line {line_num}: Cannot redefine macro argument '{var_name}'{self.RESET}"
                        self.logger.error(error_msg)
                    return f"// ERROR Line {line_num}: Cannot redefine macro argument '{var_name}'\n"
                elif var_name not in self.defined_vars:
                    if self.logger:
                        error_msg = f"{self.RED}ERROR Line {line_num}: Cannot redefine undefined variable '{var_name}'{self.RESET}"
                        self.logger.error(error_msg)
                    return f"// ERROR Line {line_num}: Cannot redefine undefined variable '{var_name}'\n"
                else:
                    self.variables[var_name] = var_value
                    if self.logger:
                        info_msg = f"{self.ORANGE}INFO Line {line_num}: Variable '{var_name}' redefined to '{var_value}'{self.RESET}"
                        self.logger.info(info_msg)
                    return None
            else:
                return f"// WARNING Line {line_num}: Malformed $redefinevariable: {line}\n"
        except Exception as e:
            return f"// WARNING Line {line_num}: Failed to parse $redefinevariable: {line} ({e})\n"
    
    def _handle_include(self, original_line: str, command_parts: list, line_num: int, 
                     base_dir: Path, include_stack: set, processed_line: str) -> str:
        if len(command_parts) < 2:
            return f"// WARNING Line {line_num}: $include without path: {original_line.rstrip()}\n"
        
        include_file_raw = command_parts[1]
        include_file, has_error = self._substitute_variables(include_file_raw, line_num)
        if has_error:
            if self.logger:
                warning_msg = f"{self.ORANGE}WARNING Line {line_num}: Undefined variable in $include path, treating $...$ as literal{self.RESET}"
                self.logger.warn(warning_msg)
            include_file = include_file_raw
        
        resolve_base = self.root_dir if self.root_dir else base_dir
        target_path = (resolve_base / include_file).resolve()
        original_path_missing = False
        
        if not target_path.exists():
            original_path_missing = True
            found = False
            include_filename = Path(include_file).name
            for include_dir in self.include_dirs:
                include_dir_path = Path(include_dir)
                if not include_dir_path.is_absolute():
                    include_dir_path = (resolve_base / include_dir_path).resolve()
                
                candidate_path = (include_dir_path / include_filename).resolve()
                if candidate_path.exists():
                    target_path = candidate_path
                    found = True
                    if self.logger:
                        self.logger.info(f"Found include via includedirs: {candidate_path}")
                    break
            
            if not found:
                error_msg = f"Include file not found at line {line_num}: {include_file}"
                if self.logger: self.logger.error(error_msg)

                error_comment = f"// ERROR Line {line_num}: Include file not found: {include_file}\n"
                error_comment += f"// Original line: {original_line.rstrip()}\n"
                error_comment += f"// Searched in base directory and includedirs, file does not exist\n"

                raise FileNotFoundError(error_msg)
        
        if target_path in include_stack:
            return f"// WARNING Line {line_num}: Circular include detected: {include_file}\n"
        
        comment_header = "\n"
        if original_path_missing:
            comment_header = f"\n// NOTE: Original path '{include_file}' not found, using includedirs: {target_path}\n"

        try:
            nested_content = flatten_qc(
                target_path,
                _include_stack=include_stack.copy(),
                _variables=self.variables,
                _macros=self.macros,
                logger=self.logger,
                _defined_vars=self.defined_vars,
                include_dirs=self.include_dirs,
                _root_dir=self.root_dir
            )
            return comment_header + nested_content
        except Exception as e:
            error_msg = f"ERROR Line {line_num}: Failed to process include '{include_file}': {e}"
            if self.logger:
                self.logger.error(error_msg)
            return f"// {error_msg}\n"
    
    def _handle_macro_expansion(self, active_command: str, command_parts: list, base_dir: Path, include_stack: set, line_num: int) -> str:
        macro_name = active_command[1:]
        macro_def = self.macros[macro_name]
        macro_args_provided = command_parts[1:] if len(command_parts) > 1 else []
        
        macro_arg_mapping = {}
        for i, arg_name in enumerate(macro_def['args']):
            if i < len(macro_args_provided):
                macro_arg_mapping[arg_name] = macro_args_provided[i]
            else:
                if self.logger:
                    warning_msg = f"{self.ORANGE}WARNING Line {line_num}: Macro '{macro_name}' expects argument '{arg_name}' but none provided{self.RESET}"
                    self.logger.error(warning_msg)
        
        macro_content = '\n'.join(macro_def['body']) + '\n'
        
        processor = QCProcessor(
            self.variables.copy(), 
            self.macros, 
            self.logger,
            macro_args_override=macro_arg_mapping,
            include_dirs=self.include_dirs,
            root_dir=self.root_dir
        )
        processor.defined_vars = self.defined_vars.copy()
        
        return processor.process_content(macro_content, base_dir, include_stack.copy())
    
    def process_content(self, content: str, base_dir: Path, include_stack: set) -> str:
        self.output_lines = []
        self.if_stack = []
        
        for line_num, line in enumerate(content.splitlines(True), 1):
            result = self.process_line(line, line_num, base_dir, include_stack)
            if result is not None:
                self.output_lines.append(result)
        
        return "".join(self.output_lines)


def flatten_qc(qc_path: Path, _include_stack: set = None, _variables: dict = None, _macros: dict = None, logger=None, _defined_vars: set = None, include_dirs: list = None, _root_dir: Path = None) -> str:
    if _include_stack is None:
        _include_stack = set()
    if _variables is None:
        _variables = {}
    if _macros is None:
        _macros = {}
    if _defined_vars is None:
        _defined_vars = set(_variables.keys())

    header = ""
    #if _variables and not _include_stack: # Only add header for the root file
    #    for key, value in _variables.items():
    #        if isinstance(value, str):
    #            header += f'$definevariable "{key}" "{value}"\n'
    #        else:
    #            header += f'$definevariable "{key}" {value}\n'
    #    if header:
    #        header += '\n'

    try:
        resolved_path = qc_path.resolve(strict=True)
    except FileNotFoundError:
        return f"// ERROR: $include or qc file not found: {qc_path.as_posix()}\n"
    except Exception as e:
        return f"// ERROR: Failed to resolve path '{qc_path.as_posix()}': {e}\n"
    
    if resolved_path in _include_stack:
        return f"// ERROR: Circular $include detected! '{resolved_path.as_posix()}' is already in the include stack.\n"
    
    _include_stack.add(resolved_path)
    
    if _root_dir is None:
        _root_dir = resolved_path.parent
    
    processor = QCProcessor(_variables, _macros, logger, include_dirs=include_dirs if include_dirs is not None else [], root_dir=_root_dir)
    processor.defined_vars = _defined_vars
    output_lines = []
    current_macro = None
    macro_lines = []
    
    with resolved_path.open("r", encoding="utf-8", errors="ignore") as f:
        for line_num, line in enumerate(f, 1):
            stripped_line = line.strip()
            command_parts = processor._parse_command(stripped_line)
            command = command_parts[0].lower() if command_parts else ""
            
            if current_macro is not None:
                if stripped_line.endswith("\\\\"):
                    macro_lines.append(line.rstrip()[:-2].rstrip())
                else:
                    macro_lines.append(line.rstrip())
                    _macros[current_macro['name']] = {
                        'args': current_macro['args'],
                        'body': macro_lines
                    }
                    current_macro = None
                    macro_lines = []
                continue
            
            is_skipping = processor.if_stack and not processor.if_stack[-1][0]
            
            if processor._handle_conditional(command, command_parts, line_num, is_skipping):
                continue
            
            if is_skipping:
                continue
            
            if command == "$definemacro":
                try:
                    line_without_continuation = stripped_line
                    if line_without_continuation.endswith("\\\\"):
                        line_without_continuation = line_without_continuation[:-2].strip()
                    
                    macro_command_parts = processor._parse_command(line_without_continuation)
                    
                    if len(macro_command_parts) >= 2:
                        macro_name = macro_command_parts[1]
                        macro_args = macro_command_parts[2:] if len(macro_command_parts) > 2 else []
                        current_macro = {'name': macro_name, 'args': macro_args}
                        macro_lines = []
                    else:
                        output_lines.append(f"// WARNING Line {line_num}: Malformed $definemacro: {stripped_line}\n")
                except Exception as e:
                    output_lines.append(f"// WARNING Line {line_num}: Failed to parse $definemacro: {stripped_line} ({e})\n")
                continue
            
            result = processor.process_line(line, line_num, resolved_path.parent, _include_stack)
            if result is not None:
                output_lines.append(result)
    
    output_lines.extend(processor.output_lines)
    _include_stack.remove(resolved_path)
    
    final_output = header + "".join(output_lines)
    final_output = re.sub(r'\n{2,}', '\n\n', final_output)
    return final_output

def qc_read_includes(qc_path: Path) -> list[Path]:
    if not qc_path.exists():
        return []

    visited = set()
    includes = []

    def scan(path: Path):
        if path in visited or not path.exists():
            return
        visited.add(path)

        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line.lower().startswith("$include"):
                    continue

                raw = line.split(None, 1)[1].strip().strip('"')
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

    def parse_renamematerial(path: Path) -> dict[str, str]:
        mapping = {}
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line.lower().startswith("$renamematerial"):
                    continue
                raw = line[len("$renamematerial"):].strip()
                args, buf, in_quote = [], [], False
                for ch in raw:
                    if ch == '"':
                        if in_quote and buf:
                            args.append("".join(buf).strip().replace("\\", "/"))
                            buf.clear()
                        in_quote = not in_quote
                    elif ch.isspace() and not in_quote:
                        if buf:
                            args.append("".join(buf).strip().replace("\\", "/"))
                            buf.clear()
                    else:
                        buf.append(ch)
                if buf:
                    args.append("".join(buf).strip().replace("\\", "/"))
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
                tokens, buf, in_quote = [], [], False
                for ch in raw:
                    if ch == '"':
                        if in_quote and buf:
                            tokens.append("".join(buf).strip().replace("\\", "/"))
                            buf.clear()
                        in_quote = not in_quote
                    elif ch.isspace() and not in_quote:
                        if buf:
                            tokens.append("".join(buf).strip().replace("\\", "/"))
                            buf.clear()
                    else:
                        buf.append(ch)
                if buf:
                    tokens.append("".join(buf).strip().replace("\\", "/"))
                found.extend(tokens)
        return found

    def parse_texturegroup(path: Path) -> list[str]:
        mats = []
        content = path.read_text(encoding="utf-8", errors="ignore")
        lines = content.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip().lower()
            if line.startswith("$texturegroup") and "skinfamilies" in line:
                while i < len(lines) and "{" not in lines[i]:
                    i += 1
                i += 1
                while i < len(lines) and "}" not in lines[i]:
                    row = lines[i].strip()
                    tokens, buf, in_quote = [], [], False
                    for ch in row:
                        if ch == '"':
                            if in_quote and buf:
                                tokens.append("".join(buf).strip().replace("\\", "/"))
                                buf.clear()
                            in_quote = not in_quote
                        else:
                            if in_quote:
                                buf.append(ch)
                    mats.extend(tokens)
                    i += 1
            else:
                i += 1
        return mats

    rename_map = parse_renamematerial(qc_path)
    cdmats = parse_cdmaterials(qc_path)
    texmats = parse_texturegroup(qc_path)

    for inc in qc_read_includes(qc_path):
        rename_map.update(parse_renamematerial(inc))
        cdmats.extend(parse_cdmaterials(inc))
        texmats.extend(parse_texturegroup(inc))

    renamed_dumps = [rename_map.get(m, m) for m in dumped_materials]

    all_materials = renamed_dumps + [rename_map.get(m, m) for m in texmats]

    combined_mats = []

    if cdmats:
        # Combine each material with each cdmaterial base
        for base in cdmats:
            base = base.rstrip("/")
            for mat in all_materials:
                combined_mats.append(f"{base}/{mat}")
    else:
        # No cdmaterials, just keep materials as-is
        combined_mats = all_materials

    return renamed_dumps + combined_mats