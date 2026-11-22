const API_URL = 'http://127.0.0.1:8000';

async function register() {
  const email = document.getElementById('email').value;
  const password = document.getElementById('password').value;
  const msg = document.getElementById('msg');

  try {
    const res = await fetch(`${API_URL}/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Erreur');
    }
    window.location.href = 'index.html';
  } catch (err) {
    msg.textContent = err.message;
  }
}
async function generatePost() {
  const title = document.getElementById('post-title').value;
  const context = document.getElementById('post-context').value;
  const msg = document.getElementById('gen-msg');
  const token = localStorage.getItem('token');

  msg.textContent = 'Génération en cours...';
  try {
    const res = await fetch(`${API_URL}/content/generate`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify({ title, context, source: 'manual' })
    });
    if (!res.ok) throw new Error('Erreur de génération');
    msg.textContent = '✅ Post généré !';
    loadQueue();
  } catch (err) {
    msg.textContent = '❌ ' + err.message;
  }
}

async function loadQueue() {
  const token = localStorage.getItem('token');
  const res = await fetch(`${API_URL}/content/queue`, {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  const items = await res.json();
  const list = document.getElementById('queue-list');
  list.innerHTML = items.map(item => `
    <div style="border:1px solid #ddd; padding:10px; margin-bottom:10px; border-radius:4px;">
      <strong>${item.title}</strong> <em>(${item.status})</em>
      <p style="white-space:pre-wrap;">${item.post_text}</p>
    </div>
  `).join('');
}



async function login() {
  const email = document.getElementById('email').value;
  const password = document.getElementById('password').value;
  const msg = document.getElementById('msg');

  try {
    const res = await fetch(`${API_URL}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    });
    if (!res.ok) throw new Error('Identifiants invalides');
    
    const data = await res.json();
    localStorage.setItem('token', data.access_token);
    
    // Décode le JWT pour récupérer l'user_id
    const payload = JSON.parse(atob(data.access_token.split('.')[1]));
    localStorage.setItem('user_id', payload.sub);
    
    window.location.href = 'dashboard.html';
  } catch (err) {
    msg.textContent = err.message;
  }
}

async function connectLinkedIn() {
  await redirectToOAuth('linkedin');
}

// Asks the backend for the consent URL (authenticated), then sends the browser there.
async function redirectToOAuth(provider) {
  const token = localStorage.getItem('token');
  const res = await fetch(`${API_URL}/oauth/${provider}/authorize-url`, {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  if (!res.ok) {
    alert('Session expirée : reconnecte-toi puis réessaie.');
    return;
  }
  const data = await res.json();
  window.location.href = data.url;
}

function logout() {
  localStorage.clear();
  window.location.href = 'index.html';
}

async function loadQueue() {
  const token = localStorage.getItem('token');
  const res = await fetch(`${API_URL}/content/queue`, {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  const items = await res.json();
  const list = document.getElementById('queue-list');
  list.innerHTML = items.map(item => `
    <div style="border:1px solid #ddd; padding:10px; margin-bottom:10px; border-radius:4px;">
      <strong>${item.title}</strong> <em>(${item.status})</em>
      <p style="white-space:pre-wrap;">${item.post_text}</p>
      ${item.status === 'draft' ? `<button onclick="publishPost(${item.id})">Publier sur LinkedIn</button>` : ''}
    </div>
  `).join('');
}

async function publishPost(id) {
  const token = localStorage.getItem('token');
  try {
    const res = await fetch(`${API_URL}/content/${id}/publish`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail);
    }
    alert('✅ Publié sur LinkedIn !');
    loadQueue();
  } catch (err) {
    alert('❌ ' + err.message);
  }
}


async function connectGitHub() {
  await redirectToOAuth('github');
}

async function loadRepos() {
  const token = localStorage.getItem('token');
  try {
    const res = await fetch(`${API_URL}/content/github/repos`, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (!res.ok) return;
    const repos = await res.json();
    const list = document.getElementById('repos-list');
    list.innerHTML = repos.map(r => `
      <div style="border:1px solid #ddd; padding:10px; margin-bottom:8px; border-radius:4px;">
        <strong>${r.name}</strong> (${r.language})
        <p>${r.description}</p>
        <button onclick="generateFromRepo('${r.name.replace(/'/g, "\\'")}', '${(r.description || '').replace(/'/g, "\\'")}', '${r.url}')">Générer un post depuis ce repo</button>
      </div>
    `).join('');
  } catch (err) {
    console.log('GitHub non connecté ou erreur:', err);
  }
}

async function generateFromRepo(title, description, url) {
  const token = localStorage.getItem('token');
  const context = `${description} | ${url}`;
  try {
    const res = await fetch(`${API_URL}/content/generate`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify({ title, context, source: 'github' })
    });
    if (!res.ok) throw new Error('Erreur de génération');
    alert('✅ Post généré depuis ' + title);
    loadQueue();
  } catch (err) {
    alert('❌ ' + err.message);
  }
}

// Charge repos si sur le dashboard



async function runWatch() {
  const token = localStorage.getItem('token');
  const msg = document.getElementById('watch-msg');
  msg.textContent = 'Scan en cours...';
  try {
    const res = await fetch(`${API_URL}/watch/run`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}` }
    });
    const data = await res.json();
    msg.textContent = `✅ ${data.new_items} nouveaux éléments trouvés`;
    loadWatchResults();
  } catch (err) {
    msg.textContent = '❌ ' + err.message;
  }
}

async function loadWatchResults() {
  const token = localStorage.getItem('token');
  const res = await fetch(`${API_URL}/watch/results`, {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  if (!res.ok) return;
  const items = await res.json();
  document.getElementById('watch-results').innerHTML = items.map(i => `
    <div style="border:1px solid #ddd; padding:10px; margin-bottom:8px; border-radius:4px;">
      <strong>[${i.type === 'job' ? 'Offre' : 'Certif'}]</strong> ${escapeHtml(i.title)}
      ${i.match_score ? `<p>Score: ${i.match_score}/100 — ${escapeHtml(i.justification || '')}</p>` : ''}
      <a href="${escapeHtml(i.link)}" target="_blank">Voir</a>
      ${i.cover_letter ? `<details><summary>Lettre de motivation</summary><p style="white-space:pre-wrap;">${escapeHtml(i.cover_letter)}</p></details>` : ''}
      ${i.type === 'job' ? `<br><button onclick="prepareApplication(${i.id})" style="margin-top:8px;">Préparer candidature</button><div id="apply-${i.id}"></div>` : ''}
    </div>
  `).join('');
}

async function prepareApplication(id) {
  const token = localStorage.getItem('token');
  const box = document.getElementById(`apply-${id}`);
  box.innerHTML = '<p>🔄 Préparation (30-60 s)...</p>';
  try {
    const res = await fetch(`${API_URL}/apply/prepare/${id}`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (!res.ok) throw new Error('Erreur ' + res.status);
    const d = await res.json();
    box.innerHTML = `
      ${d.warning ? `<p style="color:#b26a00;">⚠️ ${escapeHtml(d.warning)}</p>` : ''}
      <label>Email destinataire ${d.recipient_email ? '' : '(non trouvé dans la page : à remplir)'}</label>
      <input id="apply-to-${id}" value="${escapeHtml(d.recipient_email || '')}">
      <label>Sujet</label>
      <input id="apply-subject-${id}" value="${escapeHtml(d.subject)}">
      <label>Message</label>
      <textarea id="apply-body-${id}" rows="8" style="width:100%;">${escapeHtml(d.body)}</textarea>
      <label>Résumé CV adapté</label>
      <textarea id="apply-cv-${id}" rows="8" style="width:100%;">${escapeHtml(d.cv_summary)}</textarea>`;
  } catch (err) {
    box.innerHTML = `<span style="color:red;">❌ ${escapeHtml(err.message)}</span>`;
  }
}

async function loadProfile() {
  const token = localStorage.getItem('token');
  if (!token) return;
  const res = await fetch(`${API_URL}/profile`, {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  const data = await res.json();
  document.getElementById('field').value = data.field || '';
  document.getElementById('skills').value = data.skills || '';
  document.getElementById('level').value = data.level || '';
  document.getElementById('min_match_score').value = data.min_match_score || 70;
  document.getElementById('job_feed_url').value = data.job_feed_url || '';
  document.getElementById('cert_feed_url').value = data.cert_feed_url || '';
}

async function saveProfile() {
  const token = localStorage.getItem('token');
  const msg = document.getElementById('profile-msg');
  const payload = {
    field: document.getElementById('field').value,
    skills: document.getElementById('skills').value,
    level: document.getElementById('level').value,
    min_match_score: parseInt(document.getElementById('min_match_score').value),
    job_feed_url: document.getElementById('job_feed_url').value,
    cert_feed_url: document.getElementById('cert_feed_url').value,
  };

  try {
    const res = await fetch(`${API_URL}/profile`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error('Erreur de sauvegarde');
    msg.textContent = '✅ Profil sauvegardé';
  } catch (err) {
    msg.textContent = '❌ ' + err.message;
  }
}
if (window.location.pathname.includes('dashboard')) {
  loadQueue();
  loadRepos();
  loadWatchResults();
  loadAgentHistory();
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function addBubble(role, html) {
  const chat = document.getElementById('agent-chat');
  const div = document.createElement('div');
  div.style.cssText = `padding:10px; margin:8px 0; border-radius:6px; white-space:pre-wrap; background:${role === 'user' ? '#e8f0fe' : '#f7f7f7'};`;
  div.innerHTML = html;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

async function loadAgentHistory() {
  const token = localStorage.getItem('token');
  const res = await fetch(`${API_URL}/agent/history`, {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  if (!res.ok) return;
  const msgs = await res.json();
  document.getElementById('agent-chat').innerHTML = '';
  msgs.forEach(m => addBubble(m.role, `<strong>${m.role === 'user' ? 'Toi' : 'Agent'} :</strong> ${escapeHtml(m.content)}`));
}

async function askAgent() {
  const token = localStorage.getItem('token');
  const input = document.getElementById('agent-input');
  const request = input.value.trim();
  if (!request) return;

  addBubble('user', `<strong>Toi :</strong> ${escapeHtml(request)}`);
  input.value = '';
  const pending = addBubble('assistant', '🔄 L\'agent cherche et lit les sources...');

  try {
    const res = await fetch(`${API_URL}/agent/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ request })
    });
    if (!res.ok) throw new Error('Erreur ' + res.status);
    const data = await res.json();

    const sources = data.sources.map((s, i) =>
      `<li>[${i+1}] <a href="${escapeHtml(s.link)}" target="_blank">${escapeHtml(s.title)}</a></li>`).join('');
    pending.innerHTML = `
      <strong>Agent :</strong> ${escapeHtml(data.answer)}
      <br><small><strong>Recherches :</strong> ${data.queries.map(escapeHtml).join(' • ')}</small>
      <ul>${sources}</ul>
      ${data.post_saved ? '<p style="color:green;">✅ Post LinkedIn ajouté à ta file d\'attente (brouillon)</p>' : ''}`;
    if (data.post_saved) loadQueue();
  } catch (err) {
    pending.innerHTML = `<span style="color:red;">❌ ${escapeHtml(err.message)}</span>`;
  }
}

async function clearAgentHistory() {
  const token = localStorage.getItem('token');
  await fetch(`${API_URL}/agent/history`, {
    method: 'DELETE',
    headers: { 'Authorization': `Bearer ${token}` }
  });
  document.getElementById('agent-chat').innerHTML = '';
}
async function addKnowledge() {
  const token = localStorage.getItem('token');
  const input = document.getElementById('knowledge-input');
  const msg = document.getElementById('knowledge-msg');
  const content = input.value.trim();
  if (!content) return;
  msg.textContent = 'Ajout en cours (le 1er appel télécharge le modèle)...';
  try {
    const res = await fetch(`${API_URL}/profile/knowledge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ content })
    });
    if (!res.ok) throw new Error('Erreur ' + res.status);
    input.value = '';
    msg.textContent = '✅ Ajouté';
    loadKnowledge();
  } catch (err) {
    msg.textContent = '❌ ' + err.message;
  }
}

async function loadKnowledge() {
  const token = localStorage.getItem('token');
  const res = await fetch(`${API_URL}/profile/knowledge`, {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  if (!res.ok) return;
  const items = await res.json();
  document.getElementById('knowledge-list').innerHTML = items.map(k => `
    <div style="border:1px solid #ddd; padding:8px; margin-bottom:6px; border-radius:4px;">
      <span style="white-space:pre-wrap;">${escapeHtml(k.content)}</span>
      <br><button onclick="deleteKnowledge(${k.id})" style="background:#888; width:auto; padding:4px 10px; margin-top:6px;">Supprimer</button>
    </div>`).join('');
}

async function deleteKnowledge(id) {
  const token = localStorage.getItem('token');
  await fetch(`${API_URL}/profile/knowledge/${id}`, {
    method: 'DELETE',
    headers: { 'Authorization': `Bearer ${token}` }
  });
  loadKnowledge();
}
async function suggestComments() {
  const token = localStorage.getItem('token');
  const topic = document.getElementById('engage-topic').value.trim();
  const out = document.getElementById('engage-results');
  if (!topic) return;
  out.innerHTML = '<p>🔄 Recherche de posts et rédaction (20-40 s)...</p>';
  try {
    const res = await fetch(`${API_URL}/engage/suggest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ topic })
    });
    if (!res.ok) throw new Error('Erreur ' + res.status);
    const items = await res.json();
    out.innerHTML = items.map((s, i) => `
      <div style="border:1px solid #ddd; padding:10px; margin:8px 0; border-radius:4px;">
        <a href="${escapeHtml(s.post_link)}" target="_blank">${escapeHtml(s.post_title)}</a>
        <textarea id="comment-${i}" rows="4" style="width:100%; margin-top:8px;">${escapeHtml(s.suggested_comment)}</textarea>
        <button onclick="copyComment(${i})" style="margin-top:6px;">Copier le commentaire</button>
      </div>`).join('') || '<p>Aucun post trouvé.</p>';
  } catch (err) {
    out.innerHTML = `<span style="color:red;">❌ ${escapeHtml(err.message)}</span>`;
  }
}

function copyComment(i) {
  navigator.clipboard.writeText(document.getElementById(`comment-${i}`).value);
}