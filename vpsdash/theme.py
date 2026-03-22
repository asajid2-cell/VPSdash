APP_QSS = """
QWidget {
    background-color: #060607;
    color: #e5e5e5;
    font-size: 13px;
}

QMainWindow,
#RootFrame,
#ContentFrame,
#ContentColumn,
#PageStack {
    background-color: #060607;
}

QLabel {
    background: transparent;
}

#SidebarFrame {
    background-color: rgba(0, 0, 0, 0.85);
    border-right: 1px solid rgba(72, 72, 72, 0.38);
}

#SidebarBrand,
#SidebarToolsCard,
#DesktopTopbar {
    background-color: #0f1012;
    border: 1px solid rgba(72, 72, 72, 0.38);
    border-radius: 16px;
}

#SidebarNavBox {
    background: transparent;
    border: none;
}

#SidebarTitle {
    color: #f4f6ff;
    font-size: 24px;
    font-weight: 750;
}

#SidebarBody,
#SidebarFooter,
#DesktopTopbarBody {
    color: #ababab;
    font-size: 12px;
}

#SidebarStatus {
    color: #d6daf7;
    font-size: 12px;
    font-weight: 600;
    padding-top: 2px;
}

#DesktopTopbarTitle {
    color: #f4f6ff;
    font-size: 20px;
    font-weight: 730;
}

QLabel[class="SidebarKicker"],
QLabel[class="SidebarSection"],
QLabel[class="TopbarKicker"],
QLabel[class="PageKicker"],
QLabel[class="MetricLabel"] {
    color: #bdc2ff;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
}

QPushButton[nav="true"] {
    background-color: transparent;
    color: #c5c9d4;
    border: 1px solid transparent;
    border-radius: 12px;
    text-align: left;
    padding: 0 16px;
    min-height: 46px;
    font-weight: 650;
}

QPushButton[nav="true"]:hover {
    background-color: rgba(189, 194, 255, 0.08);
    border-color: rgba(189, 194, 255, 0.18);
    color: #f4f6ff;
}

QPushButton[nav="true"]:checked {
    background-color: rgba(189, 194, 255, 0.12);
    color: #ffffff;
    border-color: rgba(189, 194, 255, 0.28);
}

QFrame[card="true"] {
    background-color: #0f1012;
    border: 1px solid rgba(72, 72, 72, 0.38);
    border-radius: 18px;
}

#HeroCard {
    background-color: #101114;
    border: 1px solid rgba(112, 118, 156, 0.24);
}

#WarningCard {
    background-color: rgba(251, 191, 36, 0.08);
    border: 1px solid rgba(251, 191, 36, 0.22);
}

#HeroTitle {
    font-size: 34px;
    font-weight: 760;
    color: #f4f6ff;
}

#HeroBody,
QLabel[class="CardBody"],
QLabel[class="HelperText"] {
    color: #ababab;
    font-size: 13px;
}

QLabel[class="SectionTitle"] {
    color: #f4f6ff;
    font-size: 22px;
    font-weight: 730;
}

QLabel[class="CardTitle"] {
    color: #f4f6ff;
    font-size: 16px;
    font-weight: 700;
}

QLabel[class="FormLabel"] {
    color: #cfd2de;
    font-size: 12px;
    font-weight: 650;
    padding-top: 8px;
}

QLabel[class="MetricValue"] {
    color: #f4f6ff;
    font-size: 34px;
    font-weight: 760;
}

QLabel#ChipLabel {
    color: #f4f6ff;
    background-color: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(72, 72, 72, 0.38);
    border-radius: 999px;
    padding: 6px 10px;
    font-size: 11px;
    font-weight: 700;
}

QLabel#ChipLabel[tone="neutral"] {
    color: #d0d3dd;
    background-color: rgba(255, 255, 255, 0.04);
    border-color: rgba(72, 72, 72, 0.38);
}

QLabel#ChipLabel[tone="accent"] {
    color: #eef0ff;
    background-color: rgba(189, 194, 255, 0.12);
    border-color: rgba(189, 194, 255, 0.28);
}

QLabel#ChipLabel[tone="success"] {
    color: #d8ffef;
    background-color: rgba(52, 211, 153, 0.12);
    border-color: rgba(52, 211, 153, 0.28);
}

QLabel#ChipLabel[tone="warn"] {
    color: #ffe2a1;
    background-color: rgba(251, 191, 36, 0.12);
    border-color: rgba(251, 191, 36, 0.28);
}

QPushButton {
    background-color: #2e3aa2;
    color: #f4f6ff;
    border: 1px solid #2e3aa2;
    border-radius: 12px;
    padding: 0 16px;
    min-height: 44px;
    font-size: 13px;
    font-weight: 650;
}

QPushButton:hover {
    background-color: #3b49c2;
    border-color: #4c5ce0;
}

QPushButton:pressed {
    background-color: #25308a;
    border-color: #25308a;
}

QPushButton:disabled {
    background-color: rgba(255, 255, 255, 0.05);
    color: #737784;
    border-color: rgba(72, 72, 72, 0.26);
}

QPushButton[variant="secondary"] {
    background-color: rgba(255, 255, 255, 0.03);
    color: #e5e5e5;
    border: 1px solid rgba(112, 118, 156, 0.3);
}

QPushButton[variant="secondary"]:hover {
    background-color: rgba(189, 194, 255, 0.08);
    border-color: rgba(189, 194, 255, 0.28);
}

QPushButton[variant="ghost"] {
    background-color: transparent;
    color: #d7daf0;
    border: 1px solid rgba(72, 72, 72, 0.38);
}

QPushButton[variant="ghost"]:hover {
    background-color: rgba(255, 255, 255, 0.04);
    border-color: rgba(189, 194, 255, 0.24);
}

QLineEdit,
QComboBox,
QPlainTextEdit,
QSpinBox,
QTableWidget,
QTreeWidget,
QTextBrowser {
    background-color: rgba(255, 255, 255, 0.03);
    color: #e5e5e5;
    border: 1px solid rgba(72, 72, 72, 0.38);
    border-radius: 12px;
    padding: 10px 12px;
    selection-background-color: #2e3aa2;
    selection-color: #ffffff;
}

QLineEdit:focus,
QComboBox:focus,
QPlainTextEdit:focus,
QSpinBox:focus,
QTableWidget:focus,
QTreeWidget:focus,
QTextBrowser:focus {
    border: 1px solid rgba(189, 194, 255, 0.45);
}

QProgressBar {
    background-color: rgba(255, 255, 255, 0.03);
    color: #eef0ff;
    border: 1px solid rgba(112, 118, 156, 0.3);
    border-radius: 12px;
    min-height: 26px;
    padding: 2px;
    text-align: center;
    font-size: 11px;
    font-weight: 700;
}

QProgressBar::chunk {
    border-radius: 9px;
    background-color: #2e3aa2;
    margin: 1px;
}

#InitialSetupProgress {
    background-color: #0f1012;
    border-color: rgba(189, 194, 255, 0.2);
    color: #f4f6ff;
    min-height: 30px;
}

#InitialSetupProgress::chunk {
    background-color: #3b49c2;
}

QLineEdit:disabled,
QComboBox:disabled,
QPlainTextEdit:disabled,
QSpinBox:disabled {
    background-color: rgba(255, 255, 255, 0.025);
    color: #737784;
    border-color: rgba(72, 72, 72, 0.28);
}

QComboBox {
    padding-right: 28px;
}

QComboBox::drop-down {
    width: 22px;
    border: none;
}

QComboBox QAbstractItemView {
    background-color: #131313;
    color: #e5e5e5;
    border: 1px solid rgba(72, 72, 72, 0.38);
    selection-background-color: rgba(189, 194, 255, 0.14);
    selection-color: #ffffff;
}

QPlainTextEdit,
QTextBrowser[output="true"] {
    padding: 12px 14px;
}

QTextBrowser[output="true"] {
    background-color: rgba(0, 0, 0, 0.26);
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 12px;
    border-radius: 14px;
}

QTextBrowser[role="warning"] {
    background-color: rgba(251, 191, 36, 0.08);
    border: 1px solid rgba(251, 191, 36, 0.24);
    color: #ffe2a1;
}

QTableWidget,
QTreeWidget {
    gridline-color: rgba(72, 72, 72, 0.3);
    alternate-background-color: rgba(255, 255, 255, 0.015);
}

QTableWidget::item,
QTreeWidget::item {
    padding: 8px 6px;
}

QTableWidget::item:selected,
QTreeWidget::item:selected {
    background-color: rgba(189, 194, 255, 0.12);
    color: #ffffff;
}

QHeaderView::section {
    background-color: #141418;
    color: #c8c9d2;
    border: none;
    border-bottom: 1px solid rgba(72, 72, 72, 0.38);
    padding: 10px 12px;
    font-weight: 700;
}

QTableCornerButton::section {
    background-color: #141418;
    border: none;
    border-bottom: 1px solid rgba(72, 72, 72, 0.38);
}

QCheckBox {
    spacing: 10px;
    color: #e5e5e5;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 5px;
    border: 1px solid rgba(112, 118, 156, 0.32);
    background-color: rgba(255, 255, 255, 0.02);
}

QCheckBox::indicator:checked {
    background-color: #2e3aa2;
    border-color: #5665e0;
}

QScrollArea {
    border: none;
    background: transparent;
}

QScrollBar:vertical {
    background: rgba(255, 255, 255, 0.02);
    width: 16px;
    margin: 8px 2px 8px 10px;
    border-radius: 7px;
}

QScrollBar::handle:vertical {
    background: rgba(189, 194, 255, 0.28);
    border: 1px solid rgba(189, 194, 255, 0.12);
    border-radius: 7px;
    min-height: 42px;
}

QScrollBar::handle:vertical:hover {
    background: rgba(189, 194, 255, 0.42);
    border-color: rgba(189, 194, 255, 0.24);
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical,
QScrollBar::left-arrow:vertical,
QScrollBar::right-arrow:vertical,
QScrollBar::up-arrow:vertical,
QScrollBar::down-arrow:vertical {
    background: transparent;
    border: none;
    height: 0px;
}

QToolTip {
    background-color: #111216;
    color: #f4f6ff;
    border: 1px solid rgba(72, 72, 72, 0.38);
    padding: 6px 8px;
}
"""
