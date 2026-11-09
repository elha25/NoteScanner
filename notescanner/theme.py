"""Mørkt tema i samme stil som Nedlaster: kort med avrundede hjørner, blå aksentfarge."""
from __future__ import annotations

import sys

from PySide6.QtGui import QColor, QFont, QPalette

C = {
    "bg": "#1c1d20",        # vindu
    "card": "#25262a",      # kort
    "card_hi": "#2b2c31",   # kort, hover
    "border": "#34363c",
    "input": "#1a1b1e",
    "input_border": "#3a3c43",
    "text": "#e9eaed",
    "muted": "#9a9da5",
    "faint": "#6c6f77",
    "accent": "#4c9dff",
    "accent_hi": "#66adff",
    "accent_lo": "#1f3552",
    "accent_text": "#ffffff",
    "green": "#3ecf6e",
    "amber": "#f0b429",
    "red": "#ef5f5f",
    "canvas": "#151619",
    "select": "#22344c",
}

UI_FONT = "Segoe UI Variable Text" if sys.platform == "win32" else "Inter"
MONO_FONT = "Cascadia Mono, Consolas, monospace" if sys.platform == "win32" else "DejaVu Sans Mono, monospace"


def palette() -> QPalette:
    p = QPalette()
    col = {QPalette.Window: C["bg"], QPalette.WindowText: C["text"], QPalette.Base: C["input"],
           QPalette.AlternateBase: C["card"], QPalette.ToolTipBase: C["card_hi"], QPalette.ToolTipText: C["text"],
           QPalette.Text: C["text"], QPalette.Button: C["card"], QPalette.ButtonText: C["text"],
           QPalette.BrightText: "#ffffff", QPalette.Highlight: C["accent"], QPalette.HighlightedText: "#ffffff",
           QPalette.PlaceholderText: C["faint"], QPalette.Link: C["accent"], QPalette.Mid: C["border"],
           QPalette.Dark: C["bg"], QPalette.Shadow: "#000000", QPalette.Light: C["card_hi"]}
    for role, c in col.items():
        p.setColor(role, QColor(c))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, QColor(C["faint"]))
    return p


def font(size: float = 10, weight: QFont.Weight = QFont.Normal) -> QFont:
    f = QFont()
    f.setFamilies([UI_FONT, "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans"])
    f.setPointSizeF(size)
    f.setWeight(weight)
    return f


QSS = """
* {{ font-family: "{ui}", "Segoe UI", "Noto Sans", "DejaVu Sans"; }}
QMainWindow, QWidget#root {{ background: {bg}; }}
QToolTip {{ background: {card_hi}; color: {text}; border: 1px solid {border}; padding: 5px 8px; border-radius: 6px; }}

QFrame#card {{ background: {card}; border: 1px solid {border}; border-radius: 10px; }}
QLabel {{ color: {text}; background: transparent; }}
QLabel#h1 {{ font-size: 17pt; font-weight: 600; }}
QLabel#sub, QLabel#muted, QLabel#hint {{ color: {muted}; }}
QLabel#hint {{ font-size: 8.5pt; }}
QFrame#banner {{ background: {select}; border: 1px solid {accent}; border-radius: 10px; }}
QFrame#banner QLabel {{ color: {text}; }}
QLabel#version {{ color: {faint}; font-size: 8.5pt; }}
QLabel#cardtitle {{ font-weight: 600; font-size: 10pt; }}
QLabel#kbd {{ font-family: {mono}; }}
QLabel#pill_ok {{ color: {green}; background: rgba(62,207,110,0.12); border-radius: 9px; padding: 2px 9px;
                 font-size: 8.5pt; font-weight: 600; }}
QLabel#pill_warn {{ color: {amber}; background: rgba(240,180,41,0.12); border-radius: 9px; padding: 2px 9px;
                   font-size: 8.5pt; font-weight: 600; }}
QLabel#pill_info {{ color: {accent}; background: rgba(76,157,255,0.13); border-radius: 9px; padding: 2px 9px;
                   font-size: 8.5pt; font-weight: 600; }}

QPushButton {{ background: {bg}; color: {text}; border: 1px solid {input_border}; border-radius: 7px;
              padding: 6px 12px; font-weight: 600; }}
QPushButton:hover {{ background: {card_hi}; border-color: #4a4d55; }}
QPushButton:pressed {{ background: {input}; }}
QPushButton:disabled {{ color: {faint}; border-color: {border}; }}
QPushButton#primary {{ background: {accent}; color: {accent_text}; border: none; border-radius: 8px;
                      padding: 11px 16px; font-size: 10.5pt; }}
QPushButton#primary:hover {{ background: {accent_hi}; }}
QPushButton#primary:pressed {{ background: #3b8ae8; }}
QPushButton#primary:disabled {{ background: #2c4566; color: #9fb3cc; }}
QPushButton#seg {{ background: {bg}; border: 1px solid {input_border}; border-radius: 8px; padding: 9px 6px;
                  color: {text}; }}
QPushButton#seg:hover {{ background: {card_hi}; }}
QPushButton#seg:checked {{ background: {accent_lo}; border: 1.5px solid {accent}; color: #ffffff; }}
QToolButton#tool {{ background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 5px; }}
QToolButton#tool:hover {{ background: {card_hi}; border-color: {border}; }}
QToolButton#tool:pressed {{ background: {input}; }}
QToolButton#tool:disabled {{ background: transparent; }}
QToolButton#expander {{ background: transparent; border: none; color: {text}; font-weight: 600; padding: 2px 0; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {input}; color: {text}; border: 1px solid {input_border};
    border-radius: 7px; padding: 6px 9px; selection-background-color: {accent}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1.5px solid {accent}; }}
QLineEdit:disabled {{ color: {faint}; }}
QLineEdit[auto="true"] {{ border-color: #6b5a24; background: #26231a; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {card_hi}; color: {text}; border: 1px solid {border};
    selection-background-color: {accent_lo}; outline: none; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 18px; border: none; background: transparent; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover,
QDoubleSpinBox::down-button:hover {{ background: {card_hi}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{up}"); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QComboBox::down-arrow {{ image: url("{down}"); width: 10px;
    height: 10px; }}

QCheckBox {{ color: {text}; spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1.5px solid #5a5d66;
    background: {input}; }}
QCheckBox::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; image: url("{check}"); }}

QTreeWidget {{ background: transparent; border: none; outline: none; color: {text}; }}
QTreeWidget::item {{ border: none; }}
QTreeWidget::item:selected {{ background: transparent; }}
QTreeWidget::branch {{ background: transparent; image: none; border-image: none; }}
QListWidget#archive {{ background: {input}; border: 1px solid {border}; border-radius: 8px; outline: none;
    color: {text}; padding: 4px; }}
QListWidget#archive::item {{ padding: 5px 4px; border-radius: 6px; }}
QListWidget#archive::item:selected {{ background: {select}; color: {text}; }}
QListWidget#archive::item:hover {{ background: {card_hi}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #43464e; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #575b64; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #43464e; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: transparent; }}
QStatusBar {{ background: {bg}; color: {muted}; border-top: 1px solid {border}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {muted}; padding: 0 6px; }}
QProgressBar {{ background: {input}; border: 1px solid {border}; border-radius: 5px; height: 10px; text-align: center;
    color: transparent; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}
QMessageBox {{ background: {card}; }}
QMenu {{ background: {card_hi}; color: {text}; border: 1px solid {border}; padding: 4px; }}
QMenu::item:selected {{ background: {accent_lo}; }}
"""


def stylesheet(check_icon_path: str, up_path: str = "", down_path: str = "") -> str:
    fix = lambda p: p.replace("\\", "/")  # noqa: E731
    return QSS.format(ui=UI_FONT, mono=MONO_FONT, check=fix(check_icon_path), up=fix(up_path), down=fix(down_path),
                      **C)
