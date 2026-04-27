import { useState, useEffect } from "react";
import { api } from "../api/client";

// Module-level cache so every <CardAvatar /> doesn't re-fetch /cards
// on mount. Cards rarely change at runtime; refresh on full reload.
let _cardsCache = null;
let _cardsPromise = null;

function fetchOnce() {
  if (_cardsCache) return Promise.resolve(_cardsCache);
  if (!_cardsPromise) {
    _cardsPromise = api
      .getCards()
      .then((data) => {
        _cardsCache = Array.isArray(data) ? data : [];
        return _cardsCache;
      })
      .catch(() => {
        _cardsCache = [];
        return _cardsCache;
      });
  }
  return _cardsPromise;
}

export function useCards() {
  const [cards, setCards] = useState(_cardsCache || []);
  useEffect(() => {
    if (_cardsCache) return;
    let cancelled = false;
    fetchOnce().then((data) => {
      if (!cancelled) setCards(data);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return cards;
}

export function getCardSync(cardId) {
  if (!_cardsCache || !cardId) return null;
  return _cardsCache.find((c) => c.id === cardId) || null;
}
