'use strict';
// Auth pages — login and register.
// JWT is stored in httpOnly cookie set by the server — no localStorage.

const API = '/api/v1';

async function handleLogin(e) {
  e.preventDefault();
  const btn = document.getElementById('login-btn');
  const err = document.getElementById('login-error');
  btn.disabled = true;
  btn.textContent = 'Signing in…';
  err.style.display = 'none';

  try {
    const res = await fetch(API + '/auth/login', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: document.getElementById('email').value,
        password: document.getElementById('password').value,
      }),
    });

    if (res.ok) {
      window.location.href = '/';
    } else if (res.status === 403) {
      // Email not verified — show inline resend option
      err.innerHTML =
        'Email not verified. Check your inbox, or ' +
        '<a href="/verify-email.html" style="color:var(--accent)">request a new link</a>.';
      err.style.display = 'block';
    } else {
      let msg = 'Invalid credentials';
      try {
        const contentType = res.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
          const j = await res.json();
          msg = j.detail || msg;
        }
      } catch {}
      err.textContent = msg;
      err.style.display = 'block';
    }
  } catch {
    err.textContent = 'Network error. Is the server running?';
    err.style.display = 'block';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Sign in';
  }
}

async function handleRegister(e) {
  e.preventDefault();
  const btn = document.getElementById('register-btn');
  const err = document.getElementById('register-error');
  btn.disabled = true;
  btn.textContent = 'Creating account…';
  err.style.display = 'none';

  try {
    const res = await fetch(API + '/auth/register', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: document.getElementById('email').value,
        password: document.getElementById('password').value,
      }),
    });

    if (res.status === 201) {
      // Attempt auto-login; if server requires email verification first, show the prompt.
      const loginRes = await fetch(API + '/auth/login', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: document.getElementById('email').value,
          password: document.getElementById('password').value,
        }),
      });
      if (loginRes.ok) {
        window.location.href = '/';
      } else if (loginRes.status === 403) {
        err.style.color = 'var(--success)';
        err.textContent = 'Account created! Check your inbox to verify your email before signing in.';
        err.style.display = 'block';
        btn.disabled = false;
        btn.textContent = 'Create account';
      } else {
        window.location.href = '/login.html';
      }
      return;
    } else {
      let msg = 'Registration failed';
      try {
        const contentType = res.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
          const j = await res.json();
          msg = j.detail || msg;
        }
      } catch {}
      err.textContent = msg;
      err.style.display = 'block';
    }
  } catch {
    err.textContent = 'Network error. Is the server running?';
    err.style.display = 'block';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Create account';
  }
}
