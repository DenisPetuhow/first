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
| файлов кода | 86 |
| строк кода | 38504 ≈ 500 тыс. токенов |
| строк карты | 1536 ≈ 13 тыс. токенов |
| частей | 2 (порог деления — 1500 строк) |

## Модули: где искать

| Модуль | Строк | ≈токенов | Классов | Функций | Где в карте |
|---|---|---|---|---|---|
| `view_qt/threat_view.py` | 5319 | 69147 | 4 | 235 | [КАРТА_1.md](КАРТА_1.md) стр. **10** |
| `model/threat_grid.py` | 5260 | 68380 | 2 | 242 | [КАРТА_1.md](КАРТА_1.md) стр. **271** |
| `controller_qt/threat_controller.py` | 2106 | 27378 | 3 | 118 | [КАРТА_1.md](КАРТА_1.md) стр. **547** |
| `tools/gui_check.py` | 2046 | 26598 | 0 | 2 | [КАРТА_1.md](КАРТА_1.md) стр. **706** |
| `model/threat_routes.py` | 1713 | 22269 | 0 | 42 | [КАРТА_1.md](КАРТА_1.md) стр. **715** |
| `tools/переносимое/make_figures.py` | 1090 | 14170 | 0 | 26 | [КАРТА_1.md](КАРТА_1.md) стр. **767** |
| `config.py` | 1073 | 13949 | 1 | 3 | [КАРТА_1.md](КАРТА_1.md) стр. **799** |
| `tools/flow_check.py` | 852 | 11076 | 0 | 3 | [КАРТА_1.md](КАРТА_1.md) стр. **821** |
| `view_qt/map_image.py` | 757 | 9841 | 1 | 24 | [КАРТА_1.md](КАРТА_1.md) стр. **830** |
| `model/trajectories.py` | 708 | 9204 | 0 | 39 | [КАРТА_1.md](КАРТА_1.md) стр. **861** |
| `view_qt/sensors_window.py` | 619 | 8047 | 1 | 32 | [КАРТА_1.md](КАРТА_1.md) стр. **908** |
| `tools/motion_check.py` | 603 | 7839 | 0 | 12 | [КАРТА_1.md](КАРТА_1.md) стр. **952** |
| `view_qt/input_window.py` | 559 | 7267 | 2 | 26 | [КАРТА_1.md](КАРТА_1.md) стр. **971** |
| `model/map_fit.py` | 542 | 7046 | 0 | 16 | [КАРТА_1.md](КАРТА_1.md) стр. **1011** |
| `view_qt/geomap.py` | 535 | 6955 | 0 | 25 | [КАРТА_1.md](КАРТА_1.md) стр. **1033** |
| `view_qt/view_qt.py` | 514 | 6682 | 2 | 45 | [КАРТА_1.md](КАРТА_1.md) стр. **1064** |
| `tools/переносимое/codemap.py` | 488 | 6344 | 0 | 16 | [КАРТА_1.md](КАРТА_1.md) стр. **1119** |
| `view_qt/basemap_mixin.py` | 423 | 5499 | 3 | 16 | [КАРТА_1.md](КАРТА_1.md) стр. **1143** |
| `tools/pack_area.py` | 416 | 5408 | 0 | 15 | [КАРТА_1.md](КАРТА_1.md) стр. **1170** |
| `view/view.py` | 414 | 5382 | 1 | 33 | [КАРТА_1.md](КАРТА_1.md) стр. **1191** |
| `tools/переносимое/docmap.py` | 399 | 5187 | 0 | 12 | [КАРТА_1.md](КАРТА_1.md) стр. **1238** |
| `tools/reference_cases.py` | 399 | 5187 | 0 | 6 | [КАРТА_1.md](КАРТА_1.md) стр. **1261** |
| `model/optimization.py` | 398 | 5174 | 1 | 11 | [КАРТА_1.md](КАРТА_1.md) стр. **1275** |
| `model/area_start.py` | 396 | 5148 | 1 | 28 | [КАРТА_1.md](КАРТА_1.md) стр. **1295** |
| `view_qt/sensor_types_window.py` | 367 | 4771 | 1 | 19 | [КАРТА_1.md](КАРТА_1.md) стр. **1337** |
| `area_file.py` | 365 | 4745 | 1 | 14 | [КАРТА_1.md](КАРТА_1.md) стр. **1363** |
| `main_qt.py` | 362 | 4706 | 0 | 5 | [КАРТА_1.md](КАРТА_1.md) стр. **1384** |
| `model/sensor_zones.py` | 360 | 4680 | 0 | 19 | [КАРТА_1.md](КАРТА_1.md) стр. **1395** |
| `view_qt/tile_layer.py` | 338 | 4394 | 5 | 17 | [КАРТА_1.md](КАРТА_1.md) стр. **1422** |
| `tools/plot_run.py` | 331 | 4303 | 0 | 8 | [КАРТА_1.md](КАРТА_1.md) стр. **1452** |
| `tools/reference_run.py` | 324 | 4212 | 0 | 2 | [КАРТА_1.md](КАРТА_1.md) стр. **1468** |
| `view_qt/area_view.py` | 306 | 3978 | 1 | 18 | [КАРТА_2.md](КАРТА_2.md) стр. **10** |
| `model/sensor_types.py` | 303 | 3939 | 3 | 15 | [КАРТА_2.md](КАРТА_2.md) стр. **40** |

## Мелкие модули — читаются целиком

Короче 300 строк: открыть файл дешевле, чем расписывать по функциям.

| Модуль | Строк | Что внутри |
|---|---|---|
| `tools/переносимое/check_names.py` | 285 | `_find_root`, `collect_code_names`, `is_noise`, `docs`, `main` |
| `controller/controller.py` | 285 | `SimulationController`, `__init__`, `_make_timer`, `_toggles`, `_layers`, `on_apply`, `on_mode`, `on_traj`, `on_profile`, `on_law`, `on_speed`, `on_toggle`, … (27 всего) |
| `view_qt/map_anchor.py` | 275 | `MapAnchor`, `__init__`, `choose_model`, `matrix`, `_from_bbox`, `_from_points`, `px_to_km`, `invert`, `km_to_px`, `px_box_for_view`, `corners_km`, `bbox_km`, … (17 всего) |
| `tools/переносимое/check_claims.py` | 266 | `_find_root`, `docs`, `paragraphs`, `selftest`, `main` |
| `tools/placement_check.py` | 265 | `build_model`, `pair_stats`, `measure`, `print_table`, `exp_k`, `cross_rule_stats`, `exp_spread`, `exp_kmode`, `main` |
| `model/target_stage.py` | 261 | `area_mask`, `target_zone`, `capacity`, `floors`, `approach_share`, `first_line`, `approach`, `gain_pct`, `level_pct`, `_free`, `protection`, `stage` |
| `tools/переносимое/check_dates.py` | 258 | `folder_log_name`, `_find_root`, `git_date`, `_key`, `docs`, `git_rows`, `write_folder_logs`, `main` |
| `tools/tiles_check.py` | 227 | `check`, `_merc_lat_of_row`, `_mosaic_y`, `main` |
| `model/joint_greedy.py` | 227 | `_Cover`, `__init__`, `_Kind`, `__init__`, `phi`, `gains`, `note_bound`, `bound`, `top_gain`, `_take`, `_exclude`, `joint_greedy` |
| `tools/переносимое/check_comments.py` | 224 | `_find_root`, `_run`, `repo_root`, `_abs`, `changed_lines`, `doc_lines`, `head_span`, `check_file`, `main` |
| `view_qt/map_vector.py` | 220 | `is_vector`, `svg_size`, `inspect_svg`, `describe_content`, `make_item`, `place_item`, `render_for_fit`, `measure_draw` |
| `model/simulation.py` | 217 | `SimulationModel`, `__init__`, `reset`, `_compute_view_bbox`, `set_traj_model`, `corridor_bbox`, `corridor_outline`, `view_bbox`, `sample_trajectory`, `_anchor_idx`, `frequent_paths`, `fan_paths`, … (15 всего) |
| `tools/area_check.py` | 214 | `check`, `rejects`, `config_under`, `main` |
| `tools/переносимое/check_dead_code.py` | 212 | `find_root`, `py_files`, `collect_defs`, `collect_uses`, `unused_functions`, `unused_config`, `main` |
| `view_qt/targets_window.py` | 203 | `TargetsDialog`, `__init__`, `refresh`, `_add_row`, `_edited`, `_clear`, `_with_row` |
| `model/flight_log.py` | 194 | `routes_dir`, `default_file_name`, `_route_length_km`, `save_flights`, `split_repeats`, `load_flights`, `check_compatible` |
| `model/sensors.py` | 191 | `SensorSpec`, `active`, `name`, `label`, `ManualSensor`, `_num`, `sensor_types`, `_n_types`, `type_ids`, `active_small`, `min_small_radius`, `format_types_header` |
| `view_qt/route_viewer.py` | 182 | `RouteViewerDialog`, `__init__`, `set_generalize_state`, `set_data`, `_current_source`, `_refresh_list`, `_show_route`, `_show_on_map`, `_reset_preview` |
| `tools/area_bounds.py` | 179 | `_varint`, `_zigzag`, `_fields`, `pbf_bbox`, `raster_bounds`, `main` |
| `view_qt/measure_tool.py` | 177 | `format_time`, `MeasureTool`, `__init__`, `reset`, `add_point`, `move_to`, `status`, `total_km`, `_hours`, `_text`, `_label`, `_angle_deg`, … (14 всего) |
| `model/sensor_track.py` | 172 | `hungarian`, `_hungarian_square`, `match_by_type`, `total_movement` |
| `tools/download_tiles.py` | 166 | `_area_bbox`, `_area_tile_dir`, `_pad_km`, `main` |
| `model/uav_types.py` | 166 | `UavType`, `validate`, `turn_radius_km`, `FixedWing`, `Multirotor`, `turn_radius_km`, `defaults`, `path`, `_num`, `to_row`, `from_row`, `load`, … (14 всего) |
| `view_qt/uav_types_window.py` | 161 | `UavTypesDialog`, `__init__`, `_fill`, `_add_row`, `_row_type`, `_collect`, `_add`, `_remove`, `_reset`, `_save` |
| `model/targets.py` | 161 | `Target`, `equal_share`, `TargetSet`, `__init__`, `__len__`, `points_km`, `add`, `replace_all`, `move`, `set_area`, `clear`, `weight_sum`, … (20 всего) |
| `controller_qt/area_controller.py` | 152 | `AreaStartController`, `__init__`, `_sync_geometry`, `_redraw_idle_or_last`, `_update_metrics_idle`, `on_create`, `on_route_ready`, `on_map_layer`, `on_map_offline`, `on_movement`, `on_zone`, `on_apply`, … (17 всего) |
| `tools/perf_check.py` | 150 | `measure`, `_len`, `main` |
| `tools/metrics_check.py` | 148 | `check`, `main` |
| `tools/map_fit.py` | 148 | `main` |
| `view_qt/area_choice.py` | 146 | `_remember`, `current_area_folder`, `_size_text`, `restart_program`, `choose_area`, `choose_tile_root` |
| `tools/inspect_tile_cache.py` | 142 | `_list_dirs`, `_list_files`, `print_tree`, `probe_sample_files`, `guess_xyz_or_tms`, `main` |
| `tools/переносимое/check_numbers.py` | 139 | `_find_root`, `config_values`, `docs`, `same`, `main` |
| `view_qt/sector_window.py` | 136 | `SectorDialog`, `__init__`, `_params_changed`, `_apply_params`, `set_state` |
| `view_qt/entries_table.py` | 132 | `EntriesTableDialog`, `__init__`, `refresh`, `_add_row`, `_edited`, `_reset_all` |
| `model/geo_frame.py` | 131 | `km_per_deg_lon`, `lonlat_to_km`, `km_to_lonlat`, `bbox_lonlat_to_km`, `bbox_size_km`, `_dms`, `format_dms`, `format_deg`, `format_rad`, `format_point` |
| `tools/tile_coverage.py` | 115 | `count_layer`, `main` |
| `model/route_model.py` | 106 | `RouteModelBase`, `set_mode`, `set_profile`, `weights`, `recompute_placement`, `add_and_replace`, `step`, `run_batch`, `compare_modes`, `live_metrics` |
| `view_qt/sim_conditions_window.py` | 100 | `SimConditionsDialog`, `__init__`, `_type_changed`, `set_state` |
| `tools/build_threat_grid.py` | 98 | `main` |
| `model/detection.py` | 80 | `_min_dist_point_to_sensors`, `n_detections`, `continuous_coverage`, `segment_coverage`, `utility`, `avg_utility` |
| `model/geometry.py` | 76 | `ellipse_geometry`, `point_in_ellipse`, `points_in_ellipse`, `auto_axes_limits`, `geo_to_local_km` |
| `view_qt/ui_common.py` | 73 | `apply_dark_theme`, `make_side_panel` |
| `view_qt/analysis_window.py` | 70 | `AnalysisDialog`, `__init__`, `refresh`, `_show` |
| `view_qt/ui_state.py` | 63 | `_path`, `load_all`, `get`, `save` |
| `tools/переносимое/hook_bez_cd.py` | 57 | `main` |
| `controller_qt/controller_qt.py` | 34 | `_QtTimer`, `__init__`, `add_callback`, `start`, `stop`, `QtSimulationController`, `_make_timer` |
| `main.py` | 29 | `main` |

**Пустые модули** (задают пакет, содержимого нет): `check_env.py`, `controller/__init__.py`, `controller_qt/__init__.py`, `model/__init__.py`, `view/__init__.py`, `view_qt/__init__.py`.

