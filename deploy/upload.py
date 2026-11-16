"""打包并上传到魔搭：代码 -> Docker 创空间，会员数据 -> 私有数据集。

用法（modelscope 只在上传时需要，不装进项目环境）：
    uv run --with modelscope==1.40.3 python deploy/upload.py studio   # 只传代码（改了代码、页面、方法论）
    uv run --with modelscope==1.40.3 python deploy/upload.py data     # 只传数据（重跑了管线、改了 playbook.yaml）
    uv run --with modelscope==1.40.3 python deploy/upload.py all      # 两样都传

需要 .env 里的 MODELSCOPE_WRITE_TOKEN（写权限令牌，只在本机用，不要填到线上）。
上传后到创空间"空间管理 -> 重新部署 -> 确认部署"，新代码和数据才会生效。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from member_ops import settings  # noqa: E402,F401  读取 .env

STUDIO_REPO = os.environ.get("STUDIO_REPO", "Henrywu111/member-ops-agent")
DATA_REPO = os.environ.get("DATA_DATASET", "Henrywu111/member-ops-data")
TARGETS = {"studio": ("studio", STUDIO_REPO, ROOT / "dist" / "studio"),
           "data": ("dataset", DATA_REPO, ROOT / "dist" / "data")}


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    if which not in ("studio", "data", "all"):
        sys.exit(__doc__)
    token = os.environ.get("MODELSCOPE_WRITE_TOKEN")
    if not token:
        sys.exit("缺少 MODELSCOPE_WRITE_TOKEN：在 .env 里填魔搭写权限令牌（访问令牌页的 default）")

    subprocess.run([sys.executable, str(ROOT / "deploy" / "package.py")], check=True)

    from modelscope.hub.api import HubApi

    api = HubApi()
    api.login(token)
    for key in (["studio", "data"] if which == "all" else [which]):
        repo_type, repo_id, folder = TARGETS[key]
        n = sum(1 for p in folder.rglob("*") if p.is_file())
        print(f"上传 {n} 个文件 -> {repo_type} {repo_id}", flush=True)
        api.upload_folder(repo_id=repo_id, folder_path=str(folder), repo_type=repo_type,
                          commit_message="更新会员运营智能体部署文件")
    print("上传完成。到创空间「空间管理 -> 重新部署 -> 确认部署」让改动生效。")


if __name__ == "__main__":
    main()
