// Guardian AI — Browser Voice Interaction (Push-to-Talk)
// Records microphone audio as WAV via Web Audio API, sends to /api/voice/transcribe

(function () {
  let stream = null;
  let audioContext = null;
  let sourceNode = null;
  let processorNode = null;
  let audioBuffers = [];
  let recordingTimer = null;
  let isRecording = false;

  const MAX_RECORDING_MS = 25000; // Whisper upstream requires clips shorter than 30s.
  const BUFFER_SIZE = 4096;

  const chatOutput = document.getElementById("chat-output");

  function addMessage(text, type = "system") {
    const div = document.createElement("div");
    div.className = `chat-message ${type}`;
    div.textContent = text;
    if (chatOutput) {
      chatOutput.appendChild(div);
      chatOutput.scrollTop = chatOutput.scrollHeight;
    }
  }

  function newRequestId(prefix = "voice_ui") {
    const rand = (window.crypto && crypto.randomUUID)
      ? crypto.randomUUID().replaceAll("-", "").slice(0, 12)
      : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
    return `${prefix}_${rand}`;
  }

  function withRequestId(message, requestId) {
    return requestId ? `${message} [ID: ${requestId}]` : message;
  }

  function addMicButton() {
    const compose = document.getElementById("chat-compose");
    if (!compose) {
      console.warn("[voice.js] #chat-compose not found; microphone button was not added.");
      return;
    }
    if (document.getElementById("mic-btn")) return;

    const btn = document.createElement("button");
    btn.id = "mic-btn";
    btn.className = "btn secondary";
    btn.textContent = "🎤 نگه دارید و صحبت کنید";
    btn.style.userSelect = "none";
    btn.style.webkitUserSelect = "none";

    btn.addEventListener("mousedown", startRecording);
    btn.addEventListener("mouseup", () => stopRecording("manual"));
    btn.addEventListener("mouseleave", () => {
      if (isRecording) stopRecording("manual");
    });
    btn.addEventListener("touchstart", (e) => {
      e.preventDefault();
      startRecording();
    });
    btn.addEventListener("touchend", (e) => {
      e.preventDefault();
      stopRecording("manual");
    });

    compose.insertBefore(btn, compose.firstChild);
  }

  async function startRecording() {
    if (isRecording) return;

    const btn = document.getElementById("mic-btn");
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
      sourceNode = audioContext.createMediaStreamSource(stream);
      processorNode = audioContext.createScriptProcessor(BUFFER_SIZE, 1, 1);
      audioBuffers = [];

      processorNode.onaudioprocess = (event) => {
        if (!isRecording) return;
        const input = event.inputBuffer.getChannelData(0);
        audioBuffers.push(new Float32Array(input));
      };

      sourceNode.connect(processorNode);
      // Required in some browsers for onaudioprocess to fire.
      processorNode.connect(audioContext.destination);

      isRecording = true;
      if (btn) {
        btn.textContent = "🔴 در حال ضبط... حداکثر ۲۵ ثانیه";
        btn.classList.remove("secondary");
        btn.classList.add("danger");
      }

      recordingTimer = setTimeout(() => {
        if (isRecording) {
          addMessage("حداکثر زمان ضبط تمام شد؛ صدا ارسال می‌شود.", "system");
          stopRecording("timeout");
        }
      }, MAX_RECORDING_MS);
    } catch (err) {
      console.error("Microphone error:", err);
      addMessage("خطا: دسترسی به میکروفن داده نشد.", "system");
      cleanupAudioGraph();
      resetButton();
      isRecording = false;
    }
  }

  async function stopRecording(reason = "manual") {
    if (!isRecording) return;
    isRecording = false;
    resetButton();

    if (recordingTimer) {
      clearTimeout(recordingTimer);
      recordingTimer = null;
    }

    const sampleRate = audioContext ? audioContext.sampleRate : 44100;
    cleanupAudioGraph();

    const totalSamples = audioBuffers.reduce((sum, buffer) => sum + buffer.length, 0);
    if (totalSamples <= 0) {
      addMessage("صدایی ضبط نشد.", "system");
      return;
    }

    const wavBlob = encodeWav(audioBuffers, sampleRate);
    audioBuffers = [];

    if (wavBlob.size <= 44) {
      addMessage("صدای ضبط‌شده خالی است.", "system");
      return;
    }

    await sendAudio(wavBlob, reason);
  }

  function resetButton() {
    const btn = document.getElementById("mic-btn");
    if (btn) {
      btn.textContent = "🎤 نگه دارید و صحبت کنید";
      btn.classList.remove("danger");
      btn.classList.add("secondary");
    }
  }

  function cleanupAudioGraph() {
    try { if (processorNode) processorNode.disconnect(); } catch (_) {}
    try { if (sourceNode) sourceNode.disconnect(); } catch (_) {}
    try { if (stream) stream.getTracks().forEach((track) => track.stop()); } catch (_) {}
    try { if (audioContext && audioContext.state !== "closed") audioContext.close(); } catch (_) {}

    processorNode = null;
    sourceNode = null;
    stream = null;
    audioContext = null;
  }

  function flattenBuffers(buffers, totalSamples) {
    const result = new Float32Array(totalSamples);
    let offset = 0;
    for (const buffer of buffers) {
      result.set(buffer, offset);
      offset += buffer.length;
    }
    return result;
  }

  function encodeWav(buffers, sampleRate) {
    const totalSamples = buffers.reduce((sum, buffer) => sum + buffer.length, 0);
    const samples = flattenBuffers(buffers, totalSamples);
    const bytesPerSample = 2;
    const dataSize = samples.length * bytesPerSample;
    const buffer = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buffer);

    writeString(view, 0, "RIFF");
    view.setUint32(4, 36 + dataSize, true);
    writeString(view, 8, "WAVE");
    writeString(view, 12, "fmt ");
    view.setUint32(16, 16, true); // PCM chunk size
    view.setUint16(20, 1, true);  // PCM format
    view.setUint16(22, 1, true);  // mono
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * bytesPerSample, true);
    view.setUint16(32, bytesPerSample, true);
    view.setUint16(34, 16, true);
    writeString(view, 36, "data");
    view.setUint32(40, dataSize, true);

    floatTo16BitPCM(view, 44, samples);
    return new Blob([view], { type: "audio/wav" });
  }

  function floatTo16BitPCM(view, offset, input) {
    for (let i = 0; i < input.length; i++, offset += 2) {
      const s = Math.max(-1, Math.min(1, input[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
  }

  function writeString(view, offset, string) {
    for (let i = 0; i < string.length; i++) {
      view.setUint8(offset + i, string.charCodeAt(i));
    }
  }

  async function sendAudio(blob, reason) {
    const requestId = newRequestId();
    addMessage(withRequestId("... در حال پردازش صدا", requestId), "system");

    const formData = new FormData();
    formData.append("audio", blob, "recording.wav");
    formData.append("recording_reason", reason || "manual");

    try {
      const res = await fetch("/api/voice/transcribe", {
        method: "POST",
        headers: { "X-Request-ID": requestId },
        body: formData,
      });

      const responseId = res.headers.get("X-Request-ID") || requestId;
      const data = await res.json();
      data.request_id = data.request_id || responseId;

      const msgs = chatOutput.querySelectorAll(".chat-message.system");
      const last = msgs[msgs.length - 1];
      if (last && last.textContent.includes(requestId)) {
        last.remove();
      }

      if (!res.ok || !data.ok) {
        addMessage(withRequestId(`خطا: ${data.error || "unknown"}`, data.request_id), "system");
        return;
      }

      if (data.heard) {
        addMessage(data.heard, "user");
      }
      if (data.reply) {
        addMessage(data.reply, "bot");
      }
    } catch (err) {
      console.error("Send audio error:", requestId, err);
      addMessage(withRequestId("خطای شبکه در ارسال صدا", requestId), "system");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", addMicButton);
  } else {
    addMicButton();
  }
})();
