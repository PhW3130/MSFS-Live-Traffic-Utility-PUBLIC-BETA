"""Discover MSFS Community folders and read aircraft.cfg model titles."""

from dataclasses import dataclass
import os
from pathlib import Path
import re


_USERCFG_PATH_RE = re.compile(
    r"^\s*InstalledPackagesPath\s+(?P<path>.+?)\s*$", re.IGNORECASE
)
_TYPE_KEYS = ("icao_type_designator", "icao_type", "atc_model")


@dataclass(frozen=True)
class AircraftTitle:
    title: str
    icao_type: str
    config_path: str


def normalize_community_folder(path):
    """Return a usable Community path, accepting either it or its Packages root."""
    if not path:
        return ""
    raw = os.path.expandvars(os.path.expanduser(str(path).strip().strip('"')))
    if not raw:
        return ""
    candidate = Path(raw)
    try:
        if not candidate.is_dir():
            return ""
        if candidate.name.casefold() == "community":
            return str(candidate.resolve())
        nested = candidate / "Community"
        if nested.is_dir():
            return str(nested.resolve())
        if (candidate / "SimObjects" / "Airplanes").is_dir():
            return str(candidate.resolve())
    except OSError:
        return ""
    return ""


def _read_installed_packages_path(path):
    try:
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        match = _USERCFG_PATH_RE.match(line)
        if match:
            value = match.group("path").strip().strip('"').strip("'")
            return normalize_community_folder(value)
    return ""


def _existing_dirs(paths):
    for path in paths:
        try:
            if path.is_dir():
                yield str(path.resolve())
        except OSError:
            continue


def discover_community_folders(environ=None, appdata=None, local_app_data=None):
    """Find installed Community folders from MSFS package settings and common paths.

    MSFS Steam installs normally record InstalledPackagesPath in UserCfg.opt;
    Store installs commonly keep Packages under LocalCache. The environment
    overrides are useful for portable installs and troubleshooting.
    """
    env = os.environ if environ is None else environ
    explicit = normalize_community_folder(env.get("MSFS_COMMUNITY_PATH", ""))
    if not explicit:
        explicit = normalize_community_folder(env.get("MSFS_PACKAGES_PATH", ""))
    if explicit:
        return [explicit]

    appdata_root = Path(appdata or env.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    local_root = Path(
        local_app_data or env.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    )
    found = []

    usercfg_paths = {
        appdata_root / "Microsoft Flight Simulator" / "UserCfg.opt",
        appdata_root / "Microsoft Flight Simulator 2020" / "UserCfg.opt",
        appdata_root / "Microsoft Flight Simulator 2024" / "UserCfg.opt",
    }
    try:
        usercfg_paths.update(appdata_root.glob("*Flight Simulator*/UserCfg.opt"))
    except OSError:
        pass

    packages_root = local_root / "Packages"
    try:
        for package_app in packages_root.iterdir():
            if not package_app.is_dir():
                continue
            app_name = package_app.name.casefold()
            if "flightsimulator" not in app_name and "flight.simulator" not in app_name \
                    and "limitless" not in app_name:
                continue
            local_cache = package_app / "LocalCache"
            usercfg_paths.add(local_cache / "UserCfg.opt")
            found.extend(_existing_dirs((local_cache / "Packages" / "Community",)))
    except OSError:
        pass

    for usercfg in sorted(usercfg_paths, key=lambda item: str(item).casefold()):
        normalized = _read_installed_packages_path(usercfg)
        if normalized:
            found.append(normalized)

    # A few installs place Packages beside the simulator's roaming configuration.
    common_package_roots = (
        appdata_root / "Microsoft Flight Simulator" / "Packages",
        appdata_root / "Microsoft Flight Simulator 2024" / "Packages",
        local_root / "Microsoft Flight Simulator" / "Packages",
        local_root / "Microsoft Flight Simulator 2024" / "Packages",
    )
    for package_root in common_package_roots:
        normalized = normalize_community_folder(package_root)
        if normalized:
            found.append(normalized)

    unique = {}
    for item in found:
        normalized = normalize_community_folder(item)
        if normalized:
            unique[os.path.normcase(os.path.abspath(normalized))] = normalized
    return sorted(unique.values(), key=str.casefold)


def _aircraft_cfg_paths(community_directory):
    root = Path(community_directory)
    airplane_roots = []
    direct_root = root / "SimObjects" / "Airplanes"
    if direct_root.is_dir():
        airplane_roots.append(direct_root)
    try:
        for package in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            candidate = package / "SimObjects" / "Airplanes"
            if candidate.is_dir() and candidate not in airplane_roots:
                airplane_roots.append(candidate)
    except OSError:
        return

    seen = set()
    for airplane_root in airplane_roots:
        for current, directories, filenames in os.walk(airplane_root):
            directories[:] = sorted(
                (name for name in directories if not name.startswith(".")),
                key=str.casefold,
            )
            config_name = next(
                (name for name in filenames if name.casefold() == "aircraft.cfg"), None
            )
            if config_name:
                path = Path(current) / config_name
                key = os.path.normcase(os.path.abspath(str(path)))
                if key not in seen:
                    seen.add(key)
                    yield path


def _unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1].strip()
    return value


def _type_code(value, allow_compound=False):
    value = _unquote(value).upper().strip()
    if re.fullmatch(r"[A-Z0-9]{3,4}", value):
        return value
    if allow_compound:
        tokens = re.findall(r"[A-Z0-9]{3,4}", value)
        for token in tokens:
            if any(character.isdigit() for character in token):
                return token
    return ""


def parse_aircraft_cfg(path):
    """Read title entries and the most useful ICAO type from one aircraft.cfg."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []

    section = ""
    general = {}
    section_types = {}
    titles = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().casefold()
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().casefold()
        value = value.split(";", 1)[0].strip()
        if section == "general" and key in _TYPE_KEYS:
            general.setdefault(key, value)
        elif section.startswith("fltsim."):
            if key == "title":
                title = _unquote(value)
                if title:
                    titles.append(title)
            elif key in _TYPE_KEYS:
                section_types.setdefault(key, value)

    type_code = ""
    for key in _TYPE_KEYS:
        candidate = general.get(key) or section_types.get(key)
        if candidate:
            type_code = _type_code(candidate, allow_compound=(key == "atc_model"))
            if type_code:
                break

    config_path = str(path.resolve())
    return [AircraftTitle(title, type_code, config_path) for title in dict.fromkeys(titles)]


def _title_has_type(title, type_code):
    return bool(type_code) and re.search(
        rf"(?<![A-Z0-9]){re.escape(type_code)}(?![A-Z0-9])", title.upper()
    ) is not None


class AircraftTitleCatalog:
    """Indexed aircraft.cfg titles from the selected Community folder."""

    def __init__(self, community_directory=None, progress_callback=None):
        self.community_directory = normalize_community_folder(community_directory)
        self.models = []
        self._by_title = {}
        if not self.community_directory:
            return

        config_paths = list(_aircraft_cfg_paths(self.community_directory))
        total_configs = len(config_paths)
        if progress_callback:
            progress_callback(0, total_configs, "")

        seen = set()
        for config_count, config_path in enumerate(config_paths, 1):
            for model in parse_aircraft_cfg(config_path):
                key = model.title.casefold()
                if key in seen:
                    continue
                seen.add(key)
                self.models.append(model)
                self._by_title[key] = model
            if progress_callback and (
                config_count == 1 or config_count % 10 == 0
                or config_count == total_configs
            ):
                progress_callback(config_count, total_configs, str(config_path))

    @property
    def titles(self):
        return [model.title for model in self.models]

    def has_title(self, title):
        return bool(title) and title.casefold() in self._by_title

    def resolve_title(self, type_code="", callsign="", preferred_title=""):
        """Prefer an exact model-map title, then match type and airline in cfg data."""
        preferred_title = str(preferred_title or "").strip()
        if self.has_title(preferred_title):
            return self._by_title[preferred_title.casefold()].title
        if not self.models:
            return preferred_title

        type_code = str(type_code or "").strip().upper()
        candidates = [
            model for model in self.models
            if not type_code or model.icao_type == type_code or _title_has_type(model.title, type_code)
        ]
        if not candidates:
            return ""

        airline_prefix = str(callsign or "").strip().upper()[:3]
        if airline_prefix:
            airline_candidates = [
                model for model in candidates
                if re.search(
                    rf"(?<![A-Z0-9]){re.escape(airline_prefix)}(?![A-Z0-9])",
                    model.title.upper(),
                )
            ]
            if airline_candidates:
                candidates = airline_candidates

        if preferred_title:
            preferred_tokens = [
                token for token in re.split(r"[^A-Z0-9]+", preferred_title.upper())
                if len(token) >= 3
            ]
            scored = sorted(
                candidates,
                key=lambda model: sum(token in model.title.upper() for token in preferred_tokens),
                reverse=True,
            )
            if scored and any(
                token in scored[0].title.upper() for token in preferred_tokens
            ):
                candidates = scored

        fsltl_models = [model for model in candidates if "FSLTL" in model.title.upper()]
        if fsltl_models:
            candidates = fsltl_models
        return candidates[0].title

    def default_title(self, preferred_title="FSLTL_A20N_ZZZZ"):
        title = self.resolve_title("A20N", preferred_title=preferred_title)
        if title:
            return title
        if not self.models:
            return preferred_title
        fsltl_models = [model for model in self.models if "FSLTL" in model.title.upper()]
        return (fsltl_models or self.models)[0].title
