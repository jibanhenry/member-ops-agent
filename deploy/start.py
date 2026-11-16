"""容器启动入口：先把会员数据准备好，再启动网站（0.0.0.0:7860）。

会员数据不放进代码仓库（创空间公开后代码文件谁都能下载），而是放在魔搭的私有数据集里，
启动时用只读令牌下载。需要的环境变量（在创空间"设置 -> 环境变量"里配置）：
    MODELSCOPE_TOKEN  魔搭只读访问令牌：下载私有数据集 + 调用免费模型
    DATA_DATASET      私有数据集 ID，如 Henrywu111/member-ops-data
    DEEPSEEK_API_KEY  可选，魔搭额度用完或出错时的备用模型
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ["members.parquet", "model_report.json"]


def data_dir() -> Path:
    if os.environ.get("PROCESSED_DIR"):
        return Path(os.environ["PROCESSED_DIR"])
    # 创空间重启后只有 /mnt/workspace 会保留
    return Path("/mnt/workspace/processed") if Path("/mnt/workspace").is_dir() else ROOT / "data" / "processed"


def ensure_data(target: Path) -> None:
    dataset, token = os.environ.get("DATA_DATASET"), os.environ.get("MODELSCOPE_TOKEN")
    if dataset and token:
        from modelscope.hub.snapshot_download import dataset_snapshot_download

        print(f"从私有数据集 {dataset} 下载会员数据到 {target} …", flush=True)
        target.mkdir(parents=True, exist_ok=True)
        dataset_snapshot_download(dataset, local_dir=str(target), token=token)
    missing = [f for f in REQUIRED if not (target / f).exists()]
    if missing:
        sys.exit(f"会员数据不完整，缺少 {missing}。请检查 DATA_DATASET 和 MODELSCOPE_TOKEN 环境变量，"
                 f"以及数据集里是否上传了 deploy/package.py 生成的全部文件。")


def main() -> None:
    target = data_dir()
    ensure_data(target)
    os.environ["PROCESSED_DIR"] = str(target)
    port = os.environ.get("PORT", "7860")
    os.chdir(ROOT)
    os.execvp(sys.executable, [sys.executable, "-m", "uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", port,
                               "--proxy-headers", "--forwarded-allow-ips", "*"])


if __name__ == "__main__":
    main()
