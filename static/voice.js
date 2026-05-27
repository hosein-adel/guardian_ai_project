// Guardian AI — Browser Voice Interaction (Push-to-Talk)
// Records audio via getUserMedia + MediaRecorder, sends to /api/voice/transcribe

(function () {
  let mediaRecorder = null;
  let audioChunks = [];
  let isRecording = false;

  const chatOutput = document.getElementById("chat-output");
  const chatInput = document.getElementById("chat-input");

  function addMessage(text, type = "system") {
    const div = document.createElement("div");
    div.className = `chat-message ${type}`;
    div.textContent = text;
    if (chatOutput) {
      chatOutput.appendChild(div);
      chatOutput.scrollTop = chatOutput.scrollHeight;
    }
  }

  function addMicButton() {
    const compose = document.querySelector(".chat-compose");
    if (!compose) return;

    const btn = document.createElement("button");
    btn.id = "mic-btn";
    btn.className = "btn secondary";
    btn.textContent = "🎤 نگه دارید و صحبت کنید";
    btn.style.userSelect = "none";
    btn.style.webkitUserSelect = "none";

    btn.addEventListener("mousedown", startRecording);
    btn.addEventListener("mouseup", stopRecording);
    btn.addEventListener("mouseleave", () => {
      if (isRecording) stopRecording();
    });
    btn.addEventListener("touchstart", (e) => {
      e.preventDefault();
      startRecording();
    });
    btn.addEventListener("touchend", (e) => {
      e.preventDefault();
      stopRecording();
    });

    compose.insertBefore(btn, compose.firstChild);
  }

  async function startRecording() {
    if (isRecording) return;
    isRecording = true;

    const btn = document.getElementById("mic-btn");
    if (btn) {
      btn.textContent = "🔴 در حال ضبط...";
      btn.classList.remove("secondary");
      btn.classList.add("danger");
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
      audioChunks = [];

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunks.push(e.data);
      };

      mediaRecorder.onstop = async () => {
        const blob = new Blob(audioChunks, { type: "audio/webm" });
        await sendAudio(blob);
        stream.getTracks().forEach((t) => t.stop());
      };

      mediaRecorder.start();
    } catch (err) {
      console.error("Microphone error:", err);
      addMessage("خطا: دسترسی به میکروفن داده نشد.", "system");
      resetButton();
      isRecording = false;
    }
  }

  function stopRecording() {
    if (!isRecording || !mediaRecorder) return;
    isRecording = false;
    resetButton();
    if (mediaRecorder.state !== "inactive") {
      mediaRecorder.stop();
    }
  }

  function resetButton() {
    const btn = document.getElementById("mic-btn");
    if (btn) {
      btn.textContent = "🎤 نگه دارید و صحبت کنید";
      btn.classList.remove("danger");
      btn.classList.add("secondary");
    }
  }

  async function sendAudio(blob) {
    addMessage("... در حال پردازش صدا", "system");

    const formData = new FormData();
    formData.append("audio", blob, "recording.webm");

    try {
      const res = await fetch("/api/voice/transcribe", {
        method: "POST",
        body: formData,
      });

      const data = await res.json();

      // Remove processing message
      const msgs = chatOutput.querySelectorAll(".chat-message.system");
      const last = msgs[msgs.length - 1];
      if (last && last.textContent.includes("در حال پردازش")) {
        last.remove();
      }

      if (!data.ok) {
        addMessage(`خطا: ${data.error || "unknown"}`, "system");
        return;
      }

      if (data.heard) {
        addMessage(data.heard, "user");
      }
      if (data.reply) {
        addMessage(data.reply, "bot");
      }
    } catch (err) {
      console.error("Send audio error:", err);
      addMessage("خطای شبکه در ارسال صدا", "system");
    }
  }

  // Initialize when DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", addMicButton);
  } else {
    addMicButton();
  }
})();
