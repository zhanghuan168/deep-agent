"""打包干净的源码 zip（不含 node_modules / .venv / dist / db / logs），
用户解压后直接 git init && git push 即可。"""
import shutil
import zipfile
from pathlib import Path

ROOT = Path(r"D:\project\work\deep-agent-zh")
STAGING = ROOT / "_staging_pkg"
OUT_ZIP = ROOT / "deep-agent-zh-source.zip"

# 这些目录/文件不打包
EXCLUDE_DIRS = {"node_modules", ".venv", "dist", "_staging_pkg", "__pycache__"}
EXCLUDE_GLOBS = ["*.db", "*.db-wal", "*.db-shm", "*.log", "deep-agent-zh*.zip"]


def should_exclude(p: Path) -> bool:
    rel = p.relative_to(ROOT)
    for part in rel.parts:
        if part in EXCLUDE_DIRS:
            return True
    name = p.name
    for pat in EXCLUDE_GLOBS:
        if Path(name).match(pat):
            return True
    return False


def main():
    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True)

    # 复制必要文件
    for p in ROOT.iterdir():
        if should_exclude(p):
            continue
        dest = STAGING / p.name
        if p.is_dir():
            shutil.copytree(p, dest, ignore=shutil.ignore_patterns(*EXCLUDE_DIRS, "*.db*", "*.log"))
        else:
            shutil.copy2(p, dest)

    # 打包
    if OUT_ZIP.exists():
        OUT_ZIP.unlink()
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in STAGING.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(STAGING))

    # 清理 staging
    shutil.rmtree(STAGING)

    size_kb = OUT_ZIP.stat().st_size / 1024
    print(f"[OK] {OUT_ZIP}")
    print(f"     size: {size_kb:.1f} KB")


if __name__ == "__main__":
    main()
