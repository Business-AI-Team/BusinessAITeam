/**
 * Reusable file upload (PDF / images) to LoanWise document API.
 * Use with Alpine: x-data="loanwiseFileUpload({ appId: 1, showRequirement: true, showKind: true })"
 */
function loanwiseFileUpload(opts) {
  const appId = opts.appId;
  const showRequirement = opts.showRequirement !== false;
  const showKind = opts.showKind !== false;
  const defaultKind = opts.defaultKind || "generic";
  return {
    appId,
    showRequirement,
    showKind,
    requirementId: "",
    docKind: defaultKind,
    uploading: false,
    dragOver: false,
    error: "",
    requirements: [],
    _inputEl: null,
    getCsrf() {
      const el = document.querySelector("[name=csrfmiddlewaretoken]");
      return el ? el.value : "";
    },
    async loadRequirements() {
      if (!this.showRequirement) return;
      const lang = document.documentElement.lang || "fr";
      try {
        const res = await fetch("/api/requirements/?lang=" + encodeURIComponent(lang), {
          credentials: "same-origin",
        });
        if (res.ok) this.requirements = await res.json();
      } catch (e) {}
    },
    bindFileInput(el) {
      this._inputEl = el;
    },
    openPicker() {
      if (this._inputEl) this._inputEl.click();
    },
    onFileChosen(ev) {
      const f = ev.target.files && ev.target.files[0];
      if (f) this.uploadFile(f);
      ev.target.value = "";
    },
    onDrop(ev) {
      ev.preventDefault();
      this.dragOver = false;
      const f = ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0];
      if (f) this.uploadFile(f);
    },
    async uploadFile(file) {
      if (!file || this.uploading) return;
      this.error = "";
      this.uploading = true;
      const fd = new FormData();
      fd.append("file", file);
      fd.append("kind", this.docKind);
      if (this.requirementId) fd.append("requirement_id", this.requirementId);
      try {
        const res = await fetch("/api/documents/upload/" + this.appId + "/", {
          method: "POST",
          credentials: "same-origin",
          headers: { "X-CSRFToken": this.getCsrf() },
          body: fd,
        });
        if (res.ok) {
          try {
            window.dispatchEvent(
              new CustomEvent("loanwise-file-uploaded", {
                bubbles: true,
                detail: { requirement_id: this.requirementId ? String(this.requirementId) : null },
              })
            );
          } catch (e) {}
        } else {
          this.error = (window.lwUploadFailed || "Upload failed");
        }
      } catch (e) {
        this.error = (window.lwUploadFailed || "Upload failed");
      } finally {
        this.uploading = false;
      }
    },
  };
}

/**
 * Required documents matrix: each slot has files_needed (e.g. 3 payslips).
 * Supports multi-file drag & drop, per-slot file list, and individual delete.
 * Payload: JSON script tag id → { slots: [{ requirement_id, label, description, files_needed, doc_kind, uploaded }] }.
 */
function loanwiseFileUploadMatrix(opts) {
  const appId = opts.appId;
  const scriptId = opts.scriptId || "lw-upload-matrix-data";
  return {
    appId,
    scriptId,
    slots: [],
    /* requirement_id → [{id, name}] — tracks uploaded files per slot */
    uploadedFiles: {},
    acceptTypes: ".pdf,.png,.jpg,.jpeg,.webp,image/*,application/pdf",
    uploadingId: null,
    errorId: null,
    lastError: "",
    dragOverId: null,
    _queueRunning: false,
    _queue: [],
    getCsrf() {
      const el = document.querySelector("[name=csrfmiddlewaretoken]");
      return el ? el.value : "";
    },

    /* ── init: load slots + fetch existing uploaded docs ── */
    async init() {
      const el = document.getElementById(this.scriptId);
      let data = { slots: [] };
      try { data = el ? JSON.parse(el.textContent) : { slots: [] }; } catch (e) {}
      this.slots = (data.slots || []).map((s) => ({
        ...s,
        uploaded: typeof s.uploaded === "number" ? s.uploaded : 0,
        files_needed: Math.max(1, parseInt(s.files_needed, 10) || 1),
      }));

      /* Load existing documents from API to populate file lists */
      try {
        const res = await fetch("/api/applications/" + this.appId + "/documents/", {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        if (res.ok) {
          const payload = await res.json();
          const uf = {};
          for (const doc of (payload.documents || [])) {
            const rid = doc.requirement_id;
            if (rid == null) continue;
            if (!uf[rid]) uf[rid] = [];
            uf[rid].push({ id: doc.id, name: doc.original_filename });
          }
          this.uploadedFiles = uf;
          /* Sync counters with server reality */
          for (const slot of this.slots) {
            const files = uf[slot.requirement_id] || [];
            if (files.length > 0) slot.uploaded = files.length;
          }
        }
      } catch (e) {}
    },

    openPicker(rid) {
      const inp = document.getElementById("fup-" + rid);
      if (inp) inp.click();
    },

    /* ── File input (supports multiple attribute) ── */
    onFileInput(ev, slot) {
      const files = ev.target.files ? Array.from(ev.target.files) : [];
      ev.target.value = "";
      if (files.length) this._enqueue(files, slot);
    },

    /* ── Drag & drop (multiple files at once) ── */
    onDropSlot(ev, slot) {
      ev.preventDefault();
      this.dragOverId = null;
      const files = ev.dataTransfer && ev.dataTransfer.files
        ? Array.from(ev.dataTransfer.files)
        : [];
      if (files.length) this._enqueue(files, slot);
    },

    /* ── Queue helpers (sequential uploads, respect files_needed limit) ── */
    _enqueue(files, slot) {
      const remaining = slot.files_needed - slot.uploaded;
      if (remaining <= 0) return;
      files.slice(0, remaining).forEach((f) => this._queue.push({ file: f, slot }));
      if (!this._queueRunning) this._processQueue();
    },

    async _processQueue() {
      this._queueRunning = true;
      while (this._queue.length > 0) {
        const { file, slot } = this._queue.shift();
        if (slot.uploaded >= slot.files_needed) continue;
        await this.sendFile(file, slot);
      }
      this._queueRunning = false;
    },

    /* ── Upload a single file ── */
    async sendFile(file, slot) {
      if (!file || slot.uploaded >= slot.files_needed) return;
      this.errorId = null;
      this.lastError = "";
      this.uploadingId = slot.requirement_id;
      const fd = new FormData();
      fd.append("file", file);
      fd.append("kind", slot.doc_kind || "generic");
      fd.append("requirement_id", String(slot.requirement_id));
      try {
        const res = await fetch("/api/documents/upload/" + this.appId + "/", {
          method: "POST",
          credentials: "same-origin",
          headers: { "X-CSRFToken": this.getCsrf() },
          body: fd,
        });
        if (res.ok) {
          const doc = await res.json();
          slot.uploaded += 1;
          const rid = slot.requirement_id;
          if (!this.uploadedFiles[rid]) this.uploadedFiles[rid] = [];
          this.uploadedFiles[rid] = [...this.uploadedFiles[rid], { id: doc.id, name: doc.original_filename || file.name }];
          try {
            window.dispatchEvent(new CustomEvent("loanwise-file-uploaded", {
              bubbles: true,
              detail: { requirement_id: rid, uploaded: slot.uploaded, files_needed: slot.files_needed },
            }));
          } catch (e) {}
        } else {
          this.errorId = slot.requirement_id;
          this.lastError = (window.lwUploadFailed || "Upload failed");
        }
      } catch (e) {
        this.errorId = slot.requirement_id;
        this.lastError = (window.lwUploadFailed || "Upload failed");
      } finally {
        this.uploadingId = null;
      }
    },

    /* ── Remove a file (DELETE API + update local state) ── */
    async removeFile(docId, slot) {
      try {
        const res = await fetch("/api/documents/" + docId + "/", {
          method: "DELETE",
          credentials: "same-origin",
          headers: { "X-CSRFToken": this.getCsrf() },
        });
        if (res.ok || res.status === 204) {
          const rid = slot.requirement_id;
          this.uploadedFiles[rid] = (this.uploadedFiles[rid] || []).filter((f) => f.id !== docId);
          slot.uploaded = Math.max(0, slot.uploaded - 1);
          try {
            window.dispatchEvent(new CustomEvent("loanwise-file-uploaded", {
              bubbles: true,
              detail: { requirement_id: rid, uploaded: slot.uploaded, files_needed: slot.files_needed },
            }));
          } catch (e) {}
        }
      } catch (e) {}
    },
  };
}
