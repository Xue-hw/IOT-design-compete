(() => {
  "use strict";

  const queryApiBase = new URLSearchParams(window.location.search).get("api");
  const dashboardPathIndex = window.location.pathname.indexOf("/dashboard");
  const backendHostedBase = dashboardPathIndex >= 0
    ? `${window.location.origin}${window.location.pathname.slice(0, dashboardPathIndex)}`
    : null;
  const API_BASE = (queryApiBase || backendHostedBase || "http://82.156.238.244/focuscube").replace(/\/$/, "");
  const API_PATHS = {
    status: "/api/v1/status",
    report: "/api/v1/report/daily",
    reminders: "/api/v1/reminders",
    timeseries: "/api/v1/timeseries"
  };
  const STATUS_POLL_MS = 2000;
  const SECONDARY_POLL_MS = 6000;
  const DIAGNOSTIC_POLL_MS = 15000;

  const state = {
    status: null,
    report: null,
    reminders: [],
    series: {
      "light.lux": { metric: "light.lux", points: [] },
      "imu.activity": { metric: "imu.activity", points: [] },
      "power.battery_pct": { metric: "power.battery_pct", points: [] },
      "edge.environment.score": { metric: "edge.environment.score", points: [] }
    },
    focusTimeline: { metric: "focus.state", segments: [] },
    selectedDeviceId: "focuscube-base-01",
    installation: null,
    activeMetric: "light.lux",
    chartWindow: 60,
    currentPage: "overview",
    requestLog: [],
    events: [],
    modelTrace: [],
    endpointHealth: {},
    latency: null,
    statusPollCount: 0,
    lastStatusAt: null,
    lastSecondaryAt: null,
    statusBusy: false,
    secondaryBusy: false,
    diagnosticsBusy: false,
    reportBusy: false,
    reportRevealTimer: null,
    reportDisplayText: "",
    lastReportText: "",
    lastDeviceSnapshot: new Map(),
    previousValues: { lux: null, activity: null, battery: null, ratio: null },
    timers: []
  };

  const el = (id) => document.getElementById(id);
  const qs = (selector, root = document) => root.querySelector(selector);
  const qsa = (selector, root = document) => [...root.querySelectorAll(selector)];
  const nowSec = () => Math.floor(Date.now() / 1000);
  const hasOwn = (obj, key) => Boolean(obj && Object.prototype.hasOwnProperty.call(obj, key));

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function setText(id, value) {
    const node = el(id);
    if (node) node.textContent = value;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function round(value, digits = 0) {
    const p = 10 ** digits;
    return Math.round(Number(value) * p) / p;
  }

  function todayString() {
    const d = new Date();
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  }

  function formatClock(date = new Date()) {
    return date.toLocaleTimeString("zh-CN", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit"
    });
  }

  function formatTime(unix, includeDate = false) {
    if (!Number.isFinite(Number(unix)) || Number(unix) <= 0) return "--";
    const options = includeDate
      ? { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }
      : { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false };
    return new Intl.DateTimeFormat("zh-CN", options).format(new Date(Number(unix) * 1000));
  }

  function relativeTime(unix) {
    if (!Number.isFinite(Number(unix)) || Number(unix) <= 0) return "尚无真实心跳";
    const seconds = Math.max(0, nowSec() - Number(unix));
    if (seconds < 5) return "刚刚";
    if (seconds < 60) return `${seconds} 秒前`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
    return `${Math.floor(seconds / 3600)} 小时前`;
  }

  function joinUrl(path) {
    return `${API_BASE}${path}`;
  }

  function buildUrl(kind, extra = {}) {
    const deviceId = state.selectedDeviceId || el("deviceSelect")?.value || "focuscube-base-01";
    const date = el("dateInput")?.value || todayString();
    if (kind === "status") return `${joinUrl(API_PATHS.status)}?${new URLSearchParams({ installation_id: "focuscube-base-01" })}`;
    if (kind === "report") return `${joinUrl(API_PATHS.report)}?${new URLSearchParams({ device_id: deviceId, date })}`;
    if (kind === "reminders") return `${joinUrl(API_PATHS.reminders)}?${new URLSearchParams({ device_id: deviceId, since: String(extra.since || 0) })}`;
    if (kind === "timeseries") return `${joinUrl(API_PATHS.timeseries)}?${new URLSearchParams({ device_id: deviceId, date, metric: extra.metric || "light.lux" })}`;
    throw new Error(`Unknown API kind: ${kind}`);
  }

  async function fetchJson(url, label, timeoutMs = 4500) {
    const started = performance.now();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    let responseStatus = 0;
    try {
      const response = await fetch(url, {
        headers: { Accept: "application/json" },
        signal: controller.signal,
        cache: "no-store"
      });
      responseStatus = response.status;
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      const duration = Math.round(performance.now() - started);
      state.latency = duration;
      state.endpointHealth[label.split(":")[0]] = true;
      addRequestLog({ time: new Date(), label, url, ok: true, status: response.status, duration });
      return payload;
    } catch (error) {
      const message = error.name === "AbortError" ? "请求超时" : error.message;
      state.endpointHealth[label.split(":")[0]] = false;
      addRequestLog({ time: new Date(), label, url, ok: false, status: responseStatus, duration: Math.round(performance.now() - started) });
      throw new Error(`${label}: ${message}`);
    } finally {
      clearTimeout(timeout);
    }
  }

  function normalizePoint(point) {
    if (point == null) return null;
    if (Array.isArray(point)) {
      const ts = Number(point[0]);
      const value = Number(point[1]);
      return Number.isFinite(ts) && Number.isFinite(value) ? { ts, value } : null;
    }
    const ts = Number(point.ts ?? point.time ?? point.timestamp ?? point.x);
    const value = Number(point.value ?? point.y ?? point.v);
    if (!Number.isFinite(ts) || !Number.isFinite(value)) return null;
    return { ...point, ts, value };
  }

  function normalizeSeries(payload, metric) {
    if (!payload) return { metric, points: [] };
    if (Array.isArray(payload)) return { metric, points: payload.map(normalizePoint).filter(Boolean) };
    if (Array.isArray(payload.points)) return { ...payload, metric: payload.metric || metric, points: payload.points.map(normalizePoint).filter(Boolean) };
    if (Array.isArray(payload.data)) return { ...payload, metric: payload.metric || metric, points: payload.data.map(normalizePoint).filter(Boolean) };
    return { metric, points: [] };
  }

  function normalizeReminders(payload) {
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload?.reminders)) return payload.reminders;
    if (Array.isArray(payload?.data)) return payload.data;
    return [];
  }

  function devices() {
    if (Array.isArray(state.installation?.members)) return state.installation.members;
    return Array.isArray(state.status?.devices) ? state.status.devices : [];
  }

  function selectedDevice() {
    if (state.installation) {
      return {
        device_id: state.installation.viewId,
        source: "fusion",
        online: state.installation.online,
        ready: state.installation.ready,
        last_seen: Math.max(0, ...state.installation.members.map((item) => Number(item.last_seen || 0))),
        telemetry: state.installation.telemetry,
        summary: state.installation.ready ? "FocusCube 基座已就绪" : state.installation.online ? "FocusCube 基座降级运行" : "FocusCube 基座离线"
      };
    }
    return devices().find((device) => String(device.device_id) === String(state.selectedDeviceId))
      || devices().find((device) => String(device.source).toLowerCase() === "s3")
      || devices()[0]
      || null;
  }

  function isS3(device) {
    return String(device?.source || "").toLowerCase() === "s3" || String(device?.device_id || "").toLowerCase().includes("s3");
  }

  function deviceDataValid(device) {
    if (!device) return false;
    if (hasOwn(device.telemetry, "valid")) return device.telemetry.valid !== false;
    if (hasOwn(device, "valid")) return device.valid !== false;
    return true;
  }

  function currentTelemetry() {
    if (state.installation) return state.installation.telemetry;
    const device = selectedDevice();
    if (!device || !deviceDataValid(device)) return null;

    const source = device.telemetry && typeof device.telemetry === "object"
      ? device.telemetry
      : {
          light: device.light,
          imu: device.imu,
          focus: device.focus,
          power: device.power
        };

    const cleanGroup = (group) => {
      if (!group || typeof group !== "object") return null;
      return group.valid === false ? { valid: false } : group;
    };

    return {
      ...source,
      light: cleanGroup(source.light),
      imu: cleanGroup(source.imu),
      focus: cleanGroup(source.focus),
      power: cleanGroup(source.power)
    };
  }

  function normalizeInstallationStatus(response) {
    if (!response || typeof response !== "object") return null;
    const fallbackDevice = Array.isArray(response.devices) ? response.devices[0] : null;
    const nested = fallbackDevice?.telemetry;
    const nestedHasBlocks = nested && ["light", "imu", "focus", "edge", "power"].some((key) => nested[key] && typeof nested[key] === "object");
    const rawTelemetry = response.telemetry && typeof response.telemetry === "object" && Object.keys(response.telemetry).length
      ? response.telemetry
      : nestedHasBlocks ? nested : fallbackDevice || {};
    const availability = response.availability || {};
    const normalizeBlock = (name) => {
      const block = rawTelemetry[name];
      if (!block || typeof block !== "object") return null;
      const quality = block.quality || (name === "edge" ? "derived" : "measured");
      const availabilityState = availability[name]?.state;
      const invalid = block.valid === false || ["partial", "missing", "invalid"].includes(quality);
      if (invalid) return { valid: false, quality, displayState: quality, warnings: availability[name]?.reason ? [availability[name].reason] : [] };
      return {
        ...block,
        valid: true,
        quality,
        displayState: availabilityState || (block.stale ? "stale" : quality === "estimated" ? "estimated" : "fresh"),
        sourceDeviceId: block.source_device_id || null,
        warnings: availability[name]?.reason ? [availability[name].reason] : []
      };
    };
    const members = Array.isArray(response.members) ? response.members : [];
    return {
      viewId: response.view_id || response.installation_id || fallbackDevice?.device_id || "focuscube-base-01",
      online: Boolean(response.online ?? fallbackDevice?.online),
      ready: Boolean(response.ready ?? (members.length >= 2 && members.every((item) => item.online))),
      telemetry: {
        light: normalizeBlock("light"),
        imu: normalizeBlock("imu"),
        focus: normalizeBlock("focus"),
        edge: normalizeBlock("edge"),
        power: normalizeBlock("power")
      },
      availability,
      members,
      timestamps: Object.fromEntries(["light", "imu", "focus", "edge"].map((name) => [name, rawTelemetry[name]?.ts || null]))
    };
  }

  function hasFinite(value) {
    if (value === null || value === undefined || value === "" || typeof value === "boolean") return false;
    return Number.isFinite(Number(value));
  }

  function telemetryValue(group, key) {
    const telemetry = currentTelemetry();
    if (!telemetry || !telemetry[group] || telemetry[group].valid === false || !hasOwn(telemetry[group], key)) return null;
    const value = telemetry[group][key];
    return hasFinite(value) ? Number(value) : value;
  }

  function reportMetric(key) {
    if (!state.report?.metrics || !hasOwn(state.report.metrics, key)) return null;
    const value = state.report.metrics[key];
    return hasFinite(value) ? Number(value) : value;
  }

  function lightLabelText(label) {
    return ({ suitable: "适宜", dim: "偏暗", too_dim: "偏暗", bright: "偏亮", too_bright: "偏亮", unknown: "未知" })[label] || String(label || "未知");
  }

  function focusStateText(value) {
    return ({ running: "专注中", focus: "专注中", paused: "已暂停", break: "休息中", idle: "空闲" })[value] || (value ? String(value) : "等待真实数据");
  }

  function validityLabel(device) {
    if (!device) return "等待设备状态";
    return deviceDataValid(device) ? "真实数据有效" : "等待真实数据";
  }

  function telemetryCompleteness() {
    const telemetry = currentTelemetry();
    if (!telemetry) return 0;
    const fields = [
      telemetry.light && hasOwn(telemetry.light, "lux"),
      telemetry.imu && hasOwn(telemetry.imu, "activity"),
      telemetry.focus && hasOwn(telemetry.focus, "state"),
      telemetry.power && hasOwn(telemetry.power, "battery_pct")
    ];
    return Math.round(fields.filter(Boolean).length / fields.length * 100);
  }

  async function refreshStatus({ quiet = false } = {}) {
    if (state.statusBusy) return;
    state.statusBusy = true;
    if (!quiet) setConnection("loading", "同步状态");
    try {
      const payload = await fetchJson(buildUrl("status"), "status");
      state.statusPollCount += 1;
      state.lastStatusAt = new Date();
      applyStatus(payload);
      setConnection("live", "真实接口");
      el("fallbackBanner")?.classList.add("hidden");
    } catch (error) {
      setConnection("error", "状态接口不可用");
      showConnectionBanner(error.message);
      if (!state.status) renderNoStatus(error.message);
    } finally {
      state.statusBusy = false;
      renderDiagnosticsList();
    }
  }

  function applyStatus(payload) {
    state.status = payload && typeof payload === "object" ? payload : {};
    state.installation = normalizeInstallationStatus(state.status);
    updateDeviceOptions();
    detectStatusChanges();
    renderOverview();
    renderTrends();
    renderReportPage();
    renderLiveChannel();
  }

  async function refreshSecondary({ quiet = false } = {}) {
    if (state.secondaryBusy) return;
    state.secondaryBusy = true;
    setReportLoading(true);
    const metrics = ["light.lux", "imu.activity", "edge.environment.score"];
    const requests = [
      ["report", buildUrl("report")],
      ["reminders", buildUrl("reminders")],
      ...metrics.map((metric) => [`timeseries:${metric}`, buildUrl("timeseries", { metric })]),
      ["timeseries:focus.state", buildUrl("timeseries", { metric: "focus.state" })]
    ];
    const results = await Promise.allSettled(requests.map(([label, url]) => fetchJson(url, label)));
    results.forEach((result, index) => {
      if (result.status !== "fulfilled") return;
      const label = requests[index][0];
      if (label === "report") applyReport(result.value);
      else if (label === "reminders") state.reminders = normalizeReminders(result.value);
      else if (label === "timeseries:focus.state") state.focusTimeline = result.value || { metric: "focus.state", segments: [] };
      else {
        const metric = label.replace("timeseries:", "");
        state.series[metric] = normalizeSeries(result.value, metric);
      }
    });
    state.lastSecondaryAt = new Date();
    state.secondaryBusy = false;
    setReportLoading(false);
    renderAll();
    if (!quiet) toast("真实数据已刷新", "状态、日报、提醒和时序数据已按接口返回更新。", "ok");
  }

  function applyReport(payload) {
    state.report = payload && typeof payload === "object" ? payload : {};
    const text = typeof state.report.report_text === "string" ? state.report.report_text : "";
    if (text && text !== state.lastReportText) {
      state.lastReportText = text;
      revealReportText(text);
      addTrace("接收到后端最新日报复盘");
    } else if (!text) {
      state.reportDisplayText = "";
    }
  }

  function revealReportText(text) {
    if (state.reportRevealTimer) clearInterval(state.reportRevealTimer);
    state.reportDisplayText = "";
    let index = 0;
    state.reportRevealTimer = setInterval(() => {
      index += Math.max(1, Math.ceil(text.length / 90));
      state.reportDisplayText = text.slice(0, index);
      renderReportTextOnly();
      if (index >= text.length) {
        clearInterval(state.reportRevealTimer);
        state.reportRevealTimer = null;
      }
    }, 22);
  }

  function setReportLoading(busy) {
    state.reportBusy = busy;
    const pipeline = el("generationPipeline");
    pipeline?.classList.toggle("running", busy);
    setText("reportModelState", busy ? "正在读取真实接口" : state.report?.report_text ? "后端模型结果已同步" : "等待后端模型结果");
    setText("aiStage", busy ? "同步真实接口" : state.report?.report_text ? "模型结果已就绪" : "等待真实数据");
    setText("aiPercent", busy ? "72%" : state.report?.report_text ? "100%" : "0%");
    setBar("aiProgressBar", busy ? 72 : state.report?.report_text ? 100 : 0);
  }

  function detectStatusChanges() {
    for (const device of devices()) {
      const key = String(device.device_id || device.source || "unknown");
      const snapshot = {
        online: Boolean(device.online),
        valid: deviceDataValid(device),
        lastSeen: Number(device.last_seen || 0),
        summary: String(device.summary || "")
      };
      const previous = state.lastDeviceSnapshot.get(key);
      if (!previous) {
        addEvent("设备状态载入", `${key} · ${snapshot.online ? "在线" : isS3(device) ? "离线（当前正常）" : "离线"} · ${snapshot.valid ? "valid=true/缺省" : "valid=false"}`, "ok", false);
      } else {
        if (previous.online !== snapshot.online) addEvent("在线状态变化", `${key} → ${snapshot.online ? "在线" : "离线"}`, snapshot.online ? "ok" : "warning");
        if (previous.valid !== snapshot.valid) addEvent("数据有效性变化", `${key} → ${snapshot.valid ? "恢复正常统计" : "等待真实数据"}`, snapshot.valid ? "ok" : "warning");
        if (previous.lastSeen !== snapshot.lastSeen && snapshot.lastSeen > 0) addEvent("真实心跳更新", `${key} · ${formatTime(snapshot.lastSeen)}`, "ok", false);
      }
      state.lastDeviceSnapshot.set(key, snapshot);
    }
  }

  function updateDeviceOptions() {
    const select = el("deviceSelect");
    if (!select) return;
    select.innerHTML = '<option value="focuscube-base-01">focuscube-base-01（融合视图）</option>';
    state.selectedDeviceId = "focuscube-base-01";
    select.value = state.selectedDeviceId;
  }

  function showConnectionBanner(message) {
    const banner = el("fallbackBanner");
    if (!banner) return;
    banner.classList.remove("hidden");
    const strong = qs("strong", banner);
    const span = qs("span", banner);
    if (strong) strong.textContent = "真实接口暂不可用";
    if (span) span.textContent = `${message}。页面不会生成本地占位遥测，恢复连接后会自动更新。`;
  }

  function setConnection(type, text) {
    const chip = el("connectionChip");
    if (chip) chip.className = `connection-chip ${type}`;
    setText("connectionText", text);
    setText("latencyText", Number.isFinite(state.latency) ? `${state.latency} ms` : "-- ms");
  }

  function renderNoStatus(message) {
    setText("heroSummary", "正在等待 /api/v1/status");
    setText("heroSubtext", message || `固定接口：${API_BASE}`);
    setWaitingTelemetry();
    renderDeviceList();
  }

  function setWaitingTelemetry() {
    ["stripLux", "stripActivity", "stripBattery", "metricLux", "metricFocus", "metricActivity", "metricRatio", "gaugeLux", "gaugeActivity", "gaugeBattery"].forEach((id) => setText(id, "--"));
    setText("lightTag", "光照：等待真实数据");
    setText("focusTag", "专注：等待真实数据");
    setText("batteryTag", "电量：等待真实数据");
    setText("countdownValue", "--:--");
    setText("countdownState", "等待真实数据");
    setText("currentSession", "--");
    setText("cubeCaption", "IMU 等待真实数据");
    setText("latestLuxText", "最新：等待真实数据");
    ["stripLuxBar", "stripActivityBar", "stripBatteryBar", "confidenceBar"].forEach((id) => setBar(id, 0));
  }

  function renderAll() {
    renderOverview();
    renderTrends();
    renderReportPage();
    renderDiagnosticsList();
    renderRequestLog();
    renderLiveChannel();
  }

  function renderOverview() {
    const device = selectedDevice();
    const telemetry = currentTelemetry();
    const valid = device ? deviceDataValid(device) : false;
    const online = devices().filter((item) => Boolean(item.online)).length;
    const lux = telemetryValue("light", "lux");
    const activity = telemetryValue("imu", "activity");
    const edgeScore = telemetry?.edge?.valid !== false && hasFinite(telemetry?.edge?.environment?.score)
      ? Number(telemetry.edge.environment.score) * 100 : null;
    const edgeState = telemetry?.edge?.environment?.state || null;
    const battery = edgeScore;
    const focusState = telemetry?.focus && hasOwn(telemetry.focus, "state") ? telemetry.focus.state : null;
    const focusMinutes = reportMetric("focus_minutes");
    const ratioRaw = reportMetric("suitable_light_ratio");
    const ratio = hasFinite(ratioRaw) ? Number(ratioRaw) * 100 : null;

    if (!device) {
      setText("heroSummary", "等待设备状态卡片");
      setText("heroSubtext", `正在轮询 ${joinUrl(API_PATHS.status)}`);
      setWaitingTelemetry();
    } else if (!valid) {
      setText("heroSummary", "等待真实数据");
      setText("heroSubtext", "当前设备返回 valid:false。D 端仅保留设备在线状态，不展示 face=0、battery=0 或其他占位遥测。后续 valid:true 时自动恢复。");
      setWaitingTelemetry();
    } else {
      const onlineText = device.online ? "设备在线" : isS3(device) ? "S3 当前离线（正常，等待实物接入）" : "设备离线";
      setText("heroSummary", device.summary || onlineText);
      setText("heroSubtext", `设备状态卡片完全来自 ${joinUrl(API_PATHS.status)}；缺少的字段保持“等待真实数据”，不由前端补造。`);
      setText("lightTag", hasFinite(lux) ? `光照 ${Math.round(lux)} lux${telemetry.light?.label ? ` · ${lightLabelText(telemetry.light.label)}` : ""}` : "光照：等待真实数据");
      setText("focusTag", focusState ? `状态 ${focusStateText(focusState)}${hasFinite(focusMinutes) ? ` · ${Math.round(focusMinutes)} min` : ""}` : hasFinite(focusMinutes) ? `今日专注 ${Math.round(focusMinutes)} min` : "专注：等待真实数据");
      setText("batteryTag", hasFinite(edgeScore) ? `环境 ${lightLabelText(edgeState)} · 适宜度 ${Math.round(edgeScore)}%` : "端侧环境分析：等待 EYE 数据");
      setText("syncTag", `同步 ${relativeTime(device.last_seen || state.status?.now)}`);
      setText("stripLux", hasFinite(lux) ? Math.round(lux) : "--");
      setText("stripActivity", hasFinite(activity) ? Number(activity).toFixed(2) : "--");
      setText("stripBattery", hasFinite(edgeScore) ? `${Math.round(edgeScore)}%` : "--");
      setBar("stripLuxBar", hasFinite(lux) ? clamp(Number(lux) / 700 * 100, 0, 100) : 0);
      setBar("stripActivityBar", hasFinite(activity) ? clamp(Number(activity) * 100, 0, 100) : 0);
      setBar("stripBatteryBar", hasFinite(edgeScore) ? clamp(Number(edgeScore), 0, 100) : 0);
      renderCountdown(telemetry?.focus || null);
      renderCube(telemetry?.imu || null);
    }

    setText("metricLux", valid && hasFinite(lux) ? Math.round(lux) : "--");
    setText("metricFocus", hasFinite(focusMinutes) ? Math.round(focusMinutes) : "--");
    setText("metricActivity", valid && hasFinite(activity) ? Number(activity).toFixed(2) : "--");
    setText("metricRatio", hasFinite(ratio) ? Math.round(ratio) : "--");
    setText("metricLuxHint", !valid ? "等待真实数据" : hasFinite(lux) ? "来自 status.telemetry.light.lux" : "接口未返回当前照度");
    setText("metricFocusHint", hasFinite(focusMinutes) ? "来自日报统计，valid:false 数据由后端忽略" : "等待后端日报统计");
    setText("metricActivityHint", !valid ? "等待真实数据" : hasFinite(activity) ? "来自 status.telemetry.imu.activity" : "接口未返回活动度");
    setText("metricRatioHint", hasFinite(ratio) ? "来自日报统计" : "等待后端日报统计");
    updateDelta("luxDelta", valid ? lux : null, state.previousValues.lux, " lux");
    updateDelta("activityDelta", valid ? activity : null, state.previousValues.activity, "", 2);
    setText("focusDelta", state.report?.generated_at ? `更新于 ${formatTime(state.report.generated_at)}` : "等待日报");
    setText("ratioDelta", state.lastSecondaryAt ? `刷新 ${state.lastSecondaryAt.toLocaleTimeString("zh-CN", { hour12: false })}` : "等待统计");

    renderSparkline("luxSparkline", state.series["light.lux"]?.points || []);
    renderSparkline("focusSparkline", reportSparkData("focus_minutes"));
    renderSparkline("activitySparkline", state.series["imu.activity"]?.points || []);
    renderSparkline("batterySparkline", state.series["power.battery_pct"]?.points || []);
    renderLineChart(el("overviewLuxChart"), state.series["light.lux"], { metric: "light.lux", safeMin: 300, safeMax: 500, compact: true, window: 50 });
    setText("latestLuxText", valid && hasFinite(lux) ? `最新 ${Math.round(lux)} lux` : "最新：等待真实数据");

    renderEvents();
    renderDeviceList();
    renderReportTextOnly();
    renderSuggestions("suggestionList", state.report?.suggestions);
    renderEvidenceRow();
    renderReminders("reminderList", state.reminders, 5, true);
    setText("onlineCount", `${online} / ${devices().length} 在线`);
    setText("eventCount", `${state.events.length} 条`);
    setText("reminderCount", `${state.reminders.length} 条`);
    setText("heartbeatText", formatClock());

    const statusOk = Boolean(state.status);
    setText("flowState", statusOk ? "真实接口轮询中" : "等待状态接口");
    setText("flowS3", device ? (device.online ? "状态在线" : isS3(device) ? "离线（正常）" : "离线") : "等待状态");
    setText("flowBackend", state.endpointHealth.status ? `status ${state.latency ?? "--"} ms` : "等待连接");
    setText("flowLLM", state.report?.report_text ? "日报已返回" : "等待日报接口");
    setText("flowDisplay", "本页动态刷新");
    setText("flowTelemetryRate", `${STATUS_POLL_MS / 1000}s / status`);

    state.previousValues = { lux, activity, battery, ratio };
    flashChangedValues({ lux, activity, battery, ratio });
  }

  function reportSparkData(key) {
    const value = reportMetric(key);
    if (!hasFinite(value)) return [];
    return [{ value }, { value }];
  }

  function flashChangedValues(values) {
    const map = { lux: "metricLux", activity: "metricActivity", battery: "stripBattery", ratio: "metricRatio" };
    Object.entries(values).forEach(([key, value]) => {
      if (!hasFinite(value) || !hasFinite(state.previousValues[key]) || Number(value) === Number(state.previousValues[key])) return;
      const node = el(map[key]);
      if (!node) return;
      node.classList.remove("value-flash");
      void node.offsetWidth;
      node.classList.add("value-flash");
    });
  }

  function renderCountdown(focus) {
    const remaining = focus && hasOwn(focus, "remaining_s") && hasFinite(focus.remaining_s) ? Number(focus.remaining_s) : null;
    if (!hasFinite(remaining)) {
      setText("countdownValue", "--:--");
      setText("countdownState", "等待真实数据");
      setText("currentSession", "--");
      const ring = el("countdownRing");
      ring?.style.setProperty("--progress", "0deg");
      return;
    }
    const minutes = Math.floor(remaining / 60);
    const seconds = Math.floor(remaining % 60);
    setText("countdownValue", `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`);
    setText("countdownState", focusStateText(focus.state));
    setText("currentSession", hasOwn(focus, "session_count") ? String(focus.session_count) : "--");
    const total = focus.state === "break" ? 300 : 1500;
    el("countdownRing")?.style.setProperty("--progress", `${clamp((total - remaining) / total * 360, 0, 360)}deg`);
  }

  function renderCube(imu) {
    const faceMap = { "+X": 1, "-X": 2, "+Y": 3, "-Y": 4, "+Z": 5, "-Z": 6, move: 6 };
    const rawFace = imu && hasOwn(imu, "face") ? imu.face : null;
    const face = hasFinite(rawFace) && Number(rawFace) >= 0 ? Number(rawFace) + (Number(rawFace) === 0 ? 1 : 0) : faceMap[rawFace] || null;
    const activity = imu && hasOwn(imu, "activity") && hasFinite(imu.activity) ? Number(imu.activity) : null;
    const cube = el("cube3d");
    if (!cube) return;
    if (!face) {
      cube.style.transform = "rotateX(-18deg) rotateY(30deg)";
      cube.classList.add("awaiting");
      setText("cubeCaption", "IMU 等待真实数据");
      return;
    }
    cube.classList.remove("awaiting");
    const rotations = {
      1: [-10, 18], 2: [-12, 108], 3: [-12, 198], 4: [-12, 288], 5: [-82, 20], 6: [78, 20]
    };
    const [x, y] = rotations[face] || [-18, 30];
    const jitter = hasFinite(activity) ? Number(activity) * 3 : 0;
    cube.style.transform = `rotateX(${x + jitter}deg) rotateY(${y + jitter}deg)`;
    setText("cubeCaption", `IMU ${rawFace || `FACE ${face}`}${hasFinite(activity) ? ` · ${Number(activity).toFixed(2)}` : ""}`);
  }

  function renderDeviceList() {
    const target = el("deviceList");
    if (!target) return;
    if (!devices().length) {
      target.innerHTML = '<div class="empty-state waiting-live"><span></span>正在等待 /api/v1/status 返回设备列表</div>';
      return;
    }
    target.innerHTML = devices().map(renderDeviceItem).join("");
  }

  function renderDeviceItem(device) {
    const online = Boolean(device.online);
    if (device.role) {
      const health = device.health || {};
      const roleName = device.role === "edge_controller" ? "EYE · 边缘控制器" : "C3 · AS7341 光照节点";
      const healthChips = [
        health.run_state && `<span>${escapeHtml(health.run_state)}</span>`,
        device.role === "edge_controller" && hasOwn(health, "c3_connected") && `<span>C3 链路 ${health.c3_connected ? "已连接" : "未连接"}</span>`,
        device.role === "edge_controller" && hasOwn(health, "c3_control_ok") && `<span>控制 ACK ${health.c3_control_ok ? "正常" : "异常"}</span>`,
        health.firmware_version && `<span>${escapeHtml(health.firmware_version)}</span>`
      ].filter(Boolean);
      return `<div class="device-item ${online ? "online" : "offline"} valid">
        <div class="device-orb"><span></span>${device.role === "edge_controller" ? "EYE" : "C3"}</div>
        <div class="device-info"><div><strong>${escapeHtml(device.device_id)}</strong><span class="device-status-text">${online ? "在线" : "离线"}</span></div>
        <p>${roleName}</p><div class="device-fields">${healthChips.length ? healthChips.join("") : "<span>尚无健康数据</span>"}</div>
        <small>last_seen：${escapeHtml(relativeTime(device.last_seen))}</small></div></div>`;
    }
    const valid = deviceDataValid(device);
    const telemetry = valid && device.telemetry && typeof device.telemetry === "object" ? device.telemetry : null;
    const chips = [];
    if (telemetry?.light && hasOwn(telemetry.light, "lux") && hasFinite(telemetry.light.lux)) chips.push(`<span>光照 ${Math.round(Number(telemetry.light.lux))} lux</span>`);
    if (telemetry?.imu && hasOwn(telemetry.imu, "face") && hasFinite(telemetry.imu.face) && Number(telemetry.imu.face) > 0) chips.push(`<span>face ${Number(telemetry.imu.face)}</span>`);
    if (telemetry?.focus && hasOwn(telemetry.focus, "state")) chips.push(`<span>${escapeHtml(focusStateText(telemetry.focus.state))}</span>`);
    if (telemetry?.power && hasOwn(telemetry.power, "battery_pct") && hasFinite(telemetry.power.battery_pct)) chips.push(`<span>电量 ${Math.round(Number(telemetry.power.battery_pct))}%</span>`);
    const onlineText = online ? "在线" : isS3(device) ? "离线（当前正常）" : "离线";
    const body = !valid
      ? '<div class="waiting-real"><i></i><strong>等待真实数据</strong><span>valid:false，隐藏所有占位遥测</span></div>'
      : chips.length
        ? `<div class="device-fields">${chips.join("")}</div>`
        : isS3(device)
          ? '<div class="waiting-real neutral"><i></i><strong>等待真实数据</strong><span>status 未返回 telemetry 字段</span></div>'
          : '<div class="waiting-real neutral"><i></i><strong>状态已同步</strong><span>该设备未返回 telemetry 字段</span></div>';
    return `<div class="device-item ${online ? "online" : "offline"} ${valid ? "valid" : "invalid"}">
      <div class="device-orb"><span></span>${escapeHtml(String(device.source || "device").toUpperCase())}</div>
      <div class="device-info"><div><strong>${escapeHtml(device.device_id || "未命名设备")}</strong><span class="device-status-text">${escapeHtml(onlineText)}</span></div>
      <p>${escapeHtml(device.summary || (isS3(device) && !online ? "等待 S3 实物接入" : "接口未返回 summary"))}</p>
      ${body}<small>last_seen：${escapeHtml(relativeTime(device.last_seen))} · ${escapeHtml(validityLabel(device))}</small></div>
    </div>`;
  }

  function addEvent(title, detail, tone = "ok", render = true) {
    state.events.unshift({ title, detail, tone, time: new Date() });
    state.events = state.events.slice(0, 24);
    if (render) renderEvents();
  }

  function renderEvents() {
    const target = el("eventStream");
    if (!target) return;
    target.innerHTML = state.events.length
      ? state.events.map((item) => `<div class="event-item ${item.tone}"><i></i><div><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.detail)}</p></div><time>${item.time.toLocaleTimeString("zh-CN", { hour12: false })}</time></div>`).join("")
      : '<div class="empty-state waiting-live"><span></span>真实状态变化会实时出现在这里</div>';
  }

  function renderReportTextOnly() {
    const text = state.reportDisplayText || state.report?.report_text || "";
    const waiting = text || "等待后端 /api/v1/report/daily 返回真实复盘…";
    setText("reportText", waiting);
    setText("fullReportText", waiting);
  }

  function renderSuggestions(targetId, suggestions) {
    const target = el(targetId);
    if (!target) return;
    const list = Array.isArray(suggestions) ? suggestions.filter((item) => String(item || "").trim()) : [];
    if (!list.length) {
      target.innerHTML = '<div class="empty-state waiting-live"><span></span>等待后端返回建议</div>';
      return;
    }
    target.innerHTML = list.map((text, index) => target.tagName === "OL"
      ? `<li><span>${index + 1}</span>${escapeHtml(text)}</li>`
      : `<div><i>${index + 1}</i><span>${escapeHtml(text)}</span></div>`).join("");
  }

  function renderEvidenceRow() {
    const target = el("evidenceRow");
    if (!target) return;
    const rows = [];
    const metrics = state.report?.metrics || {};
    if (hasOwn(metrics, "focus_minutes") && hasFinite(metrics.focus_minutes)) rows.push(["专注", `${Math.round(Number(metrics.focus_minutes))} min`]);
    if (hasOwn(metrics, "avg_lux") && hasFinite(metrics.avg_lux)) rows.push(["平均照度", `${Math.round(Number(metrics.avg_lux))} lux`]);
    if (hasOwn(metrics, "suitable_light_ratio") && hasFinite(metrics.suitable_light_ratio)) rows.push(["适宜占比", `${Math.round(Number(metrics.suitable_light_ratio) * 100)}%`]);
    target.innerHTML = rows.length ? rows.map(([label, value]) => `<span><b>${escapeHtml(value)}</b><small>${escapeHtml(label)}</small></span>`).join("") : '<span class="waiting-inline">等待后端日报指标</span>';
  }

  function renderReminders(targetId, reminders, limit = reminders.length, actions = false) {
    const target = el(targetId);
    if (!target) return;
    const list = Array.isArray(reminders) ? reminders.slice(0, limit) : [];
    if (!list.length) {
      target.innerHTML = '<div class="empty-state waiting-live"><span></span>等待真实提醒</div>';
      return;
    }
    target.innerHTML = list.map((item) => `<div class="reminder-item priority-${Number(item.priority || 0)}" data-reminder-id="${escapeHtml(item.id || "")}">
      <i></i><div><strong>${escapeHtml(item.type || "reminder")}</strong><p>${escapeHtml(item.text || "")}</p><small>${formatTime(item.ts || item.created_at)}${hasOwn(item, "ttl_s") ? ` · TTL ${escapeHtml(item.ttl_s)}s` : ""}</small></div>
      ${actions ? '<button type="button" data-ack-reminder>已读</button>' : ""}</div>`).join("");
  }

  function renderTrends() {
    const metric = state.activeMetric;
    const series = state.series[metric] || { metric, points: [] };
    const titles = {
      "light.lux": ["光照强度（lux）", "来自 C3 / AS7341 的真实光照数据"],
      "imu.activity": ["IMU 活动度", "来自 EYE 的真实活动度数据"],
      "power.battery_pct": ["设备电量（%）", "仅在存在真实电量测量时展示"],
      "edge.environment.score": ["环境适宜度", "EYE 基于 C3 光谱生成的端侧派生结果"]
    };
    setText("trendChartTitle", titles[metric][0]);
    setText("trendChartDesc", titles[metric][1]);
    setText("trendStreamState", state.lastSecondaryAt ? `最近同步 ${state.lastSecondaryAt.toLocaleTimeString("zh-CN", { hour12: false })} · 每 ${SECONDARY_POLL_MS / 1000} 秒刷新` : "正在等待真实时序数据");
    renderLineChart(el("trendChart"), series, { metric, window: state.chartWindow, safeMin: metric === "light.lux" ? 300 : null, safeMax: metric === "light.lux" ? 500 : null });
    renderTrendStats(series);
    renderDataTicker(series);
    renderGauges();
    renderFocusTimeline();
    renderCorrelations();
  }

  function renderTrendStats(series) {
    const target = el("trendStats");
    if (!target) return;
    const values = (series?.points || []).map((point) => Number(point.value)).filter(Number.isFinite);
    if (!values.length) {
      target.innerHTML = '<span>等待真实数据</span>';
      return;
    }
    const avg = values.reduce((sum, value) => sum + value, 0) / values.length;
    target.innerHTML = `<span>最新 <b>${escapeHtml(formatMetric(values.at(-1), series.metric))}</b></span><span>平均 <b>${escapeHtml(formatMetric(avg, series.metric))}</b></span><span>样本 <b>${values.length}</b></span>`;
  }

  function renderDataTicker(series) {
    const target = el("dataTicker");
    if (!target) return;
    const points = (series?.points || []).slice(-12).reverse();
    target.innerHTML = points.length
      ? `<div class="ticker-track">${[...points, ...points].map((point) => `<span><i></i>${formatTime(point.ts)} · ${escapeHtml(formatMetric(point.value, series.metric))}</span>`).join("")}</div>`
      : '<div class="empty-state waiting-live"><span></span>时序接口暂无可展示点</div>';
  }

  function renderGauges() {
    const device = selectedDevice();
    const valid = device ? deviceDataValid(device) : false;
    const lux = valid ? telemetryValue("light", "lux") : null;
    const activity = valid ? telemetryValue("imu", "activity") : null;
    const telemetry = currentTelemetry();
    const battery = valid && telemetry?.edge?.valid !== false && hasFinite(telemetry?.edge?.environment?.score)
      ? Number(telemetry.edge.environment.score) * 100 : null;
    setText("gaugeLux", hasFinite(lux) ? Math.round(lux) : "--");
    setText("gaugeActivity", hasFinite(activity) ? Number(activity).toFixed(2) : "--");
    setText("gaugeBattery", hasFinite(battery) ? Math.round(battery) : "--");
    setText("gaugeLuxLabel", hasFinite(lux) ? "status 返回真实照度" : "等待真实数据");
    setText("gaugeActivityLabel", hasFinite(activity) ? "status 返回真实活动度" : "等待真实数据");
    setText("gaugeBatteryLabel", hasFinite(battery) ? "EYE 端侧派生 · 非用户专注度" : "等待 EYE 环境分析");
    setGauge("luxGauge", hasFinite(lux) ? clamp(Number(lux) / 700 * 360, 0, 360) : 0);
    setGauge("activityGauge", hasFinite(activity) ? clamp(Number(activity) * 360, 0, 360) : 0);
    setGauge("batteryGauge", hasFinite(battery) ? clamp(Number(battery) / 100 * 360, 0, 360) : 0);
    const count = [lux, activity, battery].filter(hasFinite).length;
    const completeness = Math.round(count / 3 * 100);
    setText("fusionState", valid && count ? "真实字段已就绪" : "等待真实数据");
    setText("fusionResult", valid && count ? `当前仅展示接口实际返回的 ${count} 项传感字段，不生成新的 telemetry 字段。` : "valid:false 或 status 未返回遥测，全部仪表保持等待态。 ");
    setText("confidenceValue", `${completeness}%`);
    setBar("confidenceBar", completeness);
    el("decisionOrb")?.style.setProperty("--completeness", `${completeness}%`);
  }

  function renderFocusTimeline() {
    const target = el("focusTimeline");
    if (!target) return;
    const segments = Array.isArray(state.focusTimeline?.segments) ? state.focusTimeline.segments : [];
    if (!segments.length) {
      target.innerHTML = '<div class="empty-state waiting-live"><span></span>等待 focus.state 时序数据</div>';
      return;
    }
    const start = Math.min(...segments.map((item) => Number(item.start_ts ?? item.start)).filter(Number.isFinite));
    const end = Math.max(...segments.map((item) => Number(item.end_ts ?? item.end)).filter(Number.isFinite));
    const span = Math.max(1, end - start);
    target.innerHTML = `<div class="timeline-track">${segments.map((segment) => {
      const segmentStart = Number(segment.start_ts ?? segment.start);
      const segmentEnd = Number(segment.end_ts ?? segment.end);
      const stateValue = segment.value ?? segment.state;
      const left = clamp((segmentStart - start) / span * 100, 0, 100);
      const width = clamp((segmentEnd - segmentStart) / span * 100, 1, 100);
      return `<div class="timeline-segment ${escapeHtml(stateValue || "idle")}" style="left:${left}%;width:${width}%"><span>${escapeHtml(focusStateText(stateValue))}</span></div>`;
    }).join("")}<i class="timeline-scan"></i></div>`;
  }

  function renderCorrelations() {
    const telemetry = currentTelemetry();
    const rows = [];
    if (telemetry?.light && hasOwn(telemetry.light, "lux") && hasFinite(telemetry.light.lux)) rows.push(["光照字段", `${Math.round(Number(telemetry.light.lux))} lux`, "来自 status.telemetry.light.lux"]);
    if (telemetry?.imu && hasOwn(telemetry.imu, "activity") && hasFinite(telemetry.imu.activity)) rows.push(["活动度字段", Number(telemetry.imu.activity).toFixed(2), "来自 status.telemetry.imu.activity"]);
    if (telemetry?.edge?.environment && hasFinite(telemetry.edge.environment.score)) rows.push(["环境适宜度", `${Math.round(Number(telemetry.edge.environment.score) * 100)}%`, "EYE 端侧派生，原始光照仍只来自 C3"]);
    if (telemetry?.focus && hasOwn(telemetry.focus, "state")) rows.push(["专注字段", focusStateText(telemetry.focus.state), "来自 status.telemetry.focus.state"]);
    const target = el("correlationList");
    if (!target) return;
    target.innerHTML = rows.length
      ? rows.map(([label, value, source]) => `<div><i></i><span><b>${escapeHtml(label)}：${escapeHtml(value)}</b><small>${escapeHtml(source)}</small></span></div>`).join("")
      : '<div class="empty-state waiting-live"><span></span>等待真实字段，前端不会用 0 值填充</div>';
  }

  function renderReportPage() {
    const metrics = state.report?.metrics || {};
    const deviceId = state.report?.device_id || state.selectedDeviceId;
    const date = state.report?.date || el("dateInput")?.value || todayString();
    setText("reportMeta", `${date} · ${deviceId}`);
    renderReportTextOnly();
    renderSuggestions("fullSuggestionList", state.report?.suggestions);
    renderReminders("reportReminderList", state.reminders, 8, false);

    const entries = [];
    const labels = {
      focus_minutes: ["专注时长", "min", (value) => Math.round(value)],
      pomodoro_count: ["专注轮次", "轮", (value) => Math.round(value)],
      avg_lux: ["平均照度", "lux", (value) => Math.round(value)],
      suitable_light_ratio: ["适宜光照占比", "%", (value) => Math.round(value * 100)]
    };
    Object.entries(labels).forEach(([key, [label, unit, formatter]]) => {
      if (hasOwn(metrics, key) && hasFinite(metrics[key])) entries.push([label, `${formatter(Number(metrics[key]))} ${unit}`]);
    });
    const evidence = el("evidenceTable");
    if (evidence) evidence.innerHTML = entries.length
      ? entries.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b><i><em style="width:${Math.min(100, Math.max(8, parseFloat(value) || 8))}%"></em></i></div>`).join("")
      : '<div class="empty-state waiting-live"><span></span>等待日报统计；valid:false 数据应由 C 端忽略</div>';

    const completeness = Math.round(entries.length / 4 * 100);
    setText("dailyScore", entries.length ? completeness : "--");
    setText("scoreLabel", entries.length ? "日报字段完整度" : "等待日报数据");
    setText("scoreExplain", entries.length ? `后端返回 ${entries.length} / 4 项约定指标。该分值仅表示接口字段完整度，不是前端生成的业务评分。` : "D 端不会依据占位值生成评分。 ");
    el("scoreRing")?.style.setProperty("--score", `${completeness * 3.6}deg`);
    renderModelTrace();
  }

  function addTrace(text) {
    state.modelTrace.unshift({ text, time: new Date() });
    state.modelTrace = state.modelTrace.slice(0, 12);
    renderModelTrace();
  }

  function renderModelTrace() {
    const target = el("modelTrace");
    if (!target) return;
    target.innerHTML = state.modelTrace.length
      ? state.modelTrace.map((item) => `<div><i></i><span>${escapeHtml(item.text)}</span><time>${item.time.toLocaleTimeString("zh-CN", { hour12: false })}</time></div>`).join("")
      : '<div class="empty-state waiting-live"><span></span>真实日报请求轨迹将在这里更新</div>';
  }

  function renderLiveChannel() {
    setText("frameCounter", String(state.statusPollCount));
    setText("sideScene", state.endpointHealth.status ? "真实接口" : "等待连接");
    setText("streamRate", `status 每 ${STATUS_POLL_MS / 1000} 秒`);
    const next = state.lastStatusAt ? Math.max(0, STATUS_POLL_MS - (Date.now() - state.lastStatusAt.getTime())) : STATUS_POLL_MS;
    setText("sceneCountdown", `下一次状态刷新约 ${Math.ceil(next / 1000)} 秒`);
    setText("fixedApiUrl", API_BASE);
    setText("validRuleState", selectedDevice() ? validityLabel(selectedDevice()) : "等待设备状态");
    const monitor = el("liveMonitorPulse");
    monitor?.classList.toggle("active", Boolean(state.endpointHealth.status));
  }

  function renderDiagnosticsList(results = state.endpointHealth) {
    const list = [
      ["GET", API_PATHS.status, "status", "设备状态卡片（优先）"],
      ["GET", API_PATHS.report, "report", "大模型日报复盘"],
      ["GET", API_PATHS.reminders, "reminders", "提醒队列"],
      ["GET", API_PATHS.timeseries, "timeseries", "真实时序曲线"]
    ];
    const target = el("apiCheckList");
    if (!target) return;
    target.innerHTML = list.map(([method, path, key, desc]) => {
      const result = results[key];
      const text = result === true ? "可用" : result === false ? "失败" : "未检测";
      return `<div class="api-check ${result === true ? "ok" : result === false ? "fail" : "pending"}"><b>${method}</b><code>${escapeHtml(path)}</code><span>${escapeHtml(desc)}</span><em>${text}</em></div>`;
    }).join("");

    const device = selectedDevice();
    const targetResult = el("diagResult");
    if (targetResult && !state.diagnosticsBusy) {
      const available = Object.values(results).filter((item) => item === true).length;
      const validText = device ? (deviceDataValid(device) ? "valid=true 或缺省，允许显示真实字段" : "valid=false，D 端已进入等待真实数据") : "等待设备对象";
      targetResult.className = `diag-result ${results.status ? "success" : results.status === false ? "error" : ""}`;
      targetResult.innerHTML = `<div class="diag-spinner"></div><div><strong>${results.status ? "状态接口已连接" : results.status === false ? "状态接口不可用" : "等待接口检测"}</strong><p>固定地址 ${escapeHtml(API_BASE)} · ${escapeHtml(validText)} · 当前 ${available} / 4 类接口可用。</p></div>`;
    }
  }

  async function runDiagnostics() {
    if (state.diagnosticsBusy) return;
    state.diagnosticsBusy = true;
    const target = el("diagResult");
    if (target) {
      target.className = "diag-result";
      target.innerHTML = '<div class="diag-spinner"></div><div><strong>正在检测真实接口</strong><p>逐项请求固定地址，不启动本地仿真。</p></div>';
    }
    const checks = [
      ["status", () => fetchJson(buildUrl("status"), "status:diagnostic", 3500)],
      ["report", () => fetchJson(buildUrl("report"), "report:diagnostic", 3500)],
      ["reminders", () => fetchJson(buildUrl("reminders"), "reminders:diagnostic", 3500)],
      ["timeseries", () => fetchJson(buildUrl("timeseries", { metric: "light.lux" }), "timeseries:diagnostic", 3500)]
    ];
    const results = {};
    for (const [key, run] of checks) {
      try { await run(); results[key] = true; } catch (_) { results[key] = false; }
      renderDiagnosticsList(results);
    }
    state.endpointHealth = { ...state.endpointHealth, ...results };
    state.diagnosticsBusy = false;
    renderDiagnosticsList();
  }

  function addRequestLog(entry) {
    state.requestLog.unshift(entry);
    state.requestLog = state.requestLog.slice(0, 50);
    renderRequestLog();
  }

  function renderRequestLog() {
    const target = el("requestLog");
    if (!target) return;
    target.innerHTML = state.requestLog.length
      ? state.requestLog.map((item) => `<div class="log-entry new-log"><span>${item.time.toLocaleTimeString("zh-CN", { hour12: false })}</span><span class="${item.ok ? "ok" : "fail"}">${item.ok ? item.status : "ERR"}</span><span title="${escapeHtml(item.url)}">${escapeHtml(item.label)}</span><span>${item.duration} ms</span></div>`).join("")
      : '<div class="empty-state waiting-live"><span></span>接口请求日志将持续更新</div>';
  }

  function renderLineChart(container, series, options = {}) {
    if (!container) return;
    const sourcePoints = (series?.points || []).map(normalizePoint).filter(Boolean);
    const points = sourcePoints.slice(-Number(options.window || sourcePoints.length));
    container.innerHTML = "";
    if (points.length < 2) {
      container.innerHTML = '<div class="empty-state waiting-live chart-wait"><span></span>等待真实时序数据</div>';
      return;
    }
    const width = Math.max(680, container.clientWidth || 900);
    const height = Math.max(220, container.clientHeight || 300);
    const margin = options.compact ? { top: 14, right: 18, bottom: 30, left: 45 } : { top: 20, right: 22, bottom: 42, left: 53 };
    const innerW = width - margin.left - margin.right;
    const innerH = height - margin.top - margin.bottom;
    const values = points.map((point) => Number(point.value));
    let min = Math.min(...values);
    let max = Math.max(...values);
    if (Number.isFinite(options.safeMin)) min = Math.min(min, Number(options.safeMin));
    if (Number.isFinite(options.safeMax)) max = Math.max(max, Number(options.safeMax));
    const padding = Math.max((max - min) * 0.14, options.metric === "imu.activity" ? 0.05 : 3);
    min -= padding;
    max += padding;
    if (max === min) max += 1;
    const x = (index) => margin.left + index / (points.length - 1) * innerW;
    const y = (value) => margin.top + (1 - (value - min) / (max - min)) * innerH;
    const line = points.map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(2)},${y(point.value).toFixed(2)}`).join(" ");
    const area = `${line} L${x(points.length - 1)},${margin.top + innerH} L${x(0)},${margin.top + innerH} Z`;
    const grid = Array.from({ length: 5 }, (_, index) => {
      const gy = margin.top + index / 4 * innerH;
      const value = max - index / 4 * (max - min);
      return `<line x1="${margin.left}" y1="${gy}" x2="${width - margin.right}" y2="${gy}"/><text class="chart-label" x="${margin.left - 8}" y="${gy + 3}" text-anchor="end">${formatAxisValue(value, options.metric)}</text>`;
    }).join("");
    const indices = [...new Set([0, Math.floor((points.length - 1) / 4), Math.floor((points.length - 1) / 2), Math.floor((points.length - 1) * 0.75), points.length - 1])];
    const labels = indices.map((index) => `<text class="chart-label" x="${x(index)}" y="${height - 7}" text-anchor="middle">${formatTime(points[index].ts).slice(0, 5)}</text>`).join("");
    let safeRect = "";
    if (Number.isFinite(options.safeMin) && Number.isFinite(options.safeMax)) {
      const top = y(options.safeMax);
      const bottom = y(options.safeMin);
      safeRect = `<rect class="chart-safe" x="${margin.left}" y="${Math.min(top, bottom)}" width="${innerW}" height="${Math.abs(bottom - top)}" rx="4"/>`;
    }
    const dots = points.map((point, index) => `<circle class="chart-dot" data-index="${index}" cx="${x(index)}" cy="${y(point.value)}" r="${index === points.length - 1 ? 4 : options.compact ? 2.2 : 2.8}"/>`).join("");
    const id = `gradient-${Math.random().toString(36).slice(2)}`;
    container.innerHTML = `<svg class="chart-live-draw" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"><defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#62e4aa" stop-opacity=".25"/><stop offset="100%" stop-color="#62e4aa" stop-opacity="0"/></linearGradient></defs><g class="chart-grid">${grid}</g>${safeRect}<path d="${area}" fill="url(#${id})"/><path class="chart-line" d="${line}"/>${dots}${labels}<line class="chart-scan-line" x1="${x(points.length - 1)}" y1="${margin.top}" x2="${x(points.length - 1)}" y2="${margin.top + innerH}"/></svg>`;
    qsa(".chart-dot", container).forEach((dot) => {
      dot.addEventListener("mouseenter", () => {
        const point = points[Number(dot.dataset.index)];
        const tip = document.createElement("div");
        tip.className = "chart-tooltip";
        tip.textContent = `${formatTime(point.ts, true)} · ${formatMetric(point.value, options.metric)}`;
        tip.style.left = `${Number(dot.getAttribute("cx")) / width * 100}%`;
        tip.style.top = `${Number(dot.getAttribute("cy")) / height * 100}%`;
        container.appendChild(tip);
      });
      dot.addEventListener("mouseleave", () => qs(".chart-tooltip", container)?.remove());
    });
  }

  function renderSparkline(id, source) {
    const svg = el(id);
    if (!svg) return;
    const points = source.slice(-24).map((item, index) => ({ x: index, value: Number(item.value ?? item) })).filter((item) => Number.isFinite(item.value));
    if (points.length < 2) {
      svg.innerHTML = "";
      return;
    }
    const width = 240;
    const height = 36;
    const min = Math.min(...points.map((point) => point.value));
    const max = Math.max(...points.map((point) => point.value));
    const range = max - min || 1;
    const coords = points.map((point, index) => ({ x: index / (points.length - 1) * width, y: height - 3 - (point.value - min) / range * (height - 7) }));
    const line = coords.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
    const area = `${line} L${width},${height} L0,${height} Z`;
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.innerHTML = `<path class="spark-area" d="${area}"/><path class="spark-live" d="${line}"/>`;
  }

  function updateDelta(id, current, previous, suffix = "", digits = 0) {
    const target = el(id);
    if (!target) return;
    if (!hasFinite(current) || !hasFinite(previous)) {
      target.textContent = "等待更新";
      target.classList.remove("down");
      return;
    }
    const diff = Number(current) - Number(previous);
    target.textContent = diff === 0 ? "本次无变化" : `${diff > 0 ? "↑" : "↓"} ${Math.abs(diff).toFixed(digits)}${suffix}`;
    target.classList.toggle("down", diff < 0);
  }

  function formatAxisValue(value, metric) {
    return metric === "imu.activity" ? Number(value).toFixed(1) : Math.round(value);
  }

  function formatMetric(value, metric) {
    if (!hasFinite(value)) return "--";
    if (metric === "light.lux") return `${Math.round(Number(value))} lux`;
    if (metric === "imu.activity") return Number(value).toFixed(2);
    if (metric === "power.battery_pct") return `${Math.round(Number(value))}%`;
    return String(round(value, 2));
  }

  function setBar(id, value) {
    const node = el(id);
    if (node) node.style.width = `${clamp(Number(value) || 0, 0, 100)}%`;
  }

  function setGauge(id, degrees) {
    const node = el(id);
    if (node) node.style.setProperty("--gauge", `${clamp(Number(degrees) || 0, 0, 360)}deg`);
  }

  function toast(title, message, type = "ok") {
    const container = el("toastContainer");
    if (!container) return;
    const item = document.createElement("div");
    item.className = `toast ${type}`;
    item.innerHTML = `<b>${escapeHtml(title)}</b><span>${escapeHtml(message)}</span>`;
    container.appendChild(item);
    setTimeout(() => item.remove(), 3800);
  }

  function switchPage(page) {
    state.currentPage = page;
    const titles = { overview: "实时总览", trends: "数据流监测", report: "AI 动态复盘", diagnostics: "联调诊断" };
    qsa(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.page === page));
    qsa(".page").forEach((item) => item.classList.toggle("active", item.id === `page-${page}`));
    setText("pageTitle", titles[page]);
    const active = el(`page-${page}`);
    active?.classList.remove("page-refresh");
    void active?.offsetWidth;
    active?.classList.add("page-refresh");
    setTimeout(() => {
      if (page === "trends") renderTrends();
      if (page === "report") renderReportPage();
      if (page === "diagnostics") renderDiagnosticsList();
    }, 40);
  }

  function acknowledgeReminder(id) {
    const reminder = state.reminders.find((item) => String(item.id) === String(id));
    if (!reminder) return;
    reminder.acknowledged = true;
    addEvent("提醒已读", reminder.text || String(reminder.type || "reminder"), "ok");
    renderReminders("reminderList", state.reminders, 5, true);
  }

  function tickClock() {
    const clock = formatClock();
    setText("sideClock", clock);
    setText("heroLiveTime", clock);
    setText("heartbeatText", clock);
    setText("timelineNow", `NOW ${clock.slice(0, 5)}`);
    renderLiveChannel();
    const device = selectedDevice();
    if (device) setText("syncTag", `同步 ${relativeTime(device.last_seen || state.status?.now)}`);
  }

  function openSettings() {
    el("settingsModal")?.classList.remove("hidden");
    if (el("modeSelect")) el("modeSelect").value = "live";
    if (el("baseUrlInput")) el("baseUrlInput").value = API_BASE;
    if (el("refreshIntervalSelect")) el("refreshIntervalSelect").value = "2";
    setText("modalTestResult", `固定接口：${API_BASE}`);
  }

  async function testConnection() {
    const result = el("modalTestResult");
    if (result) {
      result.className = "modal-test";
      result.textContent = "正在请求固定地址 /api/v1/status …";
    }
    try {
      await fetchJson(buildUrl("status"), "status:test", 4000);
      if (result) {
        result.className = "modal-test ok";
        result.textContent = `连接成功：${joinUrl(API_PATHS.status)}`;
      }
    } catch (error) {
      if (result) {
        result.className = "modal-test fail";
        result.textContent = `连接失败：${error.message}。请确认浏览器能够访问当前后端地址。`;
      }
    }
  }

  function bindEvents() {
    qsa(".nav-item").forEach((item) => item.addEventListener("click", () => switchPage(item.dataset.page)));
    el("refreshBtn")?.addEventListener("click", async () => {
      await refreshStatus();
      await refreshSecondary();
    });
    el("dateInput")?.addEventListener("change", () => refreshSecondary());
    el("deviceSelect")?.addEventListener("change", () => {
      state.selectedDeviceId = el("deviceSelect").value;
      refreshSecondary();
      renderAll();
    });
    el("dismissBanner")?.addEventListener("click", () => el("fallbackBanner")?.classList.add("hidden"));

    el("settingsBtn")?.addEventListener("click", openSettings);
    el("closeSettingsBtn")?.addEventListener("click", () => el("settingsModal")?.classList.add("hidden"));
    el("settingsModal")?.addEventListener("click", (event) => {
      if (event.target === el("settingsModal")) el("settingsModal")?.classList.add("hidden");
    });
    el("testConnectionBtn")?.addEventListener("click", testConnection);
    el("saveSettingsBtn")?.addEventListener("click", () => {
      el("settingsModal")?.classList.add("hidden");
      refreshStatus();
      refreshSecondary();
    });

    qsa("#metricTabs button").forEach((button) => button.addEventListener("click", () => {
      state.activeMetric = button.dataset.metric;
      qsa("#metricTabs button").forEach((item) => item.classList.toggle("active", item === button));
      renderTrends();
    }));
    qsa(".window-control button").forEach((button) => button.addEventListener("click", () => {
      state.chartWindow = Number(button.dataset.window);
      qsa(".window-control button").forEach((item) => item.classList.toggle("active", item === button));
      renderTrends();
    }));

    const refreshReport = async () => {
      setReportLoading(true);
      try {
        const payload = await fetchJson(buildUrl("report"), "report:manual");
        applyReport(payload);
        toast("日报已刷新", "仅展示后端真实模型结果。", "ok");
      } catch (error) {
        toast("日报刷新失败", error.message, "error");
      } finally {
        setReportLoading(false);
        renderReportPage();
      }
    };
    el("regenerateReportBtn")?.addEventListener("click", refreshReport);
    el("reportGenerateBtn")?.addEventListener("click", refreshReport);
    el("reminderList")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-ack-reminder]");
      if (!button) return;
      acknowledgeReminder(button.closest("[data-reminder-id]")?.dataset.reminderId);
    });

    el("presentationBtn")?.addEventListener("click", async () => {
      document.body.classList.toggle("presentation");
      const active = document.body.classList.contains("presentation");
      el("presentationBtn").textContent = active ? "退出演示" : "演示模式";
      if (active && document.documentElement.requestFullscreen) {
        try { await document.documentElement.requestFullscreen(); } catch (_) {}
      } else if (!active && document.fullscreenElement) {
        try { await document.exitFullscreen(); } catch (_) {}
      }
      setTimeout(renderAll, 120);
    });
    document.addEventListener("fullscreenchange", () => {
      if (!document.fullscreenElement && document.body.classList.contains("presentation")) {
        document.body.classList.remove("presentation");
        if (el("presentationBtn")) el("presentationBtn").textContent = "演示模式";
      }
    });

    el("runDiagnosticsBtn")?.addEventListener("click", runDiagnostics);
    el("clearLogBtn")?.addEventListener("click", () => {
      state.requestLog = [];
      renderRequestLog();
    });
    window.addEventListener("resize", debounce(renderAll, 180));
  }

  function debounce(fn, wait) {
    let timer;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), wait);
    };
  }

  function startTimers() {
    state.timers.push(setInterval(tickClock, 1000));
    state.timers.push(setInterval(() => refreshStatus({ quiet: true }), STATUS_POLL_MS));
    state.timers.push(setInterval(() => refreshSecondary({ quiet: true }), SECONDARY_POLL_MS));
    state.timers.push(setInterval(() => {
      if (state.currentPage === "diagnostics") runDiagnostics();
    }, DIAGNOSTIC_POLL_MS));
  }

  async function init() {
    if (el("dateInput")) el("dateInput").value = todayString();
    bindEvents();
    setText("fixedApiUrl", API_BASE);
    setText("pageTitle", "实时总览");
    renderAll();
    tickClock();
    startTimers();
    addEvent("D 端看板启动", `固定接口 ${API_BASE}`, "ok", false);
    addTrace("等待后端真实日报");
    await refreshStatus();
    await refreshSecondary({ quiet: true });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
