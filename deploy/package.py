"""打包线上部署要上传的两份东西（输出到 dist/，已被 .gitignore 排除）：

    dist/studio/  上传到魔搭 Docker 创空间的代码（不含会员数据和任何密钥）
    dist/data/    上传到魔搭私有数据集的会员数据（管线产出的处理后数据，约 16MB）

用法：uv run python deploy/package.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from member_ops.settings import PROCESSED_DIR  # noqa: E402

DIST = ROOT / "dist"
CODE = ["Dockerfile", "requirements-deploy.txt", "member_ops", "app", "playbook", "deploy/start.py"]
DATA = ["members.parquet", "model_report.json", "dept_store_orders.parquet", "dept_store_profiles.parquet",
        "dept_store_extra.json", "portrait_history.json"]
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "._*", ".DS_Store")


def main() -> None:
    shutil.rmtree(DIST, ignore_errors=True)
    studio, data = DIST / "studio", DIST / "data"

    for item in CODE:
        src, dst = ROOT / item, studio / item
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, ignore=SKIP)
        else:
            shutil.copy2(src, dst)
    shutil.copy2(ROOT / "deploy" / "studio_README.md", studio / "README.md")

    data.mkdir(parents=True)
    missing = [f for f in DATA if not (PROCESSED_DIR / f).exists()]
    if missing:
        sys.exit(f"缺少处理后数据 {missing}，请先运行 uv run python -m member_ops.pipeline")
    for f in DATA:
        shutil.copy2(PROCESSED_DIR / f, data / f)

    # macOS 在 exFAT 盘上会自动生成 ._ 开头的附属文件，不能传上去
    for junk in DIST.rglob("._*"):
        junk.unlink()

    # 再检查一遍：代码包里不能混进密钥或会员数据
    leaked = [p.relative_to(studio) for p in studio.rglob("*")
              if p.name in (".env", "AccessKey.csv") or p.suffix in (".parquet", ".csv")]
    if leaked:
        sys.exit(f"代码包里出现了不该上传的文件：{leaked}")

    size = lambda d: sum(p.stat().st_size for p in d.rglob("*") if p.is_file()) / 1e6
    print(f"代码 -> {studio}（{size(studio):.1f} MB），上传到创空间")
    print(f"数据 -> {data}（{size(data):.1f} MB），上传到私有数据集")


if __name__ == "__main__":
    main()
