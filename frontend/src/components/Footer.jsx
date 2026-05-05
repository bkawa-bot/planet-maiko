import { Bug } from "lucide-react";
import "./Footer.css";


export default function Footer() {
  return (
    <footer className="footer">
      <a className="footer-section footer-bug" href="https://github.com/bkawa-bot/planet-maiko/issues/new" target="_blank" rel="noreferrer" title="Report a bug">
        <Bug size={10} />
        <span>Report Bug</span>
      </a>
    </footer>
  );
}
