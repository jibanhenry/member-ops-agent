# 魔搭 Docker 创空间：服务必须监听 0.0.0.0:7860
# 基础镜像用魔搭官方镜像仓库，国内构建更快；PyPI 走阿里云镜像
FROM modelscope-registry.cn-beijing.cr.aliyuncs.com/modelscope-repo/python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    DEMO_MODE=1 \
    LLM_BACKEND=cloud

WORKDIR /home/user/app
COPY requirements-deploy.txt .
RUN pip install -r requirements-deploy.txt

COPY member_ops ./member_ops
COPY app ./app
COPY playbook ./playbook
COPY deploy/start.py ./deploy/start.py

EXPOSE 7860
CMD ["python", "-u", "deploy/start.py"]
