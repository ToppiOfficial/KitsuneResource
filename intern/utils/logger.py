import re, sys
from pathlib import Path
from datetime import datetime


class Logger:
    LEVELS = {"INFO": 1, "WARN": 2, "ERROR": 3, "DEBUG": 4}

    COLOR = {
        "INFO": "\033[97m",
        "WARN": "\033[33m",
        "ERROR": "\033[91m",
        "DEBUG": "\033[35m",
        "RESET": "\033[0m"
    }

    CONTEXT_COLORS = {
        "MODEL": ("\033[95m", "MDL"),
        "MATERIAL": ("\033[96m", "MAT"),
        "DATA": ("\033[93m", "DAT"),
        "PACKAGER": ("\033[94m", "PACKAGER"),
        "OS": ("\033[92m", "OS"),
    }

    _ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

    def __init__(self, verbose=False, use_color=True, log_file=None, context=None, parent=None):
        if parent:
            self.verbose = parent.verbose
            self.use_color = parent.use_color
            self.log_file = parent.log_file
            self.root = parent.root if hasattr(parent, 'root') else parent
        else:
            self.verbose = verbose
            self.use_color = use_color
            self.log_file = log_file
            self.warn_count = 0
            self.error_count = 0
            self.root = self
            self._dedup_counts: dict[tuple, int] = {}

            self.model_compiled    = 0
            self.model_total       = 0
            self.submodel_compiled = 0
            self.submodel_total    = 0
            self.data_compiled     = 0
            self.data_total        = 0

        self.context = context.upper() if context else None
        self.context_label = self.context

        if self.context:
            color, label = self.CONTEXT_COLORS.get(self.context, (None, self.context))
            self.context_label = label
            if color and self.use_color:
                self.prefix = f"{color}[{label}]{self.COLOR['RESET']}"
            else:
                self.prefix = f"[{label}]"
        else:
            self.prefix = ""

    def with_context(self, context: str) -> "Logger":
        return Logger(context=context, parent=self)

    def _write_to_file(self, text):
        if self.log_file:
            try:
                with self.log_file.open("a", encoding="utf-8") as f:
                    f.write(text + "\n")
            except Exception:
                pass

    def _print(self, level, message, console_only=False):
        if level == "WARN":
            self.root.warn_count += 1
        elif level == "ERROR":
            self.root.error_count += 1

        # Suppress repeated warn/error messages on console; still write them to the log file.
        suppress_console = False
        if level in ("WARN", "ERROR"):
            key = (level, message)
            prev = self.root._dedup_counts.get(key, 0)
            self.root._dedup_counts[key] = prev + 1
            if prev > 0:
                suppress_console = True

        now = datetime.now()

        if not suppress_console and (self.verbose or level != "DEBUG"):
            timestamp_console = now.strftime("%H:%M:%S")
            level_prefix_str = f"[{level}]"
            prefix_part = f"{self.prefix} " if self.prefix else ""

            if self.use_color and level in self.COLOR:
                level_color = self.COLOR[level]
                colored_level_prefix = f"{level_color}{level_prefix_str}{self.COLOR['RESET']}"

                if level == "INFO":
                    console_line = f"{timestamp_console} | {prefix_part}{message}"
                else:
                    colored_message = message.replace(self.COLOR['RESET'], level_color)
                    console_line = f"{timestamp_console} | {prefix_part}{colored_level_prefix} {level_color}{colored_message}{self.COLOR['RESET']}"
            else:
                if level == "INFO":
                    console_line = f"{timestamp_console} | {prefix_part}{message}"
                else:
                    console_line = f"{timestamp_console} | {prefix_part}{level_prefix_str} {message}"

            print(console_line)

        if self.log_file and not console_only:
            clean_message = self._ansi_escape.sub('', message)
            timestamp_file = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            context_str = f"[{self.context_label}] " if self.context_label else ""
            file_line = f"{timestamp_file}\t[{level.upper()}] {context_str}{clean_message}"
            self._write_to_file(file_line)

    def get_dedup_summary(self) -> list:
        lines = []
        for (level, message), count in self.root._dedup_counts.items():
            if count > 1:
                clean_msg = self._ansi_escape.sub('', message)
                lines.append(f"  [{level}] \"{clean_msg}\" - seen {count}x (first shown above)")
        return lines

    def info(self, message): self._print("INFO", message)
    def warn(self, message): self._print("WARN", message)
    def error(self, message): self._print("ERROR", message)
    def debug(self, message): self._print("DEBUG", message)

    def write_raw_to_log(self, data, source="Generic"):
        if self.log_file:
            clean_data = self._ansi_escape.sub('', data)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            src = f"{self.context_label}/{source}" if self.context_label else source
            header = f"--- BEGIN {src} OUTPUT"
            footer = f"--- END {src} OUTPUT"
            full_log = f"{timestamp}\t{header}\n{clean_data}\n{timestamp}\t{footer}"
            self._write_to_file(full_log)

    def info_console(self, message): self._print("INFO", message, console_only=True)
    def warn_console(self, message): self._print("WARN", message, console_only=True)
    def error_console(self, message): self._print("ERROR", message, console_only=True)
    def debug_console(self, message): self._print("DEBUG", message, console_only=True)
