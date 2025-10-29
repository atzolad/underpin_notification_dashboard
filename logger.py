import logging
from logging.handlers import RotatingFileHandler

def setup_logging(name="main_app_log", log_file="main_app_log", max_bytes=5000000, backupcount=2 ):
    '''
    Setup logging with automatic file rotation.
    
    Args:
    name: Name of the logger (use __name__ from calling module)
    log_file: Name of the file to save logs to. Defaults to main_app_log
    max_bytes: Max filesize of the logfile before rotating to a new file. Default to 5MB
    backup_count: Number of backupfiles to keep
    
    '''
    logger = logging.getLogger(name)

    #Only add a logging handler if one doesn't already exist.
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)

        #Configure the logging handler
        handler = RotatingFileHandler(
            log_file,
            max_bytes,
            backupcount
        )

        #Configure the logging format
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        #Add console logging
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger