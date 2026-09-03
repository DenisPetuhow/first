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
| файлов кода | 42 |
| строк кода | 16503 ≈ 214 тыс. токенов |
| строк карты | 739 ≈ 6 тыс. токенов |
| частей | 1 (порог деления — 1500 строк) |

## Модули: где искать

| Модуль | Строк | ≈токенов | Классов | Функций | Где в карте |
|---|---|---|---|---|---|
| `model/threat_grid.py` | 2986 | 38818 | 2 | 126 | [КАРТА_1.md](КАРТА_1.md) стр. **10** |
| `view_qt/threat_view.py` | 2584 | 33592 | 5 | 118 | [КАРТА_1.md](КАРТА_1.md) стр. **161** |
| `model/threat_routes.py` | 1217 | 15821 | 0 | 26 | [КАРТА_1.md](КАРТА_1.md) стр. **300** |
| `tools/переносимое/make_figures.py` | 1090 | 14170 | 0 | 26 | [КАРТА_1.md](КАРТА_1.md) стр. **334** |
| `controller_qt/threat_controller.py` | 797 | 10361 | 3 | 48 | [КАРТА_1.md](КАРТА_1.md) стр. **366** |
| `config.py` | 782 | 10166 | 1 | 3 | [КАРТА_1.md](КАРТА_1.md) стр. **441** |
| `model/trajectories.py` | 708 | 9204 | 0 | 39 | [КАРТА_1.md](КАРТА_1.md) стр. **462** |
| `view_qt/view_qt.py` | 514 | 6682 | 2 | 45 | [КАРТА_1.md](КАРТА_1.md) стр. **507** |
| `tools/переносимое/codemap.py` | 451 | 5863 | 0 | 15 | [КАРТА_1.md](КАРТА_1.md) стр. **562** |
| `view_qt/geomap.py` | 429 | 5577 | 0 | 20 | [КАРТА_1.md](КАРТА_1.md) стр. **585** |
| `view/view.py` | 414 | 5382 | 1 | 33 | [КАРТА_1.md](КАРТА_1.md) стр. **611** |
| `model/area_start.py` | 396 | 5148 | 1 | 28 | [КАРТА_1.md](КАРТА_1.md) стр. **658** |
| `main_qt.py` | 340 | 4420 | 0 | 5 | [КАРТА_1.md](КАРТА_1.md) стр. **700** |
| `view_qt/area_view.py` | 306 | 3978 | 1 | 18 | [КАРТА_1.md](КАРТА_1.md) стр. **711** |

## Мелкие модули — читаются целиком

Короче 300 строк: открыть файл дешевле, чем расписывать по функциям.

| Модуль | Строк | Что внутри |
|---|---|---|
| `controller/controller.py` | 285 | `SimulationController`, `__init__`, `_make_timer`, `_toggles`, `_layers`, `on_apply`, `on_mode`, `on_traj`, `on_profile`, `on_law`, `on_speed`, `on_toggle`, … (27 всего) |
| `model/optimization.py` | 274 | `_popcount`, `filter_not_past_target`, `candidate_grid`, `CoverageCache`, `__init__`, `n_traj`, `add_trajectory`, `routes_covered_by_candidate`, `greedy`, `set_weighted_cells`, `greedy_weighted` |
| `tools/переносимое/docmap.py` | 272 | `_find_root`, `norm`, `walk_md`, `build_toc`, `apply_toc`, `verify_toc`, `check_links`, `check_structure`, `main` |
| `view_qt/basemap_mixin.py` | 233 | `_BasemapSignals`, `_BasemapTask`, `__init__`, `run`, `BasemapMixin`, `_init_basemap`, `_set_view_limits`, `_refresh_basemap`, `_on_basemap_ready`, `_fade_basemap`, `_set_scheme_visible`, `_draw_scheme`, … (15 всего) |
| `tools/переносимое/check_claims.py` | 229 | `_find_root`, `docs`, `paragraphs`, `selftest`, `main` |
| `model/simulation.py` | 217 | `SimulationModel`, `__init__`, `reset`, `_compute_view_bbox`, `set_traj_model`, `corridor_bbox`, `corridor_outline`, `view_bbox`, `sample_trajectory`, `_anchor_idx`, `frequent_paths`, `fan_paths`, … (15 всего) |
| `tools/переносимое/check_names.py` | 214 | `_find_root`, `collect_code_names`, `is_noise`, `docs`, `main` |
| `tools/переносимое/check_dead_code.py` | 206 | `find_root`, `py_files`, `collect_defs`, `collect_uses`, `unused_functions`, `unused_config`, `main` |
| `tools/reference_run.py` | 201 | `_fmt`, `main` |
| `tools/area_bounds.py` | 179 | `_varint`, `_zigzag`, `_fields`, `pbf_bbox`, `raster_bounds`, `main` |
| `controller_qt/area_controller.py` | 152 | `AreaStartController`, `__init__`, `_sync_geometry`, `_redraw_idle_or_last`, `_update_metrics_idle`, `on_create`, `on_route_ready`, `on_map_layer`, `on_map_offline`, `on_movement`, `on_zone`, `on_apply`, … (17 всего) |
| `tools/inspect_tile_cache.py` | 142 | `_list_dirs`, `_list_files`, `print_tree`, `probe_sample_files`, `guess_xyz_or_tms`, `main` |
| `tools/переносимое/check_numbers.py` | 135 | `_find_root`, `config_values`, `docs`, `same`, `main` |
| `model/route_model.py` | 106 | `RouteModelBase`, `set_mode`, `set_profile`, `weights`, `recompute_placement`, `add_and_replace`, `step`, `run_batch`, `compare_modes`, `live_metrics` |
| `tools/tile_coverage.py` | 100 | `count_layer`, `main` |
| `model/detection.py` | 80 | `_min_dist_point_to_sensors`, `n_detections`, `continuous_coverage`, `segment_coverage`, `utility`, `avg_utility` |
| `model/geometry.py` | 76 | `ellipse_geometry`, `point_in_ellipse`, `points_in_ellipse`, `auto_axes_limits`, `geo_to_local_km` |
| `tools/download_tiles.py` | 74 | `main` |
| `view_qt/ui_common.py` | 73 | `apply_dark_theme`, `make_side_panel` |
| `tools/build_threat_grid.py` | 72 | `main` |
| `controller_qt/controller_qt.py` | 34 | `_QtTimer`, `__init__`, `add_callback`, `start`, `stop`, `QtSimulationController`, `_make_timer` |
| `main.py` | 29 | `main` |

**Пустые модули** (задают пакет, содержимого нет): `check_env.py`, `controller/__init__.py`, `controller_qt/__init__.py`, `model/__init__.py`, `view/__init__.py`, `view_qt/__init__.py`.

