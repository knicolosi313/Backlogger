// Asks for mic permission from a visible page. The offscreen recorder shares it (same origin).

const button = document.getElementById('allow') as HTMLButtonElement;
const result = document.getElementById('result') as HTMLParagraphElement;

button.addEventListener('click', async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop()); // we only wanted the permission
    result.textContent = 'Microphone allowed. You can close this tab and start recording.';
  } catch {
    result.textContent =
      'Access was blocked. Click the lock icon in the address bar, allow the microphone, then try again.';
  }
});
