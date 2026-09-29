const POLL_INTERVAL_SECONDS = 60;
let SERVER_STATUS_URL = "(サーバー状態API)"; // /api/config から dns_targets.json の値を取得

let countdownRemaining = POLL_INTERVAL_SECONDS;

const el = (id) => document.getElementById(id);

// 異常の種類ごとに、モーダルのタイトルとビープ音のパラメータを分ける
const ALERT_DEFS = {
  ip_mismatch: {
    title: "⚠️ DNS不一致",
    tone: { type: "square", freq: 880, intervalMs: 400 },
  },
  ip_changed: {
    title: "⚠️ グローバルIP変化",
    tone: { type: "square", freq: 784, intervalMs: 500 },
  },
  http_down: {
    title: "🛑 サーバー応答異常",
    tone: { type: "sawtooth", freq: 660, intervalMs: 250 },
  },
  power_on_battery: {
    title: "🔌 電源異常（バッテリー動作）",
    tone: { type: "sine", freq: 550, intervalMs: 600 },
  },
  battery_low: {
    title: "🔋 バッテリー残量低下",
    tone: { type: "sine", freq: 440, intervalMs: 300 },
    threshold: { unit: "%", min: 0, max: 100, step: 1, default: 20 },
  },
  remaining_hours_low: {
    title: "⏳ 残り稼働時間わずか",
    tone: { type: "triangle", freq: 700, intervalMs: 200 },
    threshold: { unit: "時間", min: 0, max: 24, step: 0.5, default: 1 },
  },
  cpu_high: {
    title: "🖥️ CPU使用率高",
    tone: { type: "square", freq: 330, intervalMs: 500 },
    threshold: { unit: "%", min: 0, max: 100, step: 1, default: 90 },
  },
  memory_high: {
    title: "🧠 メモリ使用率高",
    tone: { type: "square", freq: 392, intervalMs: 500 },
    threshold: { unit: "%", min: 0, max: 100, step: 1, default: 90 },
  },
  disk_high: {
    title: "💾 ディスク使用率高",
    tone: { type: "square", freq: 466, intervalMs: 500 },
    threshold: { unit: "%", min: 0, max: 100, step: 1, default: 90 },
  },
};

// --- 通知設定 (表示ON/OFF・音ON/OFF・しきい値) を localStorage に保存 ---
const SETTINGS_STORAGE_KEY = "ipwatcher:settings";

function buildDefaultSettings() {
  const defaults = {};
  for (const [id, def] of Object.entries(ALERT_DEFS)) {
    defaults[id] = { enabled: true, sound: true };
    if (def.threshold) defaults[id].threshold = def.threshold.default;
  }
  return defaults;
}

function loadSettings() {
  const defaults = buildDefaultSettings();
  let stored = {};
  try {
    stored = JSON.parse(localStorage.getItem(SETTINGS_STORAGE_KEY) || "{}");
  } catch (e) {
    stored = {};
  }
  const merged = {};
  for (const id of Object.keys(defaults)) {
    merged[id] = { ...defaults[id], ...(stored[id] || {}) };
  }
  return merged;
}

function saveSettings() {
  localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
}

let settings = loadSettings();

// --- アラート音 (Web Audio API のオシレーターでビープ音を生成、種類ごとに独立して鳴らせる) ---
let audioCtx = null;
const activeTones = new Map(); // alertId -> { oscillator, gainNode, intervalId, on }
let userMuted = localStorage.getItem("ipwatcher:muted") === "1";

// 音量 (0〜100%)。初期値40%で従来の音量 (gain 0.12) と同じになる
const VOLUME_STORAGE_KEY = "ipwatcher:volume";
const DEFAULT_VOLUME = 40;
const MAX_GAIN = 0.3;
let volumePercent = (() => {
  const stored = parseInt(localStorage.getItem(VOLUME_STORAGE_KEY), 10);
  return Number.isFinite(stored) ? Math.min(100, Math.max(0, stored)) : DEFAULT_VOLUME;
})();

function currentGain() {
  return (MAX_GAIN * volumePercent) / 100;
}

function ensureAudioContext() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  return audioCtx;
}

function startTone(alertId, toneDef) {
  if (userMuted || activeTones.has(alertId)) return;

  const ctx = ensureAudioContext();
  if (ctx.state === "suspended") {
    ctx.resume().catch(() => {});
  }
  if (ctx.state !== "running") {
    el("audio-unlock-banner").classList.remove("hidden");
    return;
  }
  el("audio-unlock-banner").classList.add("hidden");

  const oscillator = ctx.createOscillator();
  const gainNode = ctx.createGain();
  oscillator.type = toneDef.type;
  oscillator.frequency.value = toneDef.freq;
  gainNode.gain.value = currentGain();
  oscillator.connect(gainNode);
  gainNode.connect(ctx.destination);
  oscillator.start();

  const tone = { oscillator, gainNode, intervalId: null, on: true };
  tone.intervalId = setInterval(() => {
    tone.on = !tone.on;
    gainNode.gain.value = tone.on ? currentGain() : 0;
  }, toneDef.intervalMs);

  activeTones.set(alertId, tone);
}

// 鳴っている途中のアラート音にも新しい音量を即座に反映する
function applyVolumeToActiveTones() {
  for (const tone of activeTones.values()) {
    if (tone.on) tone.gainNode.gain.value = currentGain();
  }
}

// 音量調整時の試聴用に短いビープを1回鳴らす
function playPreviewBeep() {
  if (userMuted || volumePercent === 0) return;
  const ctx = ensureAudioContext();
  ctx.resume().then(() => {
    const oscillator = ctx.createOscillator();
    const gainNode = ctx.createGain();
    oscillator.type = "square";
    oscillator.frequency.value = 880;
    gainNode.gain.value = currentGain();
    oscillator.connect(gainNode);
    gainNode.connect(ctx.destination);
    oscillator.onended = () => {
      oscillator.disconnect();
      gainNode.disconnect();
    };
    oscillator.start();
    oscillator.stop(ctx.currentTime + 0.25);
  }).catch(() => {});
}

function stopTone(alertId) {
  const tone = activeTones.get(alertId);
  if (!tone) return;
  clearInterval(tone.intervalId);
  try {
    tone.oscillator.stop();
  } catch (e) {
    /* already stopped */
  }
  tone.oscillator.disconnect();
  tone.gainNode.disconnect();
  activeTones.delete(alertId);
}

function stopAllTones() {
  [...activeTones.keys()].forEach(stopTone);
}

function updateMuteButton() {
  const btn = el("mute-toggle");
  btn.textContent = userMuted ? "🔇" : "🔊";
  btn.classList.toggle("muted", userMuted);
}

el("mute-toggle").addEventListener("click", () => {
  userMuted = !userMuted;
  localStorage.setItem("ipwatcher:muted", userMuted ? "1" : "0");
  updateMuteButton();
  if (userMuted) {
    stopAllTones();
  } else {
    for (const [alertId, info] of activeWindows) {
      startTone(alertId, info.toneDef);
    }
  }
});

el("audio-unlock-btn").addEventListener("click", () => {
  const ctx = ensureAudioContext();
  ctx.resume().finally(() => {
    el("audio-unlock-banner").classList.add("hidden");
    if (!userMuted) {
      for (const [alertId, info] of activeWindows) {
        startTone(alertId, info.toneDef);
      }
    }
  });
});

updateMuteButton();

function updateVolumeLabel() {
  el("volume-value").textContent = `${volumePercent}%`;
}

el("volume-slider").value = volumePercent;
updateVolumeLabel();

el("volume-slider").addEventListener("input", (event) => {
  volumePercent = parseInt(event.target.value, 10);
  updateVolumeLabel();
  applyVolumeToActiveTones();
});

el("volume-slider").addEventListener("change", () => {
  localStorage.setItem(VOLUME_STORAGE_KEY, String(volumePercent));
  if (activeTones.size === 0) playPreviewBeep();
});

// --- 設定モーダル ---
function renderSettingsList() {
  const container = el("settings-list");
  container.innerHTML = "";

  for (const [id, def] of Object.entries(ALERT_DEFS)) {
    const row = document.createElement("div");
    row.className = "settings-row";

    const header = document.createElement("div");
    header.className = "settings-row-header";

    const enabledLabel = document.createElement("label");
    const enabledCheckbox = document.createElement("input");
    enabledCheckbox.type = "checkbox";
    enabledCheckbox.checked = settings[id].enabled;
    enabledCheckbox.addEventListener("change", () => {
      settings[id].enabled = enabledCheckbox.checked;
      saveSettings();
      if (!settings[id].enabled) hideAlertWindow(id);
    });
    enabledLabel.appendChild(enabledCheckbox);
    enabledLabel.appendChild(document.createTextNode(` ${def.title}`));

    const soundLabel = document.createElement("label");
    soundLabel.className = "sound-toggle";
    const soundCheckbox = document.createElement("input");
    soundCheckbox.type = "checkbox";
    soundCheckbox.checked = settings[id].sound;
    soundCheckbox.addEventListener("change", () => {
      settings[id].sound = soundCheckbox.checked;
      saveSettings();
      if (!settings[id].sound) {
        stopTone(id);
      } else if (activeWindows.has(id)) {
        startTone(id, ALERT_DEFS[id].tone);
      }
    });
    soundLabel.appendChild(soundCheckbox);
    soundLabel.appendChild(document.createTextNode(" 🔊 音を鳴らす"));

    header.appendChild(enabledLabel);
    header.appendChild(soundLabel);
    row.appendChild(header);

    if (def.threshold) {
      const thresholdRow = document.createElement("div");
      thresholdRow.className = "settings-row-threshold";

      const label = document.createElement("label");
      label.textContent = "しきい値: ";

      const input = document.createElement("input");
      input.type = "number";
      input.min = def.threshold.min;
      input.max = def.threshold.max;
      input.step = def.threshold.step;
      input.value = settings[id].threshold;
      input.addEventListener("change", () => {
        const value = parseFloat(input.value);
        settings[id].threshold = Number.isFinite(value) ? value : def.threshold.default;
        input.value = settings[id].threshold;
        saveSettings();
      });

      label.appendChild(input);
      label.appendChild(document.createTextNode(` ${def.threshold.unit}`));
      thresholdRow.appendChild(label);
      row.appendChild(thresholdRow);
    }

    container.appendChild(row);
  }
}

el("settings-btn").addEventListener("click", () => {
  renderSettingsList();
  el("settings-modal-overlay").classList.remove("hidden");
});

el("settings-close-btn").addEventListener("click", () => {
  el("settings-modal-overlay").classList.add("hidden");
});

el("settings-reset-btn").addEventListener("click", () => {
  settings = buildDefaultSettings();
  saveSettings();
  renderSettingsList();
});

// --- ドラッグ可能なアラートウィンドウ (複数同時表示、種類ごとに個別のウィンドウ) ---
const activeWindows = new Map(); // alertId -> { el, toneDef }
let cascadeIndex = 0;

// ページ全体をドロップ可能にしておかないと dragend の座標が安定しないブラウザがあるため
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => e.preventDefault());

function makeDraggable(windowEl, headerEl) {
  let offsetX = 0;
  let offsetY = 0;

  headerEl.addEventListener("dragstart", (e) => {
    const rect = windowEl.getBoundingClientRect();
    offsetX = e.clientX - rect.left;
    offsetY = e.clientY - rect.top;
    e.dataTransfer.setData("text/plain", "");
    e.dataTransfer.effectAllowed = "move";
    windowEl.classList.add("dragging");
  });

  headerEl.addEventListener("dragend", (e) => {
    windowEl.classList.remove("dragging");
    if (e.clientX === 0 && e.clientY === 0) return; // ビューポート外にドロップされた場合は無視
    const maxLeft = Math.max(0, window.innerWidth - windowEl.offsetWidth);
    const maxTop = Math.max(0, window.innerHeight - windowEl.offsetHeight);
    const newLeft = Math.min(Math.max(0, e.clientX - offsetX), maxLeft);
    const newTop = Math.min(Math.max(0, e.clientY - offsetY), maxTop);
    windowEl.style.left = `${newLeft}px`;
    windowEl.style.top = `${newTop}px`;
  });
}

function createAlertWindowElement(alertId, title) {
  const windowEl = document.createElement("div");
  windowEl.className = "alert-window";
  windowEl.dataset.alertId = alertId;

  const header = document.createElement("div");
  header.className = "alert-window-header";
  header.draggable = true;
  header.textContent = title;

  const body = document.createElement("div");
  body.className = "alert-window-body";

  const message = document.createElement("p");
  message.className = "alert-window-message";

  const dismissBtn = document.createElement("button");
  dismissBtn.className = "alert-window-dismiss";
  dismissBtn.textContent = "確認しました";
  dismissBtn.addEventListener("click", () => hideAlertWindow(alertId));

  body.appendChild(message);
  body.appendChild(dismissBtn);
  windowEl.appendChild(header);
  windowEl.appendChild(body);

  makeDraggable(windowEl, header);

  // 狭い画面 (スマホ) でもウィンドウが画面外にはみ出さないよう位置を収める
  const offset = (cascadeIndex % 6) * 32;
  const windowWidth = Math.min(320, window.innerWidth - 16);
  windowEl.style.left = `${Math.max(8, Math.min(24 + offset, window.innerWidth - windowWidth - 8))}px`;
  windowEl.style.top = `${88 + offset}px`;
  cascadeIndex += 1;

  return windowEl;
}

function showAlertWindow(alertId, { title, message, toneDef, silent }) {
  let entry = activeWindows.get(alertId);
  if (!entry) {
    const windowEl = createAlertWindowElement(alertId, title);
    el("alert-windows-container").appendChild(windowEl);
    entry = { el: windowEl, toneDef };
    activeWindows.set(alertId, entry);
  }
  entry.el.querySelector(".alert-window-message").textContent = message;
  entry.toneDef = toneDef;
  if (silent) {
    stopTone(alertId);
  } else {
    startTone(alertId, toneDef);
  }
}

function hideAlertWindow(alertId) {
  const entry = activeWindows.get(alertId);
  if (!entry) return;
  entry.el.remove();
  activeWindows.delete(alertId);
  stopTone(alertId);
}

// 異常状態(conditionMet)と設定(表示/音のON/OFF)に応じてウィンドウ表示/音を同期する
function syncAlert(alertId, conditionMet, message) {
  const cfg = settings[alertId];
  if (conditionMet && cfg.enabled) {
    const def = ALERT_DEFS[alertId];
    showAlertWindow(alertId, { title: def.title, message, toneDef: def.tone, silent: !cfg.sound });
  } else {
    hideAlertWindow(alertId);
  }
}

// --- IP監視の表示 ---
function renderDomainStatus(domain) {
  const domains = domain.domains || [];
  const mismatched = domain.mismatched_domains || domains.filter((d) => d.mismatch).map((d) => d.fqdn);
  el("domain").textContent = domains.length ? `${domains.length}件` : "-";
  el("global-ip").textContent = domain.global_ip || "-";
  el("domain-ip").textContent = domains.length
    ? `${domains.length - mismatched.length} / ${domains.length} 一致`
    : "-";
  el("last-checked").textContent = domain.timestamp
    ? new Date(domain.timestamp).toLocaleString("ja-JP")
    : "-";

  const matchEl = el("match-value");
  const cardEl = el("card-match");
  if (!domain.global_ip) {
    matchEl.textContent = "確認中...";
    cardEl.classList.remove("ok", "alert");
  } else if (domain.domain_mismatch) {
    matchEl.textContent = `不一致 (${mismatched.length}件)`;
    cardEl.classList.add("alert");
    cardEl.classList.remove("ok");
  } else {
    matchEl.textContent = "すべて一致";
    cardEl.classList.add("ok");
    cardEl.classList.remove("alert");
  }

  renderDomainTable(domains, domain.global_ip);

  const shown = mismatched.slice(0, 5).join(", ") + (mismatched.length > 5 ? ` ほか${mismatched.length - 5}件` : "");
  syncAlert(
    "ip_mismatch",
    !!domain.global_ip && !!domain.domain_mismatch,
    `次のドメインの解決IPが現地のグローバルIP (${domain.global_ip}) と一致していません: ${shown}`
  );
  syncAlert(
    "ip_changed",
    !!domain.global_ip && !!domain.ip_changed,
    `グローバルIPが変化しました: ${domain.previous_global_ip} → ${domain.global_ip}`
  );

  const errorBanner = el("error-banner");
  if (domain.error) {
    errorBanner.textContent = domain.error;
    errorBanner.classList.remove("hidden");
  } else {
    errorBanner.classList.add("hidden");
  }
}

function renderDomainTable(domains, globalIp) {
  const body = el("domains-body");
  body.innerHTML = "";
  if (!domains.length) {
    body.innerHTML = '<tr><td colspan="4">監視対象がありません（dns_targets.json に auto_update=true のAレコードを登録してください）</td></tr>';
    return;
  }
  // 不一致を先頭に
  const sorted = [...domains].sort((a, b) => (b.mismatch - a.mismatch) || a.fqdn.localeCompare(b.fqdn));
  for (const d of sorted) {
    const tr = document.createElement("tr");
    const state = d.error ? `⚠️ ${escapeHtml(d.error)}` : d.mismatch ? "⚠️ 不一致" : "✅ 一致";
    tr.innerHTML = `<td>${escapeHtml(d.fqdn)}</td><td>${escapeHtml((d.providers || []).join(", "))}</td>` +
      `<td>${escapeHtml((d.ips || []).join(", ") || "-")}</td><td>${state}</td>`;
    if (d.mismatch) tr.classList.add("row-alert");
    body.appendChild(tr);
  }
}

function renderDnsProviderTargets(config) {
  const container = el("dns-provider-targets");
  const dns = config.dns || {};
  const domains = dns.xserver_domains || [];
  let html = "";
  if (config.config_error) {
    html += `<div class="error">⚠️ ${escapeHtml(config.config_error)}（直前の正常な設定で動作中）</div>`;
  }
  html += `<div><strong>XServer:</strong> ${dns.xserver_enabled ? "有効" : "無効"} / APIキー ${dns.xserver_api_key_configured ? "設定済み" : "未設定"}</div>`;
  if (!domains.length) {
    html += `<div class="small-note">XServerの対象ドメインはありません。</div>`;
  } else {
    html += '<ul class="dns-target-list">';
    for (const d of domains) {
      html += `<li><strong>${escapeHtml(d.domain)}</strong>`;
      const records = d.records || [];
      if (!records.length) html += ' — レコード未設定';
      else {
        html += '<ul>';
        for (const r of records) {
          const enabled = r.auto_update === true && String(r.type || 'A').toUpperCase() === 'A';
          html += `<li>${escapeHtml(r.host || '@')} / ${escapeHtml(r.type || 'A')} — ${enabled ? '自動更新' : '更新しない'}</li>`;
        }
        html += '</ul>';
      }
      html += '</li>';
    }
    html += '</ul>';
  }
  html += `<div><strong>MyDNS:</strong> ${dns.mydns_enabled ? "有効（定期IP通知）" : "無効"}</div>`;
  container.innerHTML = html;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}

function renderDnsUpdateStatus(domain) {
  const card = el("card-dns-update");
  const value = el("dns-update-status");
  const message = el("dns-update-message");
  const detail = el("dns-update-detail");

  if (!domain.dns_update_enabled) {
    value.textContent = "無効";
    card.classList.remove("ok", "alert");
    message.textContent = domain.dns_update_message || "DNS自動更新は無効です";
    detail.innerHTML = `<div><strong>なぜ？</strong> ${domain.dns_update_reason || "安全のため初期状態では無効です。"}</div><div><strong>対処:</strong> ${domain.dns_update_action || "設定ガイドを確認してください。"}</div><a href="/guide#2-xserver-apiを設定する" target="_blank">📖 XServer DNSの設定方法を見る</a>`;
    return;
  } else if (domain.dns_update_success === true) {
    value.textContent = "有効・正常";
    card.classList.add("ok");
    card.classList.remove("alert");
  } else if (domain.dns_update_success === false) {
    value.textContent = "有効・エラー";
    card.classList.add("alert");
    card.classList.remove("ok");
  } else {
    value.textContent = "有効・待機中";
    card.classList.remove("ok", "alert");
  }

  message.textContent = domain.dns_update_message || "-";
  detail.innerHTML = domain.dns_update_reason || domain.dns_update_action
    ? `<div><strong>なぜ？</strong> ${domain.dns_update_reason || "-"}</div><div><strong>対処:</strong> ${domain.dns_update_action || "-"}</div>`
    : "";
}

function renderHistory(history) {
  const body = el("history-body");
  body.innerHTML = "";
  [...history].reverse().forEach((entry) => {
    const tr = document.createElement("tr");
    const time = new Date(entry.timestamp).toLocaleString("ja-JP");
    const state = entry.domain_mismatch ? "⚠️ 不一致" : "✅ 一致";
    const names = entry.mismatched_domains || entry.domain_ips || [];
    tr.innerHTML = `<td>${time}</td><td>${escapeHtml(entry.global_ip)}</td><td>${escapeHtml(names.join(", ") || "-")}</td><td>${state}</td>`;
    if (entry.domain_mismatch) tr.classList.add("row-alert");
    body.appendChild(tr);
  });
}

// --- サーバー状態の表示 ---
function formatPercent(value) {
  return value === null || value === undefined ? "-" : `${value}%`;
}

function renderServerStatus(server) {
  const httpCard = el("card-http");
  const httpValue = el("http-status");
  if (server.http_ok === null || server.http_ok === undefined) {
    httpValue.textContent = "確認中...";
    httpCard.classList.remove("ok", "alert");
  } else if (server.http_ok) {
    httpValue.textContent = `OK (${server.http_status_code})`;
    httpCard.classList.add("ok");
    httpCard.classList.remove("alert");
  } else {
    httpValue.textContent = server.error || `NG (${server.http_status_code ?? "接続不可"})`;
    httpCard.classList.add("alert");
    httpCard.classList.remove("ok");
  }
  syncAlert(
    "http_down",
    server.http_ok === false,
    `${server.url || SERVER_STATUS_URL} への正常なアクセス(HTTP 200)が確認できません: ${server.error || "不明なエラー"}`
  );

  const powerCard = el("card-power");
  const powerAbnormal = server.power !== null && server.power !== undefined && server.power !== "AC";
  el("power-value").textContent = server.power || "-";
  powerCard.classList.toggle("alert", powerAbnormal);
  powerCard.classList.toggle("ok", server.http_ok === true && !powerAbnormal);
  syncAlert("power_on_battery", powerAbnormal, `電源がAC以外になっています (power: ${server.power})`);

  const batteryLowThreshold = settings.battery_low.threshold;
  const remainingHoursLowThreshold = settings.remaining_hours_low.threshold;
  const batteryLow =
    server.battery !== null && server.battery !== undefined && server.battery < batteryLowThreshold;
  const remainingHoursLow =
    server.remaining_hours !== null &&
    server.remaining_hours !== undefined &&
    server.remaining_hours < remainingHoursLowThreshold;

  const batteryCard = el("card-battery");
  const batteryParts = [];
  if (server.battery !== null && server.battery !== undefined) {
    batteryParts.push(`${server.battery}%`);
  }
  if (server.remaining_hours !== null && server.remaining_hours !== undefined) {
    batteryParts.push(`残り約${server.remaining_hours}時間`);
  }
  el("battery-value").textContent = batteryParts.length ? batteryParts.join(" / ") : "-";
  batteryCard.classList.toggle("alert", batteryLow || remainingHoursLow);
  syncAlert(
    "battery_low",
    batteryLow,
    `バッテリー残量が低下しています: ${server.battery}% (しきい値 ${batteryLowThreshold}%)`
  );
  syncAlert(
    "remaining_hours_low",
    remainingHoursLow,
    `残り稼働時間がわずかです: 約${server.remaining_hours}時間 (しきい値 ${remainingHoursLowThreshold}時間)`
  );

  const cpuThreshold = settings.cpu_high.threshold;
  const cpuHigh =
    server.cpu_percent !== null && server.cpu_percent !== undefined && server.cpu_percent > cpuThreshold;
  const cpuCard = el("card-cpu");
  el("cpu-value").textContent = formatPercent(server.cpu_percent);
  cpuCard.classList.toggle("alert", cpuHigh);
  syncAlert(
    "cpu_high",
    cpuHigh,
    `CPU使用率が高くなっています: ${server.cpu_percent}% (しきい値 ${cpuThreshold}%)`
  );

  const memoryThreshold = settings.memory_high.threshold;
  const memoryHigh =
    server.memory_percent !== null &&
    server.memory_percent !== undefined &&
    server.memory_percent > memoryThreshold;
  const memoryCard = el("card-memory");
  el("memory-value").textContent =
    server.memory_percent === null || server.memory_percent === undefined
      ? "-"
      : `${server.memory_percent}% (${server.memory_used_gb}GB / ${server.memory_total_gb}GB)`;
  memoryCard.classList.toggle("alert", memoryHigh);
  syncAlert(
    "memory_high",
    memoryHigh,
    `メモリ使用率が高くなっています: ${server.memory_percent}% (しきい値 ${memoryThreshold}%)`
  );

  const diskThreshold = settings.disk_high.threshold;
  const diskHigh =
    server.disk_percent !== null && server.disk_percent !== undefined && server.disk_percent > diskThreshold;
  const diskCard = el("card-disk");
  el("disk-value").textContent =
    server.disk_percent === null || server.disk_percent === undefined
      ? "-"
      : `${server.disk_percent}% (${server.disk_used_gb}GB / ${server.disk_total_gb}GB)`;
  diskCard.classList.toggle("alert", diskHigh);
  syncAlert(
    "disk_high",
    diskHigh,
    `ディスク使用率が高くなっています: ${server.disk_percent}% (しきい値 ${diskThreshold}%)`
  );
}

function renderServerHistory(history) {
  const body = el("server-history-body");
  body.innerHTML = "";
  [...history].reverse().forEach((entry) => {
    const tr = document.createElement("tr");
    const time = new Date(entry.timestamp).toLocaleString("ja-JP");
    const httpState = entry.http_ok ? "✅ OK" : "⚠️ NG";
    tr.innerHTML = `<td>${time}</td><td>${httpState}</td><td>${entry.power ?? "-"}</td><td>${entry.cpu_percent ?? "-"}</td><td>${entry.memory_percent ?? "-"}</td><td>${entry.disk_percent ?? "-"}</td>`;
    if (!entry.http_ok) tr.classList.add("row-alert");
    body.appendChild(tr);
  });
}

async function fetchStatus() {
  countdownRemaining = POLL_INTERVAL_SECONDS;

  // /api/status の取得に失敗しても、既存画面を壊さずエラーを表示する。
  try {
    const res = await fetch("/api/status", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const data = await res.json();
    const domain = data.domain || {};
    const server = data.server || {};

    // 各表示を独立して更新する。1つの表示処理の例外で、
    // IP履歴やサーバー状態まで巻き添えにしない。
    try { renderDomainStatus(domain); }
    catch (e) { console.error("domain status render failed", e); }

    try { renderDnsUpdateStatus(domain); }
    catch (e) { console.error("DNS update status render failed", e); }

    try { renderHistory(domain.history || []); }
    catch (e) { console.error("IP history render failed", e); }

    try { renderServerStatus(server); }
    catch (e) { console.error("server status render failed", e); }

    try { renderServerHistory(server.history || []); }
    catch (e) { console.error("server history render failed", e); }
  } catch (err) {
    console.error("status fetch failed", err);
    const banner = el("error-banner");
    if (banner) {
      banner.textContent = `ステータス取得に失敗しました: ${err.message || err}`;
      banner.classList.remove("hidden");
    }
  }

  // DNS Provider設定はステータス取得とは独立して取得する。
  try {
    const configResponse = await fetch("/api/config", { cache: "no-store" });
    if (!configResponse.ok) throw new Error(`HTTP ${configResponse.status}`);
    const config = await configResponse.json();
    if (config.server_status && config.server_status.url) {
      SERVER_STATUS_URL = config.server_status.url;
      const urlEl = el("server-status-url");
      if (urlEl) urlEl.textContent = config.server_status.enabled ? SERVER_STATUS_URL : `${SERVER_STATUS_URL}（無効）`;
    }
    renderDnsProviderTargets(config);
  } catch (e) {
    console.error("DNS provider config fetch failed", e);
    const target = el("dns-provider-targets");
    if (target) target.textContent = "DNS Provider設定の取得に失敗しました。";
  }
}

el("recheck-btn").addEventListener("click", async () => {
  await fetch("/api/check", { method: "POST" });
  await fetchStatus();
});

setInterval(() => {
  countdownRemaining -= 1;
  if (countdownRemaining <= 0) {
    fetchStatus();
  } else {
    el("countdown").textContent = `次回チェックまで約 ${countdownRemaining}秒`;
  }
}, 1000);

fetchStatus();
