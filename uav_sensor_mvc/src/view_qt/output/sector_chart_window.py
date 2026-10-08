# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Модуль вывода — окно «Показ сектора» (рисунок 9 «Описания ПК», заказчик 06.10.2026).

Кнопка «Показ сектора» окна «Аналитика»: сколько маршрутов приходит с каждого из 12 направлений по 30° и сколько из
них засечено. Направление — по ЗАХОДУ на свою цель: азимут от цели на конечный участок маршрута, а не на точку старта
(`threat_grid.py :: sector_stats`); 0° — север, по часовой. Настройки: цель, какие столбцы (маршрутов, засечено ≥ 1,
≥ k, без засечки), число или доля от выборки, подписи «засечено/всего», недобор кратности — красной рамкой, ширина
столбца, легенда, заголовок, шрифт; «Сохранить PNG…». Рисует `figures.py :: draw_sectors`.
"""
from view_qt.output.figure_base import FigureDialog
from view_qt.output.figures import SECTOR_DEFAULTS, draw_sectors


class SectorChartDialog(FigureDialog):
    """Окно рисунка направлений. Вход — как у `FigureDialog`; снимок — `report_data` (часть `sectors`)."""
    TITLE = "Показ сектора"                          # заголовок окна
    FILE = "направления_подлёта"                     # начало имени PNG
    SIZE_CM = (15.2, 15.2)                           # как рисунок 9 Word (6 × 6 дюймов)

    def _build_settings(self):
        """Настройки рисунка 9: какие столбцы, шкала, подписи. Отдаёт: ничего."""
        d = SECTOR_DEFAULTS                          # умолчания — как в Word
        self._section("Столбцы")
        self._add_check("routes", "маршрутов в секторе (серым)", d["routes"])
        self._add_check("ge1", "засечено хоть раз (светло-зелёным)", d["ge1"])
        self._add_check("gek", "засечено с кратностью k (зелёным)", d["gek"])
        self._add_check("zero", "без засечки (красным)", d["zero"])
        self._add_check("short", "недобор кратности — красной рамкой", d["short"])
        self._section("Шкала и подписи")
        self._add_check("share", "доля от выборки, % (иначе — число)", d["share"])
        self._add_check("values", "подписи «засечено ≥ k / всего»", d["values"])
        self._add_spin("width", "Ширина столбца:", d["width"], 5.0, 30.0, 1.0, 1, "°")
        self._add_check("legend", "легенда", d["legend"])

    def _draw(self, fig, snap, opts, colors):
        """Рисунок 9. Вход: рисунок, снимок, настройки, цвета (не нужны). Отдаёт: оси."""
        return draw_sectors(fig, snap.get("sectors") or [], opts)

    def _info(self):
        """Пояснение: что такое сектор, главное направление, где кратности не хватает. Отдаёт: список строк."""
        st = self._snap.get("sectors") or []         # строки 12 секторов
        n = sum(e["n_routes"] for e in st)           # маршрутов с направлением
        out = ["Сектор — 30° по направлению ЗАХОДА на цель (азимут от цели на конечный участок маршрута), 0° — север, "
               "по часовой; столбец — число прилётов из сектора. k = %d." % int(self._snap.get("k", 0))]   # пояснение
        if n:                                        # есть маршруты
            top = max(st, key=lambda e: e["n_routes"])   # сектор с наибольшим числом прилётов
            short = [e for e in st if not e.get("ok", True)]   # сектора с недобором кратности
            out.append("Маршрутов: %d · больше всего — с %d° (%d, %.0f %%) · засечено ≥ k: %d (%.1f %%)."
                       % (n, top["deg"], top["n_routes"], 100.0 * top["n_routes"] / n,
                          sum(e["n_ge_k"] for e in st), 100.0 * sum(e["n_ge_k"] for e in st) / n))
            out.append("Кратности не хватает: " + (", ".join("%d° (%d из %d)" % (e["deg"], e["n_routes"] - e["n_ge_k"],
                                                                                 e["n_routes"]) for e in short)
                                                   if short else "нигде."))
        return out
