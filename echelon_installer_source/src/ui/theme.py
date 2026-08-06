"""
Echelon Theme - matched to original Echelon GUI (gui/theme.py)
Original palette:
  BG #14151a, BG_ELEVATED #1c1d24, BG_INPUT #12131a, BORDER #2a2b34, TEXT #e8e8ec
  ACCENT #3d5a80 (now used), with violet glow as optional highlight for installer

Installer enhances with Echelon's elite tactical vibe while staying faithful.
"""

# Original Echelon palette (from gui/theme.py)
ECHELON_ORIGINAL = {
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

# Installer palette - faithful to Echelon but enhanced for wizard clarity
ECHELON_PALETTE = {
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
    background-color: {ECHELON_PALETTE['bg']};
    color: {ECHELON_PALETTE['text']};
    font-family: 'Segoe UI', 'JetBrains Mono', 'Consolas', monospace;
    font-size: 13px;
}}

QMainWindow {{
    background-color: {ECHELON_PALETTE['bg']};
    border: 1px solid {ECHELON_PALETTE['border']};
}}

QLabel#TitleLabel {{
    font-size: 26px;
    font-weight: 800;
    letter-spacing: 2px;
    color: {ECHELON_PALETTE['text']};
}}

QLabel#SubtitleLabel {{
    font-size: 10px;
    color: {ECHELON_PALETTE['text_muted']};
    letter-spacing: 2px;
    text-transform: uppercase;
}}

QLabel#SectionLabel {{
    font-size: 13px;
    font-weight: 700;
    color: {ECHELON_PALETTE['accent']};
    letter-spacing: 0.5px;
    margin-top: 8px;
}}

QLabel#MutedLabel {{
    color: {ECHELON_PALETTE['text_muted']};
    font-size: 12px;
}}

QLabel#PathLabel {{
    background-color: {ECHELON_PALETTE['surface']};
    border: 1px solid {ECHELON_PALETTE['border']};
    border-radius: 8px;
    padding: 10px 14px;
    color: {ECHELON_PALETTE['text']};
    font-size: 12px;
}}

QLineEdit {{
    background-color: {ECHELON_ORIGINAL['bg_input']};
    border: 1px solid {ECHELON_PALETTE['border']};
    border-radius: 8px;
    padding: 10px 14px;
    color: {ECHELON_PALETTE['text']};
    selection-background-color: {ECHELON_PALETTE['accent']};
}}
QLineEdit:focus {{
    border: 1px solid {ECHELON_PALETTE['border_light']};
    background-color: {ECHELON_PALETTE['surface']};
}}

QPushButton {{
    border-radius: 8px;
    padding: 10px 20px;
    font-weight: 600;
    font-size: 12px;
    letter-spacing: 0.3px;
}}

QPushButton#PrimaryButton {{
    background-color: {ECHELON_PALETTE['accent']};
    color: {ECHELON_PALETTE['text']};
    border: 1px solid {ECHELON_PALETTE['accent_hover']};
}}
QPushButton#PrimaryButton:hover {{
    background-color: {ECHELON_PALETTE['accent_hover']};
    color: {ECHELON_PALETTE['text']};
}}
QPushButton#PrimaryButton:disabled {{
    background-color: {ECHELON_PALETTE['surface_2']};
    color: {ECHELON_PALETTE['text_dim']};
}}

QPushButton#SecondaryButton {{
    background-color: {ECHELON_PALETTE['surface']};
    color: {ECHELON_PALETTE['text']};
    border: 1px solid {ECHELON_PALETTE['border']};
}}
QPushButton#SecondaryButton:hover {{
    background-color: {ECHELON_PALETTE['surface_2']};
    border: 1px solid {ECHELON_PALETTE['border_light']};
}}

QPushButton#GhostButton {{
    background-color: transparent;
    color: {ECHELON_PALETTE['text_muted']};
    border: 1px solid transparent;
}}
QPushButton#GhostButton:hover {{
    color: {ECHELON_PALETTE['text']};
    background-color: {ECHELON_PALETTE['surface']};
}}

QPushButton#DangerButton {{
    background-color: {ECHELON_PALETTE['danger_bg']};
    color: {ECHELON_PALETTE['danger_text']};
    border: 1px solid {ECHELON_PALETTE['danger']};
}}
QPushButton#DangerButton:hover {{
    background-color: {ECHELON_PALETTE['danger_hover']};
    color: white;
}}

QCheckBox {{
    spacing: 10px;
    color: {ECHELON_PALETTE['text']};
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 1px solid {ECHELON_PALETTE['border']};
    border-radius: 4px;
    background-color: {ECHELON_PALETTE['surface']};
}}
QCheckBox::indicator:checked {{
    background-color: {ECHELON_PALETTE['accent']};
    border: 1px solid {ECHELON_PALETTE['accent_hover']};
}}
QCheckBox::indicator:hover {{
    border: 1px solid {ECHELON_PALETTE['accent_hover']};
}}

QProgressBar {{
    background-color: {ECHELON_PALETTE['surface']};
    border: 1px solid {ECHELON_PALETTE['border']};
    border-radius: 8px;
    text-align: center;
    color: {ECHELON_PALETTE['text']};
    height: 20px;
    font-size: 11px;
}}
QProgressBar::chunk {{
    background-color: {ECHELON_PALETTE['accent']};
    border-radius: 6px;
}}

QScrollBar:vertical {{
    background-color: {ECHELON_PALETTE['bg']};
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background-color: {ECHELON_PALETTE['border']};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {ECHELON_PALETTE['border_light']};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    border: none;
    background: none;
    height: 0;
}}

QTextEdit#LogView {{
    background-color: {ECHELON_ORIGINAL['bg_input']};
    border: 1px solid {ECHELON_PALETTE['border']};
    border-radius: 8px;
    color: {ECHELON_PALETTE['text_muted']};
    font-family: 'Consolas', 'Cascadia Code', monospace;
    font-size: 11px;
    padding: 8px;
}}

QFrame#Separator {{
    background-color: {ECHELON_PALETTE['border']};
    max-height: 1px;
    border: none;
}}

QFrame#Card {{
    background-color: {ECHELON_PALETTE['surface']};
    border: 1px solid {ECHELON_PALETTE['border']};
    border-radius: 12px;
}}

QFrame#StepActive {{
    background-color: {ECHELON_PALETTE['accent']};
    border-radius: 12px;
}}
QFrame#StepDone {{
    background-color: {ECHELON_PALETTE['success']};
    border-radius: 12px;
}}
QFrame#StepPending {{
    background-color: {ECHELON_PALETTE['surface_2']};
    border: 1px solid {ECHELON_PALETTE['border']};
    border-radius: 12px;
}}
"""

ECHELON_ASCII = r"""
 ███████╗ ██████╗██╗  ██╗███████╗██╗      ██████╗ ███╗  ██╗
 ██╔════╝██╔════╝██║  ██║██╔════╝██║     ██╔═══██╗████╗ ██║
 █████╗  ██║     ███████║█████╗  ██║     ██║   ██║██╔██╗██║
 ██╔══╝  ██║     ██╔══██║██╔══╝  ██║     ██║   ██║██║╚████║
 ███████╗╚██████╗██║  ██║███████╗███████╗╚██████╔╝██║ ╚███║
 ╚══════╝ ╚═════╝╚═╝  ╚═╝╚══════╝╚══════╝ ╚═════╝ ╚═╝  ╚══╝
"""

VERSION = "1.1.3"
