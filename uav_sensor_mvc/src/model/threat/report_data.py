# -*- coding: utf-8 -*-
"""
MODEL · Снимок данных для рисунков отчёта — модуль вывода «Аналитики» (рисунки 5–9 «Описания ПК», заказчик 06.10.2026).

Одним словарём то, что рисуют окна «Показ карты», «Анализ нагрузки», «Показ сектора» и генератор Word
(`docs/make_pk_pictures.py`): весовая сетка, маршруты выборки, средства с радиусами, типы, цели, точки входа, нагрузка
(`sensor_load`) и засечка по 12 направлениям (`sector_stats`). Рисует `view_qt/output/figures.py`.
⚠️ ТОЛЬКО ЧИТАЕТ модель: расстановку, выборку и показатели не трогает; без Qt.
"""
import numpy as np


def report_data(m, target=None):
    """Снимок модели для рисунков. Вход: `ThreatModel`, None — все цели, номер с нуля — маршруты одной цели.
    Отдаёт: dict(grid, bbox, routes, sensors, types, targets, entries, load, sectors, k, target, source)."""
    from model.placement.sensors import sensor_types
    g = m.grid                                       # весовая сетка; None — карта не построена
    grid = None if g is None else dict(weight=np.asarray(g.weight), ox=float(g.ox), oy=float(g.oy), h=float(g.h),
                                       nx=int(g.nx), ny=int(g.ny))   # сетка для фона
    bbox = None if g is None else (float(g.ox), float(g.ox + g.nx * g.h), float(g.oy), float(g.oy + g.ny * g.h))   # рамка сетки, км
    load = m.sensor_load(target)                     # нагрузка: строки, выборка, названия; цели нет — все цели
    tgt = load.get("target")                         # номер цели, если такая есть; иначе None
    parts = m.routes_by_target() if len(m.target_points_km()) else []   # выборка по целям — как у «Анализа»
    routes = (parts[tgt] if tgt is not None else [r for p in parts for r in p])   # маршруты для рисунка, км
    kinds = {s.type_id: s for s in sensor_types(m.p)}   # паспорта типов: номер -> тип
    types = {t: dict(name=s.name, r_km=float(s.r_km)) for t, s in kinds.items()}   # имя и радиус типа, км
    sensors = [dict(n=int(r["n"]), type_id=int(r["type_id"]), x_km=float(r["x_km"]), y_km=float(r["y_km"]),
                    r_km=types.get(int(r["type_id"]), {}).get("r_km", float(m.p.threat_R)))
               for r in m.sensors_table()]           # средства на карте с радиусом своего типа, км
    return dict(grid=grid, bbox=bbox, routes=list(routes), sensors=sensors, types=types,
                targets=[tuple(map(float, t)) for t in m.target_points_km()],
                entries=[tuple(map(float, e)) for e in np.asarray(m.entry_points_km(), float).reshape(-1, 2)],
                load=load, sectors=m.sector_stats(tgt), k=int(m.p.threat_k), target=tgt,
                source=load.get("source"))
