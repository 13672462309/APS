import logging
import logging.handlers
import os
from logging.config import dictConfig
from flask import has_request_context, request, current_app
import threading
import sys
import uuid
from datetime import datetime

# 统一的日志格式
LOG_FORMAT = '%(asctime)s [%(process)d] [%(levelname)s] [%(filename)s:%(lineno)d] - %(message)s'
FLASK_LOG_FORMAT = '%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] - %(message)s'

# 日志配置
LOG_CONFIG = {
    'max_bytes': 0,  # 禁用大小轮转，设置为0
    'backup_count': 10,
    'log_dir': './aps-log',
    'log_level': 'INFO'
}

# 全局logger实例
_app_logger = None

class RequestIdFilter(logging.Filter):
    def filter(self, record):
        if has_request_context():
            record.request_id = request.headers.get('X-Request-ID', '')
        else:
            record.request_id = threading.get_ident()
        return True

class MaxLevelFilter(logging.Filter):
    def __init__(self, max_level):
        super().__init__()
        self.max_level = max_level

    def filter(self, record):
        return record.levelno <= self.max_level

class LevelNameFilter(logging.Filter):
    def filter(self, record):
        # 将日志等级转换为缩写
        level_map = {
            'DEBUG': 'D',
            'INFO': 'I',
            'WARNING': 'W',
            'ERROR': 'E',
            'CRITICAL': 'C'
        }
        record.levelname = level_map.get(record.levelname, record.levelname)
        return True

def ensure_log_dir():
    """确保日志目录存在"""
    if not os.path.exists(LOG_CONFIG['log_dir']):
        os.makedirs(LOG_CONFIG['log_dir'])
    return LOG_CONFIG['log_dir']

class TimedSizeRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """
    结合了时间轮转和大小轮转功能的日志处理器
    每天零点轮转，同时当文件大小超过max_bytes时也会轮转
    """
    def __init__(self, filename, when='midnight', interval=1, backupCount=0, 
                 encoding=None, delay=False, utc=False, max_bytes=0):
        super().__init__(filename, when, interval, backupCount, 
                        encoding, delay, utc)
        self.max_bytes = max_bytes
    
    def shouldRollover(self, record):
        # 检查是否需要按时间轮转
        if super().shouldRollover(record):
            return 1
            
        # 检查是否需要按大小轮转
        if self.max_bytes > 0:
            msg = self.format(record)
            self.stream.seek(0, 2)  # 移动到文件末尾
            if self.stream.tell() + len(msg) >= self.max_bytes:
                return 1
        return 0
    
    def rotation_filename(self, default_name):
        """
        生成轮转后的文件名
        格式: {原文件名}.{年-月-日}.{序号}
        """
        # 检查是否已经包含日期
        if default_name.endswith(self.suffix):
            # 如果是按时间轮转触发的，直接使用父类的命名规则
            return default_name
        
        # 按大小轮转时，添加日期和序号
        date_str = datetime.now().strftime("%Y-%m-%d")
        
        # 查找当天现有的日志文件，确定序号
        base_name = f"{self.baseFilename}.{date_str}"
        count = 1
        
        while os.path.exists(f"{base_name}.{count}"):
            count += 1
            
        return f"{base_name}.{count}"

def create_log_handlers(log_dir, log_format, is_gunicorn=False):
    """创建通用的日志处理器"""
    handlers = []
    
    # 创建日志文件路径
    info_log_file = os.path.join(log_dir, 'info.log')
    error_log_file = os.path.join(log_dir, 'error.log')
    
    # 创建分级过滤器
    info_filter = MaxLevelFilter(logging.INFO)
    
    # 创建格式化器
    formatter = logging.Formatter(
        '[%(asctime)s|%(process)d] %(levelname)s %(filename)s:%(lineno)d - %(message)s',
        datefmt='%m-%d %H:%M'
    )
    
    # 创建INFO处理器 - 仅使用时间轮转
    info_handler = logging.handlers.TimedRotatingFileHandler(
        info_log_file,
        when='midnight',  # 每天零点轮转
        interval=1,
        backupCount=LOG_CONFIG['backup_count'],
        encoding='utf-8'
    )
    info_handler.setLevel(logging.DEBUG)
    info_handler.addFilter(info_filter)
    info_handler.addFilter(LevelNameFilter())
    info_handler.setFormatter(formatter)
    handlers.append(info_handler)
    
    # 创建ERROR处理器 - 仅使用时间轮转
    error_handler = logging.handlers.TimedRotatingFileHandler(
        error_log_file,
        when='midnight',  # 每天零点轮转
        interval=1,
        backupCount=LOG_CONFIG['backup_count'],
        encoding='utf-8'
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.addFilter(LevelNameFilter())
    error_handler.setFormatter(formatter)
    handlers.append(error_handler)
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, LOG_CONFIG['log_level']))
    console_handler.addFilter(LevelNameFilter())
    console_handler.setFormatter(formatter)
    handlers.append(console_handler)
    
    return handlers

def validate_logger(logger):
    """验证logger配置是否正确"""
    if not logger.handlers:
        raise RuntimeError("Logger has no handlers configured")
    
    has_file_handler = False
    has_console_handler = False
    
    for handler in logger.handlers:
        if isinstance(handler, (logging.handlers.RotatingFileHandler, 
                               logging.handlers.TimedRotatingFileHandler,
                               TimedSizeRotatingFileHandler)):
            has_file_handler = True
        elif isinstance(handler, logging.StreamHandler):
            has_console_handler = True
    
    if not has_file_handler:
        raise RuntimeError("Logger has no file handler configured")
    if not has_console_handler:
        raise RuntimeError("Logger has no console handler configured")

def setup_logging(app=None):
    """
    统一的日志配置入口点
    根据运行环境自动选择合适的日志配置
    """
    global _app_logger
    
    # 如果已经初始化过logger，直接返回
    if _app_logger is not None:
        return _app_logger
        
    log_dir = ensure_log_dir()
    is_gunicorn = 'gunicorn' in sys.modules or 'gunicorn' in os.environ.get('SERVER_SOFTWARE', '')
    
    if is_gunicorn:
        _app_logger = logging.getLogger('gunicorn.error')
    else:
        _app_logger = app.logger if app else logging.getLogger('app')
    
    # 清除现有的handlers
    if _app_logger.handlers:
        for handler in _app_logger.handlers:
            _app_logger.removeHandler(handler)
    
    # 创建新的handlers
    handlers = create_log_handlers(log_dir, LOG_FORMAT, is_gunicorn)
    for handler in handlers:
        _app_logger.addHandler(handler)
    
    # 设置日志级别
    _app_logger.setLevel(getattr(logging, LOG_CONFIG['log_level']))
    
    # 添加请求ID过滤器
    _app_logger.addFilter(RequestIdFilter())
    
    # 验证logger配置
    try:
        validate_logger(_app_logger)
    except RuntimeError as e:
        if 'DEBUG' in os.environ.get('FLASK_ENV', ''):
            print(f"警告: 日志配置验证失败: {str(e)}")
        else:
            raise
    
    return _app_logger

def get_logger():
    """
    获取统一的logger实例
    """
    global _app_logger
    
    # 如果logger还没有初始化，且在Flask上下文中
    if _app_logger is None and current_app:
        return setup_logging(current_app)
    
    # 如果logger还没有初始化，且不在Flask上下文中
    if _app_logger is None:
        return setup_logging(None)
    
    return _app_logger

def setup_gunicorn_logging(gunicorn_conf=None):
    """为gunicorn配置日志系统"""
    return setup_logging(None)