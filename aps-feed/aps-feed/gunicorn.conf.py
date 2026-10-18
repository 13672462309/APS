import resource
import os
from logging_config import setup_gunicorn_logging

# gunicorn.conf.py
bind = '0.0.0.0:8123'  # 绑定的IP地址和端口
workers = 3
threads = 1  # 确保每个 worker 独立处理请求
worker_connections = 50  # 调大连接数
daemon = False

# 内存限制
limit_request_line = 4094  # 请求行的最大长度
limit_request_fields = 32768  # 请求头的最大数量
limit_request_field_size = 8190  # 请求头的最大长度

# 调整超时时间和内存相关配置
timeout = 600  # 增加超时时间到10分钟
graceful_timeout = 120  # 优雅退出时间增加到2分钟
keepalive = 50  # 保持连接的时间

# common params
loglevel = 'info'  # 日志级别, debug, info, warning, error, critical
backlog = 2048  # 等待服务客户的数量，最大为2048，即最大挂起的连接数
max_requests = 500  # 降低单个worker处理的最大请求数
max_requests_jitter = 100  # 增加抖动避免同时重启

reload = False  # 当代码有修改时，自动重启workers。适用于开发环境。
pidfile = './gunicorn.pid'  # 设置pid文件的文件名

# 日志路径配置 - 使用Python的TimedRotatingFileHandler管理，不让gunicorn自己管理轮转
errorlog = '-'  # 输出到stderr，由Python日志系统统一管理
accesslog = './aps-log/access.log'  # 访问日志文件

# worker配置
# worker_class = "gevent"  # 使用gevent处理异步请求

# 内存限制配置 (6GB per worker)
max_memory_per_worker = int(6 * 1024 * 1024 * 1024)  # 6GB in bytes

def on_starting(server):
    """在 Gunicorn 启动时设置资源限制"""
    resource.setrlimit(resource.RLIMIT_AS, (max_memory_per_worker, max_memory_per_worker))
    
    # 使用统一的日志配置系统初始化日志
    setup_gunicorn_logging(server)

def post_fork(server, worker):
    """在 worker fork 后设置资源限制"""
    resource.setrlimit(resource.RLIMIT_AS, (max_memory_per_worker, max_memory_per_worker))
    # 设置worker的优先级
    os.nice(10)