import React, { useEffect, useMemo, useState } from "react";
import { createClient } from "@supabase/supabase-js";
import { BrowserRouter, Routes, Route, Link, useNavigate } from "react-router-dom";

/* ===== Supabase (public bucket) ===== */
const SUPABASE_URL = (import.meta.env.VITE_SUPABASE_URL as string).trim();
const SUPABASE_ANON_KEY = (import.meta.env.VITE_SUPABASE_ANON_KEY as string).trim();
const VIDEO_BUCKET = ((import.meta.env.VITE_VIDEO_BUCKET as string) || "videos").trim();
const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

/* ===== Small helpers ===== */
function ImageWithFallback({
  src,
  alt,
  className,
}: {
  src: string;
  alt: string;
  className?: string;
}) {
  const [current, setCurrent] = useState(src);
  return (
    <img
      src={current}
      alt={alt}
      className={className}
      onError={() => {
        // if .jpg fails, try .png with same base name
        if (current.toLowerCase().endsWith(".jpg")) {
          setCurrent(current.replace(/\.jpg$/i, ".png"));
        }
      }}
    />
  );
}

/* ===== Shared UI ===== */
function Header() {
  return (
    <header className="flex items-center justify-center py-6">
      <Link to="/" className="block">
        <img
          src="/logo.png"
          alt="Shepherd's Desk"
          className="
            rounded-3xl border-2 border-black/50 p-3 bg-white/60 backdrop-blur shadow-md
            h-40 w-40            /* ~10rem on phones */
            sm:h-56 sm:w-56      /* ~14rem on small screens */
            md:h-64 md:w-64      /* ~16rem on tablets/desktops */
            xl:h-72 xl:w-72      /* ~18rem on large screens */
          "
        />
      </Link>
    </header>
  );
}


function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <Header />
      <main className="px-4 md:px-8 pb-16">{children}</main>
    </div>
  );
}

function Card({
  children,
  onClick,
}: {
  children: React.ReactNode;
  onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="rounded-3xl shadow-xl bg-white p-4 transition-transform duration-200 hover:scale-105"
    >
      {children}
    </button>
  );
}

/* ===== Landing ===== */
function Landing() {
  const navigate = useNavigate();
  return (
    <Shell>
      <div className="flex flex-col items-center gap-10 mt-2">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-10 w-full max-w-5xl">
          <Card onClick={() => navigate("/watch")}>
            <div className="w-full">
              <div className="w-full aspect-square overflow-hidden rounded-2xl p-2">
                <ImageWithFallback
                  src="/watch-thumb.jpg"
                  alt="Watch"
                  className="h-full w-full object-cover rounded-xl"
                />
              </div>
              <div className="text-center text-2xl font-black mt-1">
                Watch Videos
              </div>
            </div>
          </Card>

          <Card onClick={() => navigate("/create")}>
            <div className="w-full">
              <div className="w-full aspect-square overflow-hidden rounded-2xl p-2">
                <ImageWithFallback
                  src="/create-thumb.jpg"
                  alt="Create"
                  className="h-full w-full object-cover rounded-xl"
                />
              </div>
              <div className="text-center text-2xl font-black mt-1">
                Create Videos
              </div>
            </div>
          </Card>
        </div>
      </div>
    </Shell>
  );
}

/* ===== Create ===== */
const STYLES = [
  { id: "storybook", label: "Storybook", img: "/style-storybook.jpg" },
  { id: "painting", label: "Painting", img: "/style-painting.jpg" },
  { id: "realistic", label: "Realistic", img: "/style-realistic.jpg" },
];

function CreateVideo() {
  const [style, setStyle] = useState(STYLES[0].id);
  const [language, setLanguage] = useState("English");
  const [topic, setTopic] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const navigate = useNavigate();

  async function startJob() {
    setBusy(true);
    setMessage(null);
    try {
      const resp = await fetch("/api/create-video", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ style, language, topic }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json().catch(() => ({}));
      setMessage(data?.message || "Video created.");
      navigate("/watch");
    } catch (err: any) {
      setMessage(`Failed to start job: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell>
      <div className="max-w-5xl mx-auto">
        <h1 className="text-center text-4xl font-extrabold mb-6">
          Create a video
        </h1>

        <div className="rounded-3xl bg-white p-4 md:p-6 shadow-xl">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {STYLES.map((s) => (
              <button
                key={s.id}
                onClick={() => setStyle(s.id)}
                className={`rounded-2xl border-4 transition-all duration-200 hover:scale-105 ${
                  style === s.id ? "border-black" : "border-black/20"
                }`}
              >
                <div className="w-full aspect-square overflow-hidden rounded-xl p-2">
                  <ImageWithFallback
                    src={s.img}
                    alt={s.label}
                    className="h-full w-full object-cover rounded-lg"
                  />
                </div>
                <div className="text-center font-bold text-lg py-2">
                  {s.label}
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="mt-8 grid gap-4 md:grid-cols-[1fr_320px]">
          <textarea
            className="w-full h-36 p-4 rounded-2xl bg-white shadow-xl"
            placeholder="Describe the topic, scripture, or prompt"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
          />
          <div className="rounded-2xl bg-white shadow-xl p-4">
            <label className="block text-sm font-semibold">Language</label>
            <select
              className="mt-2 w-full rounded-xl border p-2"
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
            >
              {["English", "Japanese", "Spanish"].map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
            <button
              onClick={startJob}
              disabled={busy}
              className="mt-4 w-full rounded-2xl bg-black text-white py-3 font-bold"
            >
              {busy ? "Starting..." : "Create Video"}
            </button>
            {message && <div className="mt-3 text-sm">{message}</div>}
          </div>
        </div>
      </div>
    </Shell>
  );
}

/* ===== Watch (public list) ===== */
function useSupabasePublicVideoUrls() {
  const [urls, setUrls] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        const { data, error } = await supabase.storage
          .from(VIDEO_BUCKET)
          .list("", { limit: 200, sortBy: { column: "updated_at", order: "desc" } });
        if (error) throw error;

        const out: string[] = [];
        for (const f of data || []) {
          if (!/\.(mp4|webm|mov|m4v)$/i.test(f.name)) continue;
          const { data: pub } = supabase.storage.from(VIDEO_BUCKET).getPublicUrl(f.name);
          if (pub?.publicUrl) out.push(pub.publicUrl);
        }
        if (mounted) setUrls(out);
      } catch (e: any) {
        if (mounted) {
          setErr(e?.message || "Failed to list");
          setUrls([]);
        }
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  return { urls, loading, err };
}

function Watch() {
  const { urls, loading, err } = useSupabasePublicVideoUrls();
  const [idx, setIdx] = useState(0);

  const title = useMemo(() => {
    const u = urls[idx];
    if (!u) return "";
    try {
      const last = decodeURIComponent(new URL(u).pathname.split("/").pop() || "");
      return last.replace(/\?.*$/, "").replace(/\.[a-z0-9]+$/i, "").replace(/[-_]+/g, " ").trim();
    } catch {
      return "";
    }
  }, [urls, idx]);

  useEffect(() => {
    if (idx >= urls.length) setIdx(0);
  }, [urls, idx]);

  const next = () => setIdx((i) => (i + 1) % Math.max(1, urls.length));
  const prev = () => setIdx((i) => (i - 1 + Math.max(1, urls.length)) % Math.max(1, urls.length));

  return (
    <Shell>
      <div className="max-w-3xl mx-auto">
        <h1 className="text-center text-4xl font-extrabold mb-6">Watch</h1>
        {loading && <div className="text-center">Loading…</div>}
        {err && <div className="text-center text-red-600">{err}</div>}
        {!loading && !err && urls.length === 0 && <div className="text-center">No videos yet.</div>}
        {urls.length > 0 && (
          <div className="relative">
            <video
              key={urls[idx]}
              className="w-full rounded-3xl shadow-xl bg-black"
              src={urls[idx]}
              controls
              autoPlay
              onEnded={() => setIdx((i) => (i + 1) % urls.length)}
            />
            <button
              onClick={prev}
              className="absolute left-2 top-1/2 -translate-y-1/2 bg-white/80 rounded-full h-12 w-12 grid place-content-center text-xl font-bold"
              aria-label="Previous"
            >
              ‹
            </button>
            <button
              onClick={next}
              className="absolute right-2 top-1/2 -translate-y-1/2 bg-white/80 rounded-full h-12 w-12 grid place-content-center text-xl font-bold"
              aria-label="Next"
            >
              ›
            </button>
            <div className="text-center mt-3 text-base font-semibold">
              {title || `Video ${idx + 1}`}{" "}
              <span className="text-slate-500">
                ({idx + 1} / {urls.length})
              </span>
            </div>
          </div>
        )}
      </div>
    </Shell>
  );
}

/* ===== App ===== */
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/create" element={<CreateVideo />} />
        <Route path="/watch" element={<Watch />} />
      </Routes>
    </BrowserRouter>
  );
}
