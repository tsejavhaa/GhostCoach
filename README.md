# 🎯 GhostCoach (Interview Assistant)

A macOS desktop app that helps you during technical and behavioural job interviews in real time.  
It captures a remote screen, extracts text via OCR, and generates polished answers or clean code solutions using a local or cloud LLM.

---

## Architecture

```
Computer A (iMac — runs this app)          Computer B (MacBook — the interview screen)
┌────────────────────────────────┐         ┌──────────────────────────────────┐
│  main.py  (CustomTkinter UI)   │◄──HTTP──│  screenshot_http_server.py       │
│  ├── vnc_capture.py            │         │  (serves /screenshot and /health) │
│  ├── local_window_capture.py   │         └──────────────────────────────────┘
│  ├── webcam_capture.py         │
│  ├── ocr_module.py             │
│  ├── llm_client.py             │
│  ├── interview_history.py      │
│  └── interview_history_view.py │
└────────────────────────────────┘
```

Two screen-source modes are supported:

| Mode | How it works |
|---|---|
| **MacBook Server (HTTP)** | `screenshot_http_server.py` runs on Computer B and serves PNG/JPEG screenshots over HTTP. Computer A fetches them via `vnc_capture.py`. |
| **Screen Sharing Window (Local)** | A macOS Screen Sharing / VNC viewer is already open on Computer A. `local_window_capture.py` captures that window directly using Quartz. |

---

## Features

- **Live screen feed** — continuous frame polling from Computer B or a local Screen Sharing window  
- **Region OCR** — drag to select any rectangle on the captured screen; Tesseract extracts the text  
- **Interview answer generation** — streams a first-person, bullet-pointed answer via Ollama or OpenAI  
- **Coding problem solver** — two-pass approach: draft → self-review, returns clean Python (or other language) code  
- **Candidate context** — paste your resume/skills so answers are personalised  
- **Interview history** — every Q&A is saved to both JSONL and Markdown for easy review  
- **Webcam viewer** — optional live webcam feed in a separate tab  
- **Adjustable text size** — slider in Settings  
- **Persistent settings** — server credentials, LLM config, and context survive restarts  

---

## Requirements

### macOS (Computer A — iMac running the app)

| Requirement | Notes |
|---|---|
| macOS 12 Monterey or newer | Quartz / AppKit APIs required |
| Python 3.11+ | Tested on 3.11 and 3.12 |
| Tesseract OCR | `brew install tesseract` |
| Ollama (optional) | `brew install ollama` then `ollama serve` |

### macOS (Computer B — MacBook running the screenshot server)

| Requirement | Notes |
|---|---|
| Python 3.9+ | Standard library only + Pillow |
| Screen Recording permission | System Settings → Privacy & Security → Screen Recording → allow Python / Terminal |

---

## Installation

### Computer A (iMac)

```bash
# 1. Clone or copy the project
cd interview-assistant

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install Tesseract (if not already present)
brew install tesseract

# 5. Run
python main.py
```

### Computer B (MacBook)

```bash
# Only Pillow is needed on the server side
pip install Pillow

# Start the screenshot server
python screenshot_http_server.py --host 0.0.0.0 --port 8765 --request-access
```

> **Firewall note:** Make sure macOS Firewall on Computer B allows incoming connections to Python / Terminal on port 8765.

---

## Configuration

All settings are stored in `interview_assistant_settings.json` (auto-created in the app folder).

### Screen source
Go to **Settings → Screen Source** and choose:
- `MacBook Server (HTTP)` — enter Computer B's LAN IP and port (default `8765`)
- `Screen Sharing Window (Local)` — click **Scan Windows**, pick the Screen Sharing window, then **Use Selected Window**

### LLM provider
- **Local (Ollama):** start `ollama serve`, then click **Refresh Models**. Recommended models: `llama3.2:3b`, `qwen3.2`, `phi4-mini`, `gemma2:2b`
- **OpenAI API:** enter your model name (e.g. `gpt-4o-mini`) and API key

### Candidate context
Paste your resume summary, job description, or key skills in **Settings → Your Context**.  
The LLM uses this to personalise interview answers. Leave blank to get generic answers.

---

## Usage

1. **Connect** — click **Connect** in Settings (HTTP mode) or **Use Selected Window** (local mode)
2. **Capture** — click **📷 Capture Screen** or **▶ Start Live** for continuous updates
3. **Select region** — drag on the screen canvas to highlight the question text
4. **OCR** — click **🔍 OCR Selection**; edit the extracted text if needed
5. **Generate** — click **✨ Generate Answer** or **⚡ Generate Code**
6. **Copy** — use **📋 Copy** to copy the answer to the clipboard

All Q&A pairs are automatically saved to **🗂️ Saved Interviews**.

---

## Project Structure

```
interview-assistant/
├── main.py                          # Main UI application (CustomTkinter)
├── screenshot_http_server.py        # Standalone server — runs on Computer B
├── modules/
│   ├── vnc_capture.py               # HTTP screenshot client (fetches from Computer B)
│   ├── local_window_capture.py      # Quartz-based local window capture
│   ├── webcam_capture.py            # OpenCV webcam capture
│   ├── ocr_module.py                # Tesseract OCR wrapper
│   ├── llm_client.py                # Ollama / OpenAI LLM wrapper
│   ├── interview_history.py         # JSONL + Markdown persistence
│   └── interview_history_view.py    # History tab UI component
├── interview_assistant_settings.json   # Auto-generated; gitignore this
├── interview_session_log.md            # Auto-generated Markdown log
├── interview_history.jsonl             # Auto-generated JSONL log
├── requirements.txt
└── README.md
```

> **Note:** `audio_stt.py` (`modules/audio_stt.py`) is included in the repo but is not yet wired into the main application. It provides live microphone transcription via `faster-whisper` for a future "listen and auto-answer" feature.

---

## Generated Files

| File | Description |
|---|---|
| `interview_assistant_settings.json` | Saved settings (server, LLM, context, UI). Add to `.gitignore`. |
| `interview_session_log.md` | Human-readable Markdown log of all Q&A sessions |
| `interview_history.jsonl` | Machine-readable JSONL log; used for the Saved Interviews tab |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Screenshots show only wallpaper | Grant Screen Recording to Python/Terminal on Computer B, then restart the server |
| `ollama chat` timeout | Run `ollama serve` and wait for the model to load; use `llama3.2:3b` for speed |
| OCR text is garbled | Select a tighter region; avoid including UI chrome; increase contrast in source app |
| Camera not found | Go to Settings → Webcam → Scan; try index 1 for an external camera |
| `pyobjc` import errors | Run `pip install pyobjc-framework-Quartz pyobjc-framework-AppKit` |

### `ModuleNotFoundError: No module named '_tkinter'`

This means your Python was compiled without Tk support. It affects both pyenv-installed and some Homebrew Pythons. The recommended fix is to use Homebrew's Python 3.11 with its Tk package explicitly installed.

**Option A — Homebrew Python 3.11 (recommended, simplest)**

```bash
# Install Python 3.11 and its Tk support
brew install python@3.11
brew install python-tk@3.11

# Verify Tk works — a small test window should appear
/opt/homebrew/bin/python3.11 -c "import tkinter; tkinter._test()"

# Recreate the virtualenv with that Python
cd /path/to/GhostCoach
rm -rf .venv
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python main.py
```

**Option B — Recompile pyenv Python with Tk linked**

Use this if you want to keep using pyenv with a pinned Python version.

```bash
# Install Tcl/Tk first
brew install tcl-tk

# Reinstall Python 3.11 in pyenv with Tk flags
env \
  LDFLAGS="-L$(brew --prefix tcl-tk)/lib" \
  CPPFLAGS="-I$(brew --prefix tcl-tk)/include" \
  PKG_CONFIG_PATH="$(brew --prefix tcl-tk)/lib/pkgconfig" \
  PYTHON_CONFIGURE_OPTS="--with-tcltk-includes='-I$(brew --prefix tcl-tk)/include' \
    --with-tcltk-libs='-L$(brew --prefix tcl-tk)/lib -ltcl8.6 -ltk8.6'" \
  pyenv install 3.11.10 --force

# Verify
python -c "import tkinter; tkinter._test()"

# Recreate the virtualenv
rm -rf .venv
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## License

MIT