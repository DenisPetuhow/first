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
всей выборке либо ОДНА цель по её маршрутам, в том же виде. Цель одна — выбора нет.

⭐ «АНАЛИТИКА» (заказчик 06.10.2026): окно переименовано, текст — как был; под ним четыре кнопки —
«Рассчитать нагрузку» (таблица и Word, как было), «Анализ нагрузки», «Показ сектора», «Показ карты»
(рисунки 8, 9, 5–7 «Описания ПК» с настройкой и PNG — `view_qt/output/`); выбранная цель уходит в окно."""
from PyQt5 import QtWidgets

GENERAL = "Обобщённый анализ — все цели"   # первая строка выбора
# окна рисунков: вид окна для `on_figure`, подпись кнопки, подсказка
FIGURES = (("load", "Анализ нагрузки", "Рисунок нагрузки на каждое средство (как рисунок 8 описания ПК): "
            "порядок, тип, подписи, цвета — настраиваются; сохранение в PNG."),
           ("sector", "Показ сектора", "Прилёты по 12 направлениям по 30° — по заходу на цель — и сколько из них "
            "засечено (как рисунок 9 описания ПК); настройка и сохранение в PNG."),
           ("map", "Показ карты", "Карта района для отчёта (как рисунки 5–7 описания ПК): весовая карта, маршруты, "
            "средства с зонами; фоном — карта местности или своя карта; сохранение в PNG."))


class AnalysisDialog(QtWidgets.QDialog):
    """Окно немодальное — не мешает работать с картой, пока открыто.

    Снимок статичен: при повторном открытии/нажатии кнопки текст перечитывается заново
    (`refresh`), но сам по себе не обновляется — иначе «на момент нажатия» потеряло бы
    смысл. Колбэки: `data(номер цели или None)` → HTML, `choices()` → подписи целей."""

    def __init__(self, parent, data=None, choices=None, on_load=None, on_figure=None):
        super().__init__(parent)
        self.setWindowTitle("Аналитика")
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
        # НАГРУЗКА НА СРЕДСТВА (план 13.4, заказчик 30.09.2026) — своё окно с таблицей по типу и общей
        self.btn_load = QtWidgets.QPushButton("Рассчитать нагрузку")
        self.btn_load.setToolTip("Доля маршрутов выборки, прошедших через каждое средство: таблица по типу или по всем "
                                 "средствам, по убыванию нагрузки; выгрузка в Word.")
        self.btn_load.clicked.connect(on_load or (lambda: None))
        self.btn_load.setVisible(on_load is not None)   # без колбэка кнопке нечего делать
        grid = QtWidgets.QGridLayout()               # четыре кнопки вывода — два ряда
        grid.addWidget(self.btn_load, 0, 0)
        self.btn_figure = {}                         # кнопки рисунков: вид -> кнопка
        for i, (kind, title, tip) in enumerate(FIGURES, start=1):   # кнопка на каждое окно рисунка
            b = QtWidgets.QPushButton(title)         # кнопка окна рисунка
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, k=kind: on_figure(k, self.target()))
            b.setVisible(on_figure is not None)      # без колбэка кнопке нечего делать
            grid.addWidget(b, i // 2, i % 2)
            self.btn_figure[kind] = b
        col.addLayout(grid)
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

    def target(self):
        """Выбранная цель. Отдаёт: None — обобщённый анализ (все цели), иначе номер цели с нуля."""
        i = self.combo.currentIndex()                # строка выбора
        return None if i <= 0 else i - 1

    def _show(self):
        """Показать отчёт по текущему выбору: 0 — обобщённый, иначе цель с этим номером."""
        self.text.setHtml(self._data(self.target()))
