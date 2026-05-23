import time
import re
from functools import wraps

from .logger import Logger
from .constants import SOFTVERSION, SOFTBUILDDATE, IS_DEV_BUILD, SOFTSHA256


def timer(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        logger = None
        try:
            logger = func(*args, **kwargs)
        finally:
            elapsed = time.time() - start_time
            if logger:
                logger.info('')
                print_summary(logger, elapsed)
            else:
                print(f"Total time elapsed: {elapsed:.2f} seconds")
        return logger
    return wrapper


def _colorize_art(lines):
    RESET = "\033[0m"
    start = (255, 130, 0)
    end   = (255, 245, 150)
    max_w = max((len(l) for l in lines if l.strip()), default=1)
    result = []
    for line in lines:
        row = []
        for i, ch in enumerate(line):
            if ch != ' ':
                t = i / max(max_w - 1, 1)
                r = int(start[0] + t * (end[0] - start[0]))
                g = int(start[1] + t * (end[1] - start[1]))
                b = int(start[2] + t * (end[2] - start[2]))
                row.append(f"\033[38;2;{r};{g};{b}m{ch}")
            else:
                row.append(ch)
        row.append(RESET)
        result.append("".join(row))
    return result


def print_wine_badge(wine_prefix: list) -> None:
    RESET = "\033[0m"
    cmd = " ".join(wine_prefix)
    content = f"=  wine: {cmd}  ="
    inner = f"  {content}  "
    hbar = "─" * len(inner)
    raw_lines = [
        f"┌{hbar}┐",
        f"│{inner}│",
        f"└{hbar}┘",
    ]

    centered = [line.center(_HEADER_MAX_W) for line in raw_lines]

    start = (180, 0, 0)
    end   = (255, 110, 110)
    colored = []
    for line in centered:
        n = len(line)
        row = []
        for i, ch in enumerate(line):
            t = i / max(n - 1, 1)
            r = int(start[0] + t * (end[0] - start[0]))
            g = int(start[1] + t * (end[1] - start[1]))
            b = int(start[2] + t * (end[2] - start[2]))
            row.append(f"\033[38;2;{r};{g};{b}m{ch}")
        row.append(RESET)
        colored.append("".join(row))

    print("\n".join(colored))
    print()


def print_summary(logger, elapsed):
    use_color = getattr(logger, 'use_color', True)

    ORANGE = "\033[38;2;255;130;0m"
    GOLD   = "\033[38;2;255;200;80m"
    WHITE  = "\033[97m"
    RED    = "\033[91m"
    YELLOW = "\033[33m"
    RESET  = "\033[0m"

    _ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

    def c(text, color):
        return f"{color}{text}{RESET}" if use_color else text

    def plain_len(text):
        return len(_ansi_escape.sub('', text))

    # Collect all content lines to determine required box width
    content_lines = []

    if logger.model_total > 0 or logger.data_total > 0:
        content_lines.append(f"{c('Models:', WHITE)}     {c(str(logger.model_compiled), GOLD)}/{c(str(logger.model_total), GOLD)}")
        if logger.submodel_total > 0:
            content_lines.append(f"{c('Submodels:', WHITE)} {c(str(logger.submodel_compiled), GOLD)}/{c(str(logger.submodel_total), GOLD)}")
        content_lines.append(f"{c('Data:', WHITE)}       {c(str(logger.data_compiled), GOLD)}/{c(str(logger.data_total), GOLD)}")
        content_lines.append(None)  # divider marker

    if logger.warn_count > 0 or logger.error_count > 0:
        err_str = c(str(logger.error_count), RED)
        warn_str = c(str(logger.warn_count), YELLOW)
        content_lines.append(f"{c('Build finished with ', WHITE)}{err_str}{c(' errors and ', WHITE)}{warn_str}{c(' warnings.', WHITE)}")
        for dedup_line in logger.get_dedup_summary():
            content_lines.append(c(dedup_line.strip(), YELLOW))
        content_lines.append(None)  # divider marker

    content_lines.append(f"{c('Total time elapsed:', WHITE)} {c(f'{elapsed:.2f} seconds', GOLD)}")

    # Width = max of all non-divider line lengths, minimum 54
    W = max(54, max(plain_len(l) for l in content_lines if l is not None))

    def color_border(ch):
        return c(ch, ORANGE) if use_color else ch

    def row(content=""):
        padding = max(0, W - plain_len(content) - 2)
        return f"{color_border('║')} {content}{' ' * padding} {color_border('║')}"

    if use_color:
        top = f"{color_border('╔')}{c('═' * W, ORANGE)}{color_border('╗')}"
        div = f"{color_border('╠')}{c('═' * W, ORANGE)}{color_border('╣')}"
        bot = f"{color_border('╚')}{c('═' * W, ORANGE)}{color_border('╝')}"
    else:
        top = f"+{'-' * W}+"
        div = f"+{'-' * W}+"
        bot = f"+{'-' * W}+"

    output = [top]
    for line in content_lines:
        if line is None:
            output.append(div)
        else:
            output.append(row(line))
    output.append(bot)

    print()
    print("\n".join(output))
    print()


_KITSUNE_RAW = [
    "██╗  ██╗██╗████████╗███████╗██╗   ██╗███╗   ██╗███████╗",
    "██║ ██╔╝██║╚══██╔══╝██╔════╝██║   ██║████╗  ██║██╔════╝",
    "█████╔╝ ██║   ██║   ███████╗██║   ██║██╔██╗ ██║█████╗  ",
    "██╔═██╗ ██║   ██║   ╚════██║██║   ██║██║╚██╗██║██╔══╝  ",
    "██║  ██╗██║   ██║   ███████║╚██████╔╝██║ ╚████║███████╗",
    "╚═╝  ╚═╝╚═╝   ╚═╝   ╚══════╝ ╚═════╝ ╚═╝  ╚═══╝╚══════╝",
]
_RESOURCE_RAW = [
    "██████╗  ███████╗███████╗ ██████╗ ██╗   ██╗██████╗   ██████╗███████╗",
    "██╔══██╗ ██╔════╝██╔════╝██╔═══██╗██║   ██║██╔══██╗ ██╔════╝██╔════╝",
    "███████╔╝█████╗  ███████╗██║   ██║██║   ██║███████╔╝██║     █████╗  ",
    "██╔══██╗ ██╔══╝  ╚════██║██║   ██║██║   ██║██╔══██╗ ██║     ██╔══╝  ",
    "██║  ██║ ███████╗███████║╚██████╔╝╚██████╔╝██║  ██║ ╚██████╗███████╗",
    "╚═╝  ╚═╝ ╚══════╝╚══════╝ ╚═════╝  ╚═════╝ ╚═╝  ╚═╝  ╚═════╝╚══════╝",
]
_HEADER_MAX_W = max(len(l) for l in _RESOURCE_RAW)


def print_header():
    max_w = _HEADER_MAX_W
    all_lines = [l.center(max_w) for l in _KITSUNE_RAW] + [""] + _RESOURCE_RAW
    colored = _colorize_art(all_lines)

    GOLD = "\033[38;2;255;200;80m"
    RESET = "\033[0m"

    if IS_DEV_BUILD:
        extra_lines = [f"KitsuneResource {SOFTVERSION} dev"]
    else:
        extra_lines = [
            f"KitsuneResource {SOFTVERSION} - {SOFTBUILDDATE}",
            f"SHA256 {SOFTSHA256}",
        ]

    print()
    print("\n".join(colored))
    print()
    for line in extra_lines:
        print(f"{GOLD}{line.center(max_w)}{RESET}")
    print()
