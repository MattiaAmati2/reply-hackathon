(() => {
  const TASKS_CDN = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/+esm";
  const WASM_ROOT = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm";
  const MODEL_URLS = {
    face: "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    hand: "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
    pose: "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
  };

  let visionMod = null;
  let faceLandmarker = null;
  let handLandmarker = null;
  let poseLandmarker = null;
  let stream = null;
  let rafId = null;
  let running = false;
  let lastInferMs = 0;

  let stats = createEmptyStats();

  function createEmptyStats() {
    return {
      sampleCount: 0,
      eyeContactHits: 0,
      eyeContactSamples: 0,
      postureSum: 0,
      postureSamples: 0,
      gestureHits: 0,
      gestureSamples: 0,
      prevHandCentroid: null,
      prevNose: null,
      headStabilitySum: 0,
      headStabilitySamples: 0,
      torsoXs: [],
    };
  }

  function clamp01(val) {
    return Math.max(0, Math.min(1, val));
  }

  async function ensureModels(onStatus) {
    if (!visionMod) {
      onStatus("Loading MediaPipe...");
      visionMod = await import(TASKS_CDN);
    }
    const { FilesetResolver, FaceLandmarker, HandLandmarker, PoseLandmarker } = visionMod;
    const fileset = await FilesetResolver.forVisionTasks(WASM_ROOT);

    if (!faceLandmarker) {
      faceLandmarker = await FaceLandmarker.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: MODEL_URLS.face, delegate: "CPU" },
        runningMode: "VIDEO",
        numFaces: 1,
      });
    }
    if (!handLandmarker) {
      handLandmarker = await HandLandmarker.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: MODEL_URLS.hand, delegate: "CPU" },
        runningMode: "VIDEO",
        numHands: 2,
      });
    }
    if (!poseLandmarker) {
      poseLandmarker = await PoseLandmarker.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: MODEL_URLS.pose, delegate: "CPU" },
        runningMode: "VIDEO",
        numPoses: 1,
      });
    }
  }

  function drawPoints(ctx, width, height, points, color, radius = 2.5) {
    if (!points || !points.length) return;
    ctx.fillStyle = color;
    for (const pt of points) {
      ctx.beginPath();
      ctx.arc(pt.x * width, pt.y * height, radius, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function processFrame(videoEl, canvasEl, nowMs, onMetrics) {
    if (videoEl.readyState < 2 || !videoEl.videoWidth) return;

    const width = videoEl.videoWidth;
    const height = videoEl.videoHeight;
    if (canvasEl.width !== width) canvasEl.width = width;
    if (canvasEl.height !== height) canvasEl.height = height;

    const ctx = canvasEl.getContext("2d");
    ctx.clearRect(0, 0, width, height);

    stats.sampleCount += 1;

    // 1. Face: eye contact proxy (nose tip #1 centered between outer eyes #33 & #263) + head stability
    if (faceLandmarker) {
      const faceRes = faceLandmarker.detectForVideo(videoEl, nowMs);
      const lm = faceRes?.faceLandmarks?.[0];
      if (lm && lm.length > 263) {
        const nose = lm[1];
        const leftEye = lm[33];
        const rightEye = lm[263];
        const eyeMidX = (leftEye.x + rightEye.x) / 2;
        const eyeSpan = Math.max(0.05, Math.abs(rightEye.x - leftEye.x));
        const yawOffset = Math.abs(nose.x - eyeMidX) / eyeSpan;

        stats.eyeContactSamples += 1;
        if (yawOffset < 0.28) {
          stats.eyeContactHits += 1;
        }

        if (stats.prevNose) {
          const delta = Math.hypot(nose.x - stats.prevNose.x, nose.y - stats.prevNose.y);
          stats.headStabilitySum += clamp01(1 - delta * 12);
          stats.headStabilitySamples += 1;
        }
        stats.prevNose = { x: nose.x, y: nose.y };

        drawPoints(ctx, width, height, [nose, leftEye, rightEye], "#38bdf8", 3);
      }
    }

    // 2. Hands: gesture activity ratio
    if (handLandmarker) {
      const handRes = handLandmarker.detectForVideo(videoEl, nowMs);
      const hands = handRes?.landmarks || [];
      stats.gestureSamples += 1;
      if (hands.length > 0) {
        const wrist = hands[0][0];
        let moved = true;
        if (stats.prevHandCentroid) {
          const dist = Math.hypot(wrist.x - stats.prevHandCentroid.x, wrist.y - stats.prevHandCentroid.y);
          moved = dist > 0.008 || hands.length > 0;
        }
        if (moved) stats.gestureHits += 1;
        stats.prevHandCentroid = { x: wrist.x, y: wrist.y };
        for (const h of hands) {
          drawPoints(ctx, width, height, h, "#34d399", 2);
        }
      }
    }

    // 3. Pose: posture score (shoulder levelness) & stage coverage (torso X spread)
    if (poseLandmarker) {
      const poseRes = poseLandmarker.detectForVideo(videoEl, nowMs);
      const pose = poseRes?.landmarks?.[0];
      if (pose && pose.length > 24) {
        const lShoulder = pose[11];
        const rShoulder = pose[12];
        const lHip = pose[23];
        const rHip = pose[24];

        const shoulderTilt = Math.abs(lShoulder.y - rShoulder.y);
        const shoulderMidX = (lShoulder.x + rShoulder.x) / 2;
        const hipMidX = (lHip.x + rHip.x) / 2;
        const lean = Math.abs(shoulderMidX - hipMidX);

        const posture = clamp01(1 - shoulderTilt * 4 - lean * 2.5);
        stats.postureSum += posture;
        stats.postureSamples += 1;

        stats.torsoXs.push((shoulderMidX + hipMidX) / 2);
        drawPoints(ctx, width, height, [lShoulder, rShoulder, lHip, rHip], "#fbbf24", 4);
      }
    }

    if (onMetrics) onMetrics(getSummary());
  }

  function getSummary() {
    if (stats.sampleCount === 0) return {};

    const eyeContactRatio =
      stats.eyeContactSamples > 0 ? stats.eyeContactHits / stats.eyeContactSamples : 0;
    const postureScore =
      stats.postureSamples > 0 ? stats.postureSum / stats.postureSamples : 0;
    const gestureActivityRatio =
      stats.gestureSamples > 0 ? stats.gestureHits / stats.gestureSamples : 0;
    const headStability =
      stats.headStabilitySamples > 0 ? stats.headStabilitySum / stats.headStabilitySamples : 0.75;

    let stageCoverage = 0;
    if (stats.torsoXs.length >= 2) {
      const minX = Math.min(...stats.torsoXs);
      const maxX = Math.max(...stats.torsoXs);
      // Normalize horizontal movement range (0.35 frame width ~= full stage coverage)
      stageCoverage = clamp01((maxX - minX) / 0.35);
    }

    return {
      eye_contact_ratio: Number(eyeContactRatio.toFixed(3)),
      posture_score: Number(postureScore.toFixed(3)),
      gesture_activity_ratio: Number(gestureActivityRatio.toFixed(3)),
      stage_coverage: Number(stageCoverage.toFixed(3)),
      head_stability: Number(headStability.toFixed(3)),
      sample_count: stats.sampleCount,
    };
  }

  async function start(videoEl, canvasEl, onStatus = () => {}, onMetrics = () => {}) {
    if (running) return true;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      onStatus("Camera unavailable (requires HTTPS or localhost)");
      return false;
    }

    try {
      onStatus("Requesting camera...");
      stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 360 }, facingMode: "user" },
        audio: false,
      });
      videoEl.srcObject = stream;
      await videoEl.play();
    } catch (err) {
      const msg = err?.name === "NotAllowedError" ? "Camera permission denied" : "Camera unavailable";
      onStatus(`${msg} — audio-only fallback`);
      return false;
    }

    try {
      await ensureModels(onStatus);
    } catch (err) {
      onStatus("MediaPipe CDN unavailable — audio-only");
      stop(videoEl, canvasEl);
      return false;
    }

    running = true;
    onStatus("Tracking active");

    const loop = (nowMs) => {
      if (!running) return;
      if (nowMs - lastInferMs >= 160) {
        lastInferMs = nowMs;
        try {
          processFrame(videoEl, canvasEl, nowMs, onMetrics);
        } catch {
          // Keep loop alive across transient frame errors.
        }
      }
      rafId = requestAnimationFrame(loop);
    };
    rafId = requestAnimationFrame(loop);
    return true;
  }

  function stop(videoEl, canvasEl) {
    running = false;
    if (rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      stream = null;
    }
    if (videoEl) videoEl.srcObject = null;
    if (canvasEl) {
      const ctx = canvasEl.getContext("2d");
      if (ctx) ctx.clearRect(0, 0, canvasEl.width, canvasEl.height);
    }
  }

  function reset() {
    stats = createEmptyStats();
  }

  window.KairosMediaPipe = {
    start,
    stop,
    reset,
    getSummary,
    isRunning: () => running,
  };
})();
