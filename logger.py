import logging
import sys


def setup_logging(name="main_app_log"):
    """
    Setup logging with output directly to the console which is recorded by Google Cloud Logging

    Args:
    name: Name of the logger (use __name__ from calling module)

    """
    logger = logging.getLogger(name)

    # Only add a logging handler if one doesn't already exist.
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)

        # Configure the logging format
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ"
        )

        # Add console logging
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger
