import json
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal as Signal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# Dark theme QSS stylesheet for SettingsWindow
SETTINGS_STYLESHEET = """
QDialog, QWidget#settingsRoot {
    background-color: #181825;
    color: #cdd6f4;
    font-family: 'Segoe UI', sans-serif;
}

/* Tab Widget Styling */
QTabWidget::pane {
    border: 1px solid #313244;
    border-radius: 8px;
    background-color: #181825;
    top: -1px;
}

QTabBar::tab {
    background-color: #1e1e2e;
    color: #a6adc8;
    padding: 10px 18px;
    margin-right: 4px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    font-weight: 500;
}

QTabBar::tab:selected {
    background-color: #313244;
    color: #89b4fa;
    font-weight: bold;
    border-bottom: 2px solid #89b4fa;
}

QTabBar::tab:hover:!selected {
    background-color: #2b2b3d;
    color: #cdd6f4;
}

/* Provider & Section Cards */
QFrame#providerCard {
    background-color: #1e1e2e;
    border: 1px solid #313244;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
}

/* Labels */
QLabel {
    color: #cdd6f4;
    font-size: 13px;
}

QLabel#sectionTitle {
    font-size: 15px;
    font-weight: bold;
    color: #89b4fa;
    margin-bottom: 4px;
}

QLabel#fieldLabel {
    font-weight: 500;
    color: #bac2de;
}

/* Form Controls (Inputs & Combos) */
QLineEdit, QComboBox {
    background-color: #11111b;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 13px;
    min-height: 20px;
    selection-background-color: #45475a;
}

QLineEdit:hover, QComboBox:hover {
    border: 1px solid #585b70;
}

QLineEdit:focus, QComboBox:focus {
    border: 1px solid #89b4fa;
    background-color: #181825;
}

QComboBox::drop-down {
    border: none;
    padding-right: 8px;
}

QComboBox QAbstractItemView {
    background-color: #1e1e2e;
    color: #cdd6f4;
    border: 1px solid #45475a;
    selection-background-color: #313244;
    padding: 4px;
}

/* Buttons */
QPushButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 8px 20px;
    font-size: 13px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #45475a;
    border-color: #585b70;
}

QPushButton:pressed {
    background-color: #585b70;
}

QPushButton#saveBtn {
    background-color: #89b4fa;
    color: #11111b;
    border: none;
}

QPushButton#saveBtn:hover {
    background-color: #b4befe;
}

/* ScrollArea */
QScrollArea {
    border: none;
    background-color: transparent;
}
"""


class SettingsWindow(QDialog):
    settings_saved = Signal(dict)

    def __init__(self, parent=None, config_path="config.json"):
        super().__init__(parent)
        self.config_path = Path(config_path)
        self.setWindowTitle("Eche - Settings")
        self.resize(650, 700)
        self.setObjectName("settingsRoot")
        self.setStyleSheet(SETTINGS_STYLESHEET)

        self.init_ui()
        self.load_settings()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(16)

        # Tabs Container
        self.tabs = QTabWidget()
        self.tabs.addTab(self.create_ai_providers_tab(), "AI & Providers")
        self.tabs.addTab(self.create_bot_tab(), "Bot Config")
        self.tabs.addTab(self.create_general_tab(), "General")

        main_layout.addWidget(self.tabs)

        # Action Buttons (Save / Cancel)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setObjectName("saveBtn")
        self.save_btn.clicked.connect(self.save_settings)
        btn_layout.addWidget(self.save_btn)

        main_layout.addLayout(btn_layout)

    def create_ai_providers_tab(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(16)
        layout.setContentsMargins(12, 12, 12, 12)

        # --- Card 1: Active Provider Selection ---
        provider_card = QFrame()
        provider_card.setObjectName("providerCard")
        p_layout = QFormLayout(provider_card)
        p_layout.setSpacing(14)
        p_layout.setContentsMargins(16, 16, 16, 16)

        title1 = QLabel("Active Provider")
        title1.setObjectName("sectionTitle")
        p_layout.addRow(title1)

        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["local", "groq", "openai"])
        p_layout.addRow(self._make_label("Provider Mode:"), self.provider_combo)

        layout.addWidget(provider_card)

        # --- Card 2: Local LLM Settings ---
        local_card = QFrame()
        local_card.setObjectName("providerCard")
        l_layout = QFormLayout(local_card)
        l_layout.setSpacing(14)
        l_layout.setContentsMargins(16, 16, 16, 16)

        title2 = QLabel("Local LLM Settings (Ollama / LM Studio)")
        title2.setObjectName("sectionTitle")
        l_layout.addRow(title2)

        self.local_url_input = QLineEdit()
        self.local_url_input.setPlaceholderText("http://localhost:11434/v1")
        l_layout.addRow(self._make_label("API Base URL:"), self.local_url_input)

        self.local_model_input = QLineEdit()
        self.local_model_input.setPlaceholderText("llama3:latest")
        l_layout.addRow(self._make_label("Model Name / Tag:"), self.local_model_input)

        layout.addWidget(local_card)

        # --- Card 3: Groq Cloud Settings ---
        groq_card = QFrame()
        groq_card.setObjectName("providerCard")
        g_layout = QFormLayout(groq_card)
        g_layout.setSpacing(14)
        g_layout.setContentsMargins(16, 16, 16, 16)

        title3 = QLabel("Groq Cloud Settings")
        title3.setObjectName("sectionTitle")
        g_layout.addRow(title3)

        self.groq_key_input = QLineEdit()
        self.groq_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.groq_key_input.setPlaceholderText("gsk_...")
        g_layout.addRow(self._make_label("Groq API Key:"), self.groq_key_input)

        self.groq_model_combo = QComboBox()
        self.groq_model_combo.addItems([
            "llama3-70b-8192",
            "llama3-8b-8192",
            "mixtral-8x7b-32768",
            "gemma2-9b-it"
        ])
        g_layout.addRow(self._make_label("Groq Model:"), self.groq_model_combo)

        layout.addWidget(groq_card)

        # --- Card 4: OpenAI Settings ---
        openai_card = QFrame()
        openai_card.setObjectName("providerCard")
        o_layout = QFormLayout(openai_card)
        o_layout.setSpacing(14)
        o_layout.setContentsMargins(16, 16, 16, 16)

        title4 = QLabel("OpenAI Settings")
        title4.setObjectName("sectionTitle")
        o_layout.addRow(title4)

        self.openai_key_input = QLineEdit()
        self.openai_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_key_input.setPlaceholderText("sk-...")
        o_layout.addRow(self._make_label("OpenAI API Key:"), self.openai_key_input)

        self.openai_model_input = QLineEdit()
        self.openai_model_input.setPlaceholderText("gpt-4o-mini")
        o_layout.addRow(self._make_label("OpenAI Model:"), self.openai_model_input)

        layout.addWidget(openai_card)

        # Push items to top to prevent vertical stretching
        layout.addStretch()

        scroll.setWidget(container)
        return scroll

    def create_bot_tab(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(16)
        layout.setContentsMargins(12, 12, 12, 12)

        card = QFrame()
        card.setObjectName("providerCard")
        f_layout = QFormLayout(card)
        f_layout.setSpacing(14)
        f_layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("Discord Bot Integration")
        title.setObjectName("sectionTitle")
        f_layout.addRow(title)

        self.bot_token_input = QLineEdit()
        self.bot_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.bot_token_input.setPlaceholderText("MTAx...")
        f_layout.addRow(self._make_label("Bot Token:"), self.bot_token_input)

        self.command_prefix_input = QLineEdit()
        self.command_prefix_input.setPlaceholderText("!")
        f_layout.addRow(self._make_label("Command Prefix:"), self.command_prefix_input)

        self.home_guild_input = QLineEdit()
        self.home_guild_input.setPlaceholderText("123456789012345678")
        f_layout.addRow(self._make_label("Home Guild ID:"), self.home_guild_input)

        layout.addWidget(card)
        layout.addStretch()
        return container

    def create_general_tab(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(16)
        layout.setContentsMargins(12, 12, 12, 12)

        card = QFrame()
        card.setObjectName("providerCard")
        f_layout = QFormLayout(card)
        f_layout.setSpacing(14)
        f_layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("Application Settings")
        title.setObjectName("sectionTitle")
        f_layout.addRow(title)

        self.autostart_checkbox = QCheckBox("Start Bot automatically on app launch")
        f_layout.addRow(self.autostart_checkbox)

        self.debug_checkbox = QCheckBox("Enable verbose debug logging")
        f_layout.addRow(self.debug_checkbox)

        layout.addWidget(card)
        layout.addStretch()
        return container

    def _make_label(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("fieldLabel")
        return lbl

    def load_settings(self):
        if not self.config_path.exists():
            return

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # AI Settings
            provider = data.get("provider", "local").lower()
            idx = self.provider_combo.findText(provider, Qt.MatchFlag.MatchExactly)
            if idx >= 0:
                self.provider_combo.setCurrentIndex(idx)

            self.local_url_input.setText(data.get("local_url", "http://localhost:11434/v1"))
            self.local_model_input.setText(data.get("local_model", "llama3:latest"))

            self.groq_key_input.setText(data.get("groq_api_key", ""))
            groq_m = data.get("groq_model", "llama3-70b-8192")
            g_idx = self.groq_model_combo.findText(groq_m)
            if g_idx >= 0:
                self.groq_model_combo.setCurrentIndex(g_idx)

            self.openai_key_input.setText(data.get("openai_api_key", ""))
            self.openai_model_input.setText(data.get("openai_model", "gpt-4o-mini"))

            # Bot Settings
            self.bot_token_input.setText(data.get("bot_token", ""))
            self.command_prefix_input.setText(data.get("command_prefix", "!"))
            self.home_guild_input.setText(str(data.get("home_guild_id", "")))

            # General Settings
            self.autostart_checkbox.setChecked(data.get("autostart", False))
            self.debug_checkbox.setChecked(data.get("debug_mode", False))

        except Exception as e:
            QMessageBox.warning(self, "Error Loading Config", f"Failed to read settings: {e}")

    def save_settings(self):
        config_data = {
            "provider": self.provider_combo.currentText(),
            "local_url": self.local_url_input.text().strip() or "http://localhost:11434/v1",
            "local_model": self.local_model_input.text().strip() or "llama3:latest",
            "groq_api_key": self.groq_key_input.text().strip(),
            "groq_model": self.groq_model_combo.currentText(),
            "openai_api_key": self.openai_key_input.text().strip(),
            "openai_model": self.openai_model_input.text().strip() or "gpt-4o-mini",
            "bot_token": self.bot_token_input.text().strip(),
            "command_prefix": self.command_prefix_input.text().strip() or "!",
            "home_guild_id": self.home_guild_input.text().strip(),
            "autostart": self.autostart_checkbox.isChecked(),
            "debug_mode": self.debug_checkbox.isChecked(),
        }

        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=4)

            self.settings_saved.emit(config_data)
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error Saving Config", f"Failed to save settings: {e}")