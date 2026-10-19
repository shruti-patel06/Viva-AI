"use client";

import { useEffect, useRef, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";

// The most the speech service takes in one request. A longer answer is recorded in parts.
const MAX_SECONDS = 30;
// What each browser can record, best first: Chrome and Edge record WebM, Safari MP4.
const FORMATS = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];

type Phase = "idle" | "recording" | "transcribing";

// Browsers allow the microphone only on https or localhost, and older ones have no recorder.
export function canRecord(): boolean {
  return typeof window !== "undefined" && window.isSecureContext && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined";
}

// Records one part of a spoken answer and hands back what the speech service heard. Every
// failure ends the same way: a message, and the answer box is still there to type in.
export function Recorder({
  sessionId,
  position,
  disabled,
  onHeard,
}: {
  sessionId: string;
  position: number;
  disabled: boolean;
  onHeard: (text: string) => void;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);

  // Stop the clock at the limit, and release the microphone if the question changes mid-recording.
  useEffect(() => {
    if (phase !== "recording") return;
    const started = Date.now();
    const timer = setInterval(() => {
      const seconds = Math.floor((Date.now() - started) / 1000);
      setElapsed(seconds);
      if (seconds >= MAX_SECONDS) recorder.current?.stop();
    }, 250);
    return () => clearInterval(timer);
  }, [phase]);

  useEffect(() => () => recorder.current?.stream.getTracks().forEach((t) => t.stop()), []);

  async function start() {
    setProblem(null);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setProblem("The microphone is blocked or missing. Type your answer instead.");
      return;
    }
    const mimeType = FORMATS.find((f) => MediaRecorder.isTypeSupported(f));
    const media = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks: Blob[] = [];
    media.ondataavailable = (e) => chunks.push(e.data);
    media.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      void transcribe(new Blob(chunks, { type: media.mimeType }));
    };
    recorder.current = media;
    media.start();
    setElapsed(0);
    setPhase("recording");
  }

  async function transcribe(recording: Blob) {
    setPhase("transcribing");
    const body = new FormData();
    body.append("position", String(position));
    body.append("recording", recording, "answer");
    try {
      const response = await apiFetch(`/sessions/${sessionId}/transcribe`, { method: "POST", body });
      if (!response.ok) {
        setProblem(await errorMessage(response));
        return;
      }
      const { transcript } = await response.json();
      if (transcript) onHeard(transcript);
      else setProblem("Nothing was heard. Try again closer to the microphone, or type your answer.");
    } catch {
      setProblem("Could not reach the server. Type your answer instead, or try recording again.");
    } finally {
      setPhase("idle");
    }
  }

  return (
    <div className="space-y-1">
      {phase === "recording" ? (
        <button
          type="button"
          onClick={() => recorder.current?.stop()}
          className="rounded-md bg-red-600 px-3 py-2 text-white"
        >
          ■ Stop ({MAX_SECONDS - elapsed}s left)
        </button>
      ) : (
        <button
          type="button"
          disabled={disabled || phase === "transcribing"}
          onClick={start}
          className="rounded-md border border-zinc-300 px-3 py-2 disabled:opacity-50"
        >
          {phase === "transcribing" ? "Turning speech into text…" : "🎤 Speak your answer"}
        </button>
      )}
      {problem && <p className="text-sm text-amber-800">{problem}</p>}
    </div>
  );
}
