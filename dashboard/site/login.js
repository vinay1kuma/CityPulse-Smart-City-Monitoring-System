'use strict';
const form = document.getElementById('login-form');
const errorBox = document.getElementById('login-error');
const submitBtn = document.getElementById('submit-btn');
const passwordInput = document.getElementById('password');
document.getElementById('toggle-password').addEventListener('click', () => {
  const show = passwordInput.type === 'password';
  passwordInput.type = show ? 'text' : 'password';
  document.getElementById('toggle-password').textContent = show ? 'Hide' : 'Show';
});
form.addEventListener('submit', async event => {
  event.preventDefault(); errorBox.textContent = '';
  submitBtn.disabled = true; submitBtn.querySelector('span').textContent = 'Signing in…';
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({username: document.getElementById('username').value.trim(), password: passwordInput.value})
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Unable to sign in. Please try again.');
    sessionStorage.setItem('citypulse_admin_token', data.access_token);
    location.replace('/');
  } catch (error) {
    errorBox.textContent = error.message || 'Network error. Check that CityPulse is running.';
  } finally {
    submitBtn.disabled = false; submitBtn.querySelector('span').textContent = 'Sign in to CityPulse';
  }
});
