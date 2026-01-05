import shlex
import re
from pathlib import Path
from typing import Optional, TextIO


def _evaluate_condition(expression: str, variables: dict, is_ifdef: bool) -> bool:
    """Safely evaluates a QC conditional expression."""
    expression = expression.strip()

    if is_ifdef:
        return expression in variables

    def get_value(val_str: str):
        val_str = val_str.strip().strip('"')
        if val_str in variables:
            return variables[val_str]
        return val_str

    def compare(left_str, op, right_str):
        left = get_value(left_str)
        right = get_value(right_str)
        
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
            
            # Use a more specific regex to avoid matching things that aren't comparisons
            match = re.match(r'^\s*([^\s"]+|"[^"]+")\s*(==|!=|>=|<=|>|<)\s*([^\s"]+|"[^"]+")\s*$', and_part)
            if match:
                left, op, right = match.groups()
                if not compare(left, op, right):
                    is_and_term_true = False
                    break
            elif and_part: # Handle lone variable check for truthiness/existence
                val = get_value(and_part)
                if val is None or str(val).strip() in ("0", "", "false"):
                    is_and_term_true = False
                    break
        
        if is_and_term_true:
            return True

    return False

class QCProcessor:
    COMMENT_OUT_COMMANDS = {"$msg", "$echo"}
    
    ORANGE = "\033[38;5;208m"
    RESET = '\033[0m'
    
    def __init__(self, variables: dict = None, macros: dict = None, logger=None):
        self.variables = variables if variables is not None else {}
        self.macros = macros if macros is not None else {}
        self.logger = logger
        self.if_stack = []
        self.output_lines = []
        
    def process_line(self, line: str, line_num: int, base_dir: Path, include_stack: set) -> Optional[str]:
        stripped_line = line.strip()
        command_parts = self._parse_command(stripped_line)
        command = command_parts[0].lower() if command_parts else ""
        
        is_skipping = self.if_stack and not self.if_stack[-1][0]
        
        if self._handle_conditional(command, command_parts, line_num, is_skipping):
            return None
        
        if is_skipping:
            return None
        
        processed_line = self._substitute_variables(line)
        active_command_parts = self._parse_command(processed_line.strip())
        active_command = active_command_parts[0].lower() if active_command_parts else ""
        
        if active_command == "$definevariable":
            return self._handle_define_variable(active_command_parts, line_num, processed_line.strip())
        
        elif active_command == "$include":
            return self._handle_include(line, active_command_parts, line_num, base_dir, include_stack, processed_line.strip())
        
        elif active_command.startswith('$') and active_command[1:] in self.macros:
            return self._handle_macro_expansion(active_command, active_command_parts, base_dir, include_stack)
        
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
    
    def _substitute_variables(self, line: str) -> str:
        result = line
        for var_name in sorted(self.variables.keys(), key=len, reverse=True):
            result = result.replace(f'${var_name}$', str(self.variables[var_name]))
        return result
    
    def _handle_conditional(self, command: str, command_parts: list, line_num: int, is_skipping: bool) -> bool:
        if command in ("$if", "$ifdef"):
            parent_is_skipping = is_skipping
            if parent_is_skipping:
                self.if_stack.append((False, False))
            else:
                condition_str = " ".join(command_parts[1:])
                condition_str = self._substitute_variables(condition_str)
                result = _evaluate_condition(condition_str, self.variables, is_ifdef=(command == "$ifdef"))
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
                condition_str = self._substitute_variables(condition_str)
                result = _evaluate_condition(condition_str, self.variables, is_ifdef=False)
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
    
    def _handle_define_variable(self, command_parts: list, line_num: int, line: str) -> None:
        try:
            if len(command_parts) == 3:
                _, var_name, var_value = command_parts
                self.variables[var_name] = var_value
            else:
                self.output_lines.append(f"// WARNING Line {line_num}: Malformed $definevariable: {line}\n")
        except Exception as e:
            self.output_lines.append(f"// WARNING Line {line_num}: Failed to parse $definevariable: {line} ({e})\n")
        return None
    
    def _handle_include(self, original_line: str, command_parts: list, line_num: int, base_dir: Path, include_stack: set, processed_line: str) -> str:
        try:
            include_indent = re.match(r"^\s*", original_line).group(0)
            include_path_str = command_parts[1]
            next_qc_path = base_dir / include_path_str
            
            include_content = flatten_qc(next_qc_path, include_stack, self.variables, self.macros, self.logger)
            
            if include_indent:
                indented_lines = []
                for content_line in include_content.splitlines(True):
                    indented_lines.append(include_indent + content_line)
                include_content = "".join(indented_lines)
            
            if include_content and not include_content.endswith('\n'):
                include_content += '\n'
            
            return include_content
        except (IndexError, ValueError) as e:
            self.output_lines.append(f"// WARNING Line {line_num}: Failed to parse $include: {processed_line} ({e})\n")
            return original_line
    
    def _handle_macro_expansion(self, active_command: str, command_parts: list, base_dir: Path, include_stack: set) -> str:
        macro_name = active_command[1:]
        macro_def = self.macros[macro_name]
        macro_args_provided = command_parts[1:] if len(command_parts) > 1 else []
        
        macro_body_text = []
        for macro_line in macro_def['body']:
            expanded_line = macro_line
            for i, arg_name in enumerate(macro_def['args']):
                if i < len(macro_args_provided):
                    expanded_line = expanded_line.replace(f'${arg_name}$', macro_args_provided[i])
            macro_body_text.append(expanded_line)
        
        macro_content = '\n'.join(macro_body_text) + '\n'
        
        processor = QCProcessor(self.variables.copy(), self.macros, self.logger)
        return processor.process_content(macro_content, base_dir, include_stack.copy())
    
    def process_content(self, content: str, base_dir: Path, include_stack: set) -> str:
        self.output_lines = []
        self.if_stack = []
        
        for line_num, line in enumerate(content.splitlines(True), 1):
            result = self.process_line(line, line_num, base_dir, include_stack)
            if result is not None:
                self.output_lines.append(result)
        
        return "".join(self.output_lines)


def flatten_qc(qc_path: Path, _include_stack: set = None, _variables: dict = None, _macros: dict = None, logger=None) -> str:
    if _include_stack is None:
        _include_stack = set()
    if _variables is None:
        _variables = {}
    if _macros is None:
        _macros = {}
    
    try:
        resolved_path = qc_path.resolve(strict=True)
    except FileNotFoundError:
        return f"// ERROR: $include or qc file not found: {qc_path.as_posix()}\n"
    except Exception as e:
        return f"// ERROR: Failed to resolve path '{qc_path.as_posix()}': {e}\n"
    
    if resolved_path in _include_stack:
        return f"// ERROR: Circular $include detected! '{resolved_path.as_posix()}' is already in the include stack.\n"
    
    _include_stack.add(resolved_path)
    
    processor = QCProcessor(_variables, _macros, logger)
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
            
            processed_line = processor._substitute_variables(line)
            active_command_parts = processor._parse_command(processed_line.strip())
            active_command = active_command_parts[0].lower() if active_command_parts else ""
            
            if active_command == "$definemacro":
                try:
                    if len(active_command_parts) >= 2:
                        macro_name = active_command_parts[1]
                        macro_args = active_command_parts[2:] if len(active_command_parts) > 2 else []
                        current_macro = {'name': macro_name, 'args': macro_args}
                        macro_lines = []
                    else:
                        output_lines.append(f"// WARNING Line {line_num}: Malformed $definemacro: {processed_line.strip()}\n")
                except Exception as e:
                    output_lines.append(f"// WARNING Line {line_num}: Failed to parse $definemacro: {processed_line.strip()} ({e})\n")
                continue
            
            result = processor.process_line(line, line_num, resolved_path.parent, _include_stack)
            if result is not None:
                output_lines.append(result)
    
    output_lines.extend(processor.output_lines)
    _include_stack.remove(resolved_path)
    
    return "".join(output_lines)


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