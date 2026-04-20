import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { showToast } from "./Toast";
import "./PetMaikoFooter.css";

/**
 * Compact Pet Maiko chip for the bottom bar. Same API + semantics as
 * the old sidebar widget, just a single footer-section-sized row:
 *
 *   [ avatar ] 24 pets today    log →
 *
 * Click the avatar to pet. Cap state grays it out. Particles still
 * fire on click for the tactile feedback.
 */

const PARTICLE_EMOJI = ["🌸", "🍃", "💗", "✨"];

export default function PetMaikoFooter() {
  const [count, setCount] = useState(null);
  const [remaining, setRemaining] = useState(null);
  const [petting, setPetting] = useState(false);
  const [atCap, setAtCap] = useState(false);
  const [particles, setParticles] = useState([]);
  const particleId = useRef(0);

  const fetchCount = async () => {
    try {
      const r = await api.getPetCount();
      setCount(r);
      setRemaining(r.your_remaining);
      setAtCap(r.cap != null && r.your_remaining === 0);
    } catch {
      // non-fatal — chip just renders with null counts
    }
  };

  useEffect(() => { fetchCount(); }, []);

  const pet = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (petting || atCap) return;
    setPetting(true);

    const burst = Array.from({ length: 3 }, () => ({
      id: ++particleId.current,
      emoji: PARTICLE_EMOJI[Math.floor(Math.random() * PARTICLE_EMOJI.length)],
      x: 40 + (Math.random() * 40 - 20),
      delay: Math.random() * 100,
    }));
    setParticles((prev) => [...prev, ...burst]);
    setTimeout(() => {
      setParticles((prev) => prev.filter((p) => !burst.some((b) => b.id === p.id)));
    }, 1400);

    try {
      const r = await api.petMaiko();
      setRemaining(r.remaining_today);
      setAtCap(r.remaining_today === 0);
      fetchCount();
    } catch (err) {
      const msg = err?.message || "";
      if (msg.toLowerCase().includes("cap")) {
        setAtCap(true);
        showToast("Maiko's had enough for today. See you tomorrow.", "normal");
      } else if (msg.toLowerCase().includes("disabled")) {
        // Deployment opted out — hide the chip entirely (no toast spam).
      } else {
        showToast(`Couldn't pet Maiko. ${msg}`, "high");
      }
    }
    setTimeout(() => setPetting(false), 500);
  };

  // If the cap is null on the backend, petting is disabled on this
  // deployment — just don't render the chip.
  if (count === null) return null;
  if (count?.cap === null || count?.cap === undefined) return null;

  const hasGlobal = typeof count.global_today === "number";
  const today = hasGlobal ? count.global_today : (count.today ?? 0);

  return (
    <div className="footer-section footer-pet">
      <button
        type="button"
        className={`footer-pet-avatar ${petting ? "petted" : ""} ${atCap ? "sleepy" : ""}`}
        onClick={pet}
        disabled={atCap}
        title={atCap ? "Maiko's had enough for today, see you tomorrow." : "Pet Maiko"}
        aria-label={atCap ? "Maiko's had enough for today" : "Pet Maiko"}
      >
        <img src="/icon.png" alt="Maiko" />
        <span className="footer-pet-particles" aria-hidden>
          {particles.map((p) => (
            <span
              key={p.id}
              className="footer-pet-particle"
              style={{ left: `${p.x}%`, animationDelay: `${p.delay}ms` }}
            >
              {p.emoji}
            </span>
          ))}
        </span>
      </button>
      <span className="footer-pet-count" title={atCap ? "all out" : (remaining != null ? `${remaining} left today` : "")}>
        {today} {today === 1 ? "pet" : "pets"}
      </span>
      <Link to="/pet-log" className="footer-pet-log" title="Pet log — for the deployment owner">log</Link>
    </div>
  );
}
