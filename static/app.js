/* ==========================================================================
   QuarkMover Studio Frontend Controller
   ========================================================================== */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);

  // ---- State ----
  let currentMode = 'reshare_only';
  let defaultPromptCache = '';
  let lastGeneratedLinks = [];
  let loginPollTimer = null;
  let jobPollTimer = null;
  let elapsedTickTimer = null;
  let jobStartMs = 0;
  let stepRecords = [];
  let isLoggedIn = false;

  const THEME_KEY = 'quark-mover-theme';
  const SHARE_DETECT_RE = /(?:https?:\/\/)?pan\.qu?o?ark\.cn\/s\/([A-Za-z0-9]+)(?:\?[^\s#"<>]*)?/gi;
  const PWD_QUERY_RE = /[?&]pwd=([A-Za-z0-9]{4,8})/i;
  const PWD_TEXT_RE = /(?:提取码|口令|密码)\s*[:：]?\s*([A-Za-z0-9]{4})\b/;

  // ---- Utilities ----
  function escapeHtml(str) {
    return String(str || '').replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[ch]));
  }

  async function apiGet(url) {
    const res = await fetch(url, { cache: 'no-store' });
    return res.json();
  }

  async function apiPost(url, payload) {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: payload ? JSON.stringify(payload) : '{}',
    });
    return res.json();
  }

  function showToast(message, type = 'ok') {
    const stack = $('toastStack');
    if (!stack) return;
    const item = document.createElement('div');
    item.className = 'toast ' + type;
    const svg = type === 'err'
      ? '<svg class="icon icon-sm toast-icon" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 8v4m0 4h.01"/></svg>'
      : '<svg class="icon icon-sm toast-icon" viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>';
    item.innerHTML = svg + '<span>' + escapeHtml(message) + '</span>';
    stack.appendChild(item);
    setTimeout(() => {
      item.style.opacity = '0';
      item.style.transform = 'translateY(6px) scale(0.97)';
      item.style.transition = 'opacity 160ms ease, transform 160ms ease';
      setTimeout(() => item.remove(), 180);
    }, 2600);
  }

  async function copyToClipboard(text, label = '已复制到剪贴板') {
    if (!text) return;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
      }
      showToast(label, 'ok');
    } catch (e) {
      showToast('复制失败，请手动选中复制', 'err');
    }
  }

  // ---- Theme Switcher ----
  function getSavedTheme() {
    return localStorage.getItem(THEME_KEY) || 'system';
  }

  function applyTheme(theme) {
    const root = document.documentElement;
    if (theme === 'light' || theme === 'dark') {
      root.setAttribute('data-theme', theme);
    } else {
      root.removeAttribute('data-theme');
    }
    document.querySelectorAll('.theme-opt').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.themeVal === theme);
    });
  }

  document.querySelectorAll('.theme-opt').forEach((btn) => {
    btn.addEventListener('click', () => {
      const val = btn.dataset.themeVal || 'system';
      localStorage.setItem(THEME_KEY, val);
      applyTheme(val);
    });
  });
  applyTheme(getSavedTheme());

  // ---- Modal Helpers ----
  function openModal(id) {
    const el = $(id);
    if (el) el.setAttribute('data-open', 'true');
  }

  function closeModal(id) {
    const el = $(id);
    if (el) el.setAttribute('data-open', 'false');
    if (id === 'loginModal') {
      if (loginPollTimer) clearTimeout(loginPollTimer);
      apiPost('/api/login/stop').catch(() => {});
    }
  }

  document.querySelectorAll('[data-close-modal]').forEach((btn) => {
    btn.addEventListener('click', () => closeModal(btn.dataset.closeModal));
  });

  document.querySelectorAll('.modal-backdrop').forEach((bg) => {
    bg.addEventListener('click', (e) => {
      if (e.target === bg) closeModal(bg.id);
    });
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      document.querySelectorAll('.modal-backdrop[data-open="true"]').forEach((bg) => {
        closeModal(bg.id);
      });
    }
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      const anyOpenModal = document.querySelector('.modal-backdrop[data-open="true"]');
      if (!anyOpenModal && !$('runBtn').disabled) {
        e.preventDefault();
        $('runBtn').click();
      }
    }
  });

  // ---- System State Synchronization ----
  async function syncSystemState(force = false) {
    try {
      const s = await apiGet('/api/state' + (force ? '?force=1' : ''));
      isLoggedIn = Boolean(s.logged_in);

      // Quark Pill
      const qPill = $('pillQuark');
      const qText = $('pillQuarkText');
      if (s.logged_in) {
        const nick = s.account && s.account.username ? s.account.username : '夸克已连接';
        const quota = s.account && s.account.quota ? ` · ${s.account.quota}` : '';
        qText.textContent = nick + quota;
        qPill.className = 'status-pill ok';
        $('loginBtnLabel').textContent = '账号管理';
        $('logoutBtn').style.display = 'inline-flex';
      } else {
        qText.textContent = '夸克未登录 · 点击连接';
        qPill.className = 'status-pill warn';
        $('loginBtnLabel').textContent = '扫码登录';
        $('logoutBtn').style.display = 'none';
      }

      // AI Pill
      const aiPill = $('pillAI');
      const aiText = $('pillAIText');
      if (s.has_key) {
        aiText.textContent = 'AI · ' + (s.ai_model || '已就绪');
        aiPill.className = 'status-pill ok';
      } else {
        aiText.textContent = 'AI 未配置';
        aiPill.className = 'status-pill';
      }

      // Target Directory Pill
      $('pillDirText').textContent = s.target_dir || '/QuarkMover';

      // Quick Share Controls
      $('quickPasscode').checked = Boolean(s.share_use_passcode);
      $('quickExpired').value = String(s.share_expired_type || 1);
      return s;
    } catch (e) {
      return null;
    }
  }

  // ---- Mode Switch & Live Link Detector ----
  function setMode(mode) {
    currentMode = mode === 'full' ? 'full' : 'reshare_only';
    const tabs = $('modeTabs');
    tabs.setAttribute('data-mode', currentMode);
    tabs.querySelectorAll('.mode-tab').forEach((btn) => {
      const active = btn.dataset.mode === currentMode;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });

    if (currentMode === 'reshare_only') {
      $('inputCardTitle').textContent = '输入分享链接';
      $('inputEditor').placeholder = '支持直接粘贴单条或多条夸克链接（如 pan.quark.cn/s/xxxxx），也支持粘贴带提取码的完整分享口令，自动识别提取码并批量转存换链…';
      $('runBtnLabel').textContent = '立即转存换链';
    } else {
      $('inputCardTitle').textContent = '输入对标推文与链接';
      $('inputEditor').placeholder = '在此粘贴对标账号的完整推文内容（含 pan.quark.cn/s/xxxxx 分享链接），AI 将重写原创文案并自动替换为你自己的网盘链接…';
      $('runBtnLabel').textContent = '一键二创并转存';
    }
    inspectInputContent();
  }

  document.querySelectorAll('.mode-tab').forEach((btn) => {
    btn.addEventListener('click', () => setMode(btn.dataset.mode));
  });

  function inspectInputContent() {
    const raw = $('inputEditor').value || '';
    $('inputCount').textContent = raw.length + ' 字符';

    const seen = new Set();
    let hasPwd = false;
    SHARE_DETECT_RE.lastIndex = 0;
    let m;
    while ((m = SHARE_DETECT_RE.exec(raw)) !== null) {
      seen.add(m[1]);
      if (PWD_QUERY_RE.test(m[0])) {
        hasPwd = true;
      } else {
        const tail = raw.slice(m.index + m[0].length, m.index + m[0].length + 60);
        if (PWD_TEXT_RE.test(tail)) hasPwd = true;
      }
    }

    const badge = $('detectedBadge');
    if (!raw.trim()) {
      badge.textContent = '等待粘贴链接';
      badge.className = 'badge tabular';
    } else if (seen.size > 0) {
      badge.textContent = `已识别 ${seen.size} 条链接` + (hasPwd ? ' · 含提取码' : '');
      badge.className = 'badge brand tabular';
    } else if (currentMode === 'full') {
      badge.textContent = '纯文案二创（无网盘链接）';
      badge.className = 'badge warn tabular';
    } else {
      badge.textContent = '未检测到夸克链接';
      badge.className = 'badge warn tabular';
    }
  }

  $('inputEditor').addEventListener('input', inspectInputContent);
  $('outputEditor').addEventListener('input', () => {
    const val = $('outputEditor').value || '';
    $('outputCount').textContent = val.length + ' 字符';
    $('copyAllBtn').disabled = !val.trim();
  });

  // ---- Quick Share Controls Auto-Save ----
  async function saveQuickShareOptions() {
    try {
      await apiPost('/api/config', {
        share_use_passcode: $('quickPasscode').checked,
        share_expired_type: parseInt($('quickExpired').value, 10) || 1,
      });
      showToast('分享偏好已更新', 'ok');
    } catch (e) {
      showToast('保存分享偏好失败', 'err');
    }
  }

  $('quickPasscode').addEventListener('change', saveQuickShareOptions);
  $('quickExpired').addEventListener('change', saveQuickShareOptions);

  // ---- Paste & Clear Buttons ----
  $('pasteBtn').addEventListener('click', async () => {
    try {
      const clip = await navigator.clipboard.readText();
      if (clip) {
        const editor = $('inputEditor');
        editor.value = editor.value.trim() ? editor.value.trim() + '\n' + clip.trim() : clip.trim();
        inspectInputContent();
        editor.focus();
        showToast('已粘贴剪贴板内容', 'ok');
      } else {
        showToast('剪贴板为空', 'err');
      }
    } catch (e) {
      $('inputEditor').focus();
      showToast('请按 ⌘V / Ctrl+V 粘贴内容', 'err');
    }
  });

  $('clearBtn').addEventListener('click', () => {
    $('inputEditor').value = '';
    $('outputEditor').value = '';
    $('outputCount').textContent = '0 字符';
    $('shareCards').innerHTML = '';
    $('outputAlert').className = 'inline-alert';
    $('resultContent').classList.add('hidden');
    $('resultEmpty').classList.remove('hidden');
    $('outputBadge').textContent = '就绪';
    $('outputBadge').className = 'badge tabular';
    $('copyAllBtn').disabled = true;
    $('copyLinksOnlyBtn').disabled = true;
    lastGeneratedLinks = [];
    resetPipelineBox();
    inspectInputContent();
  });

  // ---- Login Modal (QR + Manual Cookie) ----
  function switchLoginTab(tab) {
    document.querySelectorAll('[data-login-tab]').forEach((b) => {
      b.classList.toggle('active', b.dataset.loginTab === tab);
    });
    $('loginPaneQr').style.display = tab === 'qr' ? 'block' : 'none';
    $('loginPaneCookie').style.display = tab === 'cookie' ? 'block' : 'none';
  }

  document.querySelectorAll('[data-login-tab]').forEach((btn) => {
    btn.addEventListener('click', () => switchLoginTab(btn.dataset.loginTab));
  });

  async function openAndStartLogin() {
    openModal('loginModal');
    switchLoginTab('qr');
    const img = $('qrImage');
    img.removeAttribute('src');
    img.classList.remove('ready');
    $('qrPlaceholder').style.display = 'flex';
    $('qrPlaceholderText').textContent = '正在启动安全无头浏览器…';
    $('qrStatusText').textContent = '正在获取夸克登录二维码…';
    $('qrStatusText').className = 'qr-caption';
    $('qrMetaText').textContent = '请使用手机「夸克 App」扫码授权';

    try {
      await apiPost('/api/login/start');
      pollLoginWorker();
    } catch (e) {
      $('qrStatusText').textContent = '启动登录服务失败';
      $('qrStatusText').className = 'qr-caption err';
      $('qrMetaText').textContent = String(e);
    }
  }

  $('loginBtn').addEventListener('click', openAndStartLogin);
  $('pillQuark').addEventListener('click', openAndStartLogin);

  $('refreshQrBtn').addEventListener('click', async () => {
    $('qrImage').classList.remove('ready');
    $('qrPlaceholder').style.display = 'flex';
    $('qrPlaceholderText').textContent = '正在刷新二维码…';
    $('qrStatusText').textContent = '正在重新加载二维码…';
    $('qrStatusText').className = 'qr-caption';
    try {
      await apiPost('/api/login/refresh');
      pollLoginWorker();
    } catch (e) {
      showToast('刷新二维码失败', 'err');
    }
  });

  async function pollLoginWorker() {
    if (loginPollTimer) clearTimeout(loginPollTimer);
    if ($('loginModal').getAttribute('data-open') !== 'true') return;

    try {
      const s = await apiGet('/api/login/state');
      if (s.qr_data_url) {
        $('qrImage').src = s.qr_data_url;
        $('qrImage').classList.add('ready');
        $('qrPlaceholder').style.display = 'none';
      }

      if (s.state === 'starting') {
        $('qrStatusText').textContent = '正在启动浏览器加载夸克页面…';
        $('qrStatusText').className = 'qr-caption';
      } else if (s.state === 'waiting_scan') {
        $('qrStatusText').textContent = '请使用夸克 App 扫描二维码';
        $('qrStatusText').className = 'qr-caption';
        const rem = s.remaining_seconds ? `（剩余有效期 ${s.remaining_seconds}s）` : '';
        $('qrMetaText').textContent = '扫码并在手机上确认后将自动完成连接 ' + rem;
      } else if (s.state === 'logged_in') {
        $('qrStatusText').textContent = '连接成功！正在同步账号信息…';
        $('qrStatusText').className = 'qr-caption ok';
        const nick = s.account && s.account.username ? `欢迎你，${s.account.username}` : '登录状态已保存';
        $('qrMetaText').textContent = nick;
        await syncSystemState(true);
        showToast('夸克网盘账号登录成功', 'ok');
        setTimeout(() => closeModal('loginModal'), 750);
        return;
      } else if (s.state === 'failed') {
        $('qrPlaceholder').style.display = 'flex';
        $('qrPlaceholderText').textContent = '二维码获取中断';
        $('qrStatusText').textContent = '未能完成扫码登录';
        $('qrStatusText').className = 'qr-caption err';
        $('qrMetaText').textContent = s.error || '可点击「刷新二维码」重试，或切换至「手动粘贴 Cookie」';
        return;
      }
    } catch (e) {}

    if ($('loginModal').getAttribute('data-open') === 'true') {
      loginPollTimer = setTimeout(pollLoginWorker, 1400);
    }
  }

  $('saveCookieBtn').addEventListener('click', async () => {
    const raw = $('manualCookieInput').value.trim();
    if (!raw) {
      showToast('请先粘贴 Cookie 字符串', 'err');
      return;
    }
    const btn = $('saveCookieBtn');
    btn.disabled = true;
    btn.textContent = '验证中…';
    try {
      const r = await apiPost('/api/login/cookie', { cookie: raw });
      if (!r.ok) {
        showToast(r.error || 'Cookie 无效', 'err');
        return;
      }
      $('manualCookieInput').value = '';
      await syncSystemState(true);
      showToast('Cookie 验证通过，账号已连接', 'ok');
      closeModal('loginModal');
    } catch (e) {
      showToast('保存 Cookie 失败: ' + e, 'err');
    } finally {
      btn.disabled = false;
      btn.textContent = '验证并保存 Cookie';
    }
  });

  $('logoutBtn').addEventListener('click', async () => {
    try {
      await apiPost('/api/login/logout');
      await syncSystemState(true);
      showToast('已退出当前夸克账号', 'ok');
      closeModal('loginModal');
    } catch (e) {
      showToast('退出失败', 'err');
    }
  });

  // ---- Settings Modal & AI Test ----
  async function openSettingsModal(focusFieldId) {
    $('aiTestResult').textContent = '';
    try {
      const cfg = await apiGet('/api/config');
      $('cfgKey').value = cfg.ai_api_key || '';
      $('cfgBaseUrl').value = cfg.ai_base_url || 'https://api.deepseek.com';
      $('cfgModel').value = cfg.ai_model || 'deepseek-chat';
      $('cfgPrompt').value = cfg.rewrite_prompt || '';
      $('cfgTargetDir').value = cfg.target_dir || '/QuarkMover';
      $('cfgExpired').value = String(cfg.share_expired_type || 1);
      $('cfgPasscode').checked = Boolean(cfg.share_use_passcode);
      defaultPromptCache = cfg.default_prompt || '';
    } catch (e) {}
    openModal('settingsModal');
    if (focusFieldId && $(focusFieldId)) {
      setTimeout(() => $(focusFieldId).focus(), 80);
    }
  }

  $('settingsBtn').addEventListener('click', () => openSettingsModal());
  $('pillAI').addEventListener('click', () => openSettingsModal('cfgKey'));
  $('pillDir').addEventListener('click', () => openSettingsModal('cfgTargetDir'));

  document.querySelectorAll('.preset-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      $('cfgBaseUrl').value = chip.dataset.base || '';
      $('cfgModel').value = chip.dataset.model || '';
      showToast(`已填入 ${chip.textContent} 接口预设`, 'ok');
    });
  });

  $('toggleKeyVis').addEventListener('click', () => {
    const inp = $('cfgKey');
    inp.type = inp.type === 'password' ? 'text' : 'password';
  });

  $('resetPromptBtn').addEventListener('click', () => {
    if (defaultPromptCache) {
      $('cfgPrompt').value = defaultPromptCache;
      showToast('已恢复默认改写指令', 'ok');
    }
  });

  $('testAiBtn').addEventListener('click', async () => {
    const btn = $('testAiBtn');
    const out = $('aiTestResult');
    btn.disabled = true;
    btn.textContent = '连接中…';
    out.textContent = '正在请求 AI 接口…';
    out.style.color = 'var(--text-muted)';
    try {
      const r = await apiPost('/api/ai/test', {
        ai_api_key: $('cfgKey').value,
        ai_base_url: $('cfgBaseUrl').value,
        ai_model: $('cfgModel').value,
      });
      if (r.ok) {
        out.textContent = `连通正常 · ${r.latency_ms}ms (${r.model})`;
        out.style.color = 'var(--ok)';
        showToast(`AI 接口连通成功 (${r.latency_ms}ms)`, 'ok');
      } else {
        out.textContent = r.error || '连接失败';
        out.style.color = 'var(--err)';
      }
    } catch (e) {
      out.textContent = '测试请求异常: ' + e;
      out.style.color = 'var(--err)';
    } finally {
      btn.disabled = false;
      btn.textContent = '测试连通性';
    }
  });

  $('saveSettingsBtn').addEventListener('click', async () => {
    const btn = $('saveSettingsBtn');
    btn.disabled = true;
    try {
      await apiPost('/api/config', {
        ai_api_key: $('cfgKey').value.trim(),
        ai_base_url: $('cfgBaseUrl').value.trim() || 'https://api.deepseek.com',
        ai_model: $('cfgModel').value.trim() || 'deepseek-chat',
        rewrite_prompt: $('cfgPrompt').value,
        target_dir: $('cfgTargetDir').value.trim() || '/QuarkMover',
        share_expired_type: parseInt($('cfgExpired').value, 10) || 1,
        share_use_passcode: $('cfgPasscode').checked,
      });
      closeModal('settingsModal');
      await syncSystemState();
      showToast('系统设置已保存', 'ok');
    } catch (e) {
      showToast('保存设置失败: ' + e, 'err');
    } finally {
      btn.disabled = false;
    }
  });

  // ---- Promo Modal ----
  $('openPromoBtn').addEventListener('click', () => openModal('promoModal'));

  // ---- Pipeline Runner & Stepper ----
  function resetPipelineBox() {
    if (jobPollTimer) clearTimeout(jobPollTimer);
    if (elapsedTickTimer) clearInterval(elapsedTickTimer);
    const box = $('pipelineBox');
    box.className = 'pipeline-box';
    $('pipelineSteps').innerHTML = '';
    $('pipelineElapsed').textContent = '0.0s';
    stepRecords = [];
  }

  function startPipelineBox() {
    resetPipelineBox();
    jobStartMs = Date.now();
    const box = $('pipelineBox');
    box.classList.add('show');
    $('pipelineTitle').textContent = '正在执行自动化流程…';
    elapsedTickTimer = setInterval(() => {
      $('pipelineElapsed').textContent = ((Date.now() - jobStartMs) / 1000).toFixed(1) + 's';
    }, 100);
  }

  function appendStepLog(text) {
    if (!text) return;
    if (stepRecords.length && stepRecords[stepRecords.length - 1] === text) return;
    stepRecords.push(text);
    const elapsed = ((Date.now() - jobStartMs) / 1000).toFixed(1) + 's';
    const container = $('pipelineSteps');
    container.querySelectorAll('.p-step.active').forEach((el) => {
      el.classList.remove('active');
      el.classList.add('done');
    });
    const row = document.createElement('div');
    row.className = 'p-step active';
    row.innerHTML = `<span class="p-step-time tabular">${elapsed}</span><span>${escapeHtml(text)}</span>`;
    container.appendChild(row);
    container.scrollTop = container.scrollHeight;
  }

  function renderShareCards(shares) {
    const wrap = $('shareCards');
    wrap.innerHTML = '';
    if (!Array.isArray(shares) || !shares.length) return;

    shares.forEach((item) => {
      const card = document.createElement('div');
      if (item.error && !item.new_link) {
        card.className = 'share-card has-err';
        card.innerHTML = `
          <div class="share-card-top">
            <span class="share-card-title">链接 ${escapeHtml(item.pwd_id)} 转存失败</span>
            <span class="badge warn">失败</span>
          </div>
          <div style="font-size:0.8125rem;color:var(--err);">${escapeHtml(item.error)}</div>
        `;
        wrap.appendChild(card);
        return;
      }

      card.className = 'share-card';
      const fileLabel = item.file_count ? `${item.file_count} 个文件` : '已转存';
      const pwdBadge = item.passcode ? `<span class="badge brand">提取码 ${escapeHtml(item.passcode)}</span>` : '<span class="badge ok">公开免密</span>';

      card.innerHTML = `
        <div class="share-card-top">
          <span class="share-card-title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</span>
          <div style="display:flex;gap:6px;flex-shrink:0;">
            <span class="badge tabular">${fileLabel}</span>
            ${pwdBadge}
          </div>
        </div>
        <div class="share-url-row">
          <a class="share-url-mono" href="${escapeHtml(item.new_link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.new_link)}</a>
          <div class="share-card-btns">
            <button type="button" class="btn btn-sm btn-copy-one" data-link="${escapeHtml(item.new_link)}">复制链接</button>
            <button type="button" class="btn btn-ghost btn-sm btn-copy-block" data-block="${escapeHtml(item.quark_block || item.new_link)}">复制口令</button>
          </div>
        </div>
      `;
      wrap.appendChild(card);
    });

    wrap.querySelectorAll('.btn-copy-one').forEach((b) => {
      b.addEventListener('click', () => copyToClipboard(b.dataset.link, '已复制新分享链接'));
    });
    wrap.querySelectorAll('.btn-copy-block').forEach((b) => {
      b.addEventListener('click', () => copyToClipboard(b.dataset.block, '已复制夸克完整口令'));
    });
  }

  $('runBtn').addEventListener('click', async () => {
    const text = $('inputEditor').value.trim();
    if (!text) {
      showToast('请先输入或粘贴分享链接/推文内容', 'err');
      $('inputEditor').focus();
      return;
    }

    $('runBtn').disabled = true;
    startPipelineBox();

    try {
      const r = await apiPost('/api/generate', { text, mode: currentMode });
      if (!r.ok) {
        if (elapsedTickTimer) clearInterval(elapsedTickTimer);
        $('pipelineBox').classList.add('failed');
        $('pipelineTitle').textContent = r.error || '提交任务失败';
        $('runBtn').disabled = false;
        return;
      }
      pollJobStatus(r.job_id);
    } catch (e) {
      if (elapsedTickTimer) clearInterval(elapsedTickTimer);
      $('pipelineBox').classList.add('failed');
      $('pipelineTitle').textContent = '网络请求异常: ' + e;
      $('runBtn').disabled = false;
    }
  });

  async function pollJobStatus(jobId) {
    try {
      const snap = await apiGet('/api/job/' + jobId);
      const label = snap.step + (snap.sub ? ` · ${snap.sub}` : '');

      if (snap.status === 'running') {
        appendStepLog(label);
        jobPollTimer = setTimeout(() => pollJobStatus(jobId), 650);
        return;
      }

      if (elapsedTickTimer) clearInterval(elapsedTickTimer);
      const totalSec = ((Date.now() - jobStartMs) / 1000).toFixed(1) + 's';
      $('pipelineElapsed').textContent = totalSec;
      $('runBtn').disabled = false;

      $('pipelineSteps').querySelectorAll('.p-step.active').forEach((el) => {
        el.classList.remove('active');
        el.classList.add('done');
      });

      if (snap.status === 'failed') {
        $('pipelineBox').classList.add('failed');
        $('pipelineTitle').textContent = snap.error || '处理失败';
        showToast(snap.error || '任务执行失败', 'err');
        return;
      }

      const res = snap.result || {};
      const warnings = [];
      if (res.rewrite_error) warnings.push('AI 二创：' + res.rewrite_error);
      if (res.quark_error) warnings.push('夸克转存：' + res.quark_error);

      const hasValidResult = Boolean((res.all_links && res.all_links.length) || res.output);

      if (hasValidResult) {
        $('pipelineBox').classList.add('done');
        $('pipelineTitle').textContent = '转存与生成已完成';
      } else {
        $('pipelineBox').classList.add('failed');
        $('pipelineTitle').textContent = warnings[0] || '未能生成有效结果';
      }

      // Populate Right Output Panel
      $('resultEmpty').classList.add('hidden');
      $('resultContent').classList.remove('hidden');

      const alertBox = $('outputAlert');
      if (warnings.length) {
        alertBox.className = 'inline-alert show ' + (hasValidResult ? 'warn' : 'err');
        $('outputAlertText').textContent = warnings.join(' ｜ ');
      } else {
        alertBox.className = 'inline-alert';
      }

      renderShareCards(res.shares || []);

      $('outputEditor').value = res.output || '';
      const outLen = (res.output || '').length;
      $('outputCount').textContent = outLen + ' 字符';
      $('copyAllBtn').disabled = !outLen;

      lastGeneratedLinks = res.all_links || (res.new_link ? [res.new_link] : []);
      $('copyLinksOnlyBtn').disabled = lastGeneratedLinks.length === 0;

      if (lastGeneratedLinks.length > 0) {
        $('outputBadge').textContent = `已生成 ${lastGeneratedLinks.length} 条新链接 · ${totalSec}`;
        $('outputBadge').className = 'badge ok tabular';
        showToast('转存换链完成！', 'ok');
      } else if (res.rewritten) {
        $('outputBadge').textContent = `已完成文案二创 · ${totalSec}`;
        $('outputBadge').className = 'badge ok tabular';
      } else {
        $('outputBadge').textContent = '需要处理异常';
        $('outputBadge').className = 'badge warn tabular';
        // 若因未登录失败，同步刷新顶部状态提示
        syncSystemState(true);
      }

      loadHistoryList();
    } catch (e) {
      if (elapsedTickTimer) clearInterval(elapsedTickTimer);
      $('runBtn').disabled = false;
      $('pipelineBox').classList.add('failed');
      $('pipelineTitle').textContent = '轮询任务状态失败: ' + e;
    }
  }

  $('copyAllBtn').addEventListener('click', () => {
    copyToClipboard($('outputEditor').value, '已复制完整推文与分享链接');
  });

  $('copyLinksOnlyBtn').addEventListener('click', () => {
    if (lastGeneratedLinks.length) {
      copyToClipboard(lastGeneratedLinks.join('\n'), '已复制纯净分享链接');
    }
  });

  // ---- Local History Section ----
  async function loadHistoryList() {
    try {
      const data = await apiGet('/api/history');
      const items = data.items || [];
      $('historyCountBadge').textContent = String(items.length);
      $('clearHistoryBtn').style.display = items.length ? 'inline-flex' : 'none';

      const listEl = $('historyList');
      if (!items.length) {
        listEl.innerHTML = '<div class="history-empty">暂无历史记录，完成转存后会自动保存在本机方便随时回溯复制。</div>';
        return;
      }

      listEl.innerHTML = '';
      items.forEach((it) => {
        const row = document.createElement('div');
        row.className = 'history-row';
        const modeTag = it.mode === 'full' ? 'AI二创' : '仅转存';
        const linkHtml = it.new_link
          ? `<a class="history-link" href="${escapeHtml(it.new_link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(it.new_link)}</a>`
          : '<span class="history-time">纯文案</span>';

        row.innerHTML = `
          <div class="history-main">
            <span class="badge">${modeTag}</span>
            <span class="history-title" title="${escapeHtml(it.title)}">${escapeHtml(it.title)}</span>
            ${linkHtml}
          </div>
          <div class="history-actions">
            <span class="history-time tabular">${escapeHtml(it.created_at || '')}</span>
            ${it.new_link ? `<button type="button" class="btn btn-sm h-copy-link" data-link="${escapeHtml(it.new_link)}">复制链接</button>` : ''}
            <button type="button" class="btn btn-ghost btn-sm h-load">载入</button>
            <button type="button" class="btn btn-danger-ghost btn-sm h-del" data-id="${escapeHtml(it.id)}" title="删除此条记录">删除</button>
          </div>
        `;

        const copyBtn = row.querySelector('.h-copy-link');
        if (copyBtn) {
          copyBtn.addEventListener('click', () => copyToClipboard(copyBtn.dataset.link, '已复制历史分享链接'));
        }

        row.querySelector('.h-load').addEventListener('click', () => {
          $('resultEmpty').classList.add('hidden');
          $('resultContent').classList.remove('hidden');
          $('outputAlert').className = 'inline-alert';
          $('shareCards').innerHTML = '';
          $('outputEditor').value = it.output || it.new_link || '';
          $('outputCount').textContent = ($('outputEditor').value || '').length + ' 字符';
          $('copyAllBtn').disabled = !$('outputEditor').value;
          lastGeneratedLinks = it.all_links || (it.new_link ? [it.new_link] : []);
          $('copyLinksOnlyBtn').disabled = lastGeneratedLinks.length === 0;
          $('outputBadge').textContent = '已载入历史记录';
          $('outputBadge').className = 'badge brand tabular';
          window.scrollTo({ top: 0, behavior: 'smooth' });
        });

        row.querySelector('.h-del').addEventListener('click', async () => {
          await apiPost('/api/history/delete', { id: it.id });
          loadHistoryList();
        });

        listEl.appendChild(row);
      });
    } catch (e) {}
  }

  $('clearHistoryBtn').addEventListener('click', async () => {
    await apiPost('/api/history/clear');
    loadHistoryList();
    showToast('历史记录已清空', 'ok');
  });

  // ---- Startup Initialization ----
  (async function init() {
    setMode('reshare_only');
    loadHistoryList();
    const state = await syncSystemState();
    setInterval(() => syncSystemState(false), 25000);

    // First-visit gentle onboarding if not logged in
    if (state && !state.logged_in && !sessionStorage.getItem('quark-mover-prompted')) {
      sessionStorage.setItem('quark-mover-prompted', '1');
      setTimeout(() => {
        if (!isLoggedIn) openAndStartLogin();
      }, 600);
    }
  })();
})();
