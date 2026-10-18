# 使用官方的 Python 基础镜像
FROM registry-vpc.cn-hangzhou.aliyuncs.com/haid-library/python:3.10

# 安装系统依赖（gunicorn 走 pip/requirements.txt，不在这里 apt 装，避免两套版本打架）
# RUN yum install -y vim curl net-tools git wget unzip && yum clean all
RUN apt-get update && \
    apt-get install -y vim curl net-tools git wget unzip  && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 创建非root用户
RUN useradd --uid 3000 -m -s /bin/bash appuser

# 设置工作目录
WORKDIR /opt/haid

# 复制当前目录内容到工作目录
COPY . /opt/haid

# 修改目录权限
RUN chown -R appuser:appuser /opt/haid

# 安装所需的 Python 依赖
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

# 切换到非root用户
USER appuser

RUN mkdir aps-log

# 暴露应用运行的端口
EXPOSE 8123

# 健康检查（/test 为常驻健康检查路由）
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://localhost:8123/test || exit 1

# 运行 Gunicorn 应用
CMD ["gunicorn", "-c", "gunicorn.conf.py", "aps_feed:app"]
