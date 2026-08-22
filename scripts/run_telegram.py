"""
Entrypoint: Telegram listener (the phone control surface).

A long-running process (long-polling, no public URL needed) that lets you drive
the bot from your phone:

    /status /positions      view equity, positions, current stops, halt state
    /pending                re-show pending trade proposals (Approve/Deny)
    /buy SYM QTY [--stop N]  place a risk-gated manual buy (with confirm)
    /close SYM               liquidate one position
    /flatten                 liquidate everything (with confirm)
    /halt  /reset            stop everything / clear a HALT
    /run                     run a decision cycle now and push fresh proposals

    Natural language: just type what you want -- parsed by the nl_router agent
      when ANTHROPIC_API_KEY is set, else a local regex parser (graceful fallback)
      e.g. "grab 15 Tesla, tight 8% stop"  ·  "how's my book?"  ·  "stop everything"

    Voice messages: send a voice message; transcribed locally with openai-whisper
      (pip install openai-whisper  -- also needs ffmpeg on PATH)

Approvals and orders are routed to src/core/trade_service, which runs them
through the risk gate before any order is placed. This script holds the Telegram
token (notify) and, as the composition root, constructs the broker/service.

Run it always-on (e.g. a Windows scheduled task "at logon, restart on failure"):
    python -m scripts.run_telegram
"""

from __future__ import annotations

import time

from src.agents.nl import NLCommandParser
from src.common.config import load_config
from src.common.logging import get_logger
from src.common.secrets import load_notification_credentials
from src.core.orchestrator import Orchestrator
from src.core.portfolio_view import positions_snapshot, scoreboard_snapshot
from src.core.proposals import ProposalStore
from src.core.rotation import RotationService
from src.core.symbols import SymbolResolver
from src.core.trade_service import TradeService
from src.data.queries import indicator_snapshot
from src.discovery.builder import build_discovery_pipeline
from src.discovery.ledger import DiscoveryLedger
from src.discovery.pipeline import Account
from src.discovery.scorer import Scorer
from src.discovery.weight_advisor import DiscoveryWeightService
from src.execution.broker_alpaca import build_broker
from src.notify.briefs import strategy_review_brief, symbol_brief
from src.notify.digest import idea_text, ideas_header, source_summary
from src.notify.telegram import TelegramClient, build_notifier

log = get_logger("telegram")

# Company-name → ticker table (used by the local NLP parser; no API needed).
_COMPANY_TO_TICKER: dict[str, str] = {
    "tesla": "TSLA", "apple": "AAPL", "nvidia": "NVDA", "microsoft": "MSFT",
    "amazon": "AMZN", "alphabet": "GOOGL", "google": "GOOGL", "meta": "META",
    "facebook": "META", "netflix": "NFLX", "amd": "AMD", "intel": "INTC",
    "palantir": "PLTR", "coinbase": "COIN", "block": "SQ", "square": "SQ",
    "uber": "UBER", "lyft": "LYFT", "robinhood": "HOOD", "airbnb": "ABNB",
    "snowflake": "SNOW", "salesforce": "CRM", "oracle": "ORCL",
    "jpmorgan": "JPM", "jpm": "JPM", "goldman": "GS", "goldman sachs": "GS",
    "bank of america": "BAC", "berkshire": "BRK.B", "ford": "F",
    "gm": "GM", "general motors": "GM", "boeing": "BA",
    "lockheed": "LMT", "lockheed martin": "LMT",
    "exxon": "XOM", "exxonmobil": "XOM", "chevron": "CVX",
    "walmart": "WMT", "disney": "DIS", "spy": "SPY", "qqq": "QQQ",
}

HELP = (
    "🤖 Trading bot — commands\n"
    "/status — equity, positions, stops, halt state\n"
    "/positions — same as /status\n"
    "/pending — re-show pending proposals\n"
    "/buy SYM QTY [--stop N] — gated manual buy\n"
    "/close SYM — liquidate one position\n"
    "/flatten — liquidate everything\n"
    "/halt — stop all trading\n"
    "/reset — clear a HALT\n"
    "/run — run a decision cycle now\n"
    "/ideas — discover & rank fresh buy ideas (congress + technical)\n"
    "/sources — what each discovery signal is contributing\n"
    "/strategies — scoreboard (verdicts + live P&L)\n"
    "/review — strategy brief to paste into Claude.ai\n"
    "/brief SYM — symbol brief to paste into Claude.ai\n"
    "/rotate <enable|disable|reweight> SYM [w] — propose a rotation\n"
    "/reweight — suggest a discovery source reweighting from the ledger\n\n"
    "💬 Natural language — just type or speak:\n"
    '  "buy 15 Tesla with 8% stop"\n'
    '  "show my positions"\n'
    '  "close my Apple position"\n'
    '  "halt the bot"\n\n'
    "🎤 Voice: hold mic and speak — transcribed locally\n"
    "  (needs: pip install openai-whisper  +  ffmpeg on PATH)"
)


class Listener:
    def __init__(self) -> None:
        self.config = load_config()
        self.creds = load_notification_credentials()
        self.client = TelegramClient(self.creds)
        # No allow_live here: phone orders are paper-only in v1 (README), by
        # construction, not just by convention.
        self.broker = build_broker(self.config)
        # Dynamic symbol resolution: the broker's full asset catalog (cached),
        # with the nickname table as an alias layer on top.
        self.service = TradeService(
            broker=self.broker, config=self.config,
            symbol_resolver=SymbolResolver(self.broker, aliases=_COMPANY_TO_TICKER))
        self.proposals = ProposalStore()
        self._whisper_model = None  # lazy-loaded on first voice message
        # Smart NL parsing via the nl_router agent; the local regex (_parse_nl)
        # is the fallback when no ANTHROPIC_API_KEY is set or the agent errors.
        self.nl = NLCommandParser(fallback=self._parse_nl)
        # Strategy rotation: the analyst proposes, you approve from here. Same
        # rotation state the orchestrator reads, so an approval takes effect next cycle.
        strategy_names = tuple(self.config.strategies.get("strategies", {}).keys()) or (
            "trend_following", "mean_reversion", "breakout")
        # Same config-seeded defaults the orchestrator uses, so guardrails
        # (e.g. "can't disable the last active strategy") see a strategy
        # that ships disabled in config as already inactive, not as a false
        # "still on" that a rotation could silently stack disables on top of.
        strategy_defaults = {
            name: self.config.get(f"strategies.strategies.{name}.enabled", True)
            for name in strategy_names
        }
        self.rotation = RotationService(strategy_names, strategy_defaults=strategy_defaults)
        # Discovery source reweighting: same propose-then-approve shape as
        # rotation above, but the suggestion is computed from the ledger
        # (src/discovery/weight_advisor.py) instead of an analyst. Reuses
        # Scorer's own config parsing so "current weights" here can never
        # drift from what the live pipeline actually scores with.
        _scorer_defaults = Scorer.from_config(self.config)
        self.weight_advisor = DiscoveryWeightService(
            active_sources=_scorer_defaults.active_sources,
            default_weights=_scorer_defaults.weights,
        )

    # --- top-level message routing ---

    def handle_message(self, msg: dict) -> None:
        chat_id = msg.get("chat", {}).get("id")
        from_id = msg.get("from", {}).get("id")
        text = (msg.get("text") or "").strip()

        # Bootstrapping: un-allowlisted users only ever get their chat id on /start.
        if not self.creds.is_allowed(from_id):
            if text.startswith("/start"):
                self._send(chat_id, f"Your chat id is {from_id}.\n"
                           "Add it to TELEGRAM_ALLOWED_CHAT_IDS in .env and restart the listener.")
            else:
                log.warning("ignoring message from un-allowlisted id %s", from_id)
            return

        # Voice message: transcribe first, then treat as natural language.
        if "voice" in msg:
            text = self._transcribe_voice(chat_id, msg["voice"])
            if text is None:
                return  # error already sent

        if not text:
            return

        if text.startswith("/"):
            cmd, _, rest = text.partition(" ")
            self._dispatch(chat_id, cmd.lstrip("/").lower(), rest.strip())
        else:
            self._handle_natural_language(chat_id, text)

    def _dispatch(self, chat_id: int, cmd: str, rest: str) -> None:
        """Route a parsed slash command (or NL-derived command) to the right handler."""
        if cmd in ("start", "help"):
            self._send(chat_id, HELP)
        elif cmd in ("status", "positions"):
            self._send(chat_id, self.service.status().text())
        elif cmd == "pending":
            self._show_pending(chat_id)
        elif cmd == "buy":
            self._buy_confirm(chat_id, rest)
        elif cmd == "close":
            self._send(chat_id, self.service.close(rest.split()[0]).message
                       if rest else "Usage: /close SYM")
        elif cmd == "flatten":
            self._send(chat_id, "Flatten ALL positions?",
                       buttons=[[("⚠️ Confirm flatten", "flatten"), ("Cancel", "cancel")]])
        elif cmd == "halt":
            self._send(chat_id, self.service.halt("via phone").message)
        elif cmd == "reset":
            self._send(chat_id, self.service.reset().message)
        elif cmd == "run":
            self._run_cycle(chat_id)
        elif cmd in ("ideas", "discover"):
            self._run_discovery(chat_id)
        elif cmd == "sources":
            self._send(chat_id, source_summary(DiscoveryLedger().summarize()))
        elif cmd in ("strategies", "scoreboard"):
            self._show_strategies(chat_id)
        elif cmd == "review":
            self._run_review(chat_id)
        elif cmd == "brief":
            self._symbol_brief(chat_id, rest)
        elif cmd == "rotate":
            self._rotate(chat_id, rest)
        elif cmd == "reweight":
            self._reweight(chat_id)
        else:
            self._send(chat_id, f"Unknown command: /{cmd}\n\n{HELP}")

    # --- natural language ---

    def _handle_natural_language(self, chat_id: int, text: str) -> None:
        """Parse free-form text (nl_router agent, regex fallback) and dispatch."""
        parsed = self.nl.parse(text)
        if parsed is None:
            self._send(chat_id, "Couldn't parse that. " + HELP)
            return

        cmd = parsed.get("cmd", "unknown")

        if cmd == "unknown":
            reply = parsed.get("reply", "I didn't understand that.")
            self._send(chat_id, reply + "\n\nUse /help to see all commands.")
            return

        if cmd == "buy":
            sym = (parsed.get("sym") or "").strip().upper()
            try:
                qty = int(parsed["qty"])
            except (KeyError, ValueError, TypeError):
                qty = 0
            stop = float(parsed.get("stop") or 10.0)
            if qty <= 0:
                self._send(chat_id,
                           'Couldn\'t get the quantity. Try: "buy 15 TSLA with 8% stop"')
                return
            # Validate/resolve against the broker's asset catalog -- catches
            # typos, resolves company names, and is honest about non-listings.
            res = self.service.resolve_symbol(sym or parsed.get("raw") or "")
            if res.status == "ambiguous":
                lines = [f"  {s} — {n}" for s, n in res.candidates]
                self._send(chat_id, "Did you mean:\n" + "\n".join(lines)
                           + f'\n\nTry: "buy {qty} <SYMBOL>"')
                return
            if not res.ok:
                self._send(chat_id, res.note or "I couldn't find that symbol.")
                return
            label = f"{res.symbol} ({res.name})" if res.name else res.symbol
            self._send(chat_id, f"Confirm BUY {qty} {label} (stop {stop:g}%)?",
                       buttons=[[("✅ Confirm", f"buy:{res.symbol}:{qty}:{stop}"),
                                 ("Cancel", "cancel")]])

        elif cmd == "close":
            sym = (parsed.get("sym") or "").strip().upper()
            if not sym:
                self._send(chat_id, "Which symbol do you want to close?")
                return
            self._send(chat_id, self.service.close(sym).message)

        elif cmd in ("status", "pending", "halt", "reset", "flatten", "run", "help"):
            self._dispatch(chat_id, cmd, "")

        else:
            self._dispatch(chat_id, cmd, "")

    def _parse_nl(self, text: str) -> dict | None:
        """Parse natural language locally — no API key required.

        Handles the full command vocabulary with regex and a company-name table.
        Returns the same dict shape the rest of the code expects.
        """
        import re
        t = text.lower().strip()

        # --- greetings / smalltalk: answer like a helper, not an error ---
        if re.fullmatch(r"(hey|hi|hello|yo|howdy|sup|hiya|thanks|thank you|ty"
                        r"|good (morning|afternoon|evening))[!. ]*", t):
            return {"cmd": "unknown",
                    "reply": "👋 Hey! I'm your trading bot. Try:\n"
                             '  "show my positions"\n  "buy 10 TSLA with 8% stop"\n'
                             '  "close Apple"\n  "halt the bot"'}

        # --- close / sell (checked BEFORE status so "close my AAPL position" wins) ---
        if (re.search(r"\b(close|sell|exit|liquidate|dump)\s+(all|everything)\b", t)
                or re.search(r"\bflatten\b", t)
                or re.fullmatch(r"\s*liquidate\s*", t)):
            return {"cmd": "flatten"}
        close_m = re.search(
            r"\b(close|sell|exit|get out of|liquidate)\b[^a-z0-9]*([a-z .]+?)(?:\s+position)?\s*$", t
        )
        if close_m:
            sym = _resolve_sym(close_m.group(2).strip())
            if sym:
                return {"cmd": "close", "sym": sym}

        # --- status / info ---
        if re.search(r"\b(status|portfolio|equity|account|balance|show|positions?|holdings?)\b", t):
            return {"cmd": "status"}
        if re.search(r"\bpending\b", t):
            return {"cmd": "pending"}
        if re.search(r"\bhelp\b", t):
            return {"cmd": "help"}

        # --- control ---
        if re.search(r"\b(halt|stop trading|pause|emergency)\b", t):
            return {"cmd": "halt"}
        if re.search(r"\b(reset|resume|clear halt|restart|un.?halt)\b", t):
            return {"cmd": "reset"}
        if re.search(r"\b(run( a)? (cycle|decision|scan)|decide|scan now|run now)\b", t):
            return {"cmd": "run"}

        # --- buy ---
        # patterns: "buy 15 TSLA", "buy Tesla 15", "buy 15 shares of Tesla with 8% stop"
        buy_m = re.search(
            r"\b(buy|purchase|get|pick up|long)\b", t
        )
        if buy_m:
            # extract quantity — first integer in the text
            qty_m = re.search(r"\b(\d+)\b", t)
            qty = int(qty_m.group(1)) if qty_m else 0

            # extract stop-loss % — "8%", "8 percent", "--stop 8", "with 8% stop"
            stop = 10.0
            stop_m = re.search(
                r"(?:stop[- ]?loss|stop|--stop)\s+(?:of\s+)?(\d+(?:\.\d+)?)\s*%?"
                r"|(\d+(?:\.\d+)?)\s*%\s+stop"
                r"|with\s+(?:a\s+)?(\d+(?:\.\d+)?)\s*(?:%|percent)\s+stop",
                t
            )
            if stop_m:
                raw_stop = next(g for g in stop_m.groups() if g is not None)
                stop = float(raw_stop)

            # extract symbol — look for known company names or ticker-shaped words
            # remove the qty and stop numbers so they don't confuse symbol lookup
            cleaned = re.sub(r"\b\d+(?:\.\d+)?\s*%?\b", "", t)
            cleaned = re.sub(r"\b(buy|purchase|get|pick up|long|shares?|of|with|a|an|the|stop|loss|percent|and)\b", "", cleaned)
            sym = _resolve_sym(cleaned.strip())

            if qty <= 0:
                return {"cmd": "unknown", "reply": "How many shares? Try: \"buy 10 TSLA\"."}
            if sym:
                return {"cmd": "buy", "sym": sym, "qty": qty, "stop": stop}
            # No static match: pass the raw words up -- the dispatcher resolves
            # them against the broker's full asset catalog (dynamic path).
            return {"cmd": "buy", "sym": None, "raw": cleaned.strip(),
                    "qty": qty, "stop": stop}

        return {"cmd": "unknown",
                "reply": "I didn't understand that. Try: \"buy 10 TSLA\", \"close AAPL\", \"show status\", or /help."}

    # --- voice transcription ---

    @staticmethod
    def _find_ffmpeg() -> str | None:
        """Return the full path to ffmpeg, or None if not found.

        Checks PATH first, then the WinGet install location as a fallback for
        Windows where new PATH entries aren't visible to the current process.
        """
        import shutil
        import sys
        found = shutil.which("ffmpeg")
        if found:
            return found
        if sys.platform == "win32":
            import glob as _glob
            import os as _os
            winget_base = _os.path.join(_os.path.expanduser("~"),
                                        "AppData", "Local", "Microsoft", "WinGet", "Packages")
            for candidate in _glob.glob(_os.path.join(winget_base, "Gyan.FFmpeg*", "**", "ffmpeg.exe"),
                                        recursive=True):
                return candidate
        return None

    def _transcribe_voice(self, chat_id: int, voice: dict) -> str | None:
        """Download a Telegram voice OGG and transcribe with openai-whisper.
        Returns the transcribed text, or None (error already sent to user).

        On Windows, whisper's internal ffmpeg pipe raises errno 22 on OGG files.
        We avoid this by explicitly converting OGG→WAV via ffmpeg first, then
        handing the WAV directly to whisper (which handles it without extra piping).
        """
        import os
        import subprocess
        import tempfile
        try:
            import whisper as _whisper
        except ImportError:
            self._send(chat_id,
                       "🎤 Voice requires openai-whisper:\n"
                       "  pip install openai-whisper\n"
                       "(ffmpeg must also be on your PATH; first run downloads ~140 MB model)")
            return None

        _ffmpeg_exe = self._find_ffmpeg()
        if _ffmpeg_exe is None:
            self._send(chat_id,
                       "🎤 ffmpeg not found. Install it and add it to PATH:\n"
                       "  winget install Gyan.FFmpeg\n"
                       "Then restart the Telegram listener.")
            return None

        # Guard: the model file must exist and be fully downloaded (>100 MB).
        # If it's missing or 0-byte (corrupt download), tell the user how to fix
        # it rather than crashing with a cryptic errno 22 from PyTorch.
        import os as _os
        _model_path = _os.path.join(_os.path.expanduser("~"), ".cache", "whisper", "base.pt")
        if not (_os.path.exists(_model_path) and _os.path.getsize(_model_path) > 100_000_000):
            self._send(chat_id,
                       "🎤 Whisper model not ready. Run this ONCE in a terminal:\n\n"
                       "  python -c \"import whisper; whisper.load_model('base')\"\n\n"
                       "Then try the voice message again.")
            return None

        self._send(chat_id, "🎤 Transcribing...")
        ogg_path = wav_path = None
        try:
            import requests as _req
            base = self.creds.api_base_url
            token = self.creds.bot_token
            r = _req.get(f"{base}/bot{token}/getFile",
                         params={"file_id": voice["file_id"]}, timeout=10)
            r.raise_for_status()
            file_path = r.json()["result"]["file_path"]

            audio = _req.get(f"{base}/file/bot{token}/{file_path}", timeout=30)
            audio.raise_for_status()

            # Write OGG, convert to 16 kHz mono WAV, then transcribe the WAV.
            # Using mkstemp so Windows never holds an open handle when ffmpeg runs.
            ogg_fd, ogg_path = tempfile.mkstemp(suffix=".ogg")
            wav_fd, wav_path = tempfile.mkstemp(suffix=".wav")
            os.close(ogg_fd)
            os.close(wav_fd)

            with open(ogg_path, "wb") as f:
                f.write(audio.content)

            # Convert OGG → 16 kHz mono WAV via ffmpeg.
            # stderr=DEVNULL (not PIPE): creating a pipe from a process whose own
            # stdio was set up with .NET/PowerShell pipe handles causes errno 22 on
            # Windows. DEVNULL is a real file handle and is always safe.
            import sys as _sys
            _win_flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if _sys.platform == "win32" else {}
            conv = subprocess.run(
                [_ffmpeg_exe, "-y", "-nostdin", "-i", ogg_path,
                 "-ar", "16000", "-ac", "1", "-f", "wav", wav_path],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,   # never PIPE — avoids errno 22
                **_win_flags,
            )
            if conv.returncode != 0 or not os.path.getsize(wav_path):
                raise RuntimeError(f"ffmpeg conversion failed (exit {conv.returncode})")

            if self._whisper_model is None:
                log.info("loading whisper 'base' model from cache")
                # Suppress tqdm so a re-download never fails regardless of how
                # stdio handles were inherited (errno 22 with .NET pipe handles).
                import tqdm as _tqdm_mod
                _real_tqdm = _tqdm_mod.tqdm
                _tqdm_mod.tqdm = lambda *a, **kw: iter(a[0]) if a else iter([])
                try:
                    self._whisper_model = _whisper.load_model("base")
                finally:
                    _tqdm_mod.tqdm = _real_tqdm

            # Load the WAV with Python's wave module and pass a numpy array to
            # transcribe(). When given a file path, whisper calls its own internal
            # ffmpeg subprocess (capture_output=True == stderr=PIPE) which also
            # hits errno 22. Passing an array skips that internal subprocess entirely.
            import wave as _wave

            import numpy as _np
            with _wave.open(wav_path, "rb") as wf:
                frames = wf.readframes(wf.getnframes())
            audio_array = _np.frombuffer(frames, dtype=_np.int16).flatten().astype(_np.float32) / 32768.0

            result = self._whisper_model.transcribe(audio_array, language="en", verbose=False)
            text = result["text"].strip()

        except Exception as exc:
            # Scrub the bot token before logging/sending: transport errors embed
            # the full getFile/download URL, token included.
            detail = str(exc).replace(self.creds.bot_token, "***")
            log.error("voice transcription failed: %s", detail)
            self._send(chat_id, f"Voice transcription failed: {detail}")
            return None
        finally:
            for p in (ogg_path, wav_path):
                if p:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

        if not text:
            self._send(chat_id, "Couldn't hear anything. Try speaking more clearly or typing.")
            return None
        self._send(chat_id, f'🎤 Heard: "{text}"')
        return text

    # --- callback handler ---

    def handle_callback(self, cq: dict) -> None:
        from_id = cq.get("from", {}).get("id")
        data = cq.get("data", "")
        message = cq.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")
        cq_id = cq.get("id")

        if not self.creds.is_allowed(from_id):
            self._answer(cq_id, "Not authorized.")
            return

        try:
            if data.startswith("approve:"):
                self._on_approve(chat_id, message_id, cq_id, data.split(":", 1)[1])
            elif data.startswith("deny:"):
                self._on_deny(chat_id, message_id, cq_id, data.split(":", 1)[1])
            elif data.startswith("rotapprove:"):
                self._on_rotation_approve(chat_id, message_id, cq_id, data.split(":", 1)[1])
            elif data.startswith("rotdeny:"):
                self._on_rotation_deny(chat_id, message_id, cq_id, data.split(":", 1)[1])
            elif data.startswith("wgtapprove:"):
                self._on_weight_approve(chat_id, message_id, cq_id, data.split(":", 1)[1])
            elif data.startswith("wgtdeny:"):
                self._on_weight_deny(chat_id, message_id, cq_id, data.split(":", 1)[1])
            elif data.startswith("buy:"):
                self._on_buy_confirm(chat_id, message_id, cq_id, data)
            elif data == "flatten":
                self._edit(chat_id, message_id, self.service.flatten().message)
                self._answer(cq_id)
            elif data == "cancel":
                self._edit(chat_id, message_id, "Cancelled.")
                self._answer(cq_id)
            else:
                self._answer(cq_id, "Unknown action.")
        except Exception as exc:  # never crash the loop on one bad callback
            log.exception("callback failed: %s", exc)
            self._answer(cq_id, "Error -- check the laptop logs.")

    # --- proposal actions ---

    def _on_approve(self, chat_id, message_id, cq_id, proposal_id: str) -> None:
        p = self.proposals.get(proposal_id)
        if p is None:
            self._answer(cq_id, "Proposal not found.")
            return
        if p.status != "pending":
            self._answer(cq_id, f"Already {p.status}.")
            return
        if p.is_expired():
            self.proposals.mark(proposal_id, "expired")
            self._edit(chat_id, message_id, f"⏰ Expired: {p.summary()}")
            self._answer(cq_id, "Expired.")
            return
        result = self.service.execute_approved(p)
        if result.ok:
            self.proposals.mark(proposal_id, "approved")
            self._edit(chat_id, message_id, f"✅ {result.message}")
            self._answer(cq_id, "Placed.")
        else:
            # Keep it pending (with buttons) so you can retry after fixing state.
            self._answer(cq_id, result.message[:200])

    def _on_deny(self, chat_id, message_id, cq_id, proposal_id: str) -> None:
        p = self.proposals.get(proposal_id)
        if p and p.status == "pending":
            self.proposals.mark(proposal_id, "denied")
        self._edit(chat_id, message_id, f"❌ Denied{(': ' + p.summary()) if p else ''}")
        self._answer(cq_id, "Denied.")

    def _show_pending(self, chat_id) -> None:
        self.proposals.purge_expired()
        pending = self.proposals.list_pending()
        if not pending:
            self._send(chat_id, "No pending proposals.")
            return
        for p in pending:
            self._send(chat_id, f"🟡 {p.summary()}",
                       buttons=[[("✅ Approve", f"approve:{p.id}"), ("❌ Deny", f"deny:{p.id}")]])

    # --- manual buy (with confirm) ---

    def _buy_confirm(self, chat_id, rest: str) -> None:
        parsed = _parse_buy(rest)
        if parsed is None:
            self._send(chat_id, "Usage: /buy SYM QTY [--stop PCT]   e.g. /buy NVDA 20 --stop 8")
            return
        sym, qty, stop = parsed
        res = self.service.resolve_symbol(sym)
        if res.status == "ambiguous":
            lines = [f"  {s} — {n}" for s, n in res.candidates]
            self._send(chat_id, "Did you mean:\n" + "\n".join(lines))
            return
        if not res.ok:
            self._send(chat_id, res.note or f"Unknown symbol {sym}.")
            return
        label = f"{res.symbol} ({res.name})" if res.name else res.symbol
        self._send(chat_id, f"Confirm BUY {qty} {label} (stop {stop:g}%)?",
                   buttons=[[("✅ Confirm", f"buy:{res.symbol}:{qty}:{stop}"), ("Cancel", "cancel")]])

    def _on_buy_confirm(self, chat_id, message_id, cq_id, data: str) -> None:
        _, sym, qty, stop = data.split(":")
        result = self.service.place_manual(sym, int(qty), stop_pct=float(stop))
        self._edit(chat_id, message_id, ("✅ " if result.ok else "⚠️ ") + result.message)
        self._answer(cq_id, "Done." if result.ok else "Rejected.")

    # --- on-demand decision cycle ---

    def _run_cycle(self, chat_id) -> None:
        self._send(chat_id, "Running a decision cycle...")
        orch = Orchestrator(broker=self.broker, config=self.config, propose=True,
                            notifier=build_notifier(self.config))
        report = orch.run_cycle()
        if report.halted:
            self._send(chat_id, f"🛑 HALTED: {report.halt_reason}")
            return
        if not report.proposals:
            self._send(chat_id, "Cycle done -- no setups to propose.")
            return
        for p in report.proposals:
            self.proposals.add(p)
            self._send(chat_id, f"🟡 PROPOSED\n{p.summary()}",
                       buttons=[[("✅ Approve", f"approve:{p.id}"), ("❌ Deny", f"deny:{p.id}")]])

    # --- on-demand discovery (rank fresh buy ideas) ---

    def _run_discovery(self, chat_id) -> None:
        self._send(chat_id, "🔎 Discovering ideas (congress + technical)...")
        self.proposals.purge_expired()
        account_raw = self.broker.get_account()
        account = Account(
            equity=account_raw.equity,
            last_equity=account_raw.last_equity,
            buying_power=account_raw.buying_power,
        )
        positions = self.service.store.load()
        exclude = {p.symbol for p in self.proposals.list_pending()}
        try:
            pipeline = build_discovery_pipeline(self.config)
            report = pipeline.run(account, positions, exclude=exclude)
        except Exception as exc:
            log.exception("discovery failed")
            self._send(chat_id, f"Discovery failed: {exc}")
            return

        DiscoveryLedger().record_surface(report.candidates, report.proposals)
        self._send(chat_id, ideas_header(len(report.proposals), report.screened))
        cand_by_symbol = {c.symbol: c for c in report.candidates}
        for proposal in report.proposals:
            self.proposals.add(proposal)
            cand = cand_by_symbol.get(proposal.symbol)
            text = idea_text(cand) if cand else proposal.summary()
            self._send(chat_id, text,
                       buttons=[[("✅ Approve", f"approve:{proposal.id}"),
                                 ("❌ Deny", f"deny:{proposal.id}")]])

    # --- strategy scoreboard + rotation (analyst proposes, you approve) ---

    def _show_strategies(self, chat_id) -> None:
        rows = scoreboard_snapshot()["strategies"]
        if not rows:
            self._send(chat_id, "No scoreboard yet. Run `python -m scripts.evaluate_strategies` first.")
            return
        lines = ["📊 STRATEGIES"]
        for s in rows:
            live = (f"  · live {s['live_num_trades']}t ${s['live_total_pnl']:+,.0f}"
                    if s["live_num_trades"] else "")
            lines.append(
                f"{s['strategy']}: {s['verdict'].upper()}  "
                f"PSR {s['psr']:.2f} p{s['p_value']:.2f} ({s['num_trades']}t){live}"
            )
        self._send(chat_id, "\n".join(lines))

    def _run_review(self, chat_id) -> None:
        """Emit a strategy brief to paste into Claude.ai (you are the analyst)."""
        self._send(chat_id, strategy_review_brief(scoreboard_snapshot(), positions_snapshot()))

    def _symbol_brief(self, chat_id, rest: str) -> None:
        if not rest.strip():
            self._send(chat_id, "Usage: /brief SYM   e.g. /brief NVDA")
            return
        sym = rest.split()[0].upper()
        ind = indicator_snapshot(sym)
        if "note" in ind:
            self._send(chat_id, f"No cached data for {sym}. Run a cycle or "
                                "`evaluate_strategies` to populate the cache first.")
            return
        self._send(chat_id, symbol_brief(sym, ind, self._score_for_regime(ind.get("regime"))))

    def _score_for_regime(self, regime):
        """The scoreboard row for the strategy this regime routes to, if any."""
        if not regime:
            return None
        strat = self.config.strategies.get("regime_filter", {}).get("routing", {}).get(regime)
        if not strat:
            return None
        return next((s for s in scoreboard_snapshot()["strategies"] if s["strategy"] == strat), None)

    def _rotate(self, chat_id, rest: str) -> None:
        """Propose a rotation (after you've consulted Claude.ai). Approve/Deny inline."""
        parts = rest.split()
        if len(parts) < 2:
            self._send(chat_id, "Usage: /rotate <enable|disable|reweight> STRATEGY [weight]")
            return
        action, strategy = parts[0].lower(), parts[1]
        weight = None
        if action == "reweight":
            if len(parts) < 3:
                self._send(chat_id, "reweight needs a weight 0–1: /rotate reweight breakout 0.5")
                return
            try:
                weight = float(parts[2])
            except ValueError:
                self._send(chat_id, "weight must be a number between 0 and 1")
                return
        res = self.rotation.propose(action, strategy, weight=weight, rationale="via phone")
        if not res["ok"]:
            self._send(chat_id, f"⚠️ {res['error']}")
            return
        self._send(chat_id, f"🟡 ROTATION\n{res['summary']}",
                   buttons=[[("✅ Approve", f"rotapprove:{res['proposal_id']}"),
                             ("❌ Deny", f"rotdeny:{res['proposal_id']}")]])

    def _on_rotation_approve(self, chat_id, message_id, cq_id, proposal_id: str) -> None:
        res = self.rotation.approve(proposal_id)
        if res["ok"]:
            self._edit(chat_id, message_id, f"✅ Applied: {res['summary']}")
            self._answer(cq_id, "Applied.")
        else:
            self._answer(cq_id, res.get("error", "failed")[:200])

    def _on_rotation_deny(self, chat_id, message_id, cq_id, proposal_id: str) -> None:
        self.rotation.deny(proposal_id)
        self._edit(chat_id, message_id, "❌ Rotation denied.")
        self._answer(cq_id, "Denied.")

    # --- discovery source reweighting (ledger-driven, you approve) ---

    def _reweight(self, chat_id) -> None:
        """Compute a suggested discovery-source reweighting from the ledger
        and, if there's anything meaningful, push it Approve/Deny -- never
        applies on its own (src/discovery/weight_advisor.py)."""
        res = self.weight_advisor.suggest()
        if not res["ok"]:
            self._send(chat_id, f"No reweighting suggested right now ({res['error']}).")
            return
        self._send(chat_id, f"🟡 REWEIGHT SOURCES\n{res['summary']}",
                   buttons=[[("✅ Approve", f"wgtapprove:{res['proposal_id']}"),
                             ("❌ Deny", f"wgtdeny:{res['proposal_id']}")]])

    def _on_weight_approve(self, chat_id, message_id, cq_id, proposal_id: str) -> None:
        res = self.weight_advisor.approve(proposal_id)
        if res["ok"]:
            self._edit(chat_id, message_id, f"✅ Applied: {res['summary']}")
            self._answer(cq_id, "Applied.")
        else:
            self._answer(cq_id, res.get("error", "failed")[:200])

    def _on_weight_deny(self, chat_id, message_id, cq_id, proposal_id: str) -> None:
        self.weight_advisor.deny(proposal_id)
        self._edit(chat_id, message_id, "❌ Reweighting denied.")
        self._answer(cq_id, "Denied.")

    # --- telegram helpers (best-effort) ---

    def _send(self, chat_id, text, buttons=None) -> None:
        try:
            self.client.send_message(chat_id, text, buttons)
        except Exception as exc:
            log.warning("send failed: %s", exc)

    def _edit(self, chat_id, message_id, text, buttons=None) -> None:
        try:
            self.client.edit_message(chat_id, message_id, text, buttons)
        except Exception as exc:
            log.warning("edit failed: %s", exc)

    def _answer(self, cq_id, text=None) -> None:
        try:
            self.client.answer_callback(cq_id, text)
        except Exception as exc:
            log.warning("answer failed: %s", exc)

    # --- poll loop ---

    def _beat(self) -> None:
        """Heartbeat for the watchdog (scripts/healthcheck.py): proves the
        listener loop is alive. Best-effort -- never let it affect polling."""
        try:
            from datetime import datetime, timezone
            from pathlib import Path

            from src.common.jsonio import atomic_write_json
            atomic_write_json(
                Path(__file__).resolve().parents[1] / "state" / "listener_heartbeat.json",
                {"ts": datetime.now(timezone.utc).isoformat()},
            )
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("heartbeat write failed: %s", exc)

    def run(self) -> None:
        # Skip any backlog so a restart never re-acts on stale taps.
        offset = self._drain()
        log.info("listener started (allowlist: %s)", list(self.creds.allowed_chat_ids))
        for chat_id in self.creds.allowed_chat_ids:
            self._send(chat_id, "🟢 Listener online — ready for commands.")
        while True:
            self._beat()
            try:
                updates = self.client.get_updates(offset=offset, timeout=25)
            except Exception as exc:
                log.warning("getUpdates failed: %s", exc)
                time.sleep(3)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    if "message" in update:
                        self.handle_message(update["message"])
                    elif "callback_query" in update:
                        self.handle_callback(update["callback_query"])
                except Exception as exc:
                    log.exception("update handling failed: %s", exc)

    def _drain(self) -> int | None:
        try:
            updates = self.client.get_updates(timeout=0)
        except Exception:
            return None
        return (updates[-1]["update_id"] + 1) if updates else None


def _resolve_sym(text: str) -> str | None:
    """Extract a ticker from free-form text.

    Tries company-name lookup first (longest match wins), then falls back to
    a bare uppercase ticker-shaped token (1–5 letters, optionally with a dot).
    Returns None if nothing plausible is found.
    """
    import re
    t = text.lower().strip()

    # Longest-match company name lookup.
    best = ""
    best_ticker = ""
    for name, ticker in _COMPANY_TO_TICKER.items():
        if name in t and len(name) > len(best):
            best, best_ticker = name, ticker
    if best_ticker:
        return best_ticker

    # Bare ticker: all-caps word 1-5 chars, optional .B/.A suffix.
    m = re.search(r"\b([A-Z]{1,5}(?:\.[A-Z])?)\b", text.upper())
    return m.group(1) if m else None


def _parse_buy(rest: str):
    """Parse '/buy' args -> (SYM, QTY, STOP_PCT) or None. Accepts
    'SYM QTY', 'SYM QTY --stop N', or 'SYM QTY N'."""
    tokens = rest.split()
    if len(tokens) < 2:
        return None
    sym = tokens[0].upper()
    try:
        qty = int(tokens[1])
    except ValueError:
        return None
    if qty <= 0:
        return None
    stop = 10.0
    if "--stop" in tokens:
        i = tokens.index("--stop")
        if i + 1 < len(tokens):
            try:
                stop = float(tokens[i + 1])
            except ValueError:
                return None
    elif len(tokens) >= 3:
        try:
            stop = float(tokens[2])
        except ValueError:
            pass
    if stop <= 0:
        return None
    return sym, qty, stop


def main() -> None:
    creds = load_notification_credentials()
    if not creds.configured:
        print("TELEGRAM_BOT_TOKEN is not set. Add it to .env (see .env.example).")
        return
    if not creds.allowed_chat_ids:
        print("TELEGRAM_ALLOWED_CHAT_IDS is empty -- the listener will only reveal "
              "your chat id on /start. Message the bot /start, add the id, then restart.")
    Listener().run()


if __name__ == "__main__":
    main()
