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

// 무슨 일이 있었는지 브라우저 콘솔에서 볼 수 있게 남긴다.
// 화면만 보고는 "왜 안 되지"를 알 수 없어서, 개발자도구에 HR_DEBUG 를 쳐 보면
// 마지막 판정 결과·마지막 오류가 그대로 나오게 해 둔다.
const HR_DEBUG = { analyze: null, commit: null, lastError: null, calls: [] };
window.HR_DEBUG = HR_DEBUG;

function note(kind, detail) {
  HR_DEBUG.calls.push({ kind, detail, at: new Date().toISOString() });
  if (HR_DEBUG.calls.length > 40) HR_DEBUG.calls.shift();
  console.log('[엔진] ' + kind, detail);
}

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
    let msg;
    try {
      const j = await r.json();
      msg = j.detail || j.message || (r.status + ' ' + r.statusText);
    } catch (e) {
      msg = r.status + ' ' + r.statusText;
    }
    HR_DEBUG.lastError = { url: r.url, status: r.status, message: msg };
    console.error('[엔진] 실패', HR_DEBUG.lastError);
    return msg;
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
      HR_DEBUG.analyze = a;
      note('판정', { 유형: a.sourceType.type, 과정: a.courseMatch,
                     요약: a.summary, 문맥: a.context });
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
        engineFileName: a.filename,
        engineContext: a.context || {},
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

    note('카드 생성 요청', body);
    try {
      const c = await API.post('/uploads/' + s.draftId + '/commit', body);
      HR_DEBUG.commit = c;
      note('카드 생성 결과', { 과정: c.courseTitle, 카드: (c.cards || []).length,
                              리포트: (c.reports || []).length });
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

  // ── ④ 발송 · 추적 ────────────────────────────────────────────────
  async realTestSend() {
    try {
      const st = await API.get('/reports/mail/status');
      this.showToast(st.ready
        ? '발송 준비됨 — ' + (st.from || st.host)
        : '아직 못 보냅니다 — ' + st.note);
    } catch (err) {
      this.showToast('메일 설정을 읽지 못했습니다 — ' + err.message);
    }
  },

  async realSend() {
    const s = this.state;
    if (!s.uploadId) {
      this.showToast('먼저 파일을 올려 리포트를 만들어 주세요.');
      return;
    }
    this.setState({ sendStage: 'loading' });
    try {
      const r = await API.post('/reports/send/upload/' + s.uploadId, null);
      // 메일 설정이 없으면 서버가 실제로 보내지 않고 미리보기로 돌려준다.
      // 그걸 '성공'으로 칠하면 안 간 메일을 갔다고 보여 주게 된다.
      const dry = (r.results || []).some((x) => x.dry_run);
      const rows = (r.results || []).map((x) => ({
        name: x.person, empId: x.person, email: x.to || '(주소 없음)',
        failed: !x.sent, dryRun: !!x.dry_run,
        reason: x.reason || '',
      }));
      this.setState({ sendStage: 'tracking', sendRows: rows, sendDryRun: dry });
      this.showToast(dry
        ? '미리보기 — ' + (r.mail && r.mail.note || '') + ' (실제로 보내지 않았습니다)'
        : r.sent + '/' + r.total + '명에게 보냈습니다');
    } catch (err) {
      this.setState({ sendStage: 'before' });
      this.showToast('발송 실패 — ' + err.message);
    }
  },

  // ── ⑤ 인사이트 ──────────────────────────────────────────────────
  async loadInsight() {
    const key = this.state.insightCourse;
    if (!key || this._insightOf === key) return;
    this._insightOf = key;
    try {
      const d = await API.get('/insights/course/' + encodeURIComponent(key));
      if (this._insightOf !== key) return;
      this.setState({ engineInsight: d });
    } catch (err) {
      this._insightOf = null;
      this.setState({ engineInsight: null });
    }
  },

  async loadCourseList() {
    if (this._courseListLoaded) return;
    this._courseListLoaded = true;
    try {
      const d = await API.get('/insights/courses');
      this.setState({ engineCourses: d.courses || [] });
    } catch (err) {
      this._courseListLoaded = false;
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

// ── 인사이트 판을 실제 숫자로 다시 칠한다 ──────────────────────────────
// 이 화면의 마크업은 숫자가 대부분 박혀 있다(막대 높이까지). sc-for 로 묶인
// '항목별 비교' 하나만 데이터로 그린다. 나머지는 제목 글자로 자리를 찾아
// 그 안쪽만 갈아끼운다 — 카드 껍데기와 디자인은 그대로 두기 위해서다.
function findCard(title) {
  const all = document.querySelectorAll('div');
  for (let i = 0; i < all.length; i++) {
    const n = all[i];
    if (n.children.length === 0 && n.textContent.trim() === title) {
      return n.nextElementSibling;
    }
  }
  return null;
}

function findMetric(label) {
  const all = document.querySelectorAll('div');
  for (let i = 0; i < all.length; i++) {
    const n = all[i];
    if (n.children.length === 0 && n.textContent.trim() === label) {
      return n.nextElementSibling;
    }
  }
  return null;
}

function bars(host, items) {
  if (!host || !items.length) return;
  const top = Math.max.apply(null, items.map((x) => x.value || 0)) || 1;
  host.innerHTML = items.map((x, i) => {
    const h = Math.max(8, Math.round((x.value || 0) / top * 72));
    const last = i === items.length - 1;
    const fill = last ? 'var(--color-action-red)' : 'var(--color-ink-100)';
    const weight = last ? 'font-weight:700;' : 'color:var(--muted-fg);';
    return '<div style="display:flex;flex-direction:column;align-items:center;gap:8px">'
      + '<div style="width:36px;height:' + h + 'px;background:' + fill
      + ';border-radius:var(--radius-xs)"></div>'
      + '<span style="font-size:11px;white-space:nowrap;' + weight + '">'
      + (x.value === null || x.value === undefined ? '—' : x.value)
      + ' · ' + x.label + '</span></div>';
  }).join('');
}

function rows(host, items) {
  if (!host) return;
  host.innerHTML = items.length ? items.map((x) =>
    '<div style="display:flex;justify-content:space-between">'
    + '<span style="color:var(--muted-fg);white-space:nowrap">' + x.left + '</span>'
    + '<span style="white-space:nowrap">' + x.right + '</span></div>').join('')
    : '<div style="color:var(--muted-fg)">아직 비교할 자료가 없습니다.</div>';
}

function insightText(host, lines) {
  if (!host) return;
  host.innerHTML = lines.length ? lines.map((l) =>
    '💡 ' + l.text + '<br><span style="opacity:.65">근거: ' + l.basis + '</span>')
    .join('<br><br>') : '자동 인사이트를 낼 만한 자료가 아직 없습니다.';
}

// ── 검증 화면 머리말 ─────────────────────────────────────────────────
// 파일명과 "2026.08.03 10:24 업로드 · 강사 박지훈" 이 마크업에 글자로 박혀
// 있다. 무엇을 올리든 그 문구가 나온다 — 360 진단을 올렸는데 "리더십교육
// 3차 평가결과.xlsx" 라고 나온 것이 이것이다. 실제 값으로 갈아끼운다.
const DUMMY_FILE = '리더십교육_3차_평가결과.xlsx';

Object.assign(Component.prototype, {
  paintUploadHeader() {
    const name = this.state.engineFileName;
    if (!name) return;
    const all = document.querySelectorAll('div');
    for (let i = 0; i < all.length; i++) {
      const n = all[i];
      if (n.children.length || n.textContent.trim() !== DUMMY_FILE) continue;
      n.textContent = name;
      const sub = n.nextElementSibling;
      if (sub && !sub.children.length) {
        const ctx = this.state.engineContext || {};
        const bits = Object.keys(ctx)
          .filter((k) => k.indexOf('_') !== 0 && String(ctx[k]).trim())
          .slice(0, 4)
          .map((k) => k + ' ' + ctx[k]);
        // 메타 블록이 없는 파일도 있다(360 응답데이터가 그렇다).
        // 그럴 때 '정보 없음' 대신 파일에서 실제로 읽어 낸 것을 보여 준다.
        const a = HR_DEBUG.analyze || {};
        if (!bits.length && a.sheets) bits.push('시트 ' + a.sheets.join(', '));
        if (!bits.length && a.summary) bits.push('인식 ' + a.summary.recognized + '행');
        sub.textContent = bits.join(' · ') || '파일에서 읽은 정보 없음';
      }
      return;
    }
  },

  paintInsight() {
    const d = this.state.engineInsight;
    if (!d) return;
    const stamp = d.courseId + ':' + d.kind;
    if (this._paintedInsight === stamp) return;
    this._paintedInsight = stamp;

    const m = findMetric('최근 회차 평균');
    if (m) {
      const t = (d.trend || []).filter((x) => x.average !== null);
      m.textContent = d.kind === '단발특강'
        ? (d.average === null || d.average === undefined ? '—' : d.average)
        : (t.length ? t[t.length - 1].average : '—');
    }

    if (d.kind === '누적교육') {
      bars(findCard('회차별 평균 추이'),
        (d.trend || []).map((x) => ({ label: x.round, value: x.average })));
    } else if (d.kind === '진단서베이') {
      rows(findCard('관계별 결과'), (d.byRelation || []).map((r) => {
        const seq = Object.keys(r.byWave).map((w) => r.byWave[w])
          .filter((v) => v !== null && v !== undefined);
        const arrow = r.delta === null ? ''
          : ' <span style="color:var(--status-success)">'
            + (r.delta > 0 ? '▲' : r.delta < 0 ? '▼' : '―')
            + Math.abs(r.delta) + '</span>';
        return { left: r.relation, right: seq.join(' → ') + arrow };
      }));
      const waves = d.waves || [];
      bars(findCard('시행 회차 추이'), waves.map((w) => {
        const vals = (d.byRelation || []).map((r) => r.byWave[w])
          .filter((v) => v !== null && v !== undefined);
        const avg = vals.length
          ? Math.round(vals.reduce((a, b) => a + b, 0) / vals.length * 10) / 10 : null;
        return { label: w, value: avg };
      }));
    } else {
      bars(findCard('점수 분포'), (d.distribution || []).map((x) => ({
        label: x.from + '-' + x.to, value: x.count })));
      const sum = findCard('요약');
      if (sum) {
        sum.innerHTML = '평균 ' + (d.average === null ? '—' : d.average)
          + '점 · 인원 ' + d.people + '명<br>단일 회차 과정 — 회차 추이는 제공되지 않습니다.';
      }
    }

    // 자동 인사이트는 어두운 카드 안의 💡 로 시작하는 줄이다
    const all = document.querySelectorAll('div');
    for (let i = 0; i < all.length; i++) {
      const n = all[i];
      if (n.children.length === 0 && n.textContent.trim().indexOf('💡') === 0) {
        insightText(n, d.insights || []);
        break;
      }
    }
  },
});

// ── 뒤로가기 ────────────────────────────────────────────────────────
// 이 앱은 화면 전환이 전부 상태값이라 방문 기록이 쌓이지 않는다. 그래서
// 브라우저 뒤로가기를 누르면 앱 안에서 한 발 물러서는 게 아니라 앱을
// 통째로 빠져나간다. 업로드까지 해 놓고 실수로 누르면 처음부터 다시다.
//
// 화면 이름만 기록에 남긴다. 올린 파일이나 만든 카드는 그대로 두고 화면만
// 되돌린다 — 뒤로 갔다고 방금 만든 리포트가 사라지면 그게 더 놀랍다.
const NAV = ['view', 'subTab'];

function navKey(s) {
  return NAV.map((k) => s[k]).join('|');
}

Object.assign(Component.prototype, {
  _installHistory() {
    if (this._historyOn) return;
    this._historyOn = true;
    this._navAt = navKey(this.state);
    history.replaceState({ hr: this._navAt }, '');
    window.addEventListener('popstate', (e) => {
      const st = e.state && e.state.hr;
      if (!st) return;
      const parts = st.split('|');
      const patch = {};
      NAV.forEach((k, i) => { patch[k] = parts[i]; });
      this._navAt = st;                       // 되돌린 것은 다시 쌓지 않는다
      this._fromPop = true;
      this.setState(patch);
    });
  },

  _syncHistory() {
    const now = navKey(this.state);
    if (now === this._navAt) return;
    if (this._fromPop) { this._fromPop = false; this._navAt = now; return; }
    this._navAt = now;
    history.pushState({ hr: now }, '');
    this._paintBackButton();
  },

  // 화면 왼쪽 위에 작은 뒤로 버튼. 돌아갈 곳이 있을 때만 보인다.
  _paintBackButton() {
    let b = document.getElementById('hr-back');
    if (!b) {
      b = document.createElement('button');
      b.id = 'hr-back';
      b.type = 'button';
      b.textContent = '← 뒤로';
      b.title = '이전 화면으로 (브라우저 뒤로가기와 같습니다)';
      b.setAttribute('style', [
        'position:fixed', 'left:16px', 'top:14px', 'z-index:9000',
        'height:30px', 'padding:0 14px', 'font-size:12.5px',
        'font-family:inherit', 'border-radius:999px',
        'border:1px solid rgba(0,0,0,.14)', 'background:rgba(255,255,255,.92)',
        'color:#3a3a3a', 'cursor:pointer', 'backdrop-filter:blur(6px)',
        'box-shadow:0 1px 4px rgba(0,0,0,.10)',
      ].join(';'));
      b.onclick = () => history.back();
      document.body.appendChild(b);
    }
    // 첫 화면에서는 숨긴다 — 돌아갈 곳이 앱 바깥밖에 없다
    const home = this.state.view === 'landing';
    b.style.display = home ? 'none' : '';
  },
});

// ── 화면이 바뀔 때마다 본문을 맞춰 넣는다 ──────────────────────────────
const _mounted = Component.prototype.componentDidMount;
const _updated = Component.prototype.componentDidUpdate;

Component.prototype.componentDidMount = function () {
  if (_mounted) _mounted.call(this);
  this._installHistory();
  this._paintBackButton();
  this._syncReport();
};
Component.prototype.componentDidUpdate = function (a, b) {
  if (_updated) _updated.call(this, a, b);
  this._installHistory();          // 마운트가 이미 지났을 수도 있다
  this._syncHistory();
  this._paintBackButton();
  this._syncReport();
};
Component.prototype._syncReport = function () {
  const s = this.state;
  const id = this.currentReportId();
  if (!id) this._bodyShown = null;
  if (id && s.view === 'member-report') this.injectReportBody(id);
  if (id && s.view === 'admin-review') this.loadEvidenceList(id);

  if (s.view === 'admin-validate') {
    this.paintUploadHeader();
    this.loadCourseList();          // '다른 과정 선택' 목록을 엔진 것으로 채운다
  }

  if (s.view === 'admin' && s.subTab === 'insight') {
    this.loadCourseList();
    this.loadInsight();
    this.paintInsight();
  } else {
    this._paintedInsight = null;      // 탭을 떠나면 다음에 다시 칠한다
  }
};

// ── 가짜 함수를 진짜로 바꿔 끼운다 ────────────────────────────────────
// 마크업이 쓰는 값은 `renderVals()` 가 돌려주는 객체다. 그 위에 덧칠한다.
// 이름을 잘못 짚으면 아무 일도 안 일어나고 예전 더미가 그대로 보이므로,
// 원본에서 실제 이름을 확인하고 맞춘 것들이다 (render 가 아니라 renderVals).
const _origVals = Component.prototype.renderVals;
if (typeof _origVals !== 'function') {
  console.error('[엔진 연결] renderVals 를 찾지 못했습니다 — 프로토타입 구조가 바뀌었습니다.');
}
Component.prototype.renderVals = function () {
  const s = this.state;
  const props = _origVals.call(this);

  props.simulateUpload = () => this.realUpload();
  props.generateReport = () => this.realGenerate();
  props.startSend = () => this.realSend();
  props.testSend = () => this.realTestSend();

  // 판정 근거는 엔진이 준 문장을 그대로 보여 준다 (통합 명세 §2-①)
  if (s.engineTypeReason) props.reportTypeReason = s.engineTypeReason;
  if (s.engineCourseReason) {
    props.courseLinkSuggestReason = s.engineCourseReason;
    props.courseLinkSuggestTitle = (s.engineCourseMode === 'create'
      ? '제안: 새 과정으로 생성' : '제안: ' + (s.engineCourseTitle || ''));
    props.courseLinkSuggestIsLink = s.engineCourseMode === 'link';
  }

  // 검수 화면 — 문장과 근거를 실제 리포트에서 가져온다
  if (s.engineSentences) {
    props.reviewFeedbackSentences = s.engineSentences.map((sent) => ({
      ...sent,
      bg: s.reviewSelectedSentenceId === sent.id
        ? 'var(--surface-pearl)' : 'transparent',
      onSelect: () => this.showEvidence(this.currentReportId(), sent.id),
    }));
    props.reviewEvidence = s.engineEvidence || null;
  }

  // 과정 연결 — 원래 함수들은 더미 상수(COURSE_LINK_SUGGESTIONS)와 더미 과정
  // 목록(COURSES)을 본다. 그대로 두면 무엇을 올리든 '리더십 교육' 에 붙고,
  // 새 과정을 만들면 엔진에 없는 adhoc-<시각> 키가 생겨 카드 생성이 404 로
  // 실패한다. 리포트가 안 만들어지던 원인이 이것이다.
  if (s.draftId) {
    props.linkThisCourse = () => this.setState({
      courseLinkStatus: 'linked',
      linkedCourseKey: s.linkedCourseKey,          // 엔진이 준 courseId 그대로
      courseLinkChoiceLabel: s.engineCourseTitle || s.courseLinkChoiceLabel,
    });
    props.confirmCreateCourse = () => {
      const title = (s.newCourseName || '').trim();
      if (!title) return;
      // 실제 발급은 커밋 때 엔진이 한다 — 여기서 가짜 키를 만들지 않는다
      this.setState({ courseLinkStatus: 'linked', linkedCourseKey: null,
                      courseLinkChoiceLabel: title });
    };
    // 원래 함수는 입력칸을 비운다. 엔진이 읽어 낸 과정명을 미리 채워 둔다 —
    // 매번 손으로 다시 적게 하면 표기가 조금씩 달라져 과정이 갈라진다.
    props.openLinkCreate = () => this.setState({
      courseLinkStatus: 'creating',
      newCourseName: s.newCourseName || s.engineCourseTitle || '',
    });
    props.onLinkSelectChange = (e) => {
      const key = e.target.value;
      if (!key) return;
      const c = (s.engineCourses || []).find((x) => x.courseId === key);
      this.setState({ courseLinkStatus: 'linked', linkedCourseKey: key,
                      courseLinkChoiceLabel: (c ? c.title : key) + ' (선택한 과정)' });
    };
    props.backToLinkIdle = () => this.setState({
      courseLinkStatus: 'idle', newCourseName: '',
      courseLinkChoiceLabel: '', linkedCourseKey: null,
    });
    props.linkSelectOptions = [{ value: '', label: '과정 검색·선택' }].concat(
      (s.engineCourses || []).map((c) => ({ value: c.courseId, label: c.title })));
  }

  // 인사이트 — 과정 목록과 항목별 비교는 프롭으로, 나머지는 paintInsight 가 칠한다
  if (s.engineCourses) {
    const uiType = TYPE_TO_ENGINE[s.insightType];
    props.insightCourseOptions = s.engineCourses
      .filter((c) => !c.sourceType || c.sourceType === uiType)
      .map((c) => ({ value: c.courseId, label: c.title }));
  }
  if (s.engineInsight && s.engineInsight.kind === '누적교육') {
    props.accItemComparison = (s.engineInsight.areas || []).map((a) => ({
      label: a.area, thisRound: a.latest, avg: a.courseAverage,
    }));
  } else if (s.engineInsight) {
    props.accItemComparison = [];
  }

  // 발송 — 미리보기(실제로 안 보낸 것)를 성공으로 칠하지 않는다
  if (s.sendRows) {
    props.recipients = s.sendRows.map((r, i) => ({
      ...r,
      statusLabel: r.dryRun ? '미리보기' : (r.failed ? '실패' : '성공'),
      statusTone: r.dryRun ? 'orange' : (r.failed ? 'red' : 'success'),
      rowBg: r.failed && !r.dryRun ? 'rgba(234,0,44,0.04)' : 'transparent',
      trackText: r.reason || (r.failed ? '발송 실패' : '미열람'),
      onResend: () => this.realSend(),
    }));
    props.sendFailCount = s.sendRows.filter((r) => r.failed && !r.dryRun).length;
    props.sendSuccessCount = s.sendRows.filter((r) => !r.failed).length;
  }
  // 과정·유형을 바꾸면 다시 불러오게 표시를 지운다.
  // 프로토타입이 아니라 props 로 갈아끼우는 이유 — 원래 함수들이 클래스 필드
  // (인스턴스 속성)라서 프로토타입에 덮어써도 인스턴스 쪽이 이긴다.
  props.onInsightCourseChange = (e) => {
    this._insightOf = null;
    this._paintedInsight = null;
    this.setState({ insightCourse: e.target.value });
  };
  props.setInsightType = (t) => {
    this._insightOf = null;
    this._paintedInsight = null;
    const list = (s.engineCourses || [])
      .filter((c) => !c.sourceType || c.sourceType === TYPE_TO_ENGINE[t]);
    this.setState({ insightType: t,
                    insightCourse: list.length ? list[0].courseId : '' });
  };
  return props;
};
