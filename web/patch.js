/* UI 프로토타입 ↔ 엔진 연결 패치.
 *
 * 프로토타입은 백엔드가 없어 엔진 호출 자리를 전부 setTimeout 으로 흉내 내고
 * 있었다. 그 자리를 실제 호출로 바꾼다.
 *
 * **화면 템플릿(마크업)은 건드리지 않는다.** 그 파일은 UI 트랙 소유이고,
 * 디자인 툴이 만든 번들이라 손대면 저쪽이 다시 뽑을 때 충돌한다. 대신 로직
 * 함수만 갈아끼운다 — 드롭존이 `simulateUpload` 를 부르고 있으니, 그 함수가
 * 진짜 파일 선택창을 열면 마크업은 그대로여도 동작이 진짜가 된다.
 *
 * tools/build_web.py 가 이 파일을 프로토타입 안에 끼워 넣는다.
 */

// ── 엔진 말 ↔ 화면 말 ────────────────────────────────────────────────
// 화면은 영문 키로, 엔진은 계약대로 한국어로 말한다. 여기서만 옮긴다.
const TYPE_TO_ENGINE = {
  accumulated: '누적교육', single: '단발특강', diagnosis: '진단서베이',
};
const TYPE_FROM_ENGINE = {
  '누적교육': 'accumulated', '단발특강': 'single', '진단서베이': 'diagnosis',
};

const API = {
  async get(path) {
    const r = await fetch(path, { credentials: 'same-origin' });
    if (!r.ok) throw new Error(await API._why(r));
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(path, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(await API._why(r));
    return r.json();
  },
  async upload(file) {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch('/uploads/analyze', {
      method: 'POST', credentials: 'same-origin', body: fd,
    });
    if (!r.ok) throw new Error(await API._why(r));
    return r.json();
  },
  // 서버가 준 이유를 그대로 보여 준다. "요청 실패" 라고만 뜨면 고칠 수가 없다.
  async _why(r) {
    try {
      const j = await r.json();
      return j.detail || j.message || (r.status + ' ' + r.statusText);
    } catch (e) {
      return r.status + ' ' + r.statusText;
    }
  },
};

function pickFile() {
  return new Promise((resolve) => {
    const el = document.createElement('input');
    el.type = 'file';
    el.accept = '.xlsx,.xlsm';
    el.onchange = () => resolve(el.files && el.files[0]);
    el.click();
  });
}

// 엔진의 rows[] → 검증 표가 쓰는 모양
function toValidationRows(rows) {
  return (rows || []).map((r) => ({
    row: r.rowNumber,
    name: r.name || '',
    empId: r.empId || '',
    issue: r.message,
    type: r.severity,                       // error | warning — 그대로 맞다
    field: r.field,
    issueCode: r.issueCode,
    originalValue: r.originalValue || '',
    suggestedValue: r.suggestedValue || '',
    candidates: r.candidates || null,
    resolved: false,
  }));
}

// 엔진의 cards[] → 검수 명단이 쓰는 모양
const SEVERITY_TO_STATUS = {
  hold: 'hold', review: 'review', block_direct_quote: 'summary',
};
function toMembers(cards) {
  return (cards || []).map((c) => ({
    name: c.name,
    empId: c.empId || c.cardId,
    cardId: c.cardId,
    status: SEVERITY_TO_STATUS[c.maxSeverity] || 'unreviewed',
    ...(c.maxSeverity === 'review' ? { warningAcked: false } : {}),
  }));
}

Object.assign(Component.prototype, {

  // ── ① 업로드 → 판정 ───────────────────────────────────────────────
  // 드롭존이 부르던 가짜 함수. 이제 진짜 파일 선택창을 열고 엔진에 보낸다.
  async realUpload() {
    const file = await pickFile();
    if (!file) return;
    this.showToast(file.name + ' 을(를) 읽는 중입니다…');
    try {
      const a = await API.upload(file);
      const m = a.courseMatch || {};
      const uiType = TYPE_FROM_ENGINE[a.sourceType.type] || 'accumulated';
      this.setState({
        view: 'admin-validate',
        draftId: a.draftId,
        engineAnalysis: a,
        reportType: uiType, typeEditDraft: uiType,
        typeManuallySet: false, reportTypeApproved: true,
        engineTypeReason: a.sourceType.evidence,
        engineCourseReason: m.evidence,
        engineCourseMode: m.mode,
        engineCourseTitle: m.suggestedTitle,
        linkedCourseKey: m.suggestedCourseId || null,
        courseLinkStatus: m.suggestedCourseId ? 'prefilled' : 'idle',
        courseLinkChoiceLabel: m.suggestedTitle || '',
        newCourseName: m.mode === 'create' ? (m.suggestedTitle || '') : '',
        validationRows: toValidationRows(a.rows),
        engineSummary: a.summary,
        engineWave: a.wave || null,
      });
      this.showToast('판정 완료 — ' + a.sourceType.type + ' · 인식 '
        + a.summary.recognized + '행');
    } catch (err) {
      this.showToast('업로드 실패 — ' + err.message);
    }
  },

  // ── ② 검증 확정 → 카드 생성 ────────────────────────────────────────
  async realGenerate() {
    const s = this.state;
    if (!s.draftId) {
      this.showToast('먼저 파일을 올려 주세요.');
      return;
    }
    this.setState({ validateGenerating: true });

    // 담당자가 고친 것과 뺀 것만 추린다
    const fixes = (s.validationRows || [])
      .filter((r) => r.resolved && r.fixedValue !== undefined
                     && r.fixedValue !== null && r.action !== 'exclude')
      .map((r) => ({ rowNumber: r.row, field: r.field, value: String(r.fixedValue) }));
    const excluded = (s.validationRows || [])
      .filter((r) => r.action === 'exclude').map((r) => r.row);

    const course = s.linkedCourseKey
      ? { mode: 'link', courseId: s.linkedCourseKey }
      : { mode: 'create',
          newTitle: (s.newCourseName || s.engineCourseTitle || '새 과정').trim() };

    const body = {
      confirmedSourceType: TYPE_TO_ENGINE[s.reportType] || '누적교육',
      confirmedCourse: course,
      rowFixes: fixes,
      excludedRows: excluded,
      operator: s.memberName || '담당자',
      generate: true,
    };
    if (s.engineWave) body.confirmedWave = s.engineWave.suggested;

    try {
      const c = await API.post('/uploads/' + s.draftId + '/commit', body);
      const byPerson = {};
      (c.reports || []).forEach((r) => {
        if (r.report_id) byPerson[r.name] = r.report_id;
      });
      this.setState({
        view: 'admin-review',
        validateGenerating: false,
        uploadId: c.uploadId,
        linkedCourseKey: c.courseId,
        courseLinkChoiceLabel: c.courseTitle,
        mainMembers: toMembers(c.cards),
        reportIdByName: byPerson,
        engineFlags: c.flags || [],
        selectedIdx: 0,
        courseTypeOverride: { ...s.courseTypeOverride, [c.courseId]: s.reportType },
      });
      const gen = c.generation || {};
      this.showToast('카드 ' + (c.cards || []).length + '장 생성 · 문장 '
        + (gen.accepted || 0) + '건'
        + (gen.rejected ? ' (거절 ' + gen.rejected + '건)' : ''));
    } catch (err) {
      this.setState({ validateGenerating: false });
      this.showToast('카드 생성 실패 — ' + err.message);
    }
  },

  // ── ③ 리포트 본문 · 근거 ──────────────────────────────────────────
  currentReportId() {
    const s = this.state;
    const map = s.reportIdByName || {};
    if (s.view === 'admin-review') {
      const m = (s.mainMembers || [])[s.selectedIdx];
      return m ? map[m.name] : null;
    }
    if (s.view === 'member-report') return map[s.memberName] || null;
    return null;
  },

  // 본문은 상태가 아니라 DOM 으로 넣는다. 마크업의 자리(placeholder)를 그대로
  // 두고 그 안을 갈아끼우면, 목차 스크롤 스파이가 쓰는 data-section 이 살아 있다.
  async injectReportBody(reportId) {
    if (this._bodyShown === reportId) return;
    const anchor = document.querySelector('[data-section="items"]');
    if (!anchor) return;
    this._bodyShown = reportId;
    try {
      const b = await API.get('/reports/' + reportId + '/body?scope=.hr-body');
      if (this._bodyShown !== reportId) return;   // 그새 다른 사람으로 넘어갔다

      let style = document.getElementById('hr-report-css');
      if (!style) {
        style = document.createElement('style');
        style.id = 'hr-report-css';
        document.head.appendChild(style);
      }
      style.textContent = b.css;

      const holder = document.createElement('div');
      holder.className = 'hr-body';
      holder.innerHTML = b.html;

      const parent = anchor.parentNode;
      ['items', 'feedback', 'compare', 'next'].forEach((id) => {
        const el = parent.querySelector(':scope > [data-section="' + id + '"]');
        if (el) parent.removeChild(el);
      });
      parent.insertBefore(holder, parent.querySelector('[data-section="requests"]'));

      holder.querySelectorAll('[data-sentence-id]').forEach((el) => {
        el.style.cursor = 'pointer';
        el.title = '클릭하면 이 문장의 근거를 봅니다';
        el.onclick = () => this.showEvidence(reportId, el.dataset.sentenceId);
      });
    } catch (err) {
      this._bodyShown = null;
      this.showToast('본문을 불러오지 못했습니다 — ' + err.message);
    }
  },

  async showEvidence(reportId, sentenceId) {
    try {
      const e = await API.get('/reports/' + reportId + '/evidence?sentence_id='
        + encodeURIComponent(sentenceId));
      this.setState({
        reviewSelectedSentenceId: sentenceId,
        engineEvidence: e,
      });
      this.showToast('근거: ' + e.sourceRef);
    } catch (err) {
      this.showToast('근거를 찾지 못했습니다 — ' + err.message);
    }
  },

  // 검수 화면의 문장 목록은 마크업이 이미 그리고 있다. 데이터만 진짜로 바꾼다.
  async loadEvidenceList(reportId) {
    if (this._evidenceOf === reportId) return;
    this._evidenceOf = reportId;
    try {
      const ev = await API.get('/reports/' + reportId + '/evidence');
      if (this._evidenceOf !== reportId) return;
      this.setState({
        engineSentences: (ev.items || []).map((x) => ({
          id: x.sentenceId, text: x.aiText, aiText: x.aiText,
          sourceRef: x.sourceRef, sourceText: x.sourceText,
        })),
        reviewSelectedSentenceId: null,
      });
    } catch (err) {
      this._evidenceOf = null;
    }
  },
});

// ── 화면이 바뀔 때마다 본문을 맞춰 넣는다 ──────────────────────────────
const _mounted = Component.prototype.componentDidMount;
const _updated = Component.prototype.componentDidUpdate;

Component.prototype.componentDidMount = function () {
  if (_mounted) _mounted.call(this);
  this._syncReport();
};
Component.prototype.componentDidUpdate = function (a, b) {
  if (_updated) _updated.call(this, a, b);
  this._syncReport();
};
Component.prototype._syncReport = function () {
  const id = this.currentReportId();
  if (!id) {
    this._bodyShown = null;
    return;
  }
  if (this.state.view === 'member-report') this.injectReportBody(id);
  if (this.state.view === 'admin-review') this.loadEvidenceList(id);
};

// ── 가짜 함수를 진짜로 바꿔 끼운다 ────────────────────────────────────
// 마크업이 부르는 이름은 그대로 두고 알맹이만 바꾼다.
const _origRender = Component.prototype.render;
Component.prototype.render = function () {
  const props = _origRender.call(this);
  props.simulateUpload = () => this.realUpload();
  props.generateReport = () => this.realGenerate();

  // 판정 근거는 엔진이 준 문장을 그대로 보여 준다 (통합 명세: 화면에 그대로 노출)
  if (this.state.engineTypeReason) props.typeReason = this.state.engineTypeReason;
  if (this.state.engineCourseReason) {
    props.courseLinkReason = this.state.engineCourseReason;
    props.courseSuggestTitle = (this.state.engineCourseMode === 'create'
      ? '제안: 새 과정으로 생성' : '제안: ' + this.state.engineCourseTitle);
  }
  if (this.state.engineSummary) {
    const s = this.state.engineSummary;
    props.validationSummary = '인식 ' + s.recognized + '행 · 정상 ' + s.ok
      + ' · 오류 ' + s.errors + ' · 경고 ' + s.warnings;
  }
  if (this.state.engineSentences) {
    props.reviewFeedbackSentences = this.state.engineSentences.map((sent) => ({
      ...sent,
      bg: this.state.reviewSelectedSentenceId === sent.id
        ? 'var(--surface-pearl)' : 'transparent',
      onSelect: () => this.showEvidence(this.currentReportId(), sent.id),
    }));
    props.reviewEvidence = this.state.engineEvidence || null;
  }
  return props;
};
