"""图区域与图注文本的附加回归测试(2026-08-21 第二轮修复)。

- 图区域: 页头装饰(logo/横幅)与图隔着大段空白, 不得混入图区域
  (AMCANet Fig.1 曾把整页首页渲染成缩略图);
- 图注文本: 无空格拼接的 caption 重建为可读形式, 且不拆散模型名。
"""

from types import SimpleNamespace

from app.paper.figures import _figure_region, _restore_caption_spacing


def test_figure_region_excludes_page_header():
    """页头横幅(rect y 3-37)距图(y 246-395)很远, 区域应从图本身开始。"""
    page = SimpleNamespace(
        width=612,
        height=792,
        images=[{"x0": 317, "top": 246, "x1": 553, "bottom": 395}],
        rects=[{"x0": 48, "top": 3, "x1": 564, "bottom": 37}],
        lines=[],
        curves=[],
        extract_words=lambda: [],
    )
    cap = {"x0": 317.2, "x1": 350.0, "y0": 400.7, "y1": 420.6}
    region = _figure_region(page, cap, 0.0)
    assert region["y0"] > 200, f"区域应从图本身开始, 实际 y0={region['y0']}"
    assert region["y1"] < 401
    assert region["x0"] > 300  # 图在右栏


def test_caption_spacing_reconstruction():
    s = _restore_caption_spacing
    assert s("Figure1.Manga109datasetforupscalingfactor×4.") == (
        "Figure 1. Manga 109 datasetforupscalingfactor×4."
    )
    assert s("Table4.Ablationstudy.WetrainallmodelsonDF2K,andtest") == (
        "Table 4. Ablationstudy. WetrainallmodelsonDF 2K,andtest"
    )


def test_caption_spacing_keeps_model_names():
    """模型名(ViT/AMCANet/SwinIR 等纯字母串)不得被驼峰拆分。"""
    s = _restore_caption_spacing
    assert "ViT" in s("Table3.Parameter,FLOPs,MemoryandRunningTimecompari-sonwithCNN-basedandViT-based")
    assert "AMCANet" in s("Figure2.NetworkarchitectureoftheproposedAMCANet.Themodeliscomposedofasha")
    assert s("DF2K") == "DF 2K"  # 数字块后的短字母尾保持相连
