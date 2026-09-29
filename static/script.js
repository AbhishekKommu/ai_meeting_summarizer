/* ===================================================================
   AI Meeting Summarizer — Frontend Logic
   Handles file upload, POST to /summarize, and history interaction.
   =================================================================== */

(function () {
  "use strict";

  const fileInput = document.getElementById("fileInput");
  const fileNameDisplay = document.getElementById("fileNameDisplay");
  const summarizeBtn = document.getElementById("summarizeBtn");
  const summaryText = document.getElementById("summaryText");
  const statusBadge = document.getElementById("statusBadge");
  const fileLabel = document.getElementById("fileLabel");

  // ----------------------------------------------------------------
  // File selection
  // ----------------------------------------------------------------
  if (fileInput) {
    fileInput.addEventListener("change", function (e) {
      const file = e.target.files[0];
      if (file) {
        let displayName = file.name;
        if (displayName.length > 30) {
          displayName = displayName.substring(0, 27) + "…";
        }
        fileNameDisplay.textContent = displayName;
        fileLabel.style.background = "rgba(80, 130, 255, 0.15)";
        if (statusBadge) statusBadge.style.display = "none";
      } else {
        fileNameDisplay.textContent = "Choose audio file…";
        fileLabel.style.background = "transparent";
      }
    });
  }

  // ----------------------------------------------------------------
  // Summarize button
  // ----------------------------------------------------------------
  if (summarizeBtn) {
    summarizeBtn.addEventListener("click", async function () {
      const file = fileInput.files[0];

      // Validation
      if (!file) {
        shakeUploadArea();
        summaryText.innerHTML =
          '<div class="placeholder-text" style="color:#ffb0b0;">' +
          '<i class="fas fa-exclamation-circle"></i> Please select an audio file first.' +
          "</div>";
        if (statusBadge) statusBadge.style.display = "none";
        return;
      }

      // Loading state
      summarizeBtn.disabled = true;
      summarizeBtn.innerHTML =
        '<i class="fas fa-spinner fa-pulse"></i> Summarizing...';
      summaryText.innerHTML =
        '<div style="display:flex;align-items:center;gap:10px;color:#b8d0ff;">' +
        '<i class="fas fa-brain" style="font-size:1.4rem;"></i>' +
        "<span>Analyzing audio content</span>" +
        '<span class="loading-dots"><span></span><span></span><span></span></span>' +
        "</div>";
      if (statusBadge) statusBadge.style.display = "none";

      // Build form data
      const formData = new FormData();
      formData.append("audio", file);

      try {
        const response = await fetch("/summarize", {
          method: "POST",
          body: formData,
        });

        const data = await response.json();

        if (!response.ok) {
          throw new Error(data.error || "Summarization failed");
        }

        // Render summary
        const safeSummary = escapeHtml(data.summary).replace(/\n/g, "<br>");
        summaryText.innerHTML =
          '<i class="fas fa-quote-left" style="color:#7f9eff;opacity:0.7;"></i> ' +
          safeSummary +
          ' <i class="fas fa-quote-right" style="color:#7f9eff;opacity:0.7;"></i>';

        if (statusBadge) {
          statusBadge.style.display = "inline-flex";
          statusBadge.innerHTML = '<i class="fas fa-circle-check"></i> Summary ready';
        }

        // Add to history list dynamically
        prependHistoryItem(data.filename, data.summary, data.meeting_id);
      } catch (err) {
        summaryText.innerHTML =
          '<div class="placeholder-text" style="color:#ffb0b0;">' +
          '<i class="fas fa-triangle-exclamation"></i> ' +
          escapeHtml(err.message) +
          "</div>";
      } finally {
        summarizeBtn.disabled = false;
        summarizeBtn.innerHTML =
          '<i class="fas fa-wand-magic-sparkles"></i> Summarize';
      }
    });
  }

  // ----------------------------------------------------------------
  // History item click → show in summary panel
  // ----------------------------------------------------------------
  document.querySelectorAll(".history-item").forEach(function (item) {
    item.addEventListener("click", function () {
      const summary = item.getAttribute("data-summary");
      if (summary && summaryText) {
        const safeSummary = escapeHtml(summary).replace(/\n/g, "<br>");
        summaryText.innerHTML =
          '<i class="fas fa-quote-left" style="color:#7f9eff;opacity:0.7;"></i> ' +
          safeSummary +
          ' <i class="fas fa-quote-right" style="color:#7f9eff;opacity:0.7;"></i>';
        if (statusBadge) {
          statusBadge.style.display = "inline-flex";
          statusBadge.innerHTML = '<i class="fas fa-clock-rotate-left"></i> From history';
        }
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    });
  });

  // ----------------------------------------------------------------
  // Helpers
  // ----------------------------------------------------------------
  function shakeUploadArea() {
    const uploadArea = document.querySelector(".upload-area");
    if (!uploadArea) return;
    uploadArea.style.transition = "0.1s";
    uploadArea.style.transform = "translateX(6px)";
    setTimeout(() => (uploadArea.style.transform = "translateX(-6px)"), 60);
    setTimeout(() => (uploadArea.style.transform = "translateX(4px)"), 120);
    setTimeout(() => (uploadArea.style.transform = "translateX(-4px)"), 180);
    setTimeout(() => (uploadArea.style.transform = "translateX(0)"), 240);
  }

  function escapeHtml(str) {
    if (!str) return "";
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function prependHistoryItem(filename, summary, meetingId) {
    const historyList = document.querySelector(".history-list");
    const emptyHistory = document.querySelector(".empty-history");
    if (emptyHistory) emptyHistory.remove();

    if (!historyList) return;

    const li = document.createElement("li");
    li.className = "history-item";
    li.setAttribute("data-summary", summary);
    li.innerHTML =
      '<div class="history-meta"><i class="fas fa-file-audio"></i> <strong>' +
      escapeHtml(filename) +
      "</strong></div>" +
      '<div class="history-date">just now</div>';

    li.addEventListener("click", function () {
      const safe = escapeHtml(summary).replace(/\n/g, "<br>");
      summaryText.innerHTML =
        '<i class="fas fa-quote-left" style="color:#7f9eff;opacity:0.7;"></i> ' +
        safe +
        ' <i class="fas fa-quote-right" style="color:#7f9eff;opacity:0.7;"></i>';
      if (statusBadge) {
        statusBadge.style.display = "inline-flex";
        statusBadge.innerHTML = '<i class="fas fa-clock-rotate-left"></i> From history';
      }
      window.scrollTo({ top: 0, behavior: "smooth" });
    });

    historyList.insertBefore(li, historyList.firstChild);
  }
})();