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


def print_header():
    ascii_art = r"""
  _  _______ _______ _____ _    _ _   _ ______ _____  ______  _____  ____  _    _ _____   _____ ______
 | |/ /_   _|__   __/ ____| |  | | \ | |  ____|  __ \|  ____|/ ____|/ __ \| |  | |  __ \ / ____|  ____|
 | ' /  | |    | | | (___ | |  | |  \| | |__  | |__) | |__  | (___ | |  | | |  | | |__) | |    | |__
 |  <   | |    | |  \___ \| |  | | . ` |  __| |  _  /|  __|  \___ \| |  | | |  | |  _  /| |    |  __|
 | . \ _| |_   | |  ____) | |__| | |\  | |____| | \ \| |____ ____) | |__| | |__| | | \ \| |____| |____
 |_|\_\_____|  |_| |_____/ \____/|_| \_|______|_|  \_\______|_____/ \____/ \____/|_|  \_\\_____|______|

"""

    art_lines = ascii_art.splitlines()
    max_width = max(len(line) for line in art_lines)

    if IS_DEV_BUILD:
        extra_lines = [f"KitsuneResource {SOFTVERSION} dev"]
    else:
        extra_lines = [
            f"KitsuneResource {SOFTVERSION} - {SOFTBUILDDATE}",
            f"SHA256 {SOFTSHA256}",
        ]

    centered_extra = "\n".join(line.center(max_width) for line in extra_lines)

    print(ascii_art + centered_extra + "\n")
