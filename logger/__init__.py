import logging
import os
import sys
import io
from logging.handlers import RotatingFileHandler


class LogHandler(object):
    def __init__(self, logger_name):
        self.__logger = ""
        self.__log_capture_string = io.StringIO()
        self.__log_msg(logger_name)

    def __log_msg(self, logger_name):
        file_name = '{0}'.format(logger_name)
        filename = './program-logs/{0}.log'.format(file_name)
        os.makedirs(os.path.dirname(filename), exist_ok=True)

        if logging.getLogger(logger_name).handlers:
            return

        self.__logger = logging.getLogger(logger_name)
        log_level = logging.INFO
        formatter = logging.Formatter(
            fmt='%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s',
            datefmt='%Y-%m-%d,%H:%M:%S')

        self.__logger.setLevel(log_level)

        fh = RotatingFileHandler(filename, backupCount=10, maxBytes=100000000)
        fh.setLevel(log_level)
        fh.setFormatter(formatter)

        ch = logging.StreamHandler()
        ch.setLevel(log_level)
        ch.setFormatter(logging.Formatter('%(name)-12s: %(levelname)-8s %(message)s'))

        self.__logger.addHandler(fh)
        self.__logger.addHandler(ch)
        logging.StreamHandler(self.__log_capture_string)
        sys.excepthook = self.__my_handler

    def __my_handler(self, exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        self.__logger.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))

    def get_string_logs(self):
        return str(self.__log_capture_string.getvalue()).lower()
