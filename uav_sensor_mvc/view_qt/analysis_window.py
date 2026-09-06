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
подаётся строкой через колбэк `on_analysis_data`. Окно — просто витрина."""
from PyQt5 import QtWidgets


class AnalysisDialog(QtWidgets.QDialog):
    """Окно немодальное — не мешает работать с картой, пока открыто.

    Снимок статичен: при повторном открытии/нажатии кнопки текст перечитывается заново
    (`set_html`), но сам по себе не обновляется — иначе «на момент нажатия» потеряло бы
    смысл."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Анализ моделирования")
        self.resize(560, 640)
        col = QtWidgets.QVBoxLayout(self)
        self.text = QtWidgets.QTextBrowser()
        self.text.setOpenExternalLinks(False)
        col.addWidget(self.text)
        btn_close = QtWidgets.QPushButton("Закрыть")
        btn_close.clicked.connect(self.close)
        col.addWidget(btn_close)

    def set_html(self, html):
        self.text.setHtml(html)
