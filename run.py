"""
启动脚本 — 在 import 任何项目模块之前设置 OpenBLAS 环境变量。

用法：
    python run.py

原因：
    numpy 2.4.x 的 OpenBLAS 在 Windows 上有内存分配问题，
    必须在 import numpy/faiss 之前设置 OPENBLAS_NUM_THREADS=1。
"""

import os
import sys

# ==== 必须在所有 import 之前设置 ====
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

# ==== 验证 ====
if os.environ.get("OPENBLAS_NUM_THREADS") != "1":
    print("ERROR: Cannot set OPENBLAS_NUM_THREADS", file=sys.stderr)
    sys.exit(1)

# ==== 启动 ====
if __name__ == "__main__":
    import uvicorn
    from app.config.settings import settings

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        reload_excludes=["tests", "docs", "data", "logs", ".pytest_tmp_*", "__pycache__"],
    )
