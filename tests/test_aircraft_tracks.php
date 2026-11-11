<?php
require_once __DIR__ . '/../proxy/aircraft_tracks.php';

$errors = array();
function check_track($condition, $message) {
    global $errors;
    if ($condition) echo "  [OK] $message\n";
    else { echo "  [FAIL] $message\n"; $errors[] = $message; }
}

$directory = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'ltu-track-test-'
           . bin2hex(random_bytes(6));
$now = time();
$first = array(
    'hex' => '3C6444', 'flight' => ' ewg51y ', 'lat' => 50.03, 'lon' => 8.57,
    'alt_baro' => 24000, 'track' => 90.1,
);
$moved = $first;
$moved['lat'] = 50.13;
$moved['lon'] = 8.67;

check_track(ltu_track_record_aircraft($first, $now, $directory),
            'Erster ADS-B-Punkt wird gespeichert');
$short_interval = $first;
$short_interval['lat'] = 50.031;
check_track(ltu_track_record_aircraft($short_interval, $now + 5, $directory),
            'Kurze Intervalle werden ohne Fehler dedupliziert');
check_track(ltu_track_record_aircraft($moved, $now + 10, $directory),
            'Bewegter Flieger erhält einen weiteren Spurpunkt');

$track = ltu_track_load('3c6444', $now + 11, $directory);
check_track($track !== null && count($track['points']) === 2
    && $track['flight'] === 'EWG51Y'
    && $track['points'][0]['lat'] === 50.03
    && $track['points'][1]['lon'] === 8.67,
    'ICAO-Hex liefert die geordnete, kompakte Flugspur');

$ground = array(
    'hex' => 'ABCDEF', 'flight' => 'PARKED', 'lat' => 48.0, 'lon' => 11.0,
    'alt_baro' => 'ground', 'track' => null,
);
check_track(ltu_track_record_batch(array($ground, array('flight' => 'NOHEX')), $now, $directory) === 1,
            'Batch speichert nur identifizierbare Flugzeuge');
$ground_track = ltu_track_load('ABCDEF', $now, $directory);
check_track($ground_track !== null && $ground_track['points'][0]['alt'] === 'ground',
            'Bodenhöhe bleibt in der Flugspur erhalten');
$ground_by_flight = ltu_track_load_flight('parked', $now, $directory);
check_track($ground_by_flight !== null && $ground_by_flight['hex'] === 'abcdef',
            'Flugnummer kann auf den passenden ICAO-Hex-Pfad aufgelöst werden');

$expired = ltu_track_load('3c6444', $now + LTU_TRACK_RETENTION_SEC + 11, $directory);
check_track($expired !== null && count($expired['points']) === 0,
            'Punkte älter als eine Stunde werden nicht mehr ausgeliefert');

check_track(ltu_track_file('../3c6444', $directory) === null
    && ltu_track_record_aircraft(array(
        'hex' => '../3c6444', 'lat' => 50, 'lon' => 8,
    ), $now, $directory) === false,
    'Ungültige ICAO-Schlüssel können keine Dateien ausserhalb des Track-Ordners erzeugen');

$stale_path = ltu_track_file('ABCDEF', $directory);
@touch($stale_path, time() - LTU_TRACK_RETENTION_SEC - 10);
ltu_track_prune_expired(time(), $directory);
check_track(!is_file($stale_path), 'Abgelaufene Track-Dateien werden automatisch bereinigt');

foreach ((array)scandir($directory) as $name) {
    if ($name === '.' || $name === '..') continue;
    @unlink($directory . DIRECTORY_SEPARATOR . $name);
}
@rmdir($directory);

if ($errors) exit(1);
echo "Aircraft-track PHP checks passed\n";
