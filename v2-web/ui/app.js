'use strict';

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const token = document.querySelector('meta[name="winctl-token"]').content;
const state = { monitors: [], monitorIndex: 0, dark: true, accent: '', taskbar: 1, topmost: false, backup: false, wallpaper: '', rotation: false, connected: false };
let brightnessTimer = 0;
let brightnessSequence = 0;
let pendingBrightness = null;
let brightnessBusy = false;
let toastTimer = 0;
let statusTimer = 0;
let previewUrl = '';
let confirmAction = null;
let reconnectTimer = 0;

async function api(path, options = {}) {
  const config = { method: options.method || 'GET', headers: { 'X-WinCtl-Token': token, ...(options.headers || {}) }, cache: 'no-store' };
  if (options.body !== undefined) {
    config.headers['Content-Type'] = 'application/json';
    config.body = JSON.stringify(options.body);
  }
  const response = await fetch(`/api/${path}`, config);
  let data = {};
  try { data = await response.json(); } catch { data = { message: '本地服务返回了无法识别的内容。' }; }
  if (!response.ok || data.ok === false) throw new Error(data.message || `操作未完成（${response.status}）`);
  state.connected = true;
  updateConnection();
  return data;
}

function updateConnection() {
  $('#local-pill').classList.toggle('offline', !state.connected);
  $('#local-pill').innerHTML = `<i></i>${state.connected ? '本地就绪' : '连接中断'}`;
}

function setStatus(message, type = 'normal', sticky = false) {
  clearTimeout(statusTimer);
  $('#status-text').textContent = message;
  $('#statusbar').classList.toggle('error', type === 'error');
  $('#statusbar').classList.toggle('busy', type === 'busy');
  if (!sticky && type !== 'busy') statusTimer = setTimeout(() => setStatus('就绪 · 所有改动均已备份', 'normal', true), 4200);
}

function toast(message, type = 'success') {
  clearTimeout(toastTimer);
  $('#toast span').textContent = message;
  $('#toast').classList.toggle('error', type === 'error');
  $('#toast').classList.add('show');
  toastTimer = setTimeout(() => $('#toast').classList.remove('show'), 2800);
}

function friendlyError(error) {
  state.connected = navigator.onLine ? state.connected : false;
  updateConnection();
  setStatus(error.message || '操作失败，请稍后重试。', 'error', true);
  toast(error.message || '操作失败，请稍后重试。', 'error');
}

function setBusy(busy, label = '正在应用…') {
  $('#statusbar').classList.toggle('busy', busy);
  if (busy) setStatus(label, 'busy', true);
}

function setThemeUI(dark) {
  state.dark = !!dark;
  document.body.dataset.theme = state.dark ? 'dark' : 'light';
  $$('#theme-switch button').forEach(button => {
    const active = (button.dataset.themeValue === 'dark') === state.dark;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
}

function setAccentUI(color) {
  if (!color) return;
  state.accent = color.toUpperCase();
  $('#accent-value').textContent = state.accent;
  $('#custom-accent').value = state.accent;
  $$('.swatch').forEach(button => {
    const active = button.dataset.color.toUpperCase() === state.accent;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
}

function setTaskbarUI(value) {
  state.taskbar = Number(value);
  $$('#taskbar-switch button').forEach(button => {
    const active = Number(button.dataset.align) === state.taskbar;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
}

function setTopmostUI(value) {
  state.topmost = !!value;
  $('#pin-button').setAttribute('aria-pressed', String(state.topmost));
  $('#pin-button').title = state.topmost ? '取消窗口置顶' : '窗口置顶';
}

function selectMonitor(index) {
  if (!state.monitors.length) return;
  state.monitorIndex = Math.max(0, Math.min(index, state.monitors.length - 1));
  const monitor = state.monitors[state.monitorIndex];
  const slider = $('#brightness-slider');
  slider.disabled = !monitor.supported;
  slider.value = monitor.supported ? monitor.percent : 0;
  slider.style.setProperty('--range-progress', `${slider.value}%`);
  $('#brightness-value').innerHTML = monitor.supported ? `${monitor.percent}<small>%</small>` : '<span style="font-size:12px;letter-spacing:0">不支持</span>';
  $('#monitor-name').textContent = monitor.supported ? monitor.name : '此显示器未开放 DDC/CI';
  $('#monitor-name').classList.toggle('error', !monitor.supported);
  $$('.monitor-tab').forEach((button, i) => {
    button.classList.toggle('active', i === state.monitorIndex);
    button.setAttribute('aria-selected', String(i === state.monitorIndex));
  });
}

function renderMonitors(monitors) {
  state.monitors = monitors || [];
  const tabs = $('#monitor-tabs');
  tabs.replaceChildren();
  if (!state.monitors.length) {
    $('#brightness-slider').disabled = true;
    $('#brightness-value').innerHTML = '—';
    $('#monitor-name').textContent = '未找到可控制的显示器';
    $('#monitor-name').classList.add('error');
    return;
  }
  state.monitors.forEach((monitor, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'monitor-tab';
    button.setAttribute('role', 'tab');
    button.textContent = `屏幕 ${index + 1}`;
    button.addEventListener('click', () => selectMonitor(index));
    tabs.append(button);
  });
  selectMonitor(Math.min(state.monitorIndex, state.monitors.length - 1));
}

function updateBackup(hasBackup) {
  state.backup = !!hasBackup;
  $('#restore-button').classList.toggle('has-backup', state.backup);
  $('#restore-hint').textContent = state.backup ? '有改动可还原' : '回到初次改动前';
}

function fileName(path) {
  if (!path) return '未设置壁纸';
  const parts = String(path).split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

async function updateWallpaperPreview(path) {
  const previous = previewUrl;
  previewUrl = '';
  $('#wallpaper-image').hidden = true;
  if (previous) URL.revokeObjectURL(previous);
  if (!path) return;
  try {
    const response = await fetch('/api/wallpaper-preview', { headers: { 'X-WinCtl-Token': token }, cache: 'no-store' });
    if (!response.ok) return;
    const blob = await response.blob();
    if (state.wallpaper !== path) return;
    previewUrl = URL.createObjectURL(blob);
    $('#wallpaper-image').src = previewUrl;
    $('#wallpaper-image').hidden = false;
  } catch { /* 文件缩略图不是换壁纸的必要条件。 */ }
}

function renderState(next) {
  if (!next) return;
  if (Array.isArray(next.monitors)) renderMonitors(next.monitors);
  if (next.dark !== undefined) setThemeUI(next.dark);
  if (next.accent) setAccentUI(next.accent);
  if (next.taskbar !== undefined) setTaskbarUI(next.taskbar);
  if (next.topmost !== undefined) setTopmostUI(next.topmost);
  if (next.backup !== undefined) updateBackup(next.backup);
  if (next.wallpaper !== undefined) {
    const changed = state.wallpaper !== next.wallpaper;
    state.wallpaper = next.wallpaper;
    $('#wallpaper-name').textContent = fileName(next.wallpaper);
    $('#wallpaper-name').title = next.wallpaper;
    if (changed) updateWallpaperPreview(next.wallpaper);
  }
  if (next.rotation !== undefined) {
    state.rotation = !!next.rotation;
    $('#rotate-value').textContent = state.rotation ? '开启' : '关闭';
    $('#rotate-value').classList.toggle('on', state.rotation);
    $('#rotate-detail').textContent = state.rotation ? `每 10 分钟 · ${next.rotateFolder || '已选文件夹'}` : '选择文件夹，每 10 分钟随机更换';
  }
  if (next.hotkeys) {
    const ok = next.hotkeys.brightnessUp && next.hotkeys.brightnessDown && next.hotkeys.theme;
    $('#hotkey-status').textContent = ok ? '热键已注册，可在其他窗口直接使用。' : '部分热键被其他程序占用，面板内功能不受影响。';
  }
}

async function refreshState(showBusy = false) {
  if (showBusy) setBusy(true, '正在读取系统状态…');
  try {
    const next = await api('state');
    renderState(next);
    setStatus('就绪 · 所有改动均已备份', 'normal', true);
  } catch (error) {
    state.connected = false;
    updateConnection();
    setStatus('本地服务未连接，正在重试…', 'error', true);
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => refreshState(false), 2500);
  }
}

async function runAction(label, path, body, successMessage) {
  setBusy(true, label);
  try {
    const next = await api(path, { method: 'POST', body });
    renderState(next.state || next);
    setStatus(successMessage, 'normal');
    toast(successMessage);
    return next;
  } catch (error) {
    friendlyError(error);
    throw error;
  }
}

function updateSliderVisual(value) {
  const slider = $('#brightness-slider');
  slider.value = value;
  slider.style.setProperty('--range-progress', `${value}%`);
  $('#brightness-value').innerHTML = `${value}<small>%</small>`;
}

async function sendBrightness() {
  clearTimeout(brightnessTimer);
  if (brightnessBusy || pendingBrightness === null) return;
  const request = pendingBrightness;
  pendingBrightness = null;
  brightnessBusy = true;
  const sequence = ++brightnessSequence;
  setBusy(true, `正在将屏幕 ${request.index + 1} 调至 ${request.percent}%…`);
  try {
    const next = await api('brightness', { method: 'POST', body: request });
    if (next.state) {
      const selected = state.monitorIndex;
      renderState(next.state);
      if (sequence === brightnessSequence && pendingBrightness === null) selectMonitor(selected);
    }
    updateBackup(next.backup !== undefined ? next.backup : state.backup);
    setStatus(`屏幕 ${request.index + 1} 亮度已调至 ${request.percent}%`, 'normal');
  } catch (error) {
    friendlyError(error);
    refreshState(false);
  } finally {
    brightnessBusy = false;
    if (pendingBrightness !== null) sendBrightness();
  }
}

$('#brightness-slider').addEventListener('input', event => {
  const percent = Number(event.target.value);
  updateSliderVisual(percent);
  event.target.classList.add('adjusting');
  pendingBrightness = { index: state.monitorIndex, percent };
  clearTimeout(brightnessTimer);
  brightnessTimer = setTimeout(sendBrightness, 140);
});
$('#brightness-slider').addEventListener('change', () => {
  clearTimeout(brightnessTimer);
  setTimeout(() => $('#brightness-slider').classList.remove('adjusting'), 600);
  if (!brightnessBusy) sendBrightness();
});

$$('#theme-switch button').forEach(button => button.addEventListener('click', async () => {
  const dark = button.dataset.themeValue === 'dark';
  if (dark === state.dark) return;
  setThemeUI(dark);
  try { await runAction('正在切换系统颜色模式…', 'theme', { dark }, dark ? '已切换深色模式' : '已切换浅色模式'); }
  catch { setThemeUI(!dark); }
}));

async function chooseAccent(color) {
  const previous = state.accent;
  setAccentUI(color);
  try { await runAction('正在应用强调色…', 'accent', { color }, `强调色已设为 ${color.toUpperCase()}`); }
  catch { setAccentUI(previous); }
}
$$('.swatch').forEach(button => button.addEventListener('click', () => chooseAccent(button.dataset.color)));
let accentDebounce = 0;
$('#custom-accent').addEventListener('input', event => {
  clearTimeout(accentDebounce);
  const color = event.target.value;
  setAccentUI(color);
  accentDebounce = setTimeout(() => chooseAccent(color), 260);
});

$$('#taskbar-switch button').forEach(button => button.addEventListener('click', async () => {
  const value = Number(button.dataset.align);
  if (value === state.taskbar) return;
  const previous = state.taskbar;
  setTaskbarUI(value);
  try {
    await runAction('正在设置任务栏…', 'taskbar', { value }, value ? '任务栏已设为居中' : '任务栏已设为左对齐');
    setStatus(value ? '已设为居中；若未刷新，请在更多工具中重启资源管理器' : '已设为左对齐；若未刷新，请在更多工具中重启资源管理器', 'normal', true);
  } catch { setTaskbarUI(previous); }
}));

$('#wallpaper-button').addEventListener('click', async () => {
  setBusy(true, '等待选择壁纸…');
  try {
    const next = await api('wallpaper/pick', { method: 'POST', body: {} });
    if (next.cancelled) { setStatus('已取消选择，壁纸未改动', 'normal'); return; }
    renderState(next.state || next);
    setStatus('壁纸已更换', 'normal');
    toast('桌面壁纸已更换');
  } catch (error) { friendlyError(error); }
});

function confirm({ title, message, okText = '继续', danger = false, action }) {
  confirmAction = action;
  $('#confirm-title').textContent = title;
  $('#confirm-message').textContent = message;
  $('#confirm-ok').textContent = okText;
  $('#confirm-ok').classList.toggle('danger', danger);
  $('#confirm-dialog').showModal();
  document.body.classList.add('modal-open');
}
$('#confirm-cancel').addEventListener('click', () => $('#confirm-dialog').close());
$('#confirm-ok').addEventListener('click', async () => {
  const action = confirmAction;
  confirmAction = null;
  $('#confirm-dialog').close();
  if (action) await action();
});
$('#confirm-dialog').addEventListener('close', () => { document.body.classList.remove('modal-open'); confirmAction = null; });

$('#restore-button').addEventListener('click', () => {
  if (!state.backup) { toast('没有需要还原的改动'); setStatus('备份为空，系统保持当前状态', 'normal'); return; }
  confirm({
    title: '还原全部改动？', message: '亮度、颜色模式、强调色、任务栏和壁纸将恢复到第一次改动前。此操作会清空现有备份。', okText: '全部还原',
    action: async () => {
      try { await runAction('正在还原全部设置…', 'restore', {}, '六项设置已全部还原'); await refreshState(); }
      catch { /* 错误已在统一入口提示。 */ }
    }
  });
});

$('#pin-button').addEventListener('click', async () => {
  const value = !state.topmost;
  setTopmostUI(value);
  try { const next = await api('window/topmost', { method: 'POST', body: { value } }); setTopmostUI(next.topmost); setStatus(value ? '窗口已置顶' : '已取消置顶', 'normal'); }
  catch (error) { setTopmostUI(!value); friendlyError(error); }
});
$('#minimize-button').addEventListener('click', async () => {
  try { await api('window/hide', { method: 'POST', body: {} }); }
  catch (error) { friendlyError(error); }
});
$('#close-button').addEventListener('click', () => confirm({
  title: '退出桌面控制台？', message: '退出不会自动还原系统改动。之后可双击启动器重新打开。', okText: '退出', danger: true,
  action: async () => { try { await api('exit', { method: 'POST', body: {} }); } catch { /* 服务退出后连接中断属于正常。 */ } }
}));

let dragRequested = false;
$('#titlebar').addEventListener('pointerdown', event => {
  if (event.button !== 0 || event.target.closest('button') || dragRequested) return;
  dragRequested = true;
  api('window/drag', { method: 'POST', body: {} }).catch(() => {}).finally(() => { dragRequested = false; });
});
let resizeRequested = false;
$('#resize-grip').addEventListener('pointerdown', event => {
  if (event.button !== 0 || resizeRequested) return;
  event.preventDefault();
  resizeRequested = true;
  api('window/resize', { method: 'POST', body: {} }).catch(() => {}).finally(() => { resizeRequested = false; });
});

$('#advanced-button').addEventListener('click', () => { $('#advanced-dialog').showModal(); document.body.classList.add('modal-open'); });
$$('[data-close-dialog]').forEach(button => button.addEventListener('click', () => $(`#${button.dataset.closeDialog}`).close()));
$('#advanced-dialog').addEventListener('close', () => document.body.classList.remove('modal-open'));
$$('dialog').forEach(dialog => dialog.addEventListener('click', event => {
  if (event.target !== dialog) return;
  const rect = dialog.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
}));

$('#wallpaper-restore-button').addEventListener('click', async () => {
  $('#advanced-dialog').close();
  try { await runAction('正在还原壁纸…', 'wallpaper/restore', {}, '壁纸已还原'); }
  catch { /* 已提示。 */ }
});
$('#rotate-button').addEventListener('click', async () => {
  try {
    const next = await runAction(state.rotation ? '正在关闭壁纸轮换…' : '等待选择轮换文件夹…', 'wallpaper/rotate', {}, state.rotation ? '壁纸轮换已关闭' : '壁纸轮换已开启');
    if (next.cancelled) setStatus('已取消选择，轮换未开启', 'normal');
  } catch { /* 已提示。 */ }
});
$('#theme-file-button').addEventListener('click', async () => {
  $('#advanced-dialog').close();
  setBusy(true, '等待选择主题文件…');
  try {
    const next = await api('advanced/theme', { method: 'POST', body: {} });
    setStatus(next.cancelled ? '已取消选择主题' : '主题引擎已打开，请在系统窗口中确认', 'normal', true);
    if (!next.cancelled) toast('已调用 Windows 主题引擎');
  } catch (error) { friendlyError(error); }
});
$('#lockscreen-button').addEventListener('click', () => {
  $('#advanced-dialog').close();
  confirm({
    title: '需要管理员权限', message: '锁屏图片会写入 HKLM 组策略键。继续后 Windows 会弹出 UAC，请手动确认；取消则不会写入。', okText: '选择图片',
    action: async () => {
      setBusy(true, '等待选择锁屏图片…');
      try {
        const next = await api('advanced/lockscreen', { method: 'POST', body: {} });
        setStatus(next.cancelled ? '已取消，未修改锁屏设置' : '锁屏图片已设置，按 Win + L 查看', next.cancelled ? 'normal' : 'normal', true);
        if (!next.cancelled) toast('锁屏图片已设置');
      } catch (error) { friendlyError(error); }
    }
  });
});
$('#explorer-button').addEventListener('click', () => {
  $('#advanced-dialog').close();
  confirm({
    title: '重启资源管理器？', message: '任务栏会短暂消失，已打开的资源管理器窗口也会关闭。未保存的其他程序不受影响。', okText: '重启', danger: true,
    action: async () => { try { await runAction('正在重启资源管理器…', 'advanced/restart-explorer', {}, '资源管理器已重启'); } catch { /* 已提示。 */ } }
  });
});

window.addEventListener('offline', () => { setStatus('网络已断开 · 本地功能不受影响', 'normal', true); });
window.addEventListener('online', () => setStatus('网络已恢复 · 面板始终本地运行', 'normal', true));
document.addEventListener('visibilitychange', () => { if (!document.hidden && state.connected) refreshState(false); });
refreshState(true);