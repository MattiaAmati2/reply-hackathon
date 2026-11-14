(() => {
  let currentMode = "student_training";

  const modeButtons = document.querySelectorAll(".mode-btn");
  const keyMessagesSection = document.getElementById("key-messages-section");
  const webcamSection = document.getElementById("webcam-overlay-section");
  const modeSummary = document.getElementById("mode-summary");
  const healthDot = document.getElementById("health-dot");
  const healthText = document.getElementById("health-text");
  const initBtn = document.getElementById("init-btn");
  const pdfInput = document.getElementById("pdf-input");
  const keyMessagesInput = document.getElementById("key-messages-input");
  const sessionOutput = document.getElementById("session-output");

  function setMode(mode) {
    currentMode = mode;
    modeSummary.textContent = `Active mode: ${mode}`;

    modeButtons.forEach((btn) => {
      const active = btn.dataset.mode === mode;
      btn.classList.toggle("border-indigo-500", active);
      btn.classList.toggle("bg-indigo-950/40", active);
      btn.classList.toggle("border-slate-800", !active);
      btn.classList.toggle("bg-slate-900/60", !active);
    });

    const isProfessorLive = mode === "professor_live";
    keyMessagesSection.classList.toggle("hidden", isProfessorLive);
    webcamSection.classList.toggle("hidden", isProfessorLive);
  }

  modeButtons.forEach((btn) => {
    btn.addEventListener("click", () => setMode(btn.dataset.mode));
  });

  async function pollHealth() {
    try {
      const res = await fetch("/api/health");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      healthDot.className = "w-2.5 h-2.5 rounded-full bg-emerald-400";
      healthText.textContent = `T480 Online (${data.host}:${data.port})`;
    } catch {
      healthDot.className = "w-2.5 h-2.5 rounded-full bg-rose-500";
      healthText.textContent = "T480 Offline";
    }
  }

  initBtn.addEventListener("click", async () => {
    const file = pdfInput.files[0];
    if (!file) {
      sessionOutput.textContent = "Please select a PDF slide deck first.";
      sessionOutput.classList.remove("hidden");
      return;
    }

    const formData = new FormData();
    formData.append("mode", currentMode);
    formData.append("key_messages", currentMode === "professor_live" ? "" : keyMessagesInput.value);
    formData.append("pdf", file);

    try {
      const res = await fetch("/api/session/init", { method: "POST", body: formData });
      const data = await res.json();
      sessionOutput.textContent = JSON.stringify(data, null, 2);
      sessionOutput.classList.remove("hidden");
    } catch (err) {
      sessionOutput.textContent = `Error: ${err.message}`;
      sessionOutput.classList.remove("hidden");
    }
  });

  setMode(currentMode);
  pollHealth();
  setInterval(pollHealth, 10000);
})();
