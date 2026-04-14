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
          this.error = "Upload failed";
        }
      } catch (e) {
        this.error = "Upload failed";
      } finally {
        this.uploading = false;
      }
    },
  };
}

/**
 * Required documents matrix: each slot has files_needed (e.g. 3 payslips).
 * Payload: JSON script tag id → { slots: [{ requirement_id, label, description, files_needed, doc_kind, uploaded }] }.
 */
function loanwiseFileUploadMatrix(opts) {
  const appId = opts.appId;
  const scriptId = opts.scriptId || "lw-upload-matrix-data";
  return {
    appId,
    scriptId,
    slots: [],
    acceptTypes: ".pdf,.png,.jpg,.jpeg,.webp,image/*,application/pdf",
    uploadingId: null,
    errorId: null,
    lastError: "",
    dragOverId: null,
    getCsrf() {
      const el = document.querySelector("[name=csrfmiddlewaretoken]");
      return el ? el.value : "";
    },
    init() {
      const el = document.getElementById(this.scriptId);
      let data = { slots: [] };
      try {
        data = el ? JSON.parse(el.textContent) : { slots: [] };
      } catch (e) {
        data = { slots: [] };
      }
      this.slots = (data.slots || []).map((s) => ({
        ...s,
        uploaded: typeof s.uploaded === "number" ? s.uploaded : 0,
        files_needed: Math.max(1, parseInt(s.files_needed, 10) || 1),
      }));
    },
    openPicker(rid) {
      const inp = document.getElementById("fup-" + rid);
      if (inp) inp.click();
    },
    onFileInput(ev, slot) {
      const f = ev.target.files && ev.target.files[0];
      ev.target.value = "";
      if (f) this.sendFile(f, slot);
    },
    onDropSlot(ev, slot) {
      ev.preventDefault();
      this.dragOverId = null;
      const f = ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0];
      if (f) this.sendFile(f, slot);
    },
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
          slot.uploaded += 1;
          try {
            window.dispatchEvent(
              new CustomEvent("loanwise-file-uploaded", {
                bubbles: true,
                detail: {
                  requirement_id: slot.requirement_id,
                  uploaded: slot.uploaded,
                  files_needed: slot.files_needed,
                },
              })
            );
          } catch (e) {}
        } else {
          this.errorId = slot.requirement_id;
          this.lastError = "Upload failed";
        }
      } catch (e) {
        this.errorId = slot.requirement_id;
        this.lastError = "Upload failed";
      } finally {
        this.uploadingId = null;
      }
    },
  };
}
