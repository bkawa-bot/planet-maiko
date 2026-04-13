import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import { applyCustomTheme, clearCustomTheme } from "../utils/themes";
import {
  Palette, Plus, Trash2, Save, X, Eye, Sparkles, Loader,
} from "lucide-react";
import "./Themes.css";

// The fields the form exposes. Kept in the same order as the built-in
// themes in index.css so a creator can work top-to-bottom without
// jumping around. snake_case keys match the backend whitelist.
const COLOR_FIELDS = [
  { key: "bg", label: "Background", required: true },
  { key: "bg_card", label: "Card" },
  { key: "bg_card_alt", label: "Card (alt)" },
  { key: "bg_hover", label: "Hover" },
  { key: "bg_selected", label: "Selected" },
  { key: "text", label: "Text", required: true },
  { key: "text_dim", label: "Text (dim)" },
  { key: "text_muted", label: "Text (muted)" },
  { key: "pink", label: "Primary / pink" },
  { key: "blue", label: "Blue" },
  { key: "mint", label: "Mint" },
  { key: "orange", label: "Orange" },
  { key: "lavender", label: "Lavender" },
  { key: "peach", label: "Peach" },
  { key: "lemon", label: "Lemon" },
  { key: "urgent", label: "Urgent" },
  { key: "high", label: "High" },
  { key: "normal", label: "Normal" },
  { key: "low", label: "Low" },
  { key: "green", label: "Green / success" },
  { key: "border", label: "Border" },
  { key: "border_subtle", label: "Border (subtle)" },
];

const WORLD_OPTIONS = [
  { value: "none", label: "Plain (no hills)" },
  { value: "night", label: "Night hills" },
  { value: "day", label: "Day hills" },
  { value: "morning", label: "Morning hills" },
  { value: "afternoon", label: "Afternoon hills" },
  { value: "sunset", label: "Sunset hills" },
];

const EMPTY_FORM = {
  id: "",
  name: "",
  emoji: "🎨",
  description: "",
  world_background: "night",
  colors: {},
};

export default function Themes() {
  const [themes, setThemes] = useState([]);
  const [selected, setSelected] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [previewing, setPreviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [showGenerate, setShowGenerate] = useState(false);
  const [genQuery, setGenQuery] = useState("");
  const [generating, setGenerating] = useState(false);

  const fetchThemes = () => api.getThemes().then(setThemes).catch(console.error);
  useEffect(() => { fetchThemes(); }, []);

  // When leaving the page, make sure our preview doesn't linger.
  useEffect(() => () => {
    if (previewing) {
      const saved = localStorage.getItem("maiko-theme");
      if (!saved?.startsWith("custom:")) clearCustomTheme();
      document.documentElement.setAttribute("data-theme", saved || "dark");
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const previewTheme = useMemo(
    () => ({ ...form, colors: { ...form.colors } }),
    [form],
  );

  const startNew = () => {
    setSelected(null);
    setForm(EMPTY_FORM);
  };

  const openTheme = (t) => {
    setSelected(t.id);
    setForm({
      id: t.id,
      name: t.name || "",
      emoji: t.emoji || "🎨",
      description: t.description || "",
      world_background: t.world_background || "night",
      colors: { ...(t.colors || {}) },
    });
  };

  const updateColor = (key, value) => {
    setForm((f) => ({ ...f, colors: { ...f.colors, [key]: value } }));
  };

  const clearColor = (key) => {
    setForm((f) => {
      const next = { ...f.colors };
      delete next[key];
      return { ...f, colors: next };
    });
  };

  const doPreview = () => {
    if (!previewTheme.id || !previewTheme.colors.bg || !previewTheme.colors.text) {
      showToast("Need id, bg, and text to preview", "high");
      return;
    }
    applyCustomTheme(previewTheme);
    setPreviewing(true);
    showToast("Previewing — save to keep, close to revert", "normal");
  };

  const stopPreview = () => {
    // Revert to whatever theme was selected before preview — if it was
    // a custom theme, re-apply it; otherwise just flip the attribute.
    const saved = localStorage.getItem("maiko-theme") || "dark";
    if (saved.startsWith("custom:") && saved !== `custom:${previewTheme.id}`) {
      const id = saved.slice("custom:".length);
      api.getTheme(id).then(applyCustomTheme).catch(() => {});
    } else if (!saved.startsWith("custom:")) {
      clearCustomTheme();
      document.documentElement.setAttribute("data-theme", saved);
    }
    setPreviewing(false);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const saved = await api.saveTheme(previewTheme);
      showToast(`Saved "${saved.name}"`, "normal");
      await fetchThemes();
      setSelected(saved.id);
      applyCustomTheme(saved);
      setPreviewing(false);
    } catch (err) {
      showToast(err.message || "Save failed", "high");
    }
    setSaving(false);
  };

  const handleGenerate = async () => {
    const q = genQuery.trim();
    if (!q) return;
    setGenerating(true);
    try {
      const res = await api.generateTheme(q);
      const theme = res?.theme;
      if (!theme) throw new Error(res?.error || "no theme returned");
      setSelected(null);
      setForm({
        id: theme.id || "",
        name: theme.name || "",
        emoji: theme.emoji || "🎨",
        description: theme.description || "",
        world_background: theme.world_background || "night",
        colors: { ...(theme.colors || {}) },
      });
      applyCustomTheme(theme);
      setPreviewing(true);
      setShowGenerate(false);
      setGenQuery("");
      showToast("Theme generated — tweak and save when you're happy", "normal");
    } catch (err) {
      showToast(err.message || "Generation failed", "high");
    }
    setGenerating(false);
  };

  const handleDelete = async (t) => {
    if (!confirm(`Delete "${t.name}"?`)) return;
    try {
      await api.deleteTheme(t.id);
      showToast(`Deleted "${t.name}"`, "normal");
      // If the deleted theme was active, fall back to dark.
      const active = localStorage.getItem("maiko-theme");
      if (active === `custom:${t.id}`) {
        localStorage.setItem("maiko-theme", "dark");
        clearCustomTheme();
        document.documentElement.setAttribute("data-theme", "dark");
      }
      if (selected === t.id) startNew();
      await fetchThemes();
    } catch (err) {
      showToast(err.message || "Delete failed", "high");
    }
  };

  return (
    <div className="themes-page">
      <div className="themes-header">
        <div className="themes-header-row">
          <h2><Palette size={18} /> Themes</h2>
          <button className="btn btn-primary" onClick={() => setShowGenerate(true)}>
            <Sparkles size={12} /> Design with Maiko
          </button>
        </div>
        <p className="themes-sub">
          Craft your own color palette for Maiko. Saved themes appear in the theme menu at the top right.
        </p>
      </div>

      <div className="themes-grid">
        <aside className="themes-list">
          <button className="btn btn-primary themes-new-btn" onClick={startNew}>
            <Plus size={12} /> New theme
          </button>
          {themes.length === 0 ? (
            <div className="themes-empty">No custom themes yet. Create one!</div>
          ) : (
            <ul>
              {themes.map((t) => (
                <li key={t.id}>
                  <button
                    className={`themes-list-item ${selected === t.id ? "active" : ""}`}
                    onClick={() => openTheme(t)}
                  >
                    <span className="themes-list-emoji">{t.emoji || "🎨"}</span>
                    <span className="themes-list-name">{t.name}</span>
                  </button>
                  <button
                    className="btn-ghost themes-list-delete"
                    onClick={() => handleDelete(t)}
                    title="Delete"
                  >
                    <Trash2 size={12} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <section className="themes-editor">
          <div className="themes-editor-row">
            <label>
              ID
              <input
                type="text"
                value={form.id}
                onChange={(e) => setForm({ ...form, id: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "") })}
                placeholder="ocean-dusk"
                disabled={!!selected}
              />
            </label>
            <label>
              Name
              <input
                type="text"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Ocean Dusk"
              />
            </label>
            <label>
              Emoji
              <input
                type="text"
                value={form.emoji}
                onChange={(e) => setForm({ ...form, emoji: e.target.value })}
                placeholder="🌊"
              />
            </label>
          </div>

          <label className="themes-field-full">
            Description
            <input
              type="text"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="Quiet evening by the sea"
            />
          </label>

          <label className="themes-field-full">
            Hill background
            <select
              value={form.world_background}
              onChange={(e) => setForm({ ...form, world_background: e.target.value })}
            >
              {WORLD_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </label>

          <div className="themes-colors">
            {COLOR_FIELDS.map(({ key, label, required }) => {
              const value = form.colors[key] || "";
              const looksHex = /^#[0-9a-fA-F]{3,8}$/.test(value);
              return (
                <div key={key} className="themes-color-row">
                  <span className="themes-color-label">
                    {label}
                    {required && <span className="themes-required">*</span>}
                  </span>
                  <input
                    type="color"
                    className="themes-color-swatch"
                    value={looksHex ? (value.length === 4 ? value : value.slice(0, 7)) : "#000000"}
                    onChange={(e) => updateColor(key, e.target.value)}
                    title={value || "pick a color"}
                  />
                  <input
                    type="text"
                    className="themes-color-text"
                    value={value}
                    onChange={(e) => updateColor(key, e.target.value)}
                    placeholder="#1e2529 or rgba(…)"
                  />
                  {value && !required && (
                    <button
                      className="btn-ghost"
                      onClick={() => clearColor(key)}
                      title="Clear"
                    >
                      <X size={12} />
                    </button>
                  )}
                </div>
              );
            })}
          </div>

          <div className="themes-actions">
            {previewing ? (
              <button className="btn" onClick={stopPreview}>
                <X size={12} /> Stop preview
              </button>
            ) : (
              <button className="btn" onClick={doPreview}>
                <Eye size={12} /> Preview
              </button>
            )}
            <button
              className="btn btn-primary"
              onClick={handleSave}
              disabled={saving || !form.id || !form.name}
            >
              <Save size={12} /> {saving ? "Saving..." : "Save theme"}
            </button>
          </div>
        </section>
      </div>

      {showGenerate && (
        <div className="modal-overlay" onClick={() => !generating && setShowGenerate(false)}>
          <div className="generate-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <Sparkles size={16} /> Design a theme
              <button
                className="btn btn-sm"
                onClick={() => setShowGenerate(false)}
                disabled={generating}
                style={{ marginLeft: "auto" }}
              >
                <X size={12} />
              </button>
            </div>
            <div className="generate-modal-body">
              <p className="themes-sub" style={{ marginBottom: 8 }}>
                Describe the vibe and Maiko will generate a palette.
              </p>
              <textarea
                className="generate-textarea"
                value={genQuery}
                onChange={(e) => setGenQuery(e.target.value)}
                placeholder={'e.g. "ocean at dusk, warm and moody, like a Studio Ghibli film"'}
                rows={4}
                autoFocus
                disabled={generating}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) handleGenerate();
                }}
              />
              <div className="themes-actions" style={{ borderTop: "none", paddingTop: 4 }}>
                <button className="btn" onClick={() => setShowGenerate(false)} disabled={generating}>
                  Cancel
                </button>
                <button
                  className="btn btn-primary"
                  onClick={handleGenerate}
                  disabled={generating || !genQuery.trim()}
                >
                  {generating
                    ? <><Loader size={12} className="spin" /> Designing...</>
                    : <><Sparkles size={12} /> Generate</>}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
