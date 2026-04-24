import "./PlanetSpinner.css";

/**
 * In-progress indicator that rotates the Planet Maiko logo around its
 * vertical axis — reads as a real 3D spinning planet rather than a
 * flat 360°-rotation icon. Drop-in replacement for <Loader size={N}
 * className="spin" /> at the prominent loading moments.
 *
 * Small sizes read as a moving texture; larger sizes actually "flip
 * edge-on" thanks to CSS perspective + rotateY + preserve-3d.
 */
export default function PlanetSpinner({ size = 14, className = "" }) {
  return (
    <span
      className={`planet-spinner ${className}`}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      <img src="/icon.svg" alt="" />
    </span>
  );
}
