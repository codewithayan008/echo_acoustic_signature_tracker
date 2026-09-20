const form = document.querySelector('#session-form');
const audio = document.querySelector('#audio');
const fileLabel = document.querySelector('#file-label');
const message = document.querySelector('#message');
const reportButton = document.querySelector('#report-summary');
const protocolSteps = [...document.querySelectorAll('.protocol-step')];
let activeStep = 0;
let latestReport = null;
let patientName = '';

const authScreen = document.querySelector('#auth-screen');
const authMessage = document.querySelector('#auth-message');
const registerForm = document.querySelector('#register-form');
const loginForm = document.querySelector('#login-form');
let captchaQuestion = '';
let otpRequestInFlight = false;

function authNotice(text, error = false) {
  authMessage.textContent = text;
  authMessage.style.color = error ? '#c66b43' : '';
}

async function loadCaptcha() {
  const data = await jsonFetch('/api/auth/captcha');
  captchaQuestion = data.question;
  document.querySelector('#captcha-question').textContent = captchaQuestion;
  document.querySelector('#captcha-id').value = data.challenge_id;
}

function showAccount(email, displayName = '') {
  patientName = displayName || email.split('@')[0];
  authScreen.classList.add('hidden');
  document.body.classList.add('authenticated');
  document.querySelector('#landing-page').classList.add('hidden');
  document.querySelector('#guide-widget').classList.remove('hidden');
  document.querySelector('#account-email').textContent = email;
  guideMessages.querySelector('.guide-bubble-agent').textContent = `Hi ${patientName}, what can I do for you? I can explain an ECHO result, but I cannot diagnose disease.`;
}

async function jsonFetch(url, options) {
  const response = await fetch(url, options);
  const type = response.headers.get('content-type') || '';
  const data = type.includes('application/json') ? await response.json() : {error: `Server returned ${response.status} instead of JSON.`};
  if (!response.ok) throw new Error(data.error || 'Request failed.');
  return data;
}

async function aiFetch(url, options) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 22000);
  try {
    return await jsonFetch(url, {...options, signal: controller.signal});
  } finally {
    clearTimeout(timer);
  }
}

function openAuth(mode) {
  authScreen.classList.remove('hidden');
  document.querySelector('#landing-page').classList.add('hidden');
  const tab = document.querySelector(`[data-auth-mode="${mode}"]`);
  tab.click();
}

document.querySelectorAll('[data-open-auth]').forEach(button => {
  button.addEventListener('click', () => openAuth(button.dataset.openAuth));
});

document.querySelectorAll('[data-auth-mode]').forEach(tab => tab.addEventListener('click', () => {
  const register = tab.dataset.authMode === 'register';
  document.querySelectorAll('.auth-tab').forEach(item => item.classList.toggle('active', item === tab));
  loginForm.classList.toggle('hidden', register);
  registerForm.classList.toggle('hidden', !register);
  authNotice('');
  if (register) loadCaptcha();
}));

loginForm.addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const data = await jsonFetch('/api/auth/login', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(Object.fromEntries(new FormData(loginForm))) });
    showAccount(data.email, data.display_name);
    refreshOverview();
  } catch (error) { return authNotice(error.message, true); }
});

document.querySelector('#send-otp').addEventListener('click', async () => {
  if (otpRequestInFlight) return;
  otpRequestInFlight = true;
  const otpButton = document.querySelector('#send-otp');
  otpButton.disabled = true;
  otpButton.textContent = 'Sending...';
  const values = Object.fromEntries(new FormData(registerForm));
  let data;
  try {
    data = await jsonFetch('/api/auth/request-otp', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(values) });
    authNotice(data.development_otp ? `Development code: ${data.development_otp}` : data.message);
    document.querySelector('#otp-field').classList.remove('hidden');
    document.querySelector('#verify-otp').classList.remove('hidden');
  } catch (error) { authNotice(error.message, true); loadCaptcha(); }
  finally { otpRequestInFlight = false; otpButton.disabled = false; otpButton.innerHTML = 'Send email code <span>↗</span>'; }
});

document.querySelector('#verify-otp').addEventListener('click', async () => {
  const values = Object.fromEntries(new FormData(registerForm));
  let data;
  try { data = await jsonFetch('/api/auth/verify-otp', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(values) }); } catch (error) { return authNotice(error.message, true); }
  authNotice(data.message);
  document.querySelector('#password-field').classList.remove('hidden');
  document.querySelector('#create-account').classList.remove('hidden');
});

registerForm.addEventListener('submit', async event => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(registerForm));
  let data;
  try { data = await jsonFetch('/api/auth/set-password', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(values) }); } catch (error) { return authNotice(error.message, true); }
  showAccount(data.email, data.display_name);
  refreshOverview();
});

document.querySelector('#google-login').addEventListener('click', async () => {
  try { const data = await jsonFetch('/api/auth/google'); window.location.href = data.url; } catch (error) { authNotice(error.message, true); }
});

document.querySelector('#logout').addEventListener('click', async () => {
  await fetch('/api/auth/logout', { method: 'POST' });
  window.location.reload();
});

const guideWidget = document.querySelector('#guide-widget');
const guideMessages = document.querySelector('#guide-messages');
const guideForm = document.querySelector('#guide-form');
const guideInput = document.querySelector('#guide-input');
document.querySelector('#guide-open').addEventListener('click', () => guideWidget.classList.add('open'));
document.querySelector('#guide-close').addEventListener('click', () => guideWidget.classList.remove('open'));

function addGuideMessage(text, from) {
  const bubble = document.createElement('div');
  bubble.className = `guide-bubble guide-bubble-${from}`;
  bubble.textContent = text;
  guideMessages.appendChild(bubble);
  guideMessages.scrollTop = guideMessages.scrollHeight;
}

guideForm.addEventListener('submit', async event => {
  event.preventDefault();
  const message = guideInput.value.trim();
  if (!message) return;
  addGuideMessage(message, 'user');
  guideInput.value = '';
  addGuideMessage('Thinking locally...', 'agent');
  const pending = guideMessages.lastElementChild;
  try {
    const context = { patient_name: patientName, score: document.querySelector('#score').textContent, status: document.querySelector('#score-label').textContent };
    const data = await aiFetch('/api/chat', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message, context}) });
    pending.textContent = data.reply;
  } catch (error) {
    pending.textContent = error.message;
  }
});

reportButton.addEventListener('click', async () => {
  if (!latestReport) return;
  reportButton.disabled = true;
  reportButton.innerHTML = 'Summarizing...';
  try {
    const quick = await jsonFetch('/api/report-summary?fast=1', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(latestReport) });
    addGuideMessage(quick.summary, 'agent');
    guideWidget.classList.add('open');
    const data = await aiFetch('/api/report-summary', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(latestReport) });
    const lastMessage = guideMessages.lastElementChild;
    if (data.provider === 'ollama') lastMessage.textContent = data.summary;
  } catch (error) {
    document.querySelector('#ai-result').textContent = error.message;
  } finally {
    reportButton.disabled = false;
    reportButton.innerHTML = 'Summarize this report <span>✦</span>';
  }
});

function selectStep(stepIndex) {
  activeStep = Math.max(0, Math.min(stepIndex, protocolSteps.length - 1));
  protocolSteps.forEach((step, index) => {
    step.classList.toggle('active', index === activeStep);
    step.setAttribute('aria-current', index === activeStep ? 'step' : 'false');
  });
  const selectedStep = protocolSteps[activeStep];
  selectedStep.classList.remove('stage-bump');
  requestAnimationFrame(() => selectedStep.classList.add('stage-bump'));
  fileLabel.textContent = `Upload ${protocolSteps[activeStep].querySelector('strong').textContent.toLowerCase()} recording`;
  document.querySelector('#stage').value = ['normal_breathing', 'deep_breathing', 'phonation', 'cough'][activeStep];
}

protocolSteps.forEach((step, index) => {
  step.addEventListener('click', () => selectStep(index));
});

const setMessage = (text, error = false) => {
  message.textContent = text;
  message.style.color = error ? '#c66b43' : '';
};

audio.addEventListener('change', () => {
  fileLabel.textContent = audio.files[0]?.name || 'Drop a WAV recording here or browse';
});

async function refreshOverview() {
  const data = await jsonFetch('/api/overview');
  document.querySelector('#baseline-count').textContent = `${data.baseline_sessions} baseline sample${data.baseline_sessions === 1 ? '' : 's'}`;
  const chart = document.querySelector('#chart');
  if (!data.sessions.length) return;
  chart.innerHTML = '';
  data.sessions.forEach((session, index) => {
    const bar = document.createElement('div');
    const score = session.score ?? 0;
    bar.className = `bar ${index === data.sessions.length - 1 ? 'latest' : ''} ${score >= 35 ? 'high' : ''}`;
    bar.style.height = `${Math.max(8, score || 8)}%`;
    bar.title = `${session.kind}: ${score ? score.toFixed(1) : 'baseline'}`;
    const label = document.createElement('span');
    label.textContent = score ? score.toFixed(0) : 'base';
    bar.appendChild(label);
    chart.appendChild(bar);
  });
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!audio.files[0]) return setMessage('Choose a recording first.', true);
  const button = document.querySelector('#submit');
  button.disabled = true;
  button.textContent = 'Analyzing...';
  setMessage('Processing locally...');
  try {
    const data = await jsonFetch('/api/sessions', { method: 'POST', body: new FormData(form) });
    if (data.status === 'baseline_required') {
      document.querySelector('#score').textContent = '--';
      document.querySelector('#score-label').textContent = 'Baseline required';
      document.querySelector('#score-note').textContent = 'Save at least one baseline sample before running an assessment.';
    } else if (data.score === null) {
      document.querySelector('#score').textContent = '--';
      document.querySelector('#score-label').textContent = 'Baseline sample saved';
      document.querySelector('#score-note').textContent = 'Add another baseline or run an assessment to compare against this profile.';
    } else {
      document.querySelector('#score').textContent = data.score.toFixed(1);
      document.querySelector('#score-label').textContent = data.status === 'review' ? 'Review change' : 'Within personal baseline';
      document.querySelector('#score-note').textContent = data.status === 'review' ? 'This session moved meaningfully from the personal reference. Review the signal shifts below.' : 'No meaningful multi-signal deviation from the personal reference was detected.';
      document.querySelector('#meter-fill').style.width = `${data.score}%`;
      document.querySelector('#meter-fill').style.background = data.score >= 35 ? '#ef9d55' : '#c9ead6';
      const contributions = document.querySelector('#contributions');
      contributions.innerHTML = data.contributions.length ? data.contributions.map(item => `<div class="shift"><span>${item.feature.replaceAll('_', ' ')}</span><strong>${item.shift}σ</strong></div>`).join('') : '<div class="explain-empty">No dominant shifts detected.</div>';
    }
    const modelResult = data.model_result ? ` Dataset model signal: ${data.model_result.label}${data.model_result.confidence === null ? '' : ` (${Math.round(data.model_result.confidence * 100)}% confidence)`}. This is not a diagnosis.` : '';
    document.querySelector('#ai-result').textContent = `${data.simple_result}${modelResult}`;
    latestReport = {score: data.score, status: data.status, contributions: data.contributions, model_result: data.model_result};
    reportButton.disabled = false;
    setMessage(data.status === 'baseline_required' ? 'Choose a baseline sample first.' : 'Assessment complete.');
    if (data.status !== 'baseline_required' && activeStep < protocolSteps.length - 1) {
      selectStep(activeStep + 1);
    }
    await refreshOverview();
  } catch (error) {
    setMessage(error.message, true);
    if (error.message.includes('Server returned 500')) {
      setMessage('The server could not analyze this audio. Restart ECHO and try the WAV again.', true);
    }
  } finally {
    button.disabled = false;
    button.innerHTML = 'Analyze recording <span>↗</span>';
  }
});

jsonFetch('/api/auth/me').then(data => {
  if (data.authenticated) showAccount(data.email, data.display_name);
  else loadCaptcha();
}).catch(() => loadCaptcha());
