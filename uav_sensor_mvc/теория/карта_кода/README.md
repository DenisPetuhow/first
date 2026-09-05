<!-- КАРТА КОДА · сгенерировано tools/переносимое/codemap.py — руками не править -->

# Карта кода — указатель

> **Что это.** Оглавление ИСХОДНИКОВ: где какая функция лежит, с какой строки и
> что делает. Читается вместо того, чтобы открывать модуль целиком.

> **Как пользоваться.** Найти модуль в таблице → открыть его часть → взять номер
> строки → `Read` с `offset`. Сигнатуру брать отсюда, а не по памяти.

> **Сгенерировано** `tools/переносимое/codemap.py`, руками не править.
> Пересобрать: `python tools/переносимое/codemap.py`;
> проверить свежесть: `--check`; что устарело в документации: `--stale`.

## Характеристики

| | |
|---|---|
| файлов кода | 49 |
| строк кода | 20445 ≈ 265 тыс. токенов |
| строк карты | 869 ≈ 7 тыс. токенов |
| частей | 1 (порог деления — 1500 строк) |

## Модули: где искать

| Модуль | Строк | ≈токенов | Классов | Функций | Где в карте |
|---|---|---|---|---|---|
| `model/threat_grid.py` | 3580 | 46540 | 2 | 145 | [КАРТА_1.md](КАРТА_1.md) стр. **10** |
| `view_qt/threat_view.py` | 3017 | 39221 | 4 | 132 | [КАРТА_1.md](КАРТА_1.md) стр. **183** |
| `model/threat_routes.py` | 1217 | 15821 | 0 | 26 | [КАРТА_1.md](КАРТА_1.md) стр. **338** |
| `tools/переносимое/make_figures.py` | 1090 | 14170 | 0 | 26 | [КАРТА_1.md](КАРТА_1.md) стр. **372** |
| `controller_qt/threat_controller.py` | 1086 | 14118 | 3 | 60 | [КАРТА_1.md](КАРТА_1.md) стр. **404** |
| `config.py` | 855 | 11115 | 1 | 3 | [КАРТА_1.md](КАРТА_1.md) стр. **494** |
| `view_qt/input_window.py` | 848 | 11024 | 2 | 38 | [КАРТА_1.md](КАРТА_1.md) стр. **515** |
| `model/trajectories.py` | 708 | 9204 | 0 | 39 | [КАРТА_1.md](КАРТА_1.md) стр. **568** |
| `view_qt/map_image.py` | 569 | 7397 | 1 | 17 | [КАРТА_1.md](КАРТА_1.md) стр. **613** |
| `view_qt/view_qt.py` | 514 | 6682 | 2 | 45 | [КАРТА_1.md](КАРТА_1.md) стр. **637** |
| `view_qt/geomap.py` | 481 | 6253 | 0 | 20 | [КАРТА_1.md](КАРТА_1.md) стр. **692** |
| `tools/переносимое/codemap.py` | 451 | 5863 | 0 | 15 | [КАРТА_1.md](КАРТА_1.md) стр. **718** |
| `view/view.py` | 414 | 5382 | 1 | 33 | [КАРТА_1.md](КАРТА_1.md) стр. **741** |
| `model/area_start.py` | 396 | 5148 | 1 | 28 | [КАРТА_1.md](КАРТА_1.md) стр. **788** |
| `main_qt.py` | 354 | 4602 | 0 | 5 | [КАРТА_1.md](КАРТА_1.md) стр. **830** |
| `view_qt/area_view.py` | 306 | 3978 | 1 | 18 | [КАРТА_1.md](КАРТА_1.md) стр. **841** |

## Мелкие модули — читаются целиком

Короче 300 строк: открыть файл дешевле, чем расписывать по функциям.

| Модуль | Строк | Что внутри |
|---|---|---|
| `model/optimization.py` | 285 | `_popcount`, `filter_not_past_target`, `candidate_grid`, `CoverageCache`, `__init__`, `n_traj`, `add_trajectory`, `routes_covered_by_candidate`, `greedy`, `set_weighted_cells`, `greedy_weighted` |
| `controller/controller.py` | 285 | `SimulationController`, `__init__`, `_make_timer`, `_toggles`, `_layers`, `on_apply`, `on_mode`, `on_traj`, `on_profile`, `on_law`, `on_speed`, `on_toggle`, … (27 всего) |
| `tools/переносимое/docmap.py` | 272 | `_find_root`, `norm`, `walk_md`, `build_toc`, `apply_toc`, `verify_toc`, `check_links`, `check_structure`, `main` |
| `tools/gui_check.py` | 269 | `check`, `main` |
| `view_qt/basemap_mixin.py` | 249 | `_BasemapSignals`, `_BasemapTask`, `__init__`, `run`, `BasemapMixin`, `_init_basemap`, `_set_view_limits`, `_refresh_basemap`, `_on_basemap_ready`, `_fade_basemap`, `_set_scheme_visible`, `_draw_scheme`, … (15 всего) |
| `tools/flow_check.py` | 243 | `check`, `step`, `main` |
| `tools/переносимое/check_names.py` | 242 | `_find_root`, `collect_code_names`, `is_noise`, `docs`, `main` |
| `tools/reference_run.py` | 240 | `_fmt`, `main` |
| `tools/переносимое/check_claims.py` | 229 | `_find_root`, `docs`, `paragraphs`, `selftest`, `main` |
| `model/simulation.py` | 217 | `SimulationModel`, `__init__`, `reset`, `_compute_view_bbox`, `set_traj_model`, `corridor_bbox`, `corridor_outline`, `view_bbox`, `sample_trajectory`, `_anchor_idx`, `frequent_paths`, `fan_paths`, … (15 всего) |
| `tools/переносимое/check_dead_code.py` | 206 | `find_root`, `py_files`, `collect_defs`, `collect_uses`, `unused_functions`, `unused_config`, `main` |
| `tools/area_bounds.py` | 179 | `_varint`, `_zigzag`, `_fields`, `pbf_bbox`, `raster_bounds`, `main` |
| `tools/download_tiles.py` | 166 | `_area_bbox`, `_area_tile_dir`, `_pad_km`, `main` |
| `controller_qt/area_controller.py` | 152 | `AreaStartController`, `__init__`, `_sync_geometry`, `_redraw_idle_or_last`, `_update_metrics_idle`, `on_create`, `on_route_ready`, `on_map_layer`, `on_map_offline`, `on_movement`, `on_zone`, `on_apply`, … (17 всего) |
| `tools/inspect_tile_cache.py` | 142 | `_list_dirs`, `_list_files`, `print_tree`, `probe_sample_files`, `guess_xyz_or_tms`, `main` |
| `model/sensors.py` | 139 | `SensorSpec`, `active`, `label`, `ManualSensor`, `_num`, `sensor_types`, `active_small`, `min_small_radius`, `format_types_header` |
| `tools/переносимое/check_numbers.py` | 135 | `_find_root`, `config_values`, `docs`, `same`, `main` |
| `model/geo_frame.py` | 129 | `km_per_deg_lon`, `lonlat_to_km`, `km_to_lonlat`, `bbox_lonlat_to_km`, `bbox_size_km`, `_dms`, `format_dms`, `format_deg`, `format_rad`, `format_point` |
| `tools/tile_coverage.py` | 115 | `count_layer`, `main` |
| `model/route_model.py` | 106 | `RouteModelBase`, `set_mode`, `set_profile`, `weights`, `recompute_placement`, `add_and_replace`, `step`, `run_batch`, `compare_modes`, `live_metrics` |
| `tools/build_threat_grid.py` | 98 | `main` |
| `model/detection.py` | 80 | `_min_dist_point_to_sensors`, `n_detections`, `continuous_coverage`, `segment_coverage`, `utility`, `avg_utility` |
| `model/geometry.py` | 76 | `ellipse_geometry`, `point_in_ellipse`, `points_in_ellipse`, `auto_axes_limits`, `geo_to_local_km` |
| `view_qt/ui_common.py` | 73 | `apply_dark_theme`, `make_side_panel` |
| `view_qt/ui_state.py` | 63 | `_path`, `load_all`, `get`, `save` |
| `controller_qt/controller_qt.py` | 34 | `_QtTimer`, `__init__`, `add_callback`, `start`, `stop`, `QtSimulationController`, `_make_timer` |
| `main.py` | 29 | `main` |

**Пустые модули** (задают пакет, содержимого нет): `check_env.py`, `controller/__init__.py`, `controller_qt/__init__.py`, `model/__init__.py`, `view/__init__.py`, `view_qt/__init__.py`.

