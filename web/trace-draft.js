"use strict";
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.CadyxTraceDraft = Object.freeze(api);
})(typeof window !== "undefined" ? window : null, () => {
  const DEFAULT_HISTORY_LIMIT = 128;

  function cloneOwned(value) {
    if (typeof structuredClone === "function") return structuredClone(value);
    return JSON.parse(JSON.stringify(value));
  }

  function cloneRevision(value) {
    if (Array.isArray(value)) return value.slice();
    if (value && typeof value === "object") {
      const next = { ...value };
      if (Array.isArray(value.segments)) next.segments = value.segments.slice();
      return next;
    }
    return value;
  }

  class TraceDraft {
    constructor(initial = { segments: [] }, { historyLimit = DEFAULT_HISTORY_LIMIT } = {}) {
      if (!Number.isInteger(historyLimit) || historyLimit < 1) {
        throw new TypeError("historyLimit must be a positive integer");
      }
      this._historyLimit = historyLimit;
      this._past = [];
      this._present = cloneOwned(initial);
      this._future = [];
    }

    current() {
      return this._present;
    }

    snapshot() {
      return cloneOwned(this._present);
    }

    _remember(stack, revision) {
      stack.push(revision);
      if (stack.length > this._historyLimit) stack.splice(0, stack.length - this._historyLimit);
    }

    replace(next) {
      this._remember(this._past, this._present);
      this._present = cloneOwned(next);
      this._future.length = 0;
      return this._present;
    }

    update(mutator) {
      const next = cloneRevision(this._present);
      mutator(next);
      this._remember(this._past, this._present);
      this._present = next;
      this._future.length = 0;
      return this._present;
    }

    undo() {
      if (this._past.length) {
        this._remember(this._future, this._present);
        this._present = this._past.pop();
      }
      return this._present;
    }

    redo() {
      if (this._future.length) {
        this._remember(this._past, this._present);
        this._present = this._future.pop();
      }
      return this._present;
    }

    get canUndo() { return this._past.length > 0; }
    get canRedo() { return this._future.length > 0; }
    get historySize() { return this._past.length; }
    get historyLimit() { return this._historyLimit; }
  }

  function createTraceDraft(initial = [], options) {
    return new TraceDraft(initial, options);
  }

  return { DEFAULT_HISTORY_LIMIT, TraceDraft, createTraceDraft, cloneOwned };
});
