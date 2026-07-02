# -*- coding: utf-8 -*-
"""
CONTROLLER (Qt) · Вкладка 3 «Цифровая карта угроз».

Связывает model.threat_grid.ThreatModel (построение весовой карты + расстановка
датчиков по весам) с view_qt.threat_view.ThreatMapView (ограниченная карта участка,
векторные слои, тепловой слой весов, датчики). Анимации нет — карта статична, датчики
считаются одним проходом (без итераций накопления, как и просил пользователь).
"""
import numpy as np

from config import MODE_LABELS, THREAT_LAYERS, THREAT_CELL_M


class ThreatController:

    def __init__(self, model, view):
        self.model = model
        self.view = view
        view.set_callbacks(
            on_build=self.on_build, on_place=self.on_place, on_apply=self.on_apply,
            on_reset_view=self.on_reset_view, on_mode=self.on_mode,
            on_toggle=self.on_toggle, on_map_layer=self.on_map_layer,
            on_map_offline=self.on_map_offline)
        self._idle_metrics()

    # ---- построение карты ----
    def on_build(self):
        self.view.flash_title("Строю весовую карту угроз…")
        self.view.process_pending()
        self.model.build()
        self.view.set_source(self.model.source)
        self.model.candidates = self.model.candidate_positions()
        self._render_all()
        self.view.set_title("Карта угроз построена. «Расставить датчики».")
        self._full_metrics()

    def on_place(self):
        if self.model.grid is None:
            self.on_build()
        self.view.flash_title("Расставляю датчики по весам…")
        self.view.process_pending()
        self.model.place_sensors()
        self._render_all()
        me = self.model.metrics()
        self.view.set_title(
            f"Датчиков {me['n_sensors']} · покрыто веса "
            f"{me['covered_frac']*100:.0f}% · режим {MODE_LABELS[self.model.p.mode]}")
        self._full_metrics()

    # ---- параметры / режим ----
    def on_apply(self):
        try:
            vals = self.view.get_param_values()
        except (ValueError, TypeError):
            self.view.set_param_values(self.model.p)
            self.view.flash_title("Ошибка ввода: проверьте числовые поля.")
            return
        for n, v in vals.items():
            setattr(self.model.p, n, v)
        if self.model.p.threat_N < 1 or self.model.p.threat_R <= 0:
            self.view.flash_title("N ≥ 1 и R > 0.")
            return
        if self.model.grid is None:
            self.on_build()
        self.model.candidates = self.model.candidate_positions()
        if len(self.model.sensors):
            self.on_place()
        else:
            self._render_all()
            self._full_metrics()

    def on_mode(self, key):
        self.model.p.mode = key
        if len(self.model.sensors):
            self.on_place()

    def on_toggle(self):
        self._render_all()

    def on_map_layer(self, key):
        self.model.p.map_layer3 = key

    def on_map_offline(self, flag):
        self.model.p.map_offline3 = bool(flag)

    def on_reset_view(self):
        self.view.frame_bbox()

    # ---- отрисовка ----
    def _render_all(self):
        t = self.view.get_toggles()
        g = self.model.grid
        entry, target = self.model.entry_target_km()
        self.view.render_entry_target(entry, target)
        if g is None:
            return
        extent = g.extent_km()
        self.view.render_threat(g.weight, extent, t)
        self.view.render_water(g.water_mask(), extent, t)
        self.view.render_layers(self.model.layers,
                                self.model.layers.get("bridge_pts", []), t)
        if t["show_cand"] and len(self.model.candidates) == 0:
            self.model.candidates = self.model.candidate_positions()
        self.view.render_candidates(self.model.candidates, t)
        self.view.render_sensors(self.model.sensors, self.model.p.threat_R)

    # ---- показатели ----
    def _idle_metrics(self):
        kx0, kx1, ky0, ky1 = self.model.bbox_km
        cell = THREAT_CELL_M / 1000.0
        nx = int(round((kx1 - kx0) / cell)); ny = int(round((ky1 - ky0) / cell))
        self.view.set_metrics([
            "ВКЛАДКА 3 · цифровая карта угроз", "",
            "Участок (район Северодонецка):",
            f"  {kx1-kx0:.0f}×{ky1-ky0:.0f} км",
            f"  сетка {cell*1000:.0f} м → {nx}×{ny} = {nx*ny} ячеек", "",
            "1) «Построить карту» — наложение слоёв",
            "   (реки/дороги/ЛЭП/мосты/…) на сетку;",
            "2) «Расставить датчики» — по сумме весов",
            "   (макс. покрытого веса + разнос),",
            "   без датчиков на воде.", "",
            "Веса слоёв — в config.THREAT_LAYERS.",
        ])

    def _full_metrics(self):
        me = self.model.metrics()
        p = self.model.p
        kx0, kx1, ky0, ky1 = self.model.bbox_km
        cell = THREAT_CELL_M / 1000.0
        lines = [
            "ГЕОМЕТРИЯ",
            f"  участок {kx1-kx0:.0f}×{ky1-ky0:.0f} км",
            f"  ячейка {cell*1000:.0f} м",
            f"  источник: {self.model.source}", "",
            "РЕСУРС",
            f"  N={p.threat_N}  R={p.threat_R:g} км  k={p.threat_k}",
            f"  режим: {MODE_LABELS[p.mode]}",
        ]
        if me:
            lines += [
                f"  кандидатов: {me.get('n_candidates', 0)}", "",
                "ВЕС КАРТЫ (по слоям)",
            ]
            by = me.get("by_layer", {})
            for name in sorted(by, key=lambda n: -abs(by[n])):
                lab = THREAT_LAYERS.get(name, {}).get("label", name)
                lines.append(f"  {lab:<18}{by[name]:+8.0f}")
            lines += [
                "",
                "РАССТАНОВКА",
                f"  датчиков: {me.get('n_sensors', 0)}",
                f"  покрытый вес: {me.get('covered_weight', 0):.0f} / "
                f"{me.get('total_weight', 0):.0f}",
                f"  доля покрытия: {me.get('covered_frac', 0)*100:.0f}%",
            ]
        self.view.set_metrics(lines)

    def run(self):
        self.view.show()
