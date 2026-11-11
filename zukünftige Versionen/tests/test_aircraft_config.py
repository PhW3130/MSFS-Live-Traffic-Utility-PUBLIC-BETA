import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from aircraft_config import (
    AircraftTitleCatalog,
    discover_community_folders,
    normalize_community_folder,
    parse_aircraft_cfg,
)


def _write_aircraft(community, package, folder, type_code, title):
    cfg = (
        community / package / "SimObjects" / "Airplanes" / folder / "aircraft.cfg"
    )
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        "[GENERAL]\n"
        f"icao_type_designator={type_code}\n"
        "[FLTSIM.0]\n"
        f'title="{title}"\n',
        encoding="utf-8",
    )
    return cfg


def test_parse_aircraft_cfg_reads_title_and_icao_type(tmp_path):
    cfg = _write_aircraft(
        tmp_path, "fsltl-aircraft", "A320", "A20N", "FSLTL A20N BAW British Airways"
    )

    models = parse_aircraft_cfg(cfg)

    assert len(models) == 1
    assert models[0].title == "FSLTL A20N BAW British Airways"
    assert models[0].icao_type == "A20N"
    assert models[0].config_path == str(cfg.resolve())


def test_parse_aircraft_cfg_infers_code_from_compound_atc_model(tmp_path):
    cfg = tmp_path / "aircraft.cfg"
    cfg.write_text(
        "[GENERAL]\natc_model=Airbus A320-214\n"
        "[FLTSIM.0]\ntitle=FSLTL A320 BAW British Airways\n",
        encoding="utf-8",
    )

    models = parse_aircraft_cfg(cfg)

    assert models[0].icao_type == "A320"


def test_catalog_resolves_aircraft_by_type_airline_and_preferred_title(tmp_path):
    community = tmp_path / "Community"
    _write_aircraft(community, "fsltl", "A320-BAW", "A20N", "FSLTL A20N BAW British Airways")
    _write_aircraft(community, "fsltl", "A320-DLH", "A20N", "FSLTL A20N DLH Lufthansa")
    catalog = AircraftTitleCatalog(community)

    assert catalog.resolve_title("A20N", callsign="BAW123") == "FSLTL A20N BAW British Airways"
    assert catalog.resolve_title("A20N", callsign="DLH456") == "FSLTL A20N DLH Lufthansa"
    assert catalog.resolve_title(
        "B738", preferred_title="FSLTL A20N DLH Lufthansa"
    ) == "FSLTL A20N DLH Lufthansa"
    assert catalog.default_title() == "FSLTL A20N BAW British Airways"


def test_catalog_reports_scan_progress_against_actual_config_count(tmp_path):
    community = tmp_path / "Community"
    _write_aircraft(community, "addon", "A320", "A20N", "FSLTL A20N BAW")
    _write_aircraft(community, "addon", "B738", "B738", "FSLTL B738 RYR")
    progress = []

    AircraftTitleCatalog(
        community,
        progress_callback=lambda scanned, total, path: progress.append(
            (scanned, total, path)
        ),
    )

    assert progress[0][:2] == (0, 2)
    assert progress[-1][:2] == (2, 2)
    assert [item[0] for item in progress] == sorted(item[0] for item in progress)


def test_normalize_community_folder_accepts_packages_root(tmp_path):
    packages = tmp_path / "Packages"
    community = packages / "Community"
    community.mkdir(parents=True)

    assert normalize_community_folder(packages) == str(community.resolve())
    assert normalize_community_folder(community) == str(community.resolve())
    assert normalize_community_folder(tmp_path / "missing") == ""


def test_discovery_reads_multiple_simulator_usercfg_paths(tmp_path):
    appdata = tmp_path / "Roaming"
    local_app_data = tmp_path / "Local"
    first_packages = tmp_path / "MSFS2020" / "Packages"
    second_packages = tmp_path / "MSFS2024" / "Packages"
    first_community = first_packages / "Community"
    second_community = second_packages / "Community"
    first_community.mkdir(parents=True)
    second_community.mkdir(parents=True)
    for simulator, packages in (
        ("Microsoft Flight Simulator", first_packages),
        ("Microsoft Flight Simulator 2024", second_packages),
    ):
        config = appdata / simulator / "UserCfg.opt"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(f'InstalledPackagesPath "{packages}"\n', encoding="utf-8")

    found = discover_community_folders(
        environ={}, appdata=appdata, local_app_data=local_app_data
    )

    assert found == sorted(
        (str(first_community.resolve()), str(second_community.resolve())),
        key=str.casefold,
    )


def test_discovery_supports_explicit_environment_override(tmp_path):
    community = tmp_path / "Community"
    community.mkdir()
    other_community = tmp_path / "OtherInstall" / "Packages" / "Community"
    other_community.mkdir(parents=True)
    appdata = tmp_path / "Roaming"
    config = appdata / "Microsoft Flight Simulator" / "UserCfg.opt"
    config.parent.mkdir(parents=True)
    config.write_text(
        f'InstalledPackagesPath "{other_community.parent}"\n', encoding="utf-8"
    )

    found = discover_community_folders(
        environ={"MSFS_COMMUNITY_PATH": str(community)},
        appdata=appdata,
        local_app_data=tmp_path / "Local",
    )

    assert found == [str(community.resolve())]
