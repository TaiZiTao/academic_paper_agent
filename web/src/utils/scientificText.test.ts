import { describe, expect, it } from "vitest";

import { markdownToHtml, parseScientificText, renderScientificMath } from "./scientificText";

describe("parseScientificText", () => {
  it("separates inline LaTeX from surrounding prose", () => {
    expect(parseScientificText("通道数为 \\( C \\)，缩放因子为 \\( s \\)。")).toEqual([
      { kind: "text", content: "通道数为 " },
      { kind: "math", content: "C", display: false },
      { kind: "text", content: "，缩放因子为 " },
      { kind: "math", content: "s", display: false },
      { kind: "text", content: "。" },
    ]);
  });

  it("extracts a display equation and its right-aligned number", () => {
    const segments = parseScientificText(
      "映射关系：\n\\[\nX_0 = f_{\\text{encoder}}(I_{\\text{LR}}) \\in \\mathbb{R}^{H \\times W \\times C} \\quad (1)\n\\]",
    );

    expect(segments).toEqual([
      { kind: "text", content: "映射关系：\n" },
      {
        kind: "math",
        content: "X_0 = f_{\\text{encoder}}(I_{\\text{LR}}) \\in \\mathbb{R}^{H \\times W \\times C}",
        display: true,
        equationNumber: "(1)",
      },
    ]);
  });

  it("keeps unmatched delimiters as readable text", () => {
    expect(parseScientificText("公式未闭合：\\( X_0")).toEqual([
      { kind: "text", content: "公式未闭合：\\( X_0" },
    ]);
  });

  it("renders valid math and falls back cleanly for invalid LaTeX", () => {
    expect(renderScientificMath("X_0 \\in \\mathbb{R}^{H \\times W}", true)).toContain("katex-display");
    expect(renderScientificMath("\\notARealCommand{", false)).toBeNull();
  });

  it("recognizes numbered plain-text equations from translated PDF content", () => {
    const segments = parseScientificText(
      "过程如下：  \nQ = XWQ, K = XWK, V = XVW, A = XdWA　(6)  \n继续说明。",
    );

    expect(segments.filter((segment) => segment.kind === "math")).toEqual([
      {
        kind: "math",
        content: "Q = X W_Q, K = X W_K, V = X W_V, A = X_d W_A",
        display: true,
        equationNumber: "(6)",
      },
    ]);
  });

  it("reattaches a detached square root to the following attention equation", () => {
    const segments = parseScientificText(
      "√  \nMcoarse = AKT / C, P = SoftMax(Mcoarse)·V　(7)",
    );

    expect(segments).toEqual([
      {
        kind: "math",
        content: "M_{\\mathrm{coarse}} = \\frac{A K^{T}}{\\sqrt{C}}, P = \\operatorname{SoftMax}(M_{\\mathrm{coarse}}) \\cdot V",
        display: true,
        equationNumber: "(7)",
      },
    ]);
  });

  it("restores common subscripts, transpose marks, and Greek symbols in legacy equations", () => {
    const mathSegments = parseScientificText(
      "Pi = αPi−1 + (1 − α)Pi　(8)\nKp = PWKp, Vp = PWVp　(9)\n√\nMfine = Q(Kp + A)T / C, Xp = SoftMax(Mfine)·Vp　(10)",
    ).filter((segment) => segment.kind === "math");

    expect(mathSegments).toEqual([
      {
        kind: "math",
        content: "P_i = \\alpha P_{i-1} + (1 - \\alpha) P_i",
        display: true,
        equationNumber: "(8)",
      },
      {
        kind: "math",
        content: "K_p = P W_{K_p}, V_p = P W_{V_p}",
        display: true,
        equationNumber: "(9)",
      },
      {
        kind: "math",
        content: "M_{\\mathrm{fine}} = \\frac{Q(K_p + A)^{T}}{\\sqrt{C}}, X_p = \\operatorname{SoftMax}(M_{\\mathrm{fine}}) \\cdot V_p",
        display: true,
        equationNumber: "(10)",
      },
    ]);
  });

  it("converts HTML-style subscript and superscript tags in numbered equations", () => {
    const mathSegments = parseScientificText(
      "X<sub>coarse</sub> = WSA(X<sub>p</sub>) + CSA(X<sub>p</sub>, M<sub>coarse</sub><sup>jk</sup>) + X<sub>p</sub>  (13)",
    ).filter((segment) => segment.kind === "math");

    expect(mathSegments).toEqual([
      {
        kind: "math",
        content: "X_{\\mathrm{coarse}} = WSA(X_{p}) + CSA(X_{p}, M_{\\mathrm{coarse}}^{jk}) + X_{p}",
        display: true,
        equationNumber: "(13)",
      },
    ]);
  });

  it("renders HTML-style indexed variables as inline math inside prose", () => {
    expect(parseScientificText("利用 M<sub>fine</sub><sup>jk</sup> 引导 X<sub>coarse</sub>。"))
      .toEqual([
        { kind: "text", content: "利用 " },
        { kind: "math", content: "M_{\\mathrm{fine}}^{jk}", display: false },
        { kind: "text", content: " 引导 " },
        { kind: "math", content: "X_{\\mathrm{coarse}}", display: false },
        { kind: "text", content: "。" },
      ]);
  });

  it("restores indices lost from legacy coarse-to-fine equations", () => {
    const mathSegments = parseScientificText(
      "X = WSA(X ) + CSA(X , M ) + X  (13)\n"
      + "X = FFN(LN(X )) + X  (14)\n"
      + "X = WSA(X ) + CSA(X , M ) + X  (15)\n"
      + "X = FFN(LN(X )) + X  (16)",
    ).filter((segment) => segment.kind === "math");

    expect(mathSegments.map((segment) => segment.content)).toEqual([
      "X_{\\mathrm{coarse}} = WSA(X_p) + CSA(X_p, M_{\\mathrm{coarse}}) + X_p",
      "X_{\\mathrm{coarse}} = FFN(LN(X_{\\mathrm{coarse}})) + X_{\\mathrm{coarse}}",
      "X_{\\mathrm{fine}} = WSA(X_{\\mathrm{coarse}}) + CSA(X_{\\mathrm{coarse}}, M_{\\mathrm{fine}}) + X_{\\mathrm{coarse}}",
      "X_{\\mathrm{fine}} = FFN(LN(X_{\\mathrm{fine}})) + X_{\\mathrm{fine}}",
    ]);
  });

  it("extracts a \\tag equation number from display LaTeX", () => {
    const segments = parseScientificText(
      "$$ X_{\\text{coarse}} = \\text{WSA}(X_p) + \\text{CSA}(X_p, M_{\\text{coarse}}) + X_p \\tag{13} $$",
    );

    expect(segments).toEqual([
      {
        kind: "math",
        content: "X_{\\text{coarse}} = \\text{WSA}(X_p) + \\text{CSA}(X_p, M_{\\text{coarse}}) + X_p",
        display: true,
        equationNumber: "(13)",
      },
    ]);
  });

  it("parses single-dollar inline math without dropping boundary characters", () => {
    const mathSegments = parseScientificText(
      "开销仅为 $1/d^2$ 的 $X \\in \\mathbb{R}^{H \\times W \\times C}$ 前提",
    ).filter((segment) => segment.kind === "math");

    expect(mathSegments.map((segment) => segment.content)).toEqual([
      "1/d^2",
      "X \\in \\mathbb{R}^{H \\times W \\times C}",
    ]);
  });

  it("spaces control words glued to letters so KaTeX can render them", () => {
    const mathSegments = parseScientificText(
      "P<sub>i</sub>=αP<sub>i−1</sub>+(1−α)P<sub>i</sub> (8)",
    ).filter((segment) => segment.kind === "math");

    expect(mathSegments).toEqual([
      {
        kind: "math",
        content: "P_{i}=\\alpha P_{i-1}+(1-\\alpha)P_{i}",
        display: true,
        equationNumber: "(8)",
      },
    ]);
    expect(renderScientificMath(mathSegments[0].content, true)).not.toBeNull();
  });

  it("matches canonical fine equation with detached square root even when indices use HTML tags", () => {
    const mathSegments = parseScientificText(
      "√\nM<sub>fine</sub>=Q(K<sub>p</sub>+A)<sup>T</sup>/ C, X<sub>p</sub>=SoftMax(M<sub>fine</sub>)·V<sub>p</sub> (10)",
    ).filter((segment) => segment.kind === "math");

    expect(mathSegments).toEqual([
      {
        kind: "math",
        content: "M_{\\mathrm{fine}} = \\frac{Q(K_p + A)^{T}}{\\sqrt{C}}, X_p=\\operatorname{SoftMax}(M_{\\mathrm{fine}}) \\cdot V_p",
        display: true,
        equationNumber: "(10)",
      },
    ]);
  });

  it("converts superscript or subscript tags following a closing group", () => {
    const mathSegments = parseScientificText("Y=(Z)<sup>2</sup>+W<sub>k</sub> (1)").filter(
      (segment) => segment.kind === "math",
    );

    expect(mathSegments).toEqual([
      {
        kind: "math",
        content: "Y=(Z)^{2}+W_{k}",
        display: true,
        equationNumber: "(1)",
      },
    ]);
  });
});
describe("markdownToHtml", () => {
  it("escapes HTML and keeps plain text unchanged", () => {
    expect(markdownToHtml("普通中文文本 <script>alert(1)</script>")).toBe(
      "普通中文文本 &lt;script&gt;alert(1)&lt;/script&gt;",
    );
  });

  it("renders bold, italic, inline code and headings", () => {
    expect(markdownToHtml("**重要**与*斜体*及`code`")).toBe(
      "<strong>重要</strong>与<em>斜体</em>及<code>code</code>",
    );
    expect(markdownToHtml("### 小结\n内容")).toBe("<h3>小结</h3>\n内容");
  });

  it("groups consecutive list items into ul/ol", () => {
    expect(markdownToHtml("- 第一点\n- 第二点\n\n1. 甲\n2. 乙")).toBe(
      "<ul><li>第一点</li><li>第二点</li></ul>\n\n<ol><li>甲</li><li>乙</li></ol>",
    );
  });

  it("does not touch $ math delimiters (handled by math parser separately)", () => {
    expect(markdownToHtml("损失函数为 $L = \\|x - y\\|^2$")).toBe(
      "损失函数为 $L = \\|x - y\\|^2$",
    );
  });
});

