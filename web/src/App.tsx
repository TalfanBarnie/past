import { useEffect, useRef, useState } from "react";
import { Cards } from "./Cards";
import { loadIndexes, STEP } from "./data";
import { MapView } from "./MapView";
import { getState, readHash, setState, setTime, useStore } from "./store";
import { Timeline } from "./Timeline";

const SPLIT_KEY = "past-split";

function storedSplit(): number {
  try {
    const value = Number(localStorage.getItem(SPLIT_KEY));
    return value >= 20 && value <= 80 ? value : 50;
  } catch {
    return 50;
  }
}

export function App() {
  const error = useStore((s) => s.error);
  const ready = useStore((s) => s.stations !== null);
  const [split, setSplit] = useState(storedSplit);
  const dragging = useRef(false);
  const body = useRef<HTMLDivElement>(null);

  useEffect(() => {
    readHash();
    loadIndexes().then(
      ({ stations, tropomi }) => {
        const known = new Set(stations.stations.map((s) => s.id));
        setState({ stations, tropomi, cards: getState().cards.filter((c) => known.has(c)) });
      },
      (e) => setState({ error: String(e) }),
    );
    const key = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "SELECT" || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
      e.preventDefault();
      setTime(getState().time + (e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 6 : 1) * STEP);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);

  useEffect(() => {
    const move = (e: PointerEvent) => {
      if (!dragging.current || !body.current) return;
      const box = body.current.getBoundingClientRect();
      setSplit(Math.min(80, Math.max(20, ((e.clientX - box.left) / box.width) * 100)));
    };
    const stop = () => {
      if (!dragging.current) return;
      dragging.current = false;
      document.body.classList.remove("resizing");
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(SPLIT_KEY, String(Math.round(split)));
    } catch {
      /* the split is just not remembered */
    }
  }, [split]);

  return (
    <div className="app">
      <header>
        <h1>Reykjanes eruptions: SO₂ at ground stations and from TROPOMI</h1>
        <Timeline />
      </header>
      {error && <div className="error">Could not load the data: {error}</div>}
      {!error && !ready && <div className="loading">Loading…</div>}
      <div className="body" ref={body} style={{ gridTemplateColumns: `${split}% 8px 1fr` }}>
        <MapView />
        <div
          className="divider"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize the map and the graphs"
          onPointerDown={(e) => {
            dragging.current = true;
            document.body.classList.add("resizing");
            e.preventDefault();
          }}
        />
        <Cards />
      </div>
    </div>
  );
}
