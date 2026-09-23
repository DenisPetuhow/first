# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Окно «Анализ моделирования» (план 8, задача 8.9, довесок 06.09.2026, заказчик).

Снимок ПОЛНОЙ статистики засечки на момент нажатия кнопки: сумма, разбивка по каждому
типу датчиков и разбивка по 12 направлениям компаса вокруг цели (шаг 30°) — с какой
стороны накопленной кратности не хватает. Те же 12 направлений рисует на карте галка
«Сектора» (группа «пролёт БПЛА», `ThreatMapView.render_sector_compass`).

⚠️ НОВЫЙ МЕХАНИЗМ — СВОЙ ФАЙЛ (правило проекта, CLAUDE.md), тем же приёмом, что и
`route_viewer.py`: самостоятельная надстройка над уже посчитанными показателями, а не
довесок к «Исходным данным».

Представление НИЧЕГО НЕ СЧИТАЕТ: готовый HTML-текст собирает контроллер
(`ThreatController._analysis_html`, там же и решается, какие строки красные) — окну
подаётся строкой через колбэк `on_analysis_data`. Окно — просто витрина.

⭐ НЕСКОЛЬКО ЦЕЛЕЙ (план 13, заказчик 23.09.2026): сверху выбор — «Обобщённый анализ» по
всей выборке либо ОДНА цель по её маршрутам, в том же виде. Цель одна — выбора нет."""
from PyQt5 import QtWidgets

GENERAL = "Обобщённый анализ — все цели"   # первая строка выбора


class AnalysisDialog(QtWidgets.QDialog):
    """Окно немодальное — не мешает работать с картой, пока открыто.

    Снимок статичен: при повторном открытии/нажатии кнопки текст перечитывается заново
    (`refresh`), но сам по себе не обновляется — иначе «на момент нажатия» потеряло бы
    смысл. Колбэки: `data(номер цели или None)` → HTML, `choices()` → подписи целей."""

    def __init__(self, parent, data=None, choices=None):
        super().__init__(parent)
        self.setWindowTitle("Анализ моделирования")
        self.resize(560, 640)
        self._data = data or (lambda target: "")     # отчёт по выбранному
        self._choices = choices or (lambda: [])      # подписи целей
        col = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        self.lbl_pick = QtWidgets.QLabel("Что показать:")
        self.combo = QtWidgets.QComboBox()
        self.combo.currentIndexChanged.connect(lambda _i: self._show())
        row.addWidget(self.lbl_pick)
        row.addWidget(self.combo, 1)
        col.addLayout(row)
        self.text = QtWidgets.QTextBrowser()
        self.text.setOpenExternalLinks(False)
        col.addWidget(self.text)
        btn_close = QtWidgets.QPushButton("Закрыть")
        btn_close.clicked.connect(self.close)
        col.addWidget(btn_close)

    def refresh(self):
        """Перечитать список целей и отчёт по выбранному (выбор сохраняется, если цель есть)."""
        labels = self._choices() or []               # подписи целей; пусто — цель одна
        keep = self.combo.currentIndex()             # что было выбрано
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(GENERAL)
        self.combo.addItems(labels)
        self.combo.setCurrentIndex(keep if 0 <= keep < self.combo.count() else 0)
        self.combo.blockSignals(False)
        for w in (self.lbl_pick, self.combo):
            w.setVisible(bool(labels))               # одна цель — выбирать нечего
        self._show()

    def _show(self):
        """Показать отчёт по текущему выбору: 0 — обобщённый, иначе цель с этим номером."""
        i = self.combo.currentIndex()                # строка выбора
        self.text.setHtml(self._data(None if i <= 0 else i - 1))
