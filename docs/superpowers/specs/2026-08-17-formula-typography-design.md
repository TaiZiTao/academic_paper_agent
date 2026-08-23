# 公式排版期刊化设计(翻译工作台)

> 日期:2026-08-17
> 状态:已批准(用户确认方案二)
> 说明:本项目当前不是 git 仓库,本文档落盘后不执行 commit。

## 1. 目标

单篇论文精读的「章节翻译」工作台中,公式以独立卡片形式展示(EQ 标签、米白底色、细边框、棕红竖线),视觉上过于花哨,与整体「论文感」不协调。本设计将公式排版改为标准期刊风格(LaTeX 排版观感):

- 独立公式居中,编号 `(1)` 右对齐。
- 去掉 EQ 标签、卡片底色、边框、竖线等装饰。
- 行内公式与正文同色、同基线。
- 公式编号保留。

## 2. 范围

### 2.1 改动文件

仅 `web/src/components/paper/ScientificText.vue`(模板 + scoped 样式)。

### 2.2 明确不改

- `web/src/utils/scientificText.ts` 解析逻辑(公式编号提取、LaTeX 规范化、硬编码正则)一律不动,现有 vitest 单测零影响。
- `web/src/utils/paperVisualContent.ts` 不动。
- `web/src/components/paper/PaperTaskPanel.vue` 不动(其中 `:deep(.scientific-text) { min-width: 0 }` 与根类契约保持兼容)。
- 后端、数据、KaTeX 渲染方式均不动。

### 2.3 行为不变

- 同样的分段结果(prose / inline math / display math)。
- 同样的 KaTeX 渲染,同样的失败降级(渲染失败回退 `<code>` 显示原始文本)。

## 3. 组件结构变化

### 3.1 模板

行内公式(`display: false`)结构不动,仅调整样式。

独立公式(`display: true`)删除 `EQ` 标签元素(`.scientific-text__equation-mark`)与卡片式网格,改为:

```html
<span class="scientific-text__equation">
  <span class="scientific-text__equation-scroll">KaTeX 渲染结果</span>
  <span v-if="equationNumber" class="scientific-text__equation-number">(1)</span>
</span>
```

根类 `.scientific-text`、正文段落类 `.scientific-text__prose`、行内公式类 `.scientific-text__inline-math`、降级 `<code>` 结构保留。

### 3.2 删除的类

- `.scientific-text__equation-mark`(EQ 标签)。

## 4. 样式设计

### 4.1 独立公式

```css
.scientific-text__equation {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto; /* 公式列 + 编号列 */
  align-items: center;
  margin: 8px 0; /* 桌面端;移动端 6px */
  /* 无背景、无边框、无内边距 */
}
```

- `.scientific-text__equation-scroll`:`justify-self: center; min-width: 0; overflow-x: auto`,长公式可横向滚动且不挤占编号。
- `.scientific-text__equation-number`:颜色 `#7f8582`(与现页码一致),字号 `12px`,`(1)` 格式不变。
- `.katex-display { margin: 0 }` 与 `.katex { font-size: 1.05em }` 保留,仅作用于公式本身。

### 4.2 行内公式

- 颜色改为继承正文墨色(现正文 `#344b52`),去掉深色 `#173b47`。
- 去掉 `white-space: nowrap` 强制换行限制,垂直对齐 `baseline`,与中文正文同一基线。

### 4.3 降级代码块

- 底色弱化为极浅暖灰、文字色贴近正文,不再呈现为独立色块;不隐藏、不报错、不修改内容。

### 4.4 行首换行归一化

译文正文使用 `white-space: pre-wrap`,紧跟独立公式之后的文本段若以换行符开头,会渲染出一个约一行高的空行盒子,导致公式下方留白明显大于上方。在组件 computed 中做显示层归一化:

- 文本段紧跟独立公式时,去掉行首换行符(`/^\n+/`)。
- 若行首换行符数量 >= 2(段落分隔),保留一个 `\n`。
- 仅在 ScientificText.vue 内处理,不改动 `scientificText.ts`,不修改持久化数据。

### 4.5 移动端(<720px)

- 公式仍居中、编号右对齐,上下留白 `6px`。

## 5. 错误处理

- KaTeX 渲染失败(破损 LaTeX、中文混入公式等):与现状一致,回退 `<code>` 显示原始文本,仅外观更低调。

## 6. 测试与验证

- 现有 vitest 单测(`web/src/utils/scientificText.test.ts`、`web/src/utils/paperVisualContent.test.ts`)必须保持通过。
- 后端 pytest(含 `tests/test_paper_workspace_css.py`)不受影响。
- 验证命令:
  1. `pnpm test`(web/src/utils 单测)
  2. `pnpm run build`(vue-tsc 类型检查 + vite 构建)
  3. 启动前后端,打开翻译工作台人工检查公式排版。
- 不新增组件测试:项目无组件测试基建,引入 @vue/test-utils 超出本次范围。

## 7. 非目标(YAGNI)

- 不做公式高亮、引用跳转联动、公式导出等新功能。
- 不重构精读报告、问答等其他页面(它们不使用 ScientificText)。

---

## 8. 后续修复记录(2026-08-17,用户实测反馈)

1. **公式上下留白过大**:独立公式 margin 由 `14px` 收紧至 `8px`(移动端 `6px`)。
2. **公式下方空白明显大于上方**:根因是译文正文 `white-space: pre-wrap` 下,紧跟独立公式的文本段行首换行符会渲染成空行盒子。在 `ScientificText.vue` 的 computed 中做显示层归一化:去掉行首换行符,双换行(段落分隔)保留一个。
3. **公式 (8) 显示为灰色代码块**:根因是 `α`→`\alpha` 后紧贴后续字母(`\alphaP`),KaTeX 当作未知命令导致渲染失败。修复:控制序列 `\alpha` 后紧跟字母时补空格。
4. **公式 (10) 混入字面 `<sup>T</sup>` 标签**:根因是 LLM 译文用 HTML 标签写上下标,`htmlIndexedVariableToLatex` 只处理「字母紧跟标签」,标签跟在 `)` 后漏转;`√C` 规范特判也因此未触发。修复:
   - 规范方程(Mcoarse/Mfine + √C)先在去标签纯文本上匹配,带标签译文也能命中;
   - 新增组(右括号/方括号/花括号)后上下标的转换(`)A<sup>T</sup>` → `)A^{T}`)。
5. **公式下方出现横向滚动条**:长公式宽度超过列宽时 `overflow-x: auto` 产生滚动条。处理:保留滚动能力,隐藏滚动条外观(`scrollbar-width: none` + WebKit 隐藏)。
6. **LLM 改用 `\tag{N}` 输出公式编号导致编号与公式尾部重叠**:LLM 译文以 `$$...\tag{13}$$` 输出,KaTeX 的 `\tag` 渲染在公式内部右端,窄列下与公式尾部重叠。修复:`parseScientificText` 识别 `\tag{N}` 并提取为 `equationNumber`(模板独立右对齐渲染),`\tag` 从 KaTeX 内容中剥离。
7. **公式与编号重叠 + 公式下方空行过大**(用户实测):
   - 布局重做:公式列 `minmax(0,1fr)` + 编号列 `auto` + 列间隙 `14px`;滚动容器拉伸占满列宽,`.katex-display` 用 `width:max-content; margin-inline:auto`(窄时居中,宽时向右溢出被裁剪,不再压编号)。
   - 归一化加强:显示公式边界处文本段的**行首与行尾**换行符全部去掉(公式自身 margin 提供间距),消除 pre-wrap 渲染出的空行盒子,公式与段落间距收敛到约 `13–14px`。

修复涉及 `web/src/utils/scientificText.ts`(解析层)与 `web/src/components/paper/ScientificText.vue`(布局/归一化),新增 vitest 用例;当前 20 条前端单测通过,vue-tsc + vite 构建通过。

---

## 9. 图表展示与标题翻译(2026-08-18)

章节翻译中展示论文的图和表:图片从 PDF 按区域裁剪成 PNG,标题翻译成中文。

- 新增 `app/paper/figures.py`:按页检测图/表标题(`Fig. N` / `TABLE N` / `图 N` / `表 N`),表取标题下方表格网格边界(pdfplumber `find_tables`),图取标题上方图元(内嵌图片 + 矢量矩形)并集;PyMuPDF 按区域渲染 PNG。
- 新增 `paper_figures` 表 + `PaperFigure` 模型;解析入库时检测渲染(失败不阻断),删除论文时级联清理图片与记录。
- API:`GET /papers/{id}` 返回 `figures`;`GET /papers/{id}/figures/{id}/image` 提供图片。
- 翻译章节时同步翻译章节页范围内的图表标题(仅未翻译的,持久化),SSE `figure` 事件推送。
- 前端:译文流中按页码插入图表卡片(同页图表在前),显示裁剪图 + 中文标题(回退原文)+ 页码跳转。
- 归属规则:每个图表只归属**第一个覆盖其页码的章节**,避免跨章节重复显示(如 Fig. 2 只出现在 A,不出现于 B)。
### 9.1 表格裁剪修复记录(2026-08-18,用户实测反馈)

用户反馈 TABLE IV–VII(第 11 页)裁剪错误:右栏文字混入、两张表合并成一张、表下正文被裁进来。根因不是单张表的个案,而是**三线表(仅横向规则线)整体检测失效**:

- **根因 1:pdfplumber `page.lines` 坐标是 bottom-origin**(y 从页面底部起算),而 `extract_words`/`page.rects` 是 top-origin。三线表的 3 条规则线(顶线/中隔线/底线)因此被解读到错误位置,`find_tables()` 对无竖线的三线表也返回 0 或碎片 → 旧逻辑退化为「标题下方固定高度 +220pt」,必然越界。
- **根因 2:标题行可能混入同高度另一栏文字**(如 TABLE VII 首行混入右栏 `Pi`,`x1` 被撑到 562),导致栏判断失败、右栏文字进入裁剪框。

修复(`app/paper/figures.py`):

1. **规则线定界**:`_hlines_in_band` 取标题下方区间内的横向线簇,对「top-origin / bottom-origin」两种解释都尝试,取候选多者(平局取更贴近标题者);`_table_region` 用线簇顶线→底线定 y,线簇 + 表格文字并集定 x(排除另一栏文字)。
2. **结构钳制**:表区 y1 硬性 ≤ 下一个同栏标题顶部 −4(防合并下一张表/正文);x 钳制在标题所在栏内(`_column_bounds`,防止右栏文字)。
3. **标题 x1 修正**:`_line_captions` 首行按 >8pt 词间隙截断到第一栏(不再用合并行 x1),续行吸收后仍钳制在首行所在栏内;`_next_caption_top` 提供同栏下一标题位置。
4. 无规则线时依次回退:表格网格检测 → 固定高度 + 结构钳制(带栏/下界钳制)。

验证:7 张表(TABLE I/II/III/IV/V/VI/VII)重渲染后均为单张完整表格,右栏文字、相邻表、下方正文均不再混入;18 张图/表标题全部完整(含多行续接);5 条变更标题重新翻译入库。后端 pytest 18 passed。
### 9.2 图片裁剪文字泄漏修复 + 裁剪审查(2026-08-18,用户实测反馈)

用户反馈 Fig.9 裁剪图把右栏正文文字一起截了进来,并希望「以后截图后审查一下有没有把论文里的文字截进去」。

- **根因**:Fig.9 是左栏图(x 43–295),但其标题首行与右栏正文同一行高合并(同 y 桶),标题 `x1` 被撑到 563;区域 x1 = max(内容 295, 标题 563)+12 = 575,右栏文字(312 起)整段进入裁剪框。
- **修复 1(标题列判断)**:`_line_captions` 的 x1 钳制改为按**截断后的首行宽度**分类(而非合并行 x1):首行被词间隙截断、或截断后仍在单栏内时,`x1` 钳制在本栏内(mid−4)。
- **修复 2(图区域列钳制)**:新增 `_clamp_to_column`,图区域 x 范围同样钳制在标题所在栏内(x1 ≤ mid−2 / x0 ≥ mid+2),杜绝右栏文字入图。
- **审查机制(用户要求)**:新增 `figures.audit_regions(pdf_path, regions)` —— 对每个单栏区域检查 ① 区域内是否出现另一栏的文字词(词中心在区域内且与区域栏相反)、② 区域横向是否越过中线 >4pt;返回问题清单。裁剪/重渲染脚本渲染后可调用,提交前审查。

验证:18 个区域审查全部通过(NO LEAKS);Fig.9 重新裁剪为 31.1,377.4,304.0,556.1,肉眼核对仅剩 3×3 对比网格,右栏文字不再出现;Fig.3、Fig.10 等单栏图同步钳制到栏内;后端 pytest 18 passed;后端已重启(pwsh-30)。
## 10. 工作台五功能优化(2026-08-18)

按用户确认的顺序, 依次完成五个功能(每项均前后端联调 + 浏览器验证):

1. **论文问答富渲染 + 追问建议**:
   - 后端 qa prompt 的 JSON 输出新增 `suggestions`(2~3 条追问), 存入 `paper_messages.suggestions_json`(新增列), 详情接口与 done 事件暴露;
   - LLM 客户端加 `timeout=120` + `_ainvoke_with_retry`(连接错误/超时重试 3 次退避), 解决达摩院接口偶发 Connection error;
   - `scientificText.ts` 新增 `markdownToHtml`(转义 + 行内加粗/斜体/代码 + 列表/标题), `ScientificText.vue` 正文段改用 v-html 渲染;问答答案与 stream-card 用 ScientificText 渲染, 答案下方显示可点击的追问 chips(点击直接追问)。
2. **新增论文审稿(标签 06)**:
   - 后端 `review` 任务类型: 结构化 prompt(概要/贡献/优点/主要/次要问题/分项评价/修改建议/推荐意见+评分), `_normalize_review` 规整入库;检索 k=20 + 泛化输入(如"论文审稿")退化为论文标题检索, 保证看到全文证据;
   - 前端 `PaperReview.vue`: 评分 + 推荐意见彩色徽章(Accept/Minor/Major/Reject)、概要、贡献、优点、问题列表、分项评价网格、修改建议、引用列表, 支持页码跳转。
3. **学习笔记结构化 + 可编辑保存**:
   - notes prompt 改为 Markdown 小节(核心概念/方法流程/关键结论/待查疑问/我的批注), 复用 ScientificText 渲染;
   - 新增 `PUT /papers/{id}/artifacts/{artifact_id}`(更新 content_text/content_json), 前端 `PaperNotes.vue` 提供编辑/保存/取消。
4. **汇报提纲逐页卡片 + 篇幅选择 + 导出**:
   - 后端 `presentation` 结构化 prompt(slides: title/bullets/notes, 上限 12 页), `_normalize_presentation` 生成 slides + markdown 双份入库;
   - 前端 `PaperPresentation.vue`: 逐页卡片(序号/标题/要点/演讲备注), 篇幅选择(5/15/30 分钟), 导出 Markdown 与 PPTX(pptxgenjs 4.0.1)。
5. **精读报告公式/图表渲染 + Markdown 导出**:
   - `PaperReport.vue` 各节与术语改用 ScientificText 渲染(公式/上下标), 底部新增论文图表画廊(18 张图/表, 点击跳原文页码), 顶部新增"导出 Markdown"(含全部小节/术语/引用)。

验证: 前端 vitest 25 passed + vue-tsc/vite build 通过; 后端 pytest 26 passed; 浏览器实测: 问答答案渲染 + 追问 chips、审稿意见单(6/10 Major Revision)、笔记编辑保存、提纲 8 页卡片 + md/pptx 下载、报告 8 节 + 18 图 + md 下载。
### 10.2 删除学习笔记功能(2026-08-18, 用户反馈与精读报告冲突)

- 删除: 后端 `notes` 任务类型(valid_types/schema/prompt/title_map)、PUT artifacts 编辑接口(service `update_artifact_content` + router 路由 + `ArtifactUpdateRequest` schema); 前端 04 标签、PaperTaskPanel meta、`PaperNotes.vue` 组件、`updateArtifactContent` API、saved-artifact 展示(已无使用方); DB 中 notes 产物/任务清理。
- 保留: 汇报提纲里的演讲备注(slide.notes)不受影响。
- 标签重排: 汇报提纲 04, 论文审稿 05。
- 顺带同步 4 个因早前报告结构/引用语义改动而失败的测试用例(REPORT_FIELDS 5 节、max_tokens 8192、页码纠正不重试)。全量 pytest 197 passed。

### 10.1 精读报告结构调整(2026-08-18, 用户反馈)

用户要求精读报告聚焦「来龙去脉与价值判断」, 实验等细节降级。

- **报告字段**由 8 节精简为 4 节 + 术语(`REPORT_FIELDS`): `background`(研究背景与方向) / `motivation`(论文动机) / `existing_problems`(现有方法存在的问题) / `solution`(解决方案与创新点) / `terms`;
- **生成指令**重写: 四部分分别强调领域脉络与方向、作者要解决的核心矛盾、已有方法不足(尽量引用原文)、解决方案与现有工作的本质区别;
- **前端** `PaperReport.vue` 小节定义同步为 4 节; `_report_to_markdown` 标签同步; `nodes.py` 引用降级提示改挂到 `solution` 末尾(删除旧 `limitations` 引用);
- 论文 1 的报告已用新结构重新生成(脚本直连 report graph), 实测 4 节内容均为导读视角、现有方法问题带原文引用(chunk-5/6/21), 浏览器验证通过。
- **补充(用户反馈"写详细一点")**: 生成指令原限"每字段 80-160 字"导致每节只有一两句话; 放宽为"每字段 300~600 字、逻辑完整的段落(可 Markdown 加粗/列表)、用自己的话展开并引用原文支撑"。论文 1 重新生成后各节 381/515/577/735 字, 浏览器实测 4 节均为详实段落。
- **补充(用户反馈英文引用未翻译)**: 指令新增引用规则——正文只摘录 ≤15 个英文单词的短语并在其后给出中文解释, 长句一律中文转述, 英文术语可保留; 引用列表(底部 citations)仍保留原文短句作为证据。重新生成后正文不再出现整段英文照抄, 每条引用都有中文说明。
- **补充(用户反馈"第一/第二/第三要换行")**: 指令新增排版规则——并列要点必须用 Markdown 列表(`1. `/`- `)逐条换行呈现, 说明句单独一行。重新生成后"现有方法存在的问题"等小节渲染为编号列表(每项加粗小标题 + 中文解释 + 短引用), 浏览器实测 3 个条目分行显示。
- **补充(用户反馈缺少 Introduction 贡献点)**: `REPORT_FIELDS` 增加 `contributions`(论文主要贡献), 指令要求提取论文在引言/摘要中明确陈述的贡献点(编号列表, 中文转述 + 原文支撑); 前端新增 05 节。同时把报告节点的 `max_tokens` 从 2048 提到 8192(加长后曾被截断导致 JSON 解析失败)。重新生成后"论文主要贡献"完整呈现引言的三项贡献(APs 构造 / CPB 级联块 / 五基准验证 + 0.78M 对标 12M), 浏览器实测 5 节渲染正常。
- **补充(用户反馈问答提交后问题不立即显示)**: 问答改为乐观渲染——提交时立即在本地插入用户问题气泡(`qaOptimistic`), 生成期间显示"正在检索论文证据并生成回答…"思考气泡, 答案以流式助手气泡展示, 完成后由后端消息替换(避免重复)。浏览器实测: 提交后 0.6s 内问题气泡出现, 最终 2 条消息 + 3 条追问 chips, 无重复。
- **补充(用户反馈引用"页码未核实"误伤充分证据)**: 引用校验器语义修正——引用原文在证据块中真实匹配即视为证据成立(`verified=True`), 页码缺失/越界时以证据块实际页码兜底(`page_corrected`/`page_inferred`), 不再整条判无效; 只有引用原文未找到(`quote_not_found`)/跨论文/缺块才降级。服务层: 只要有引用核实通过就不再追加"原文未提供充分证据"整段警告(失败单条由前端 ⚠ 标注), 全部未核实时才提示。实测用户问题"跟这个方法对比的有什么方法": 3 条引用中 2 条核实(p.6 TABLE I), 1 条原文转述未匹配单独标注, 答案不再挂证据不足警告。单测同步更新(31 passed)。

验证: 前端 vitest 25 passed; 后端 pytest 26 passed; 新旧字段并存时旧报告会显示"原文未提供充分证据"(重新生成即可)。
## 11. 第二篇论文(Dual-domain Modulation Network)图表检测泛化(2026-08-18)

用户上传新论文后反馈图表裁剪仍有问题。该论文排版与 PromptSR 不同(网格表/错层表头/矢量绘制图), 逐一诊断并泛化检测规则:

1. **图注被表头行污染**: TableV 的纯文字多列表头被并进图注, 导致区域起点错位。修复 `_is_table_content_row` 增加**多列词簇判定**(词间间隙 >8pt 形成 ≥3 簇); TableIII 的错层表头(数据集列头 x 从 274 起)又暴露出 x0 判定问题——通栏表的同栏判定改为**x 范围重叠**(`row.x1 > mid+20` 时用重叠而非 x0 接近)。
2. **规则线把下方内容吸进来**: 网格表的规则线是单元格碎片, `_hlines_in_band` 常找到下方正文/其他图表的线。修复: **find_tables 优先**(y0 距图注底 20pt 内即用网格结果), 规则线仅作三线表回退——TableII(83.6–407.4)、TableIV(214.4–288.5)、TableV(95.5–166.4)、TableIII(83.5–162.6) 全部修正。
3. **矢量绘制的图识别不到**: Figure10 的结构图是 73 条曲线(curves), 不在 images/rects 里 → 回退成大块重叠。修复 `_figure_region` **加入 lines/curves**(bottom-origin 翻转为 top-origin), 回退路径同时尊重 `upper_bound`。
4. **跨栏互相顶掉**: 右栏 TableVI 的底部(699)把左栏 Figure10/11 的内容全顶掉。修复 detect_figures **按栏跟踪 previous_bottom**(左/右/通栏三套上界)。

验证: Paper1(PromptSR)18 区域与修正前完全一致(零回归); Paper2 的 p9 五个图表不再重叠, TableV/II/III/IV 均为完整表格, Figure9 四联图/Figure10 结构图+小表/Figure11 对比图逐张目检正确; 18 个图注全部重新翻译; 全量 pytest 197 passed; 后端已重启。
### 11.1 残余两类"文字混入"修复(2026-08-18, 用户截图反馈)

用户截图反馈两处裁剪仍混入其他文字:

1. **Figure11 顶部混入 Figure10 的图注文字**: Figure10 的图注(410.2–432.4)在其区域底部(408.2)之下, Figure11 的区域从 408.2 开始把这段图注吞了进来。修复: 按栏下界从"区域底部"改为 **max(区域底部, 图注底部)**——上界要覆盖前项的完整图注。Figure11 现为 432.4–529.9(纯 5×2 对比图, 目检通过)。
2. **TableVI 底部混入正文**: 规则线把 Figure9 曲线网格线/正文行当成表格底线, 区域一度到 699.1(含 200+pt 正文)。新增 `_table_body_bottom`: 按栏扫描表格数据行(多列词簇/短行引用/数字), 遇到长正文行(≥45 字符或 ≥15 字符且字母数字多)即停, 规则线下界越过最后一行 15pt 时收紧。踩过的坑: 勾选符号行(无数字)导致误停、右栏表格行与左栏图注同 y 桶合并导致误判——分别用"短符号行不终止"和"按栏过滤词后再判定"解决; 公式密集行(无空格)用 `[N]` 引用/数字识别。TableVI 现为 381.6–458.4(表头+5 行, 正文 480.6 起已排除)。

验证: Paper1 的 p11 四张表/TABLE II 等全部恢复正确(之前一度被误裁), Fig.5 上界顺带修正为 423.7(排除 Fig.4 图注); Paper2 全部 18 区域正确; 全量 pytest 197 passed; 两张论文 6 处 bbox 已重渲染入库; 后端已重启。
### 11.2 裁剪质量自动化(用户反馈"不能每传一篇就手动修")

把"人工诊断修复"变成"上传即自动处理 + 告警":

1. **检测期自愈 `_self_heal_region`**: 对任意版式生效的通用兜底——
   - 图区域里若混入其他图表的图注文字(识别 `Figure 10` / `TABLE IV` 等标签+编号, 并沿行距 <14pt 收集整个图注块), 自动把上界压到图注块之下;
   - 表格区域底部若伸进正文, 按栏扫描表格数据行(多列词簇/短行引用/数字, 长正文行 ≥45 字符即停), 越过最后一行 25pt 时收紧下界。
2. **`audit_regions` 增强**: 新增"图区域混入其他图注文字"检查(与自愈同信号), 与既有的跨栏泄漏/越界检查一起构成裁剪质量清单。
3. **上传即审查**: `process_paper` 检测图表后自动跑 `audit_regions`, 可疑项(loguru warning: 序号/类型/标题/原因)写入日志, 无需用户截图反馈。

自愈经模拟验证: 旧 bug 的 Figure11 区域(408.2)自动修正到 434.4(排除整个 Figure10 图注), TableVI 区域(699.1)自动修正到 458.4(排除正文); 两篇论文实测零误伤(NO LEAKS), 全量 pytest 197 passed, 后端已重启。
## 12. 摘要提取修复(2026-08-19, 用户反馈"新上传的 PDF 没有 abstract")

根因: 摘要提取依赖章节推断找到一个干净的 "Abstract" 标题行 + pdfplumber 页面文本, 三篇论文各踩一个坑:

1. arXiv(IEEE 模板): "Abstract?正文" 同行粘连(标题不单独成行);
2. IEEE TMM: 首页文字层字形交错, pdfplumber 提取出 "rec A o b n s s t t..." 乱码(pymupdf 正常);
3. Elsevier: 标题是 "A B S T R A C T"(字母间空格)且与 "ARTICLE INFO"/"Keywords:" 挤在同一块。

修复(`app/paper/parser.py`):

- `_extract_abstract` 改用 **PyMuPDF 提取前两页文本**(对字体编码更鲁棒, 解决论文2乱码);
- 标题识别兼容多种写法: `Abstract`(任意前缀)/ `A B S T R A C T`(字母间空格)/ `摘要`;
- 截取到下一章节标题(Introduction / Keywords / Index Terms / 摘要 / 参考文献)为止;
- 清理期刊信息行与 `Keywords:` 标签, 去掉开头残留的特殊字符(em-dash 映射的 `?`), 上限 5000 字。

三篇论文摘要已回填(1834/1342/1359 字), API 验证通过; 全量 pytest 197 passed; 后端已重启。新上传的论文自动生效。
## 13. 章节推断误识作者名/伪标题修复(2026-08-19, 用户反馈 CHAPTER MAP 出现 "Y. Yang et al.")

Elsevier 论文的页眉作者名("Y. Yang et al.")被 `_LETTER_HEADING`(Y. + Yang et al.)误判为章节; 一并发现并修复多类伪标题:

1. **作者名行**: 含 `et al.` 结尾的行直接排除(注意: 最初的过宽正则 `fullmatch([A-Za-z]+)` 误伤 References/Conclusion 等单词标题, 已收窄为仅 et al. 判定);
2. **页眉重复行**: 跨页重复出现的标题视为页眉/页脚排除, 但已知顶层章节(References/Introduction/Conclusion 等)豁免——否则参考文献跨页时 "REFERENCES" 会被误删;
3. **正文句子**: 以虚词结尾(如 "4. This operation reduces the spatial resolution of the feature map to a")不是标题;
4. **参考文献条目**: 字母标题含逗号("L. Van Gool, ...")排除; 参考文献页上的其他行全部跳过;
5. **表格碎片**: 编号首段 >20("28.85 Ours...")或字母+数字碎片("M 3")排除。

三篇论文章节树重解析并回填 DB(18/15/25 节), API 验证通过; 全量 pytest 197 passed; 后端已重启。
## 14. 表格"标题在下方"版式检测修复(2026-08-19, 用户反馈 TIFF-CEM 论文 TABLE 卡片全是正文)

用户上传 TIFF-CEM 论文后, 4 张 TABLE 卡片全部裁到正文段落(如 "Table 4 reports the ablation results..."), 而 FIG 全部正常。逐页比对文字坐标与规则线后发现根因:

1. **版式差异**: 该论文(AAAI 风格)把表格 caption 放在表格**下方**(与图注同习惯), 而检测器假设 IEEE 常规(caption 在上方), 只向下搜索表格;
2. **下方误检**: 标题下方的正文区恰好有章节分割线(三线表误判为表格规则线簇), _hlines_in_band 双向取多者, 命中正文规则线 → 区域落在正文段落上;
3. **长方法名行漏判**: 修复方向后, _table_body_bottom 把 "DehazeFormer-M27.12±0.512..." / "TIFF-CEM(ours)27.28±0.455..." 两行判为非表格行(词间大间隙只有 1 个, 行长 ≥45 又不满足短行数字规则), self-heal 把下边界收窄到倒数第二行。

修复(app/paper/figures.py):

1. 新增 _table_region_above(): 在标题上方搜索同栏表格证据——≥2 条同栏规则线且底线距标题顶 ≤28pt, 或网格表底边距标题 ≤20pt; 区域上界钳制在栏上界(前一图表标题底), 下界止于标题顶 -2pt;
2. _table_region() 增加方向决策: 仅在下方**缺少紧贴证据**(网格 y0 距标题底 ≤20pt, 或首条规则线距标题底 ≤30pt)时启用上方候选, IEEE 常规版式不受影响;
3. _table_body_bottom() 增加数字密度规则(数字占比 >30% 即视为表格行), 兜住长方法名数字行;
4. detect_figures() 向 _table_region 传入栏上界, 并按方向钳制(y0 ≥ 标题底+2 或 y1 ≤ 标题顶-2)。

回归: PromptSR 18/18 区域不变; Dual-domain 17/18 不变, 1 处仅 x1 宽 3.4pt(y 界相同); MWAT-SR 本就无图表。重渲染 paper 4 并回填 DB, 后端重启, Playwright 验证 4 张 TABLE 卡片均显示真实表格(数字网格), 21 项后端测试通过。
## 15. 章节推断: 表格列名/参考文献条目/连写标题三连修(2026-08-19, 用户反馈 CHAPTER MAP 出现两个 Method 且 Introduction 含 Method 内容)

TIFF-CEM 论文的 CHAPTER MAP 出现两个假 "Method" 章节和一条参考文献条目 "G. E. 1991. Adaptive mixtures...", 且 Introduction(p1-5)吞掉了方法内容。逐行诊断三个根因:

1. **表格列名当章节**: p.5/p.6 的 "Method" 独立行是 Table 1/Table 2 的列名(前一行是表头 "ThinHaze ModerateHaze" / "RSID", 后一行是 "PSNR? FSIM? LPIPS?" 表头行), 论文里根本没有名为 Method 的章节;
2. **连写标题漏识别**: 该期刊模板提取文本把紧排标题连写("ProposedMethod"、"ExperimentalResults"), _SECTION_PATTERNS 的 method/experiments 模式要求单词间有空格, 匹配失败 → 真实方法章节(p.2-4)内容全部并入 Introduction(p1-5);
3. **参考文献续行当章节**: 参考文献条目 "Jacobs, R. A.; ... Hinton, G. E. 1991. Adaptive mixtures..." 的续行以 "G. E." 开头, 被 _LETTER_HEADING 当作字母编号标题, title_text 不含逗号(逗号在下一行)躲过已有逗号守卫。

修复(app/paper/parser.py, 全部为通用规则):

1. _SECTION_PATTERNS 的 method/experiments 模式允许 0 个空格(\s* 而非 \s+), 并接受 proposed 前缀 —— "ProposedMethod"/"ExperimentalResults" 恢复识别为 method/experiments 章节;
2. 新增表格列名守卫: 未编号的已知顶层词候选, 若下一行是表格表头/数据行(≥2 个小数、±+数字、或 ≥2 个不同指标词 PSNR/FSIM/LPIPS/Params/FLOPs 等), 判为表格列名跳过;
3. _LETTER_HEADING 增加年份守卫: 字母标题含 19xx/20xx 年份 → 参考文献条目, 拒绝。

回填 paper 4: 章节树 9 节 → 7 节(Abstract / Introduction p1-2 / ProposedMethod p2-5 / ExperimentalResults p5-7 / Discussion / Conclusion / References), chunks 的 section 引用按页范围重映射(旧 Introduction 中 p≥2 的归 ProposedMethod, 旧 Method 归 ExperimentalResults, 旧 G. E. 条目归 References)。

回归: PromptSR 18 节 0 差异; Dual-domain 16 节仅 1 处 normalized 从 other 变 method(更正确); MWAT-SR 26 节 0 差异; 全量 pytest 197 passed; 后端重启, Playwright 验证 CHAPTER MAP 显示 7 节。
## 16. 论文标题提取修复: 跨行标题 + 元数据缺失兜底(2026-08-19, 用户反馈提取的论文标题有问题)

TIFF-CEM 论文(AAAI 匿名投稿)的标题只存了第一行, 丢了 "Mechanism for Remote Sensing Image Dehazing"。

根因: 标题提取优先级为 PDF 元数据 Title → 第一页第一行。AAAI/IEEE 匿名投稿的 PDF 元数据常为空, 于是走 first_line 兜底, 而论文标题在排版上跨 2~3 行, 只取到第一行。当前 4 篇中 3 篇有元数据侥幸正确, 第 4 篇踩中。

修复(app/paper/parser.py):

1. 元数据 Title 优先(保留);
2. 元数据缺失时新增 _extract_title_from_pdf(): 用 pymupdf 读第一页 span 的字号, 取字号最大(≥ max-0.6)的文本块(标题字号通常是正文 1.5~2.5 倍), 按行聚类跨行拼接, 过滤页眉/页码/期刊名噪音(arXiv/DOI/ISSN/Contents lists/journal homepage/©/Anonymous submission/纯数字等);
3. 字号启发式失败再回退第一行; 统一清理标题首尾标点与多余空白。

验证: 4 篇论文标题全部正确(Paper 4 恢复为 "Text-Image Feature-Level Fusion with Collaborative Heterogeneous Expert Mechanism for Remote Sensing Image Dehazing"), DB 回填, 全量 pytest 197 passed, 后端重启, API 确认返回完整标题。
## 17. 章节内容切分修复: 页内偏移精确归属(2026-08-19, 用户反馈 Introduction 结尾内容跑进 ProposedMethod 翻译)

用户翻译 ProposedMethod 章节时, 译文开头出现 Introduction 的结尾(贡献列表)。根因: 上一轮回填章节树时, 用 SQL 按页范围粗暴重映射 chunks.section(`page_start>=2 的 Introduction 改 ProposedMethod`), 把 p.2 上 ProposedMethod 标题之前的 Introduction 结尾也划给了 ProposedMethod。

chunker.py 的 _page_segments 本身按页内偏移精确切分(标题在页中的位置), 修复方式是重新生成 chunks:

1. parse_pdf 重新解析 → 新章节树;
2. chunk_pages 重新分块(页内按标题偏移切段, 再滑窗);
3. 替换 paper_chunks 行(39 块: Introduction 7 / ProposedMethod 12 / ExperimentalResults 10 / References 6 / Abstract 2 / Discussion 1 / Conclusion 1);
4. 删除基于错误归属生成的 ProposedMethod/ExperimentalResults 翻译块(2 个), 用户重新翻译本章即可得到正确内容;
5. retriever.build 重建 FAISS+BM25 索引(index/papers/4), 重启后端。

验证: Introduction 的 p.2 chunk 现含贡献列表(到 ProposedMethod 标题前), ProposedMethod 从标题起。
## 18. 分块归属防线: 单元测试 + 运行时校验 + 一键重建脚本(2026-08-19, 加固防再犯)

针对第 17 节手改 section 导致归属错位的教训, 增加三道防线:

A. **单元测试**(tests/test_paper_chunker.py, 7 例): 同页双章节按标题偏移切分、跨页时前一章保留页尾、标题在页首整页归属、无章节匹配回退全文、滑窗偏移单调且章节不混、归属校验能检出错误归属、孤儿章节检出。

B. **运行时归属校验**(app/paper/chunker.py 新增 audit_chunks): 上传/重建分块后, 按页内标题偏移重算每段归属区间, 与 chunk 的 section 对照, 错位或孤儿章节打 warning 日志(接入 service.py 分块流程, 不影响主流程)。

C. **一键重建脚本**(scripts/rebuild_paper_chunks.py): 章节树修复后不再手写 SQL, 一条龙完成 重解析、重分块(页内偏移精确归属)、归属校验、替换 paper_chunks、删除孤儿翻译块(--purge-translations 可清空全部)、重建 FAISS+BM25 索引, 并提示需重启后端。

全量 pytest 204 passed(新增 7 例), 脚本对 paper 4 端到端验证通过(39 块归属校验通过, 删除 1 个孤儿翻译块, 提示重翻 Abstract/Introduction/ProposedMethod), 后端已重启。
## 19. 章节层级修复: Discussion 归入实验部分(2026-08-19, 用户反馈"消融实验和讨论不应该在实验部分吗")

TIFF-CEM 的 SECTION MAP 把 Discussion 显示为与 ExperimentalResults 并列的独立顶层章节, 而论文排版中 Discussion 是实验部分末尾的小节(与 Ablation Study 同级, Conclusion 才是通栏居中顶层)。

根因: 该论文标题无编号, parser 把 Discussion 一律判为 level 1 顶层; 且 SECTION MAP 组件(PaperOutline.vue)平铺渲染 sections, 不区分 level。

修复:

1. app/paper/parser.py: 通用规则——无编号的 Discussion 若紧随 Experiments/Results 顶层章节之后, level 降为 2(实验的小节), 不影响有编号或非实验后的 Discussion; 回归 4 篇论文章节树, Paper 1/2/3 零差异;
2. web/src/components/paper/PaperOutline.vue: SECTION MAP 按 level 构建父子层级渲染, 顶层序号连续(01-06), 子节点缩进 + 箭头标记(↳), 不占顶层序号; 翻译 tab 的 PaperSectionTree.vue 原本就按 level 分父子, 自动正确。

验证: DB 回填 Discussion level=2, 前端热更新后 SECTION MAP 显示 04 ExperimentalResults → ↳ Discussion → 05 Conclusion → 06 References; 后端 204 passed, 前端 build + 25 tests passed。
## 20. 小节标题识别: 排版通道(pymupdf 字号)补全连写论文(2026-08-19, 用户反馈 Proposed Method 和 Experimental Results 的小节没识别)

TIFF-CEM 论文的 12 个小节(Framework of TIFF-CEM / Loss Function / Ablation Study 等)全部没被识别, 因为提取文本把标题连写("FrameworkofTIFF-CEM"), 关键词正则救不了。

换检测方式: parser 增加**排版通道** _detect_layout_headings(基于 pymupdf):

1. **字号判标题**: 正文字号 = 全文行字号众数; 标题字号 > 正文+0.4 且该字号组全文行数 ≤30(标题行数少, 正文行数多);
2. **层级分级**: 字号差 ≥1.5 → level 1(顶层), 否则 level 2(小节)——TIFF-CEM 顶层 12.0、小节 10.9、正文 10.0, 分级干净;
3. **形态过滤**: 图注/表注(Figure/Table 开头)、页眉/期刊名/arXiv/DOI/CRediT 等噪音、公式/纯符号、悬空文字(标题下方必须紧跟正文行, 排除图内文字);
4. **只处理 page≥2**: 首页标题/作者/期刊名区噪音多, 由文本通道负责;
5. **双通道合并**: 排版候选与文本候选按"去空格标题"去重(重复时用排版层带空格标题+层级替换, 避免同标题两条), 按阅读顺序排序(两栏论文左栏先、栏内按 y);
6. **空结果保护**: IEEE(标题与正文同字号)和 Elsevier(有编号)论文排版通道自然返回空, 文本通道照旧——4 篇回归, Paper 1/2/3 章节树零差异。

修复 scripts/rebuild_paper_chunks.py 遗漏: 脚本此前只替换 chunks/索引, 未写 paper_sections, 已补上(章节树也由脚本统一重建)。

验证: Paper 4 章节树 7 节 → 18 节(6 顶层 + 12 小节, 标题带空格、层级正确), 38 块归属校验通过, 索引重建, 后端重启, 前端 SECTION MAP 显示完整层级(12 个 ↳ 子节点); 全量 pytest 204 passed。
## 21. 带空格标题的切分定位修复(2026-08-19, 用户反馈 Proposed Method 翻译仍含 Introduction 贡献列表)

排版通道给章节标题加了空格("Proposed Method"), 但页文本仍是连写("ProposedMethod")。chunker 的 _page_segments 用 page.text.find(section.title) 定位切分点, 带空格标题找不到 → 整页回退到 _section_for_page → p.2 整页(含 Introduction 贡献列表)错误归给 Framework of TIFF-CEM, 翻译 Proposed Method(含子章节)时把 Introduction 结尾也带进去了。

修复(app/paper/chunker.py): _page_segments 定位加 fallback——原样 find 失败后, 用**去空格标题**再找(子串搜索, 兼容单复数/连字符细微差异, 如 "Dehazing on Real-mask-synthetic Hazy Image" 匹配页文本 "...HazyImages")。

新增 3 项单元测试(带空格标题在连写文本中定位、单复数差异定位、带空格章节的 chunk 归属不混入)。全量 pytest 207 passed(原 204 + 3)。

重建 paper 4: 45 块(Introduction x7 含 p.2 贡献列表 / Proposed Method x1 仅标题 / Framework x3 从标题起), 归属校验通过, 索引重建, 后端重启, chunk 边界逐条验证正确。
## 22. 章节树完成勾选修复: 整章翻译的子章节无 artifact 导致不打勾(2026-08-19, 用户反馈父章节翻译完子章节显示了但左栏不打勾)

翻译父章节(如 Experimental Results)后, 子章节译文全部生成并展示, 但章节树左侧子章节没有 ✓。

根因: service.py _run_translation 对父章节(work_sections > 1)故意不创建 PaperArtifact(整章视图动态聚合子章节的译文块), 但前端 PaperTaskPanel.vue 的 completedSections 只从 artifacts 判定完成——整章翻译覆盖的子章节只有 translation_blocks 记录, 无 artifact → 判定未完成。截图佐证: 单独翻译过的 Framework/Text-image 有 ✓, 整章翻译才有的 Collaborative/Frequency/Loss 无 ✓。

修复(web/src/components/paper/PaperTaskPanel.vue): completedSections 增加第二条来源——translation_blocks 表中 status=completed 的块(按 section 收集), 与 artifacts 并集, 再按"全部直接子章节完成 → 父章节完成"递归。

另: 本次前端改动后 Vite dev server 热更新未生效(旧代码仍展示), 重启 dev server 后验证通过: Proposed Method / Experimental Results 及全部子章节均显示 ✓, Conclusion/References 未翻译保持空白。
## 23. 第五篇论文(PGDUN)表格检测修复: caption 污染连锁 + 下方规则线误判(2026-08-19, 用户反馈新论文 TABLE 2 截到正文、表注混入正文)

Paper 5(PGDUN, 高光谱)图正常但表有问题: TABLE 2 裁剪落在正文段落, 且多张表的 caption 文字混入正文。逐层诊断出 4 个独立缺陷(全部通用修复):

1. **表注续行混入正文**: _line_captions 的续行收集在 caption 句号结束后仍吸收下文(如 Table1 收进 "mixed illumination..."、Table3 收进 "extracted by spectral-language...")。修复: 表格 caption 某行以句号/问号结尾即视为完整停止; 图注保持宽松(句号+下行小写才停, 避免截断多句图注)。
2. **左右栏同 key 合并**: 行聚类(round(top/4))把同高度的左右栏词合并成一行(Table2 的 "minationinformation." 与右栏 "w/o(left)..." 同 key)。修复: 表格 caption 续行按栏过滤词(左栏 caption 只取 x<mid 的词)。
3. **同栏判定用未截断 x1**: 首行词间隙截断(first_words)后 cap_x1 正确, 但同栏判定用 row["x1"]——左栏 caption 首行混入右栏 Table4 文字后 x1 撑到 557, 被误判为右栏, 续行全被跳过。修复: 同栏判定改用截断后的 cap_x1。
4. **下方规则线误判紧贴**: Table2 的 caption 在表格下方(标题在下版式), 修复 1-3 后 caption y1 正确, 但下方 11pt 处有正文段落线(665.6), below_tight 误判 → 未启用上方候选。修复: below_tight 的规则线条件增加内容验证 _band_has_table_rows(下方区域须有 ≥2 行表格数据行才视为表格证据, 正文段落线不算)。

另修复连锁副作用: figure 区域 upper_bound 若深入当前图元(前一图表 bottom 含多行 caption 深度), 且该区间无前一图表图注文字时, 以当前图元上边界为准, 避免裁掉图内容(P1 #12 经核实是改进——TABLEIII 底线被正确排除)。

回归: Paper 1 有 5 处差异、Paper 2 有 3 处差异, 逐一核实均为 caption 干净化后的修正(旧 caption 混入正文撑大区域, 新区域更准; 表格仍完整覆盖, figure 差异 ≤12pt 且均为排除非图内容); Paper 3/4 零差异。Paper 5 重渲染回填: 4 张表全部正确(TABLE1/2/3/5), vision 逐张确认是数字表格。全量 pytest 207 passed, 后端重启。
## 24. 第五篇论文漏检 Table4 修复(2026-08-19, 用户纠正"一共不是有5张表吗")

第 23 节修复后 Paper 5 只检测到 4 张表, 漏了 Table4。根因: 行聚类按 round(top/4) 分桶, 把同高度的左栏 Table2 标题(y=632.6)与右栏 Table4 标题(y=631.3)合并成一行, _CAPTION 只匹配开头的 Table2, Table4 被吞掉。

修复过程(两次方案推翻):

1. **分栏分桶**(round(top/4), 左右栏)不可行——通栏标题(文字跨两栏, 同 top)被拆开, P1/P2 的 figure x1 被砍半、P4 表格崩溃;
2. **细粒度分桶**(round(top))——Table2/Table4(差 1.3pt)分开, 但通栏 caption 文字 top 微差(0.5-1pt)也被拆, P1 #7 的 x1 又变 304;
3. **最终方案**: 恢复 round(top/4) 分桶, _line_captions 检测循环改为**合并行内逐标签处理**——扫描行内所有 Table/Figure 标签词位置(label_offsets), 每个标签从该词开始生成 caption, 后续行共享同一续行收集逻辑(续行/钳制/append 缩进到标签循环内)。

配套: _table_region_above 的规则线分段改用"Table 表注标签 y"切分(而非簇间距), 每段验证表格行 + 段上方无 Table 表注标签(图注 Figure 不影响), 支持 Table4 这类"表格与标题相隔 90pt 且中间有另一张表"的错位排版; 右栏规则线过滤改为 r[2]>=mid(保留通栏表格)。

验证: Paper 5 检测到 5 张表全部正确(Table1/2/3/4/5, vision 确认 Table4 是 GAP/DAUF/DADN/PGMC 数字表格); Paper 4 零回归; Paper 1/2 差异与第 23 节后一致(caption 干净化修正); 全量 pytest 207 passed, Paper 5 重渲染回填(11 个图表), 后端重启。
## 25. 图表检测内容自愈闭环(2026-08-19, 用户要求"考虑怎么修复这个问题"——系统性兜底而非个案补丁)

反思: 图表检测反复修个案(版式、caption 污染、漏检), 核心问题是**检测结果没有运行时质量门槛**——错误区域(正文)直接入库, 用户不指出就静默错下去。

根本性修复(app/paper/figures.py): **内容自愈闭环**——每个表格区域生成后必须通过"内容验证"才接受:

1. 新增 _region_has_table_rows(): 区域内容验证——区域里必须有 ≥2 行表格数据行(多列词簇或数字密集), 否则视为把正文裁进来了;
2. detect_figures 表格分支集成: 区域无表格行时自动尝试 ① 上方候选(_table_region_above) ② find_tables 网格表, 找到有表格行的候选即采用; 全部失败保留原区域(由 audit 打 warning 标记);
3. 效果: 方向决策/规则线/网格无论怎么偏差, 不会把正文当表格输出——每次新论文即使检测逻辑不完美, 也有安全网兜底, 而不是等用户发现再修。

新增单元测试(_region_has_table_rows 区分表格行/正文段)。验证: 5 篇论文检测结果与上一轮完全一致(正确结果都通过内容验证, 自愈未误触发), P4 零回归, 全量 pytest 208 passed, 后端重启。
## 26. 摘要翻译源污染修复(2026-08-19, 用户反馈 PGDUN 摘要翻译奇怪: 漏 PGMC、出现 (a)(b) 错乱、混入图注)

PGDUN(Paper 5)的摘要翻译内容奇怪: 漏了 PGMC 模块和 spectral-language contrastive learning, 出现原文没有的 (a)(b) 编号, 末尾还混入 Figure1 的图注翻译。

根因: Abstract 翻译源用了 page chunk, 而 p.1 两栏排版的 extract_page_text 把 Abstract 文本与右栏 Figure1 图注交错——翻译源被图注从中间切开("To ad-" 截断后插入图注(a), 又拼回 Abstract 后半, 再插入图注(b)), 且 chunk 滑窗把摘要截断。

修复(app/paper/service.py _run_translation): Abstract 章节翻译**始终**用 papers.abstract(pymupdf 提取的干净摘要), 不再走 page chunk(原逻辑只在无 chunk 时兜底)。对全部论文生效。

验证: 重翻 Paper 5 摘要, 源 1473 字符干净(含 PGMC/spectral-language, 无 Figure1), 译文完整准确(PGDUN 提议、光谱-语言对比学习、PGMC、PGSA 全部译出, 无 (a)(b) 错乱); 全量 pytest 208 passed, 后端重启。
## 27. 翻译结果混入表格数据修复(2026-08-19, 用户反馈译文里表格数字折行显示错乱)

TIFF-CEM 的 Dehazing 章节译文末尾带出 Table 2 的完整数据(表头 + 10 行数字), UI 纯文本折行显示。

根因: 该章节的翻译源 chunk 包含 Table 2 区域文本(提取顺序把表格数据排进章节), LLM 把数字原样保留在译文里。

修复(app/paper/service.py): 翻译源生成后应用 _strip_table_rows 剔除表格内容——

1. **表格数据行**: 数字占比 ≥25% 且 ≥3 个小数(如 "31.55±4.932 0.9730±0.0236...");
2. **指标/数据集表头行**: PSNR/FSIM/LPIPS/SSIM/Params/FLOPs/Scene/RICE/RSID/SateHaze 等词占该行词数 >50%(如 "PSNR FSIM LPIPS PSNR FSIM LPIPS"、"RICE1 RICE2")——占比规则保护含数据集名的正文长句("on RICE1 and RICE2..." 不删);
3. 注意排除 Method(正文 "method" 常见, 误删过一次已修正)。

验证: 重翻 Dehazing 章节, 源 1333 字符无 RICE1/数据行, 正文行("Thesere- sults")正确衔接, 译文尾部为正文讨论(10.14% FLOPs/2.94% 参数); 6 项 strip 规则单元验证全过; 全量 pytest 208 passed, 后端重启。
## 28. 翻译缺页修复: strip_visual_regions 误删 caption 后正文(2026-08-19, 用户反馈 Cloud Removal 章节缺 p.7 内容)

Cloud Removal on Remote Sensing Images(p6-7)翻译只有 p.6, 缺 p.7。

根因: 该章节 p.7 段 [0, 981) 以 Table3 数据 + Table3 caption 开头, strip_visual_regions 找到 "Table3:" caption 后, 把 caption 之后的所有"非 prose"行全删——而 p.7 的 Cloud 正文是**连写行**(提取无空格、无标点、latin_words 少), 被误判为非 prose → 整段删除 → chunk #34 源为空 → p.7 没翻译。

修复(app/paper/content_filter.py): caption 后的删除加保守门槛 _looks_like_table_content_line——只删"表格数据/表头行"(数字密集 ≥2 小数、符号短行 + 26.56、指标词占比 >50% 的英文表头、短行含 #/×/数字的中文表头), 疑似正文(含连写行)一律保留。

验证: 重翻 Cloud Removal, 新块 p6-7(之前 p6-6), 源 1098 字符含 p.7 Cloud 正文(38.47/0.0174), 译文尾部为 p.7 内容(RICE2 对比); 全量 pytest 208 passed(含 content_filter 3 项测试), 后端重启。
## 29. 翻译源质量防线: 过滤审计 + 覆盖校验 + 前端提示(2026-08-19, 用户要求"修好类似的bug, 防止以后出现类似问题")

连续出现"翻译缺内容/混表格数据/混图注"三类问题(第 26-28 节), 根子都在翻译源生成环节。加三层系统性防线, 让这类问题自动暴露而不是等用户发现:

1. **翻译源过滤审计**(service.py): chunks 生成 sources 时, 若某 chunk 原始正文 ≥40 字符但清理后为空(被 _strip_table_rows/strip_visual_regions 误删), 打 warning 并记入 filtered_chunks;
2. **done 事件上报**(service.py): 完成事件带 warnings 字段(被过滤段落列表); 前端 PaperWorkspaceView 的 onDone 读到非空 warnings 时 ElMessage 提示"部分段落被过滤(可能是表格数据或图注), 译文可能不完整";
3. **单元测试**(tests/test_paper_tasks.py): 纯表格数据章节(标题+20 行数据)翻译后 done.warnings 非空——固定"过滤必须可发现"的契约。

全量 pytest 209 passed, 后端与前端 dev server 已重启。
## 30. 正文中段图注误删修复: 公式(6)(7)(8)丢失(2026-08-19, 用户反馈 Text-image 章节右上红框内容没翻译)

用户看 Collaborative Expert Mechanism 章节时发现右上红框(公式 6/7/8 + Figure 3 描述)没翻译。核实: 右上红框在论文排版里属于 **Text-image Feature-level Fusion 章节**(p.3-4 的末尾, Collaborative 是下一章), 但它的翻译块也缺这些内容——公式 6/7/8 在翻译源里丢了。

根因: Text-image 的 p.4 chunk 里, Figure 3 的图注("Figure3:t-SNE...", 提取时嵌在两栏正文中间)被 _clean_translation_page_prefix 当成页顶图注删除, 连带吞掉其后的 Figure 3 描述(只剩 "row).Moreover..." 352 字符); 且 strip_visual_regions 的 caption 前删除会把公式行(非 prose)一并删掉。

修复:

1. service.py _clean_translation_page_prefix: 图注搜索范围从"前 24 行"收窄到"前 6 行"——只删真正页顶的图注, 正文中段的图注不再触发;
2. content_filter.py strip_visual_regions: caption 前删除加 _look_like_figure_label 门槛——只删"短行(≤40 字符)且无等号"的图内标签, 公式行(含 =)与长正文行保留。

验证: chunk #17 源从 352 → 947 字符(含 To tailor/affine/t-SNE), 重翻 Text-image 后 src 5706 字符含公式 6/7/8, 译文含对应内容; 全量 pytest 209 passed, 后端重启。
## 31. 两栏章节归属修复: 右栏内容提前导致公式 6-8 归错章节(2026-08-19, 用户质疑"公式 4 直接跳公式 6, 这是 Collaborative 的内容吗")

用户看 Collaborative Expert Mechanism 章节发现公式 6/7/8(airlight/transmission priors、跨分支仿射调制)被归到了 Text-image 章节, 公式编号 4 直接跳 6。核实论文原文: 公式 6-8 在 p.4 右栏(y 56-250), Collaborative 标题在左栏(y 444), 两栏异步——右栏的 Collaborative 调制公式先于左栏标题出现。

根因(两层):

1. **extract_page_text 两栏检测失败**: p.4 顶部是公式区+正文, 左右并行跨栏行(split_rows)<3, 被误判单栏 → fallback 到 pdfplumber 全局 y 排序 → 右栏公式 6-8(y 56)排到左栏 Collaborative 标题(y 444)之前 → 归 Text-image;
2. **修复后 rebuild 脚本漏更新 paper_pages**: chunks 的 char 偏移基于新文本, 但 paper_pages 表还是旧文本, 翻译用旧 page_text 导致错位(源仍缺公式)。

修复:

1. app/paper/parser.py extract_page_text: 两栏判定加"左右栏词数(≥25)+水平范围各居一侧"双保险(不只看跨栏行); header(第一个跨栏行前的行)也按栏分, 输出顺序统一为 左栏完再右栏(阅读顺序);
2. scripts/rebuild_paper_chunks.py: 补更新 paper_pages 表(与新 chunks 偏移一致), 避免翻译用旧文本错位。

验证: p.4 新文本公式 6-8 排到 Collaborative 标题之后, Collaborative 章节 chunk 从 1 个变 2 个(含公式 6-8), 重翻后译文含公式(6)(7)(8)与 Figure 3 描述(0.261 vs 0.328); 章节树回归 P1/P2/P3/P4 零变化, P5 仅 1 处页范围微调; 全量 pytest 209 passed, 后端重启。
## 32. 图注被截断修复: Figure1 caption 只有 (a)(2026-08-19, 用户反馈"翻译的 caption 怎么这么少")

P5 的 Figure1 caption 翻译只有子图 (a), 缺 (b)(c)。根因: Figure1 图注在 p.1 右栏, 它的 (b) 续行与左栏 Abstract 正文同高度合并成一行("nationpriors... (b) The t-SNE..."), 合并行以左栏小写 'n' 开头 → 图注停止条件(句号结束+下行小写开头)误触发 → caption 只收集到 (a)。

修复(app/paper/figures.py _line_captions):

1. **图注续行按栏过滤词**(与表格 caption 一致): 合并行只取本栏词, 停止判断用"本栏第一个词的字符"而非合并行首字符;
2. **行数上限放宽**: 图注 6 → 10 行(多子图 a/b/c 可 8+ 行), 表格保持 6;
3. **where 停止**: 图注续行以 "where" 开头(公式变量说明, 如 "where beta is the balancing...")即使无句号也停止——防止 caption 的 (c) 部分直接接正文时污染。

验证: Figure1 caption 104 → 519 字符(含 (a)(b)(c) 完整); Figure3 caption 不再混入公式说明; 图注翻译已清空, 下次翻译任务自动重翻完整 caption; 全量 pytest 209 passed, 后端重启。









