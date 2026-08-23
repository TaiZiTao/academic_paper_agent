"""章节翻译中的图片、表格文本过滤测试。"""

from app.paper.content_filter import strip_visual_regions


def test_strip_visual_regions_removes_figure_labels_and_caption_but_keeps_prose_and_equation():
    source = """The preceding paragraph explains how the prompted feature captures global information.
Deep Feature Extraction
Encoder
Conv Conv Conv
P1 P2 P3
Downscale
Attention
(a) Overall architecture of PromptSR
(b) Cascade Prompting Block
Fig. 2: The detailed architecture of PromptSR and its prompting layers.
The following paragraph explains why the receptive field becomes global.
M = QK^T / sqrt(C) (7)"""

    cleaned = strip_visual_regions(source)

    assert "preceding paragraph" in cleaned
    assert "following paragraph" in cleaned
    assert "M = QK^T / sqrt(C) (7)" in cleaned
    assert "Deep Feature Extraction" not in cleaned
    assert "Downscale" not in cleaned
    assert "Fig. 2" not in cleaned


def test_strip_visual_regions_removes_table_caption_and_rows_but_keeps_following_section():
    source = """The experiment compares lightweight super-resolution models.
Table 1: Quantitative comparison on benchmark datasets.
Method Params PSNR SSIM
SwinIR 897K 26.47 0.7980
PromptSR 779K 27.02 0.8116
A. Experimental Settings
We train all models on DIV2K and report results on five benchmarks."""

    cleaned = strip_visual_regions(source)

    assert "experiment compares" in cleaned
    assert "A. Experimental Settings" in cleaned
    assert "We train all models" in cleaned
    assert "Table 1" not in cleaned
    assert "SwinIR 897K" not in cleaned


def test_strip_visual_regions_leaves_caption_free_content_unchanged():
    source = "A. Network Architecture\nThe model contains an encoder and a decoder.\nX = f(I) (1)"

    assert strip_visual_regions(source) == source


def test_strip_visual_regions_removes_strong_reversed_label_block_without_caption():
    source = """The preceding paragraph introduces the prompting module in detail.
A: Anchor P: Prompt
vnoC
elffuhS
-lexiP
dezirogetacnU
gnitpmorP
rohcnA
(b) Cascade Prompting Block
The following paragraph explains the global attention computation in detail."""

    cleaned = strip_visual_regions(source)

    assert "preceding paragraph" in cleaned
    assert "following paragraph" in cleaned
    assert "vnoC" not in cleaned
    assert "gnitpmorP" not in cleaned


def test_strip_visual_regions_removes_roman_numeral_table_caption_and_rows():
    source = """细粒度提示（Fine Prompting）。此后，我们利用计算得到的细粒度相似性图划分像素令牌。
表I：轻量级超分辨率任务上与当前最先进方法的定量对比（PSNR/SSIM）。
Set5
方法 尺度 #参数
IDN[55] ×2 553K 37.83 0.9600 33.30
CARN[5] ×2 1,592K 37.76 0.9590 33.52
我们提出的更新过程被限定于每个RG内部，避免跨不同RG更新。"""

    cleaned = strip_visual_regions(source)

    assert "细粒度提示" in cleaned
    assert "我们提出的更新过程" in cleaned
    assert "表I" not in cleaned
    assert "IDN[55]" not in cleaned
    assert "Set5" not in cleaned


def test_strip_visual_regions_removes_english_roman_numeral_caption():
    source = """The experiment compares lightweight super-resolution models.
Table I: Quantitative comparison on benchmark datasets.
Method Params PSNR SSIM
SwinIR 897K 26.47 0.7980
We train all models on DIV2K and report results on five benchmarks."""

    cleaned = strip_visual_regions(source)

    assert "experiment compares" in cleaned
    assert "We train all models" in cleaned
    assert "Table I" not in cleaned
    assert "SwinIR 897K" not in cleaned
