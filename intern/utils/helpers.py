import time
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
                logger.info("-" * 54)
                if logger.model_total > 0 or logger.data_total > 0:
                    logger.info(f"  {logger.model_compiled}/{logger.model_total} Models Compiled")
                    if logger.submodel_total > 0:
                        logger.info(f"  {logger.submodel_compiled}/{logger.submodel_total} Submodels Compiled")
                    logger.info(f"  {logger.data_compiled}/{logger.data_total} Data Compiled")
                    logger.info('')
                if logger.warn_count > 0 or logger.error_count > 0:
                    logger.info(f"Build finished with {logger.error_count} errors and {logger.warn_count} warnings.")
                    dedup = logger.get_dedup_summary()
                    if dedup:
                        logger.info(f"  Repeated messages suppressed ({len(dedup)} unique):")
                        for line in dedup:
                            logger.info(line)
                logger.info(f"Total time elapsed: {elapsed:.2f} seconds")
                logger.info("-" * 54)
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


def print_header():
    kitsune_raw = [
        "██╗  ██╗██╗████████╗███████╗██╗   ██╗███╗   ██╗███████╗",
        "██║ ██╔╝██║╚══██╔══╝██╔════╝██║   ██║████╗  ██║██╔════╝",
        "█████╔╝ ██║   ██║   ███████╗██║   ██║██╔██╗ ██║█████╗  ",
        "██╔═██╗ ██║   ██║   ╚════██║██║   ██║██║╚██╗██║██╔══╝  ",
        "██║  ██╗██║   ██║   ███████║╚██████╔╝██║ ╚████║███████╗",
        "╚═╝  ╚═╝╚═╝   ╚═╝   ╚══════╝ ╚═════╝ ╚═╝  ╚═══╝╚══════╝",
    ]
    resource_raw = [
        "██████╗  ███████╗███████╗ ██████╗ ██╗   ██╗██████╗   ██████╗███████╗",
        "██╔══██╗ ██╔════╝██╔════╝██╔═══██╗██║   ██║██╔══██╗ ██╔════╝██╔════╝",
        "███████╔╝█████╗  ███████╗██║   ██║██║   ██║███████╔╝██║     █████╗  ",
        "██╔══██╗ ██╔══╝  ╚════██║██║   ██║██║   ██║██╔══██╗ ██║     ██╔══╝  ",
        "██║  ██║ ███████╗███████║╚██████╔╝╚██████╔╝██║  ██║ ╚██████╗███████╗",
        "╚═╝  ╚═╝ ╚══════╝╚══════╝ ╚═════╝  ╚═════╝ ╚═╝  ╚═╝  ╚═════╝╚══════╝",
    ]

    max_w = max(len(l) for l in resource_raw)
    all_lines = [l.center(max_w) for l in kitsune_raw] + [""] + resource_raw
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
