<script setup lang="ts">
import { computed } from "vue";
import type { PaperArtifact, PaperCitation, PaperReviewContent } from "@/types/paper";
import PaperCitationList from "./PaperCitationList.vue";
import ScientificText from "./ScientificText.vue";

const props = defineProps<{ artifact?: PaperArtifact }>();
const emit = defineEmits<{ openCitation: [citation: PaperCitation] }>();

const review = computed<PaperReviewContent>(() => {
  const content = (props.artifact?.content || {}) as Partial<PaperReviewContent>;
  const list = (value: unknown) => (Array.isArray(value) ? value.map(String) : []);
  return {
    summary: String(content.summary || ""),
    contributions: list(content.contributions),
    strengths: list(content.strengths),
    major_issues: list(content.major_issues),
    minor_issues: list(content.minor_issues),
    ratings: {
      novelty: String(content.ratings?.novelty || ""),
      correctness: String(content.ratings?.correctness || ""),
      experiments: String(content.ratings?.experiments || ""),
      writing: String(content.ratings?.writing || ""),
    },
    suggestions: list(content.suggestions),
    recommendation: String(content.recommendation || ""),
    score: typeof content.score === "number" ? content.score : null,
  };
});

const recClass = computed(() => {
  const rec = review.value.recommendation.toLowerCase();
  if (rec.includes("accept")) return "rec-accept";
  if (rec.includes("minor")) return "rec-minor";
  if (rec.includes("major")) return "rec-major";
  if (rec.includes("reject")) return "rec-reject";
  return "";
});

type RatingKey = "novelty" | "correctness" | "experiments" | "writing";

const ratingRows = computed<Array<{ key: RatingKey; label: string }>>(() => [
  { key: "novelty", label: "创新性" },
  { key: "correctness", label: "技术正确性" },
  { key: "experiments", label: "实验充分性" },
  { key: "writing", label: "写作质量" },
]);
</script>

<template>
  <div v-if="artifact" class="review">
    <header class="review-title">
      <span>PEER REVIEW / 论文审稿</span>
      <div class="review-head">
        <h2>{{ artifact.title }}</h2>
        <div class="review-verdict">
          <span v-if="review.score !== null" class="review-score">{{ review.score }}<small>/10</small></span>
          <span v-if="review.recommendation" class="review-rec" :class="recClass">{{ review.recommendation }}</span>
        </div>
      </div>
      <p>以审稿人视角结合原文证据生成；已核验引用可点击跳转原文。</p>
    </header>

    <section v-if="review.summary" class="review-section">
      <h3>论文概要</h3>
      <ScientificText :content="review.summary" />
    </section>

    <section v-if="review.contributions.length" class="review-section">
      <h3>主要贡献</h3>
      <ol class="review-list">
        <li v-for="(item, index) in review.contributions" :key="index"><ScientificText :content="item" /></li>
      </ol>
    </section>

    <section v-if="review.strengths.length" class="review-section">
      <h3>优点</h3>
      <ul class="review-list strong">
        <li v-for="(item, index) in review.strengths" :key="index"><ScientificText :content="item" /></li>
      </ul>
    </section>

    <section v-if="review.major_issues.length" class="review-section">
      <h3>主要问题</h3>
      <ol class="review-list major">
        <li v-for="(item, index) in review.major_issues" :key="index"><ScientificText :content="item" /></li>
      </ol>
    </section>

    <section v-if="review.minor_issues.length" class="review-section">
      <h3>次要问题</h3>
      <ol class="review-list minor">
        <li v-for="(item, index) in review.minor_issues" :key="index"><ScientificText :content="item" /></li>
      </ol>
    </section>

    <section v-if="ratingRows.some((row) => review.ratings[row.key])" class="review-section">
      <h3>分项评价</h3>
      <div class="rating-grid">
        <div v-for="row in ratingRows" :key="row.key" v-show="review.ratings[row.key]">
          <strong>{{ row.label }}</strong>
          <ScientificText :content="review.ratings[row.key]" />
        </div>
      </div>
    </section>

    <section v-if="review.suggestions.length" class="review-section">
      <h3>修改建议</h3>
      <ol class="review-list">
        <li v-for="(item, index) in review.suggestions" :key="index"><ScientificText :content="item" /></li>
      </ol>
    </section>

    <PaperCitationList :citations="artifact.citations" @open="emit('openCitation', $event)" />
  </div>
  <el-empty v-else description="审稿意见正在生成" />
</template>

<style scoped>
.review { max-width: 820px; margin: 0 auto; }
.review-title { padding-bottom: 24px; border-bottom: 2px solid #183641; }
.review-title > span { color: #b86243; font-size: 10px; letter-spacing: .2em; font-weight: 700; }
.review-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; margin-top: 9px; }
.review-head h2 { margin: 0; color: #183641; font-family: "Noto Serif SC", "Source Han Serif SC", Georgia, serif; font-size: 24px; font-weight: 600; }
.review-title p { margin: 8px 0 0; color: #7b8585; font-size: 12px; }
.review-verdict { display: flex; align-items: center; gap: 12px; flex: 0 0 auto; }
.review-score { color: #183641; font: 700 30px/1 Georgia, serif; }
.review-score small { color: #8a9698; font-size: 13px; }
.review-rec { padding: 6px 12px; border-radius: 999px; font: 600 13px/1.4 "Noto Serif SC", Georgia, serif; }
.rec-accept { background: #dcebe0; color: #2c6e3c; }
.rec-minor { background: #e8efd6; color: #5f7a22; }
.rec-major { background: #f7e3d2; color: #a85c1e; }
.rec-reject { background: #f4dcd9; color: #a83a30; }
.review-section { padding: 22px 0; border-bottom: 1px solid #e0dbce; }
.review-section h3 { margin: 0 0 10px; color: #1e3942; font-family: "Noto Serif SC", Georgia, serif; font-size: 16px; }
.review-list { margin: 0; padding-left: 1.3em; }
.review-list li { margin: 6px 0; color: #3f5054; line-height: 1.8; }
.review-list.strong li::marker { color: #4c7a55; }
.review-list.major li::marker { color: #b34a3c; }
.review-list.minor li::marker { color: #c07a35; }
.rating-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 10px; }
.rating-grid > div { display: grid; gap: 4px; padding: 12px; border-left: 3px solid #d9b48f; background: #f5efe4; }
.rating-grid strong { color: #203941; font: 600 13px Georgia, "Noto Serif SC", serif; }
</style>
