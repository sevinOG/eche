"""
Eche Theme - matched to original Eche GUI (gui/theme.py)
Original palette:
  BG #14151a, BG_ELEVATED #1c1d24, BG_INPUT #12131a, BORDER #2a2b34, TEXT #e8e8ec
  ACCENT #3d5a80 (now used), with violet glow as optional highlight for installer

Installer enhances with Eche's elite tactical vibe while staying faithful.
"""

# Original Eche palette (from gui/theme.py)
ECHE_ORIGINAL = {
    "bg": "#14151a",
    "bg_elevated": "#1c1d24",
    "bg_input": "#12131a",
    "border": "#2a2b34",
    "border_focus": "#4a6fa5",
    "text": "#e8e8ec",
    "text_muted": "#8b8c9a",
    "text_dim": "#6e6f7c",
    "text_bright": "#f5f5fa",
    "accent": "#3d5a80",
    "accent_hover": "#4a6fa5",
}

# Installer palette - faithful to Eche but enhanced for wizard clarity
ECHE_PALETTE = {
    "bg": "#0B0E14",              # deeper than original #14151a for installer contrast
    "bg_alt": "#14151a",          # original BG as alt
    "surface": "#1c1d24",         # original BG_ELEVATED
    "surface_2": "#252730",       # slightly lighter
    "surface_3": "#2a2b34",       # original BORDER as surface
    "border": "#2a2b34",          # original
    "border_light": "#3a3c48",
    "text": "#e8e8ec",            # original TEXT
    "text_muted": "#8b8c9a",      # original muted
    "text_dim": "#6e6f7c",
    "accent": "#8B5CF6",          # Dark purple accent
    "accent_hover": "#7C3AED",    # Dark purple hover
    "accent_glow": "rgba(139,92,246,0.3)",
    "accent_violet": "#8B5CF6",   # installer enhancement (optional highlight)
    "secondary": "#5a6fa5",
    "secondary_hover": "#4a6fa5",
    "success": "#3d7a5a",
    "success_bg": "#2a4a3a",
    "warning": "#F59E0B",
    "danger": "#6b3040",
    "danger_bg": "#3a2228",
    "danger_text": "#f0c0c8",
    "danger_hover": "#4a2a32",
}

STYLESHEET = f"""
QWidget {{
    background-color: {ECHE_PALETTE['bg']};
    color: {ECHE_PALETTE['text']};
    font-family: 'Segoe UI', 'JetBrains Mono', 'Consolas', monospace;
    font-size: 13px;
}}

QMainWindow {{
    background-color: {ECHE_PALETTE['bg']};
    border: 1px solid {ECHE_PALETTE['border']};
}}

QLabel#TitleLabel {{
    font-size: 26px;
    font-weight: 800;
    letter-spacing: 2px;
    color: {ECHE_PALETTE['text']};
}}

QLabel#SubtitleLabel {{
    font-size: 10px;
    color: {ECHE_PALETTE['text_muted']};
    letter-spacing: 2px;
    text-transform: uppercase;
}}

QLabel#SectionLabel {{
    font-size: 13px;
    font-weight: 700;
    color: {ECHE_PALETTE['accent']};
    letter-spacing: 0.5px;
    margin-top: 8px;
}}

QLabel#MutedLabel {{
    color: {ECHE_PALETTE['text_muted']};
    font-size: 12px;
}}

QLabel#PathLabel {{
    background-color: {ECHE_PALETTE['surface']};
    border: 1px solid {ECHE_PALETTE['border']};
    border-radius: 8px;
    padding: 10px 14px;
    color: {ECHE_PALETTE['text']};
    font-size: 12px;
}}

QLineEdit {{
    background-color: {ECHE_ORIGINAL['bg_input']};
    border: 1px solid {ECHE_PALETTE['border']};
    border-radius: 8px;
    padding: 10px 14px;
    color: {ECHE_PALETTE['text']};
    selection-background-color: {ECHE_PALETTE['accent']};
}}
QLineEdit:focus {{
    border: 1px solid {ECHE_PALETTE['border_light']};
    background-color: {ECHE_PALETTE['surface']};
}}

QPushButton {{
    border-radius: 8px;
    padding: 10px 20px;
    font-weight: 600;
    font-size: 12px;
    letter-spacing: 0.3px;
}}

QPushButton#PrimaryButton {{
    background-color: {ECHE_PALETTE['accent']};
    color: {ECHE_PALETTE['text']};
    border: 1px solid {ECHE_PALETTE['accent_hover']};
}}
QPushButton#PrimaryButton:hover {{
    background-color: {ECHE_PALETTE['accent_hover']};
    color: {ECHE_PALETTE['text']};
}}
QPushButton#PrimaryButton:disabled {{
    background-color: {ECHE_PALETTE['surface_2']};
    color: {ECHE_PALETTE['text_dim']};
}}

QPushButton#SecondaryButton {{
    background-color: {ECHE_PALETTE['surface']};
    color: {ECHE_PALETTE['text']};
    border: 1px solid {ECHE_PALETTE['border']};
}}
QPushButton#SecondaryButton:hover {{
    background-color: {ECHE_PALETTE['surface_2']};
    border: 1px solid {ECHE_PALETTE['border_light']};
}}

QPushButton#GhostButton {{
    background-color: transparent;
    color: {ECHE_PALETTE['text_muted']};
    border: 1px solid transparent;
}}
QPushButton#GhostButton:hover {{
    color: {ECHE_PALETTE['text']};
    background-color: {ECHE_PALETTE['surface']};
}}

QPushButton#DangerButton {{
    background-color: {ECHE_PALETTE['danger_bg']};
    color: {ECHE_PALETTE['danger_text']};
    border: 1px solid {ECHE_PALETTE['danger']};
}}
QPushButton#DangerButton:hover {{
    background-color: {ECHE_PALETTE['danger_hover']};
    color: white;
}}

QCheckBox {{
    spacing: 10px;
    color: {ECHE_PALETTE['text']};
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 1px solid {ECHE_PALETTE['border']};
    border-radius: 4px;
    background-color: {ECHE_PALETTE['surface']};
}}
QCheckBox::indicator:checked {{
    background-color: {ECHE_PALETTE['accent']};
    border: 1px solid {ECHE_PALETTE['accent_hover']};
}}
QCheckBox::indicator:hover {{
    border: 1px solid {ECHE_PALETTE['accent_hover']};
}}

QProgressBar {{
    background-color: {ECHE_PALETTE['surface']};
    border: 1px solid {ECHE_PALETTE['border']};
    border-radius: 8px;
    text-align: center;
    color: {ECHE_PALETTE['text']};
    height: 20px;
    font-size: 11px;
}}
QProgressBar::chunk {{
    background-color: {ECHE_PALETTE['accent']};
    border-radius: 6px;
}}

QScrollBar:vertical {{
    background-color: {ECHE_PALETTE['bg']};
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background-color: {ECHE_PALETTE['border']};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {ECHE_PALETTE['border_light']};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    border: none;
    background: none;
    height: 0;
}}

QTextEdit#LogView {{
    background-color: {ECHE_ORIGINAL['bg_input']};
    border: 1px solid {ECHE_PALETTE['border']};
    border-radius: 8px;
    color: {ECHE_PALETTE['text_muted']};
    font-family: 'Consolas', 'Cascadia Code', monospace;
    font-size: 11px;
    padding: 8px;
}}

QFrame#Separator {{
    background-color: {ECHE_PALETTE['border']};
    max-height: 1px;
    border: none;
}}

QFrame#Card {{
    background-color: {ECHE_PALETTE['surface']};
    border: 1px solid {ECHE_PALETTE['border']};
    border-radius: 12px;
}}

QFrame#StepActive {{
    background-color: {ECHE_PALETTE['accent']};
    border-radius: 12px;
}}
QFrame#StepDone {{
    background-color: {ECHE_PALETTE['success']};
    border-radius: 12px;
}}
QFrame#StepPending {{
    background-color: {ECHE_PALETTE['surface_2']};
    border: 1px solid {ECHE_PALETTE['border']};
    border-radius: 12px;
}}
"""

ECHE_ASCII = r"""
 â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•— â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•—â–ˆâ–ˆâ•—  â–ˆâ–ˆâ•—â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•—â–ˆâ–ˆâ•—      â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•— â–ˆâ–ˆâ–ˆâ•—  â–ˆâ–ˆâ•—
 â–ˆâ–ˆâ•”â•â•â•â•â•â–ˆâ–ˆâ•”â•â•â•â•â•â–ˆâ–ˆâ•‘  â–ˆâ–ˆâ•‘â–ˆâ–ˆâ•”â•â•â•â•â•â–ˆâ–ˆâ•‘     â–ˆâ–ˆâ•”â•â•â•â–ˆâ–ˆâ•—â–ˆâ–ˆâ–ˆâ–ˆâ•— â–ˆâ–ˆâ•‘
 â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•—  â–ˆâ–ˆâ•‘     â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•‘â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•—  â–ˆâ–ˆâ•‘     â–ˆâ–ˆâ•‘   â–ˆâ–ˆâ•‘â–ˆâ–ˆâ•”â–ˆâ–ˆâ•—â–ˆâ–ˆâ•‘
 â–ˆâ–ˆâ•”â•â•â•  â–ˆâ–ˆâ•‘     â–ˆâ–ˆâ•”â•â•â–ˆâ–ˆâ•‘â–ˆâ–ˆâ•”â•â•â•  â–ˆâ–ˆâ•‘     â–ˆâ–ˆâ•‘   â–ˆâ–ˆâ•‘â–ˆâ–ˆâ•‘â•šâ–ˆâ–ˆâ–ˆâ–ˆâ•‘
 â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•—â•šâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•—â–ˆâ–ˆâ•‘  â–ˆâ–ˆâ•‘â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•—â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•—â•šâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•”â•â–ˆâ–ˆâ•‘ â•šâ–ˆâ–ˆâ–ˆâ•‘
 â•šâ•â•â•â•â•â•â• â•šâ•â•â•â•â•â•â•šâ•â•  â•šâ•â•â•šâ•â•â•â•â•â•â•â•šâ•â•â•â•â•â•â• â•šâ•â•â•â•â•â• â•šâ•â•  â•šâ•â•â•
"""

VERSION = "1.3.0"
