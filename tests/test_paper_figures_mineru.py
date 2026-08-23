"""MinerU content_list 解析回归测试。

验证 0-1000 归一化 bbox → PDF 点坐标的换算、caption 提取、页码转换,
以及 header/footer 图片与非法项的过滤。
"""

import pymupdf

from app.paper.figures import _mineru_content_to_regions


def _make_pdf(path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)  # 1 页
    doc.new_page(width=612, height=792)  # 第 2 页
    doc.save(path)
    doc.close()


def test_mineru_content_parsing(tmp_path):
    pdf = tmp_path / "paper.pdf"
    _make_pdf(pdf)
    data = {
        "content_list": [
            # 图: bbox 归一化 0-1000, 第 1 页(0 基)
            {
                "type": "image",
                "bbox": [100, 200, 500, 400],
                "page_idx": 0,
                "image_caption": ["Figure 1. Manga 109 dataset"],
                "img_path": "images/0.jpg",
            },
            # 表: 第 2 页
            {
                "type": "table",
                "bbox": [50, 300, 950, 600],
                "page_idx": 1,
                "table_caption": ["Table 1. Comparison of methods"],
            },
            # 页眉图: 应被过滤
            {"type": "header_image", "bbox": [0, 0, 1000, 50], "page_idx": 0},
            # 无 bbox / 越界页码: 应被过滤
            {"type": "image", "page_idx": 0},
            {"type": "image", "bbox": [0, 0, 10, 10], "page_idx": 99},
        ]
    }
    regions = _mineru_content_to_regions(pdf, data)
    assert len(regions) == 2

    fig = regions[0]
    assert fig.kind == "figure"
    assert fig.page == 1
    assert fig.caption == "Figure 1. Manga 109 dataset"
    # 100/1000 * 612 = 61.2, 200/1000 * 792 = 158.4, 500/1000*612 = 306, 400/1000*792 = 316.8
    assert abs(fig.x0 - 61.2) < 1 and abs(fig.y0 - 158.4) < 1
    assert abs(fig.x1 - 306.0) < 1 and abs(fig.y1 - 316.8) < 1

    tab = regions[1]
    assert tab.kind == "table"
    assert tab.page == 2
    assert tab.caption == "Table 1. Comparison of methods"
    assert tab.x1 > 500  # 通栏表范围
