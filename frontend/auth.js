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
      // Auto-login after register
      await fetch(API + '/auth/login', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: document.getElementById('email').value,
          password: document.getElementById('password').value,
        }),
      });
      window.location.href = '/';
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
