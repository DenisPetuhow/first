# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Модуль вывода — окно «Анализ нагрузки» (рисунок 8 «Описания ПК», заказчик 06.10.2026).

Кнопка «Анализ нагрузки» окна «Аналитика»: нагрузка на каждое средство столбцами — доля маршрутов выборки, прошедших
через его зону (та же, что в таблице «Рассчитать нагрузку»). Настройки: цель, все средства или один тип, порядок
(`sensor_load.ORDERS`), горизонтально, числа у столбцов, средняя, шкала до 100 %, сетка, легенда, цвета, заголовок,
шрифт; «Сохранить PNG…». Рисует `figures.py :: draw_load`, основа окна — `figure_base.py`.
"""
from model.placement.sensor_load import ORDERS
from view_qt.output.figure_base import FigureDialog
from view_qt.output.figures import LOAD_DEFAULTS, draw_load
from view_qt.windows.load_window import ALL, SOURCE


class LoadChartDialog(FigureDialog):
    """Окно рисунка нагрузки. Вход — как у `FigureDialog`; снимок — `report_data` (берётся его часть `load`)."""
    TITLE = "Анализ нагрузки"                        # заголовок окна
    FILE = "нагрузка_на_средства"                    # начало имени PNG
    SIZE_CM = (25.4, 9.7)                            # как рисунок 8 Word (10 × 3,8 дюйма)
    COLORS = True                                    # цвета типов — как на карте или как в Word

    def _build_settings(self):
        """Настройки рисунка 8: что показать, подписи и линии. Отдаёт: ничего."""
        d = LOAD_DEFAULTS                            # умолчания — как в Word
        self._section("Что показать")
        self.combo_type = self._add_combo("type", "Средства:", [(None, ALL)], d["type"])   # все или тип
        self._add_combo("order", "Порядок:", ORDERS, d["order"])
        self._add_check("horizontal", "горизонтальные столбцы", d["horizontal"])
        self._section("Подписи и линии")
        self._add_check("values", "числа у столбцов", d["values"])
        self._add_check("mean", "линия средней нагрузки", d["mean"])
        self._add_check("full_scale", "шкала до 100 %", d["full_scale"])
        self._add_check("grid", "сетка", d["grid"])
        self._add_check("legend", "легенда по типам", d["legend"])

    def _on_snapshot(self):
        """Новый снимок — список типов заново (выбор сохраняется, если тип есть). Отдаёт: ничего."""
        load = self._snap.get("load") or {}          # снимок нагрузки
        names = load.get("names", {})                # номер типа -> название
        keep = self.combo_type.currentData()         # выбранный тип; None — все
        self.combo_type.blockSignals(True)
        self.combo_type.clear()
        self.combo_type.addItem(ALL, None)
        for t in sorted({r["type_id"] for r in load.get("rows", [])}):   # тип на карте — строка выбора
            self.combo_type.addItem("№%d %s" % (t, names.get(t, "тип %d" % t)), t)
        i = self.combo_type.findData(keep)           # прежний выбор в новом списке
        self.combo_type.setCurrentIndex(i if keep is not None and i >= 0 else 0)
        self.combo_type.blockSignals(False)

    def _draw(self, fig, snap, opts, colors):
        """Рисунок 8. Вход: рисунок, снимок, настройки, цвета. Отдаёт: оси."""
        return draw_load(fig, snap.get("load") or {}, opts, colors)

    def _info(self):
        """Пояснение: выборка, цель, сколько средств, средняя и наибольшая нагрузка. Отдаёт: список строк."""
        load = self._snap.get("load") or {}          # снимок нагрузки
        rows = load.get("rows", [])                  # строки средств
        tid = self.combo_type.currentData()          # выбранный тип; None — все
        if tid is not None:                          # один тип — только его средства
            rows = [r for r in rows if r["type_id"] == tid]
        tgt = load.get("target")                     # номер цели; None — все цели
        whom = "все цели" if tgt is None else "маршруты к цели №%d" % (int(tgt) + 1)   # чья выборка
        out = ["Выборка: %s, %d маршрутов (%s). Нагрузка — доля маршрутов, прошедших через зону средства."
               % (SOURCE.get(load.get("source"), "—"), int(load.get("n_routes", 0)), whom)]   # пояснение
        if rows:                                     # есть что сводить
            top = max(rows, key=lambda r: r["load_pct"])   # самое нагруженное средство
            out.append("Средств: %d, без пролёта %d · средняя нагрузка %.1f %% · наибольшая — №%d, %.1f %%."
                       % (len(rows), sum(1 for r in rows if r["routes"] == 0),
                          sum(r["load_pct"] for r in rows) / len(rows), top["n"], top["load_pct"]))
        return out
