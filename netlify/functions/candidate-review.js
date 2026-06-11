// netlify/functions/candidate-review.js
// Approve / reject discovered pages. Moves item from candidates.json -> reviewed.json
// Uses existing env vars: GITHUB_TOKEN, APPROVE_PASSWORD

const REPO = 'yesvarun/Osho-events';
const BRANCH = 'main';

exports.handler = async (event) => {
  const headers = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Allow-Methods': 'POST,OPTIONS',
    'Content-Type': 'application/json'
  };
  if (event.httpMethod === 'OPTIONS') return { statusCode: 200, headers, body: '' };
  if (event.httpMethod !== 'POST')
    return { statusCode: 405, headers, body: JSON.stringify({ error: 'POST only' }) };

  let body;
  try { body = JSON.parse(event.body); }
  catch { return { statusCode: 400, headers, body: JSON.stringify({ error: 'Bad JSON' }) }; }

  const { action, url, password } = body;
  if (password !== process.env.APPROVE_PASSWORD)
    return { statusCode: 401, headers, body: JSON.stringify({ error: 'Wrong password' }) };
  if (!['approve', 'reject'].includes(action) || !url)
    return { statusCode: 400, headers, body: JSON.stringify({ error: 'Need action (approve|reject) and url' }) };

  const gh = (path, opts = {}) =>
    fetch(`https://api.github.com/repos/${REPO}/${path}`, {
      ...opts,
      headers: {
        Authorization: `Bearer ${process.env.GITHUB_TOKEN}`,
        Accept: 'application/vnd.github+json',
        'User-Agent': 'oshocamps-review',
        ...(opts.headers || {})
      }
    });

  const getFile = async (name, fallback) => {
    const r = await gh(`contents/${name}?ref=${BRANCH}`);
    if (r.status === 404) return { sha: null, data: fallback };
    if (!r.ok) throw new Error(`GET ${name}: ${r.status}`);
    const j = await r.json();
    return { sha: j.sha, data: JSON.parse(Buffer.from(j.content, 'base64').toString('utf8')) };
  };

  const putFile = async (name, data, sha, msg) => {
    const payload = {
      message: msg, branch: BRANCH,
      content: Buffer.from(JSON.stringify(data, null, 1)).toString('base64')
    };
    if (sha) payload.sha = sha;
    const r = await gh(`contents/${name}`, { method: 'PUT', body: JSON.stringify(payload) });
    if (!r.ok) throw new Error(`PUT ${name}: ${r.status}`);
  };

  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const cand = await getFile('candidates.json', []);
      const rev = await getFile('reviewed.json', { approved: [], rejected: [] });

      const idx = cand.data.findIndex(c => c.url === url);
      const item = idx >= 0 ? cand.data.splice(idx, 1)[0] : { url };
      item.reviewed = new Date().toISOString().slice(0, 10);

      const bucket = action === 'approve' ? 'approved' : 'rejected';
      if (!rev.data[bucket].some(c => c.url === url)) rev.data[bucket].push(item);

      await putFile('candidates.json', cand.data, cand.sha, `review: ${action} (queue ${cand.data.length})`);
      await putFile('reviewed.json', rev.data, rev.sha, `review: ${action} ${url}`);

      return { statusCode: 200, headers, body: JSON.stringify({ ok: true, remaining: cand.data.length }) };
    } catch (e) {
      if (attempt === 3)
        return { statusCode: 500, headers, body: JSON.stringify({ error: String(e) }) };
      await new Promise(r => setTimeout(r, 1500)); // sha conflict -> refetch & retry
    }
  }
};
