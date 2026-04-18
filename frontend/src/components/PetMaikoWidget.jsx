import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { showToast } from "./Toast";
import "./PetMaikoWidget.css";

/**
 * Maiko avatar — pet the dog.
 *
 * Click → fire POST /api/maiko/pet, run a spring-y bounce + rising
 * leaves, show remaining count. Hit the daily cap → warm message,
 * avatar settles back into idle (no failure state).
 *
 * The counter displayed is deployment-local for now. When an Upstash
 * aggregator is configured (future commit), the same getPetCount
 * endpoint returns a `global_today` field and this widget switches
 * to that number without any UI change here.
 */

const PARTICLE_EMOJI = ["🌸", "🍃", "💗", "✨"];

export default function PetMaikoWidget() {
  const [count, setCount] = useState(null);
  const [remaining, setRemaining] = useState(null);
  const [petting, setPetting] = useState(false);
  const [particles, setParticles] = useState([]);
  const [atCap, setAtCap] = useState(false);
  const avatarRef = useRef(null);
  const particleId = useRef(0);

  const fetchCount = async () => {
    try {
      const r = await api.getPetCount();
      setCount(r);
      setRemaining(r.your_remaining);
      setAtCap(r.cap != null && r.your_remaining === 0);
    } catch {
      // non-fatal — widget just renders with null counts
    }
  };

  useEffect(() => {
    fetchCount();
  }, []);

  const pet = async () => {
    if (petting || atCap) return;
    setPetting(true);

    // Spawn a few particles — purely visual, independent of the POST
    const burst = Array.from({ length: 5 }, () => ({
      id: ++particleId.current,
      emoji: PARTICLE_EMOJI[Math.floor(Math.random() * PARTICLE_EMOJI.length)],
      x: 40 + (Math.random() * 60 - 30),  // scatter around center
      delay: Math.random() * 120,
    }));
    setParticles((prev) => [...prev, ...burst]);
    // Clean up after the animation finishes (CSS animates 1.2s)
    setTimeout(() => {
      setParticles((prev) => prev.filter((p) => !burst.some((b) => b.id === p.id)));
    }, 1400);

    try {
      const r = await api.petMaiko();
      setRemaining(r.remaining_today);
      setAtCap(r.remaining_today === 0);
      showToast("Maiko wags happily.", "normal");
      // Refresh the shared count in the background so the deployment-
      // wide "N pets today" reflects your click.
      fetchCount();
    } catch (err) {
      const msg = err?.message || "";
      if (msg.toLowerCase().includes("cap")) {
        setAtCap(true);
        showToast("Maiko's had enough love from you for today — see you tomorrow.", "normal");
      } else if (msg.toLowerCase().includes("disabled")) {
        showToast("Petting is disabled on this deployment.", "normal");
      } else {
        showToast(`Couldn't pet Maiko right now. ${msg}`, "high");
      }
    }
    // Leave the bounce animation going a beat longer than the POST
    setTimeout(() => setPetting(false), 700);
  };

  // Prefer the cross-deployment number when the aggregator is
  // configured — that's the real "Maiko got N pets from the pack"
  // story. Falls back to the deployment-local count when unset.
  const hasGlobal = count && typeof count.global_today === "number";
  const today = hasGlobal ? count.global_today : (count?.today ?? 0);
  const fromThePack = hasGlobal;

  return (
    <div className="home-widget pet-widget">
      <div className="pet-avatar-wrap">
        <button
          type="button"
          ref={avatarRef}
          className={`pet-avatar ${petting ? "petted" : ""} ${atCap ? "sleepy" : ""}`}
          onClick={pet}
          disabled={atCap}
          aria-label={atCap ? "Maiko's had enough for today" : "Pet Maiko"}
          title={atCap ? "Maiko's had enough love for today — see you tomorrow." : "Pet Maiko"}
        >
          <img src="/icon.png" alt="Maiko" className="pet-avatar-img" />
        </button>
        <div className="pet-particles" aria-hidden>
          {particles.map((p) => (
            <span
              key={p.id}
              className="pet-particle"
              style={{
                left: `${p.x}%`,
                animationDelay: `${p.delay}ms`,
              }}
            >
              {p.emoji}
            </span>
          ))}
        </div>
      </div>

      <div className="pet-copy">
        <div className="pet-count">
          Maiko got <strong>{today}</strong> {today === 1 ? "pet" : "pets"}
          {fromThePack ? " from the pack" : ""} today
        </div>
        {remaining != null && (
          <div className="pet-remaining">
            {atCap
              ? "you're all out — tomorrow reset"
              : `you've got ${remaining} left today`}
          </div>
        )}
      </div>
    </div>
  );
}
