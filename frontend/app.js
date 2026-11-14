(() => {
  const state = {
    sessionId: null,
    mode: "student_training",
    slides: [],
    currentSlideIdx: 0,
    slideClicks: [],
    activeSlideStart: null,
    isRecording: false,
    recordStartMs: 0,
    timerInterval: null,
    audioBlob: null,
    audioFile: null,
    mediaRecorder: null,
    micStream: null,
    recordedChunks: [],
  };

  const $ = (id) => document.getElementById(id);
  const modeButtons = document.querySelectorAll(".mode-btn");

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function slideImageUrl(slide) {
    if (!slide?.thumbnail_path) return "";
    const file = String(slide.thumbnail_path).split(/[/\\]/).pop();
    return `/outputs/slides/${encodeURIComponent(file)}`;
  }

  function formatDuration(sec) {
    const total = Math.max(0, Math.floor(sec));
    const m = String(Math.floor(total / 60)).padStart(2, "0");
    const s = String(total % 60).padStart(2, "0");
    return `${m}:${s}`;
  }

  function elapsedSec() {
    if (!state.isRecording) return 0;
    return Number(((Date.now() - state.recordStartMs) / 1000).toFixed(2));
  }

  function setMode(mode) {
    state.mode = mode;
    $("mode-summary").textContent = `Active mode: ${mode}`;

    modeButtons.forEach((btn) => {
      const active = btn.dataset.mode === mode;
      btn.classList.toggle("border-indigo-500", active);
      btn.classList.toggle("bg-indigo-950/40", active);
      btn.classList.toggle("border-slate-800", !active);
      btn.classList.toggle("bg-slate-900/60", !active);
    });

    $("key-messages-section").classList.toggle("hidden", mode === "professor_live");
    $("webcam-overlay-section").classList.toggle("hidden", mode !== "student_training");
    $("slide-tracking-badge").textContent =
      mode === "student_training"
        ? "Manual slide click tracking"
        : "Automatic live slide alignment";

    if (mode !== "student_training" && window.KairosMediaPipe?.isRunning()) {
      window.KairosMediaPipe.stop($("webcam-video"), $("webcam-canvas"));
      $("camera-toggle-btn").textContent = "Enable Camera";
      $("camera-status-badge").textContent = "Idle";
    }
  }

  modeButtons.forEach((btn) => {
    btn.addEventListener("click", () => setMode(btn.dataset.mode));
  });

  async function pollHealth() {
    try {
      const res = await fetch("/api/health");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      $("health-dot").className = "w-2.5 h-2.5 rounded-full bg-emerald-400";
      $("health-text").textContent = `T480 Online (${data.host}:${data.port})`;
    } catch {
      $("health-dot").className = "w-2.5 h-2.5 rounded-full bg-rose-500";
      $("health-text").textContent = "T480 Offline";
    }
  }

  function renderSlideViewer() {
    const total = state.slides.length;
    if (!total) return;
    const slide = state.slides[state.currentSlideIdx];
    $("slide-viewer-img").src = slideImageUrl(slide);
    $("slide-indicator").textContent = `Slide ${state.currentSlideIdx + 1} / ${total}`;
    $("prev-slide-btn").disabled = state.currentSlideIdx === 0;
    $("next-slide-btn").disabled = state.currentSlideIdx >= total - 1;

    document.querySelectorAll(".slide-thumb-btn").forEach((el, idx) => {
      el.classList.toggle("border-indigo-500", idx === state.currentSlideIdx);
      el.classList.toggle("border-slate-800", idx !== state.currentSlideIdx);
    });
  }

  function closeActiveSlideInterval(endTime) {
    if (state.mode !== "student_training" || state.activeSlideStart === null) return;
    const slide = state.slides[state.currentSlideIdx];
    if (!slide) return;
    state.slideClicks.push({
      slide_page: slide.page_number,
      start_time: state.activeSlideStart,
      end_time: Math.max(state.activeSlideStart + 0.1, endTime),
    });
    state.activeSlideStart = null;
  }

  function goToSlide(newIdx) {
    if (newIdx < 0 || newIdx >= state.slides.length || newIdx === state.currentSlideIdx) return;
    if (state.isRecording && state.mode === "student_training") {
      const now = elapsedSec();
      closeActiveSlideInterval(now);
      state.activeSlideStart = now;
    }
    state.currentSlideIdx = newIdx;
    renderSlideViewer();
  }

  $("prev-slide-btn").addEventListener("click", () => goToSlide(state.currentSlideIdx - 1));
  $("next-slide-btn").addEventListener("click", () => goToSlide(state.currentSlideIdx + 1));

  document.addEventListener("keydown", (e) => {
    if ($("stage-section").classList.contains("hidden")) return;
    const tag = document.activeElement?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    if (e.key === "ArrowLeft") goToSlide(state.currentSlideIdx - 1);
    if (e.key === "ArrowRight") goToSlide(state.currentSlideIdx + 1);
  });

  $("init-btn").addEventListener("click", async () => {
    const file = $("pdf-input").files[0];
    const outputEl = $("session-output");
    if (!file) {
      outputEl.textContent = "Please select a PDF slide deck first.";
      outputEl.classList.remove("hidden");
      return;
    }

    outputEl.classList.add("hidden");
    $("init-btn").disabled = true;
    $("init-btn").textContent = "Processing PDF...";

    const formData = new FormData();
    formData.append("mode", state.mode);
    formData.append("key_messages", state.mode === "professor_live" ? "" : $("key-messages-input").value);
    formData.append("pdf", file);

    try {
      const res = await fetch("/api/session/init", { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

      state.sessionId = data.session_id;
      state.slides = data.slides || [];
      state.currentSlideIdx = 0;
      await resetAudioState();

      $("session-id-badge").textContent = `ID: ${data.session_id}`;
      $("session-slide-count").textContent = `${data.slide_count} slides`;
      $("slide-thumbnails").innerHTML = state.slides
        .map(
          (s, idx) => `
          <button type="button" data-idx="${idx}"
            class="slide-thumb-btn shrink-0 w-28 rounded-lg overflow-hidden border-2 border-slate-800 bg-slate-950 text-left transition">
            <img src="${slideImageUrl(s)}" alt="Slide ${s.page_number}" class="w-full h-16 object-cover" />
            <div class="px-1.5 py-0.5 text-[10px] text-slate-400">Page ${s.page_number}</div>
          </button>`
        )
        .join("");

      document.querySelectorAll(".slide-thumb-btn").forEach((btn) => {
        btn.addEventListener("click", () => goToSlide(Number(btn.dataset.idx)));
      });

      $("session-ready").classList.remove("hidden");
      $("stage-section").classList.remove("hidden");
      $("report-section").classList.add("hidden");
      $("contraddittorio-section").classList.add("hidden");
      renderSlideViewer();
    } catch (err) {
      outputEl.textContent = `Error: ${err.message}`;
      outputEl.classList.remove("hidden");
    } finally {
      $("init-btn").disabled = false;
      $("init-btn").textContent = "Initialize Session";
    }
  });

  function updateTelemetryPills(summary) {
    const fmt = (v) => (typeof v === "number" ? `${Math.round(v * 100)}%` : "--");
    $("pill-eye-contact").textContent = fmt(summary.eye_contact_ratio);
    $("pill-posture").textContent = fmt(summary.posture_score);
    $("pill-gestures").textContent = fmt(summary.gesture_activity_ratio);
    $("pill-stage-space").textContent = fmt(summary.stage_coverage);
  }

  $("camera-toggle-btn").addEventListener("click", async () => {
    const mp = window.KairosMediaPipe;
    if (!mp) return;
    const videoEl = $("webcam-video");
    const canvasEl = $("webcam-canvas");
    const statusBadge = $("camera-status-badge");

    if (mp.isRunning()) {
      mp.stop(videoEl, canvasEl);
      $("camera-toggle-btn").textContent = "Enable Camera";
      statusBadge.textContent = "Stopped";
      return;
    }

    const ok = await mp.start(
      videoEl,
      canvasEl,
      (msg) => { statusBadge.textContent = msg; },
      updateTelemetryPills
    );
    if (ok) {
      $("camera-toggle-btn").textContent = "Stop Camera";
    }
  });

  async function resetAudioState() {
    if (state.isRecording) await stopRecording();
    state.audioBlob = null;
    state.audioFile = null;
    state.slideClicks = [];
    state.activeSlideStart = null;
    $("audio-file-input").value = "";
    $("recording-timer").textContent = "00:00";
    $("audio-status").textContent = "No audio recorded yet.";
    window.KairosMediaPipe?.reset();
    updateTelemetryPills({});
  }

  $("reset-audio-btn").addEventListener("click", resetAudioState);

  $("audio-file-input").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    if (state.isRecording) await stopRecording();
    state.audioFile = file;
    state.audioBlob = null;
    $("audio-status").textContent = `Selected file: ${file.name} (${Math.round(file.size / 1024)} KB)`;
  });

  async function startRecording() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === "undefined") {
      $("audio-status").textContent = "Mic recording unavailable here — use file upload below.";
      return;
    }
    try {
      state.micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      $("audio-status").textContent = "Microphone permission denied — use file upload below.";
      return;
    }

    const mimeCandidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
    const mimeType = mimeCandidates.find((m) => MediaRecorder.isTypeSupported(m));
    state.mediaRecorder = mimeType
      ? new MediaRecorder(state.micStream, { mimeType })
      : new MediaRecorder(state.micStream);

    state.recordedChunks = [];
    state.audioFile = null;
    $("audio-file-input").value = "";
    state.slideClicks = [];
    state.activeSlideStart = state.mode === "student_training" ? 0 : null;

    state.mediaRecorder.ondataavailable = (ev) => {
      if (ev.data && ev.data.size > 0) state.recordedChunks.push(ev.data);
    };
    state.stopPromise = new Promise((resolve) => {
      state.mediaRecorder.onstop = () => {
        const type = state.mediaRecorder?.mimeType || "audio/webm";
        state.audioBlob = new Blob(state.recordedChunks, { type });
        $("audio-status").textContent = `Recorded audio ready (${Math.round(state.audioBlob.size / 1024)} KB)`;
        if (state.micStream) {
          state.micStream.getTracks().forEach((t) => t.stop());
          state.micStream = null;
        }
        resolve();
      };
    });

    state.isRecording = true;
    state.recordStartMs = Date.now();
    state.mediaRecorder.start(500);
    $("record-btn").textContent = "Stop Recording";
    $("record-btn").classList.replace("bg-rose-600", "bg-amber-600");
    $("audio-status").textContent = "Recording in progress...";

    state.timerInterval = setInterval(() => {
      $("recording-timer").textContent = formatDuration(elapsedSec());
    }, 250);
  }

  async function stopRecording() {
    if (!state.isRecording) return;
    closeActiveSlideInterval(elapsedSec());
    state.isRecording = false;
    clearInterval(state.timerInterval);
    state.timerInterval = null;
    if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
      state.mediaRecorder.stop();
      if (state.stopPromise) await state.stopPromise;
    }
    $("record-btn").textContent = "Start Recording";
    $("record-btn").classList.replace("bg-amber-600", "bg-rose-600");
  }

  $("record-btn").addEventListener("click", () => {
    if (state.isRecording) stopRecording();
    else startRecording();
  });

  $("analyze-btn").addEventListener("click", async () => {
    if (!state.sessionId) return;
    if (state.isRecording) await stopRecording();

    const audioPayload = state.audioFile || state.audioBlob;
    if (!audioPayload) {
      $("analysis-status").textContent = "Please record audio or upload an audio/video file first.";
      return;
    }

    const telemetry =
      state.mode === "student_training" ? window.KairosMediaPipe?.getSummary() || {} : {};

    const formData = new FormData();
    formData.append("session_id", state.sessionId);
    const filename = state.audioFile ? state.audioFile.name : "recording.webm";
    formData.append("audio", audioPayload, filename);
    formData.append("telemetry_json", JSON.stringify(telemetry));
    formData.append("slide_clicks_json", JSON.stringify(state.slideClicks));

    $("analyze-btn").disabled = true;
    $("analysis-progress").classList.remove("hidden");
    $("analysis-progress-bar").style.width = "45%";
    $("analysis-status").textContent = "Transcribing audio, aligning slides & running evaluation...";

    try {
      const res = await fetch("/api/session/analyze", { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

      $("analysis-progress-bar").style.width = "100%";
      $("analysis-status").textContent = "Analysis complete.";
      renderReport(data.report);
    } catch (err) {
      $("analysis-status").textContent = `Analysis failed: ${err.message}`;
    } finally {
      $("analyze-btn").disabled = false;
      setTimeout(() => $("analysis-progress").classList.add("hidden"), 800);
    }
  });

  function renderReport(report) {
    if (!report) return;
    $("report-section").classList.remove("hidden");

    const downloads = [];
    if (report.annotated_pdf_url) {
      downloads.push(
        `<a href="${escapeHtml(report.annotated_pdf_url)}" target="_blank" rel="noopener"
          class="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-semibold text-white">Download Annotated PDF</a>`
      );
    }
    if (report.digest_url) {
      downloads.push(
        `<a href="${escapeHtml(report.digest_url)}" target="_blank" rel="noopener"
          class="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-xs font-semibold text-white">Download WeBeep Digest</a>`
      );
    }
    $("report-downloads").innerHTML = downloads.join("");

    const specchioItems = (report.specchio_feedback || [])
      .map((s) => `<li class="text-xs text-slate-300">• ${escapeHtml(s)}</li>`)
      .join("");
    $("report-summary").innerHTML = `
      <div class="rounded-xl bg-slate-950/70 border border-slate-800 p-4 space-y-2">
        <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">Specchio Summary</div>
        <p class="text-sm text-slate-100">${escapeHtml(report.overall_summary)}</p>
        ${specchioItems ? `<ul class="space-y-1 pt-1">${specchioItems}</ul>` : ""}
      </div>`;

    const kmEntries = Object.entries(report.key_message_coverage || {});
    $("report-key-messages").innerHTML = kmEntries.length
      ? `<div class="rounded-xl bg-slate-950/70 border border-slate-800 p-4 space-y-2">
          <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">Key Message Coverage</div>
          <div class="flex flex-wrap gap-2">
            ${kmEntries
              .map(
                ([msg, hit]) =>
                  `<span class="text-xs px-2.5 py-1 rounded-lg border ${
                    hit
                      ? "bg-emerald-950/60 border-emerald-700 text-emerald-300"
                      : "bg-rose-950/60 border-rose-700 text-rose-300"
                  }">${hit ? "✓" : "✗"} ${escapeHtml(msg)}</span>`
              )
              .join("")}
          </div>
        </div>`
      : "";

    const flags = report.problem_flags || [];
    const sevClass = {
      high: "bg-rose-950 text-rose-300 border-rose-700",
      medium: "bg-amber-950 text-amber-300 border-amber-700",
      low: "bg-slate-800 text-slate-300 border-slate-700",
    };
    $("report-problem-flags").innerHTML = flags.length
      ? `<div class="rounded-xl bg-slate-950/70 border border-slate-800 p-4 space-y-2">
          <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">Problem Flags</div>
          <div class="space-y-2">
            ${flags
              .map(
                (f) => `<div class="flex items-start gap-2 text-xs">
                  <span class="px-2 py-0.5 rounded border uppercase font-semibold ${sevClass[f.severity] || sevClass.low}">${escapeHtml(f.severity)}</span>
                  <span class="text-slate-400 shrink-0">Slide ${f.page_number} · ${escapeHtml(f.dimension)}</span>
                  <span class="text-slate-200">${escapeHtml(f.summary)}</span>
                </div>`
              )
              .join("")}
          </div>
        </div>`
      : "";

    const lenses = report.comprehension_lenses || [];
    $("report-comprehension-lenses").innerHTML = lenses.length
      ? `<div class="space-y-2">
          <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">Comprehension Lenses</div>
          <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
            ${lenses
              .map(
                (l) => `<div class="rounded-xl bg-slate-950/70 border border-slate-800 p-4 space-y-2">
                  <div class="flex items-center justify-between">
                    <span class="text-sm font-semibold uppercase text-white">${escapeHtml(l.lens)}</span>
                    <span class="text-xs font-mono px-2 py-0.5 rounded bg-indigo-950 text-indigo-300">${l.clarity_score}/10</span>
                  </div>
                  <div class="text-xs text-slate-400">Friction: ${(l.friction_points || []).map(escapeHtml).join("; ") || "None"}</div>
                  <div class="text-xs text-emerald-300">Tips: ${(l.suggestions || []).map(escapeHtml).join("; ") || "None"}</div>
                </div>`
              )
              .join("")}
          </div>
        </div>`
      : "";

    const del = report.delivery_feedback || {};
    const tel = del.telemetry || {};
    const telKeys = Object.keys(tel);
    $("report-delivery").innerHTML = `
      <div class="rounded-xl bg-slate-950/70 border border-slate-800 p-4 space-y-2">
        <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">Delivery &amp; Telemetry</div>
        <div class="flex flex-wrap gap-4 text-xs text-slate-200">
          <span>Pace: <strong>${del.avg_wpm ?? 0} WPM</strong></span>
          <span>Fillers: <strong>${del.total_fillers ?? 0}</strong></span>
          <span>Pauses: <strong>${del.total_pauses ?? 0}</strong></span>
          ${telKeys.length ? `<span>Eye Contact: <strong>${Math.round((tel.eye_contact_ratio || 0) * 100)}%</strong></span>` : ""}
          ${telKeys.length ? `<span>Posture: <strong>${Math.round((tel.posture_score || 0) * 100)}%</strong></span>` : ""}
          ${telKeys.length ? `<span>Gestures: <strong>${Math.round((tel.gesture_activity_ratio || 0) * 100)}%</strong></span>` : ""}
          ${telKeys.length ? `<span>Stage Space: <strong>${Math.round((tel.stage_coverage || 0) * 100)}%</strong></span>` : ""}
        </div>
        ${(del.tips || []).map((t) => `<div class="text-xs text-slate-300">• ${escapeHtml(t)}</div>`).join("")}
      </div>`;

    const notes = report.slide_notes || [];
    $("report-slide-notes").innerHTML = notes.length
      ? `<div class="rounded-xl bg-slate-950/70 border border-slate-800 p-4 space-y-2">
          <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">Slide Notes</div>
          <div class="space-y-1.5">
            ${notes
              .map(
                (n) => `<div class="text-xs text-slate-300">
                  <span class="font-semibold text-indigo-300">Slide ${n.page_number} (${escapeHtml(n.dimension)}):</span>
                  ${escapeHtml(n.comment)}
                </div>`
              )
              .join("")}
          </div>
        </div>`
      : "";

    const showChat = report.mode !== "professor_live";
    $("contraddittorio-section").classList.toggle("hidden", !showChat);
    if (showChat) {
      $("contraddittorio-messages").innerHTML = "";
      $("contraddittorio-budget").textContent = "0 / 3";
      $("contraddittorio-input").disabled = false;
      $("contraddittorio-send-btn").disabled = false;
    }
  }

  function appendChatBubble(role, text, meta = "") {
    const isUser = role === "user";
    const wrapper = document.createElement("div");
    wrapper.className = `flex flex-col ${isUser ? "items-end" : "items-start"}`;
    wrapper.innerHTML = `
      <div class="max-w-[85%] rounded-xl px-3.5 py-2.5 text-sm ${
        isUser ? "bg-indigo-600 text-white" : "bg-slate-950 border border-slate-800 text-slate-200"
      }">${escapeHtml(text)}</div>
      ${meta ? `<span class="text-[10px] text-slate-500 mt-1">${escapeHtml(meta)}</span>` : ""}
    `;
    $("contraddittorio-messages").appendChild(wrapper);
    $("contraddittorio-messages").scrollTop = $("contraddittorio-messages").scrollHeight;
  }

  async function sendContraddittorio() {
    const input = $("contraddittorio-input");
    const msg = input.value.trim();
    if (!msg || !state.sessionId) return;

    input.value = "";
    appendChatBubble("user", msg);
    $("contraddittorio-send-btn").disabled = true;

    try {
      const res = await fetch("/api/contraddittorio/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: state.sessionId, message: msg }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

      $("contraddittorio-budget").textContent = `${data.turn} / ${data.max_turns}`;
      const meta = data.boundary ? `Boundary: ${data.boundary}` : `Status: ${data.status}`;
      appendChatBubble("assistant", data.reply, meta);

      const exhausted = data.status === "budget_exhausted";
      input.disabled = exhausted;
      $("contraddittorio-send-btn").disabled = exhausted;
    } catch (err) {
      appendChatBubble("assistant", `Error: ${err.message}`);
      $("contraddittorio-send-btn").disabled = false;
    }
  }

  $("contraddittorio-send-btn").addEventListener("click", sendContraddittorio);
  $("contraddittorio-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendContraddittorio();
  });

  setMode(state.mode);
  pollHealth();
  setInterval(pollHealth, 10000);
})();
