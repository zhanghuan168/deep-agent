<template>
  <div class="document-panel">
    <div class="panel-header">
      <span class="panel-title">📄 文档 & 评审</span>
      <button v-if="!collapsed" class="btn-collapse" @click="collapsed=true">收起</button>
      <button v-else class="btn-collapse" @click="collapsed=false">展开</button>
    </div>

    <div v-if="!collapsed" class="panel-body">
      <!-- 无文档时 -->
      <div v-if="documents.length === 0" class="empty-state">
        暂无关联文档
      </div>

      <!--文档列表 -->
      <div v-else class="doc-list">
        <div
          v-for="doc in documents"
          :key="doc.id"
          class="doc-item"
        >
          <div class="doc-summary" @click="toggleDoc(doc.id)">
            <span class="doc-type-badge">{{ doc.doc_type }}</span>
            <span class="doc-title">{{ doc.title }}</span>
            <span class="doc-version">v{{ doc.current_version }}</span>
            <span class="review-status" :class="latestReviewClass(doc.id)">
              {{ latestReviewLabel(doc.id) }}
            </span>
            <span class="arrow">{{ expandedDocId === doc.id ? '▲' : '▼' }}</span>
          </div>

          <!-- 版本时间线（展开时） -->
          <div v-if="expandedDocId === doc.id" class="doc-timeline">
            <div
              v-for="v in doc.versions"
              :key="v.id"
              class="timeline-item"
              :class="{ 'current-version': v.version === doc.current_version }"
            >
              <div class="timeline-dot"></div>
              <div class="timeline-content">
                <div class="timeline-header">
                  <span class="version-tag">v{{ v.version }}</span>
                  <span class="version-author">{{ v.author }}</span>
                  <span class="version-date">{{ formatDate(v.created_at) }}</span>
                  <span v-if="v.change_summary" class="version-summary">{{ v.change_summary }}</span>
                </div>

                <!-- 评审气泡 -->
                <div
                  v-for="review in getReviews(doc.id, v.version)"
                  :key="review.id"
                  class="review-bubble"
                  :class="review.decision"
                >
                  <div class="review-header">
                    <span class="reviewer">{{ review.reviewer }}</span>
                    <span class="review-decision">{{ reviewLabel(review.decision) }}</span>
                  </div>
                  <div v-if="review.comments" class="review-comments">{{ review.comments }}</div>
                  <div v-if="review.scores" class="review-scores">
                    <span v-for="(score, key) in review.scores" :key="key" class="score-item">
                      {{ key }}: {{ score }}
                    </span>
                  </div>
                </div>

                <!-- 版本对比按钮 -->
                <div v-if="v.version > 1" class="diff-actions">
                  <button class="btn-diff" @click="showDiff(doc.id, v.version - 1, v.version)">
                    对比上一版本
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Diff 展示区 -->
      <div v-if="diffResult" class="diff-panel">
        <div class="diff-header">
          <span>v{{ diffResult.v1 }} → v{{ diffResult.v2 }}差异</span>
          <button @click="diffResult = null">关闭</button>
        </div>
        <div class="diff-body">
          <div v-for="(line, i) in diffResult.deletions" :key="'d'+i" class="diff-line deletion">- {{ line }}</div>
          <div v-for="(line, i) in diffResult.additions" :key="'a'+i" class="diff-line addition">+ {{ line }}</div>
          <div v-for="(line, i) in diffResult.unchanged" :key="'u'+i" class="diff-line unchanged">  {{ line }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
export default {
  name: 'DocumentPanel',
  props: {
    parentId: { type: String, required: true },
    stageId: { type: String, default: null },
  },
  data() {
    return {
      documents: [],
      reviewsMap: {}, // docId -> list of reviews
      expandedDocId: null,
      diffResult: null,
      collapsed: false,
    };
  },
  async mounted() {
    await this.loadDocuments();
  },
  methods: {
    async loadDocuments() {
      const url = this.stageId
        ? `/api/stages/${this.stageId}/document/`
        : `/api/tasks/${this.parentId}/documents/`;
      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();
      this.documents = Array.isArray(data) ? data : [data].filter(Boolean);

      // 加载每文档的评审记录
      for (const doc of this.documents) {
        const r = await fetch(`/api/documents/${doc.id}/reviews/`);
        if (r.ok) {
          this.reviewsMap[doc.id] = await r.json();
        }
      }
    },
    toggleDoc(docId) {
      this.expandedDocId = this.expandedDocId === docId ? null : docId;
    },
    getReviews(docId, version) {
      return (this.reviewsMap[docId] || []).filter(r => r.version === version);
    },
    latestReviewClass(docId) {
      const reviews = this.reviewsMap[docId] || [];
      if (!reviews.length) return '';
      const latest = reviews[reviews.length - 1];
      return latest.decision;
    },
    latestReviewLabel(docId) {
      const reviews = this.reviewsMap[docId] || [];
      if (!reviews.length) return '未评审';
      const latest = reviews[reviews.length - 1];
      return this.reviewLabel(latest.decision);
    },
    reviewLabel(decision) {
      const map = { approve: '✅ 通过', reject: '❌ 驳回', comment:'💬意见' };
      return map[decision] || decision;
    },
    formatDate(dt) {
      if (!dt) return '';
      return new Date(dt).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    },
    async showDiff(docId, v1, v2) {
      const res = await fetch(`/api/documents/${docId}/diff/?v1=${v1}&v2=${v2}`);
      if (res.ok) {
        this.diffResult = await res.json();
      }
    },
  },
};
</script>

<style scoped>
.document-panel { border: 1px solid #e0e0e0; border-radius: 8px; margin-top: 12px; }
.panel-header { display: flex; justify-content: space-between; padding: 10px 14px; background: #f5f5f5; border-radius: 8px 8px 0 0; font-weight: 600; }
.btn-collapse { background: none; border: none; cursor: pointer; font-size: 12px; color: #666; }
.panel-body { padding: 12px 14px; }
.empty-state { color: #999; font-size: 13px; text-align: center; padding: 16px; }
.doc-list { display: flex; flex-direction: column; gap: 8px; }
.doc-item { border: 1px solid #eee; border-radius: 6px; overflow: hidden; }
.doc-summary { display: flex; align-items: center; gap: 8px; padding: 8px 12px; cursor: pointer; }
.doc-type-badge { background: #e3f2fd; color: #1565c0; font-size: 11px; padding: 2px 6px; border-radius: 4px; }
.doc-title { flex: 1; font-size: 13px; }
.doc-version { font-size: 11px; color: #888; }
.review-status { font-size: 11px; }
.review-status.approve { color: #2e7d32; }
.review-status.reject { color: #c62828; }
.arrow { font-size: 10px; color: #888; }
.doc-timeline { padding: 8px 12px; background: #fafafa; border-top: 1px solid #eee; }
.timeline-item { display: flex; gap: 10px; padding: 6px 0; position: relative; }
.timeline-item::before { content: ''; position: absolute; left: 3px; top: 16px; bottom: -6px; width: 1px; background: #ccc; }
.timeline-item:last-child::before { display: none; }
.timeline-dot { width: 8px; height: 8px; border-radius: 50%; background: #bbb; margin-top: 5px; flex-shrink: 0; position: relative; z-index: 1; }
.current-version .timeline-dot { background: #1565c0; }
.timeline-content { flex: 1; }
.timeline-header { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: 12px; }
.version-tag { background: #1565c0; color: #fff; font-size: 10px; padding: 1px 5px; border-radius: 3px; }
.version-author { color: #555; }
.version-date { color: #999; font-size: 11px; }
.version-summary { color: #666; font-style: italic; }
.review-bubble { margin-top: 6px; padding: 6px 10px; border-radius: 6px; font-size: 12px; border-left: 3px solid #ccc; }
.review-bubble.approve { background: #e8f5e9; border-color: #2e7d32; }
.review-bubble.reject { background: #ffebee; border-color: #c62828; }
.review-bubble.comment { background: #fff8e1; border-color: #f9a825; }
.review-header { display: flex; gap: 8px; margin-bottom: 4px; }
.reviewer { font-weight: 600; }
.review-decision { font-size: 11px; }
.review-comments { color: #444; line-height: 1.4; }
.review-scores { margin-top: 4px; display: flex; gap: 8px; flex-wrap: wrap; }
.score-item { background: #f5f5f5; padding: 1px 6px; border-radius: 3px; font-size: 11px; }
.diff-actions { margin-top: 4px; }
.btn-diff { font-size: 11px; padding: 2px 8px; border: 1px solid #1565c0; background: none; color: #1565c0; border-radius: 4px; cursor: pointer; }
.diff-panel { margin-top: 12px; border: 1px solid #1565c0; border-radius: 6px; overflow: hidden; }
.diff-header { display: flex; justify-content: space-between; padding: 8px 12px; background: #e3f2fd; font-size: 13px; font-weight: 600; }
.diff-header button { background: none; border: none; cursor: pointer; font-size: 12px; color: #1565c0; }
.diff-body { padding: 8px 12px; font-family: monospace; font-size: 12px; max-height: 300px; overflow-y: auto; }
.diff-line { padding: 1px 4px; }
.diff-line.addition { background: #e8f5e9; color: #1b5e20; }
.diff-line.deletion { background: #ffebee; color: #b71c1c; }
.diff-line.unchanged { color: #888; }
</style>