import { describe, expect, it } from "vitest";

import { stripVisualRegions } from "./paperVisualContent";

describe("stripVisualRegions", () => {
  it("removes translated figure labels and caption while preserving surrounding prose and formulas", () => {
    const content = `前文介绍了级联提示块的整体设计。
深层特征提取
编码器
α α α
P₁ P₂ P₃
vnoC vnoC
1 − α 1 − α
BPC BPC BPC
(a) PromptSR总体结构示意图
粗到细提示（Coarse-to-Fine Prompting）
elacsnwoD
noitnettA
(b) 级联提示块（CPB）
图2：（a）PromptSR、（b）GAPL和（c）LPL的详细架构。
后文继续解释级联提示如何扩大感受野。
Q = XWQ, K = XWK　(6)`;

    const cleaned = stripVisualRegions(content);

    expect(cleaned).toContain("前文介绍了级联提示块的整体设计。");
    expect(cleaned).toContain("后文继续解释级联提示如何扩大感受野。");
    expect(cleaned).toContain("Q = XWQ, K = XWK　(6)");
    expect(cleaned).not.toContain("vnoC");
    expect(cleaned).not.toContain("elacsnwoD");
    expect(cleaned).not.toContain("图2");
  });

  it("removes table rows but preserves the following section", () => {
    const content = `实验比较了多种轻量级超分辨率模型。
表1：基准数据集上的定量比较。
方法 参数量 PSNR SSIM
SwinIR 897K 26.47 0.7980
PromptSR 779K 27.02 0.8116
A. 实验设置
所有模型均在DIV2K数据集上训练。`;

    const cleaned = stripVisualRegions(content);

    expect(cleaned).toContain("实验比较了多种轻量级超分辨率模型。");
    expect(cleaned).toContain("A. 实验设置");
    expect(cleaned).toContain("所有模型均在DIV2K数据集上训练。");
    expect(cleaned).not.toContain("SwinIR 897K");
  });

  it("leaves content without figure or table captions unchanged", () => {
    const content = "A. 网络架构\n正文段落。\nX = f(I)　(1)";

    expect(stripVisualRegions(content)).toBe(content);
  });

  it("removes table rows under a roman-numeral Chinese caption", () => {
    const content = `细粒度提示（Fine Prompting）。此后，我们利用计算得到的细粒度相似性图划分像素令牌。
表I：轻量级超分辨率任务上与当前最先进方法的定量对比（PSNR/SSIM）。
Set5
方法 尺度 #参数
IDN[55] ×2 553K 37.83 0.9600 33.30
CARN[5] ×2 1,592K 37.76 0.9590 33.52
我们提出的更新过程被限定于每个RG内部，避免跨不同RG更新。`;

    const cleaned = stripVisualRegions(content);

    expect(cleaned).toContain("细粒度提示");
    expect(cleaned).toContain("我们提出的更新过程");
    expect(cleaned).not.toContain("表I");
    expect(cleaned).not.toContain("IDN[55]");
    expect(cleaned).not.toContain("Set5");
  });

  it("removes table rows under an English roman-numeral caption", () => {
    const content = `The experiment compares lightweight super-resolution models.
Table I: Quantitative comparison on benchmark datasets.
Method Params PSNR SSIM
SwinIR 897K 26.47 0.7980
We train all models on DIV2K and report results on five benchmarks.`;

    const cleaned = stripVisualRegions(content);

    expect(cleaned).toContain("experiment compares");
    expect(cleaned).toContain("We train all models");
    expect(cleaned).not.toContain("Table I");
    expect(cleaned).not.toContain("SwinIR 897K");
  });

  it("removes a strong reversed-label block even when its caption was lost", () => {
    const content = `前文介绍了全局锚点提示的整体设计。
A：锚点 P：提示
vnoC
elffuhS
-lexiP
dezirogetacnU
gnitpmo
rP ezirogetaC rohcnA
以及（b）CPB核心组件的结构、（c）提示的结构
由加权因子控制更新
且LPL显著扩大感受野。
后文继续解释全局注意力的具体计算过程。`;

    const cleaned = stripVisualRegions(content);

    expect(cleaned).toContain("前文介绍了全局锚点提示的整体设计。");
    expect(cleaned).toContain("后文继续解释全局注意力的具体计算过程。");
    expect(cleaned).not.toContain("vnoC");
    expect(cleaned).not.toContain("ezirogetaC");
  });
});
